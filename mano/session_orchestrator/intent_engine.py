from __future__ import annotations

import dataclasses
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .intent_runtime import IntentRegistry, TERMINAL_STATES
from .metrics import PrometheusReader
from .models import ServiceIntent, SliceProfile, TrafficMetrics
from .oai_control import OaiTelnetControl
from .policy import assess_service_sla, evaluate_profiles, sla_ok
from .qos_enforcement import QosEnforcer
from .slice_manager import SliceManager
from .telemetry import measure_ping
from .ue_telemetry import interface_for


class LiveTelemetryReader:
    """Read current passive UE metrics from Prometheus with freshness checks."""

    def __init__(self, base_url: str, max_age_seconds: int = 30):
        self.base_url = base_url.rstrip("/")
        self.max_age_seconds = max_age_seconds

    def _query(self, expression: str) -> tuple[float, float] | None:
        url = self.base_url + "/api/v1/query?" + urlencode({"query": expression})
        with urlopen(url, timeout=4) as response:
            document = json.load(response)
        values = document.get("data", {}).get("result", [])
        if not values:
            return None
        timestamp, value = values[0]["value"]
        return float(timestamp), float(value)

    def value(self, metric: str, ue_id: int, pdu_session_id: int) -> float | None:
        selector = (
            f'{metric}{{ue_id="{ue_id}",'
            f'pdu_session_id="{pdu_session_id}"}}'
        )
        result = self._query(selector)
        if result is None:
            return None
        timestamp, value = result
        if time.time() - timestamp > self.max_age_seconds:
            return None
        return value

    def flow_value(self, metric: str, flow_id: str) -> float | None:
        result = self._query(f'{metric}{{flow_id="{flow_id}"}}')
        if result is None:
            return None
        timestamp, value = result
        if time.time() - timestamp > self.max_age_seconds:
            return None
        return value

    def pdu_up(self, ue_id: int, profile: SliceProfile) -> bool:
        return (self.value(
            "ansa_ue_pdu_up", ue_id, profile.pdu_session_id
        ) or 0.0) > 0

    def observe(
        self,
        ue_id: int,
        profile: SliceProfile,
        service: str,
    ) -> tuple[TrafficMetrics, dict[str, Any]]:
        if not self.pdu_up(ue_id, profile):
            raise RuntimeError(
                f"PDU {profile.pdu_session_id} for UE {ue_id} is not active"
            )
        ready = self.value(
            "ansa_ue_passive_sample_ready", ue_id, profile.pdu_session_id
        )
        if not ready:
            raise RuntimeError("passive telemetry has no completed sample yet")
        rx = self.value("ansa_ue_rx_mbps", ue_id, profile.pdu_session_id) or 0.0
        tx = self.value("ansa_ue_tx_mbps", ue_id, profile.pdu_session_id) or 0.0
        rx_rate = self.value(
            "ansa_ue_rx_packets_per_second", ue_id, profile.pdu_session_id
        ) or 0.0
        tx_rate = self.value(
            "ansa_ue_tx_packets_per_second", ue_id, profile.pdu_session_id
        ) or 0.0
        rx_size = self.value(
            "ansa_ue_average_rx_packet_bytes", ue_id, profile.pdu_session_id
        ) or 0.0
        tx_size = self.value(
            "ansa_ue_average_tx_packet_bytes", ue_id, profile.pdu_session_id
        ) or 0.0
        packet_rate = max(rx_rate, tx_rate)
        packet_size = int(max(rx_size, tx_size))
        document = {
            "source": "passive",
            "interface": interface_for(profile, ue_id),
            "throughput_mbps": rx + tx,
            "message_rate_per_second": packet_rate,
            "packet_size_bytes": packet_size,
            "rtt_ms": None,
            "jitter_ms": None,
            "loss_percent": None,
            "service": service,
        }
        # Missing latency metrics remain null in the audit record. Zeroes are
        # only compatibility inputs to the existing explicit-intent ranker and
        # must never be exported as measured RTT/jitter/loss.
        return TrafficMetrics(
            rx + tx, 0.0, 0.0, 0.0, packet_size, packet_rate, 1,
            interface_for(profile, ue_id), profile.gateway,
        ), document

    def observe_flow(
        self,
        flow: dict[str, Any],
        profile: SliceProfile,
    ) -> tuple[TrafficMetrics, dict[str, Any]]:
        ue_id = int(flow["ue_id"])
        if not self.pdu_up(ue_id, profile):
            raise RuntimeError(
                f"source PDU {profile.pdu_session_id} for UE {ue_id} is not active"
            )
        flow_id = str(flow["flow_id"])
        age = self.flow_value("ansa_application_flow_sample_age_seconds", flow_id)
        if age is None or age > self.max_age_seconds:
            raise RuntimeError(f"application flow telemetry is stale: {flow_id}")
        names = {
            "throughput_mbps": "ansa_application_throughput_mbps",
            "rtt_ms": "ansa_application_rtt_ms",
            "jitter_ms": "ansa_application_jitter_ms",
            "loss_percent": "ansa_application_packet_loss_percent",
            "packet_size_bytes": "ansa_application_packet_size_bytes",
            "message_rate_per_second": "ansa_application_message_rate_per_second",
        }
        values = {name: self.flow_value(metric, flow_id) for name, metric in names.items()}
        missing = sorted(name for name, value in values.items() if value is None)
        if missing:
            raise RuntimeError(
                f"Prometheus has not scraped flow {flow_id} metrics: {missing}"
            )
        interface = str(flow.get("interface") or interface_for(profile, ue_id))
        gateway = str(flow.get("gateway") or profile.gateway)
        metrics = TrafficMetrics(
            float(values["throughput_mbps"]),
            float(values["rtt_ms"]),
            float(values["jitter_ms"]),
            float(values["loss_percent"]),
            int(float(values["packet_size_bytes"])),
            float(values["message_rate_per_second"]),
            int(flow.get("sample_count", 0)),
            interface,
            gateway,
        )
        return metrics, {
            "source": "application",
            "flow_id": flow_id,
            "interface": interface,
            "throughput_mbps": metrics.throughput_mbps,
            "message_rate_per_second": metrics.message_rate_per_second,
            "packet_size_bytes": metrics.packet_size_bytes,
            "rtt_ms": metrics.rtt_ms,
            "jitter_ms": metrics.jitter_ms,
            "loss_percent": metrics.loss_percent,
            "service": flow["service"],
            "sample_age_seconds": age,
        }


class IntentExecutor:
    def __init__(
        self,
        registry: IntentRegistry,
        profiles: dict[str, SliceProfile],
        telemetry: LiveTelemetryReader,
        runtime_reader: PrometheusReader,
        slices: SliceManager,
        oai: OaiTelnetControl,
        stable_seconds: int = 5,
        drain_seconds: int = 2,
        idle_timeout_seconds: int = 300,
        ue_ids: list[int] | None = None,
        establish_timeout_seconds: int = 45,
        max_pdu_sessions_per_ue: int = 3,
        qos_enforcer: QosEnforcer | None = None,
        min_flow_samples: int = 2,
        min_observation_seconds: float = 3.0,
    ):
        self.registry = registry
        self.profiles = profiles
        self.telemetry = telemetry
        self.runtime_reader = runtime_reader
        self.slices = slices
        self.oai = oai
        self.stable_seconds = stable_seconds
        self.drain_seconds = drain_seconds
        self.idle_timeout_seconds = idle_timeout_seconds
        self.ue_ids = ue_ids or [1]
        self.establish_timeout_seconds = establish_timeout_seconds
        self.max_pdu_sessions_per_ue = max_pdu_sessions_per_ue
        self.qos_enforcer = qos_enforcer
        self.min_flow_samples = max(1, min_flow_samples)
        self.min_observation_seconds = max(0.0, min_observation_seconds)
        self._ue_locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _ue_lock(self, ue_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._ue_locks.setdefault(ue_id, threading.Lock())

    def _source_profile(
        self,
        intent: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> SliceProfile | None:
        ue_id = int(intent["ue_id"])
        replaced_id = intent.get("replaces_intent_id")
        if replaced_id:
            replaced = snapshot.get("intents", {}).get(replaced_id) or {}
            target = replaced.get("target") or {}
            profile = self.profiles.get(target.get("profile"))
            if profile and self.telemetry.pdu_up(ue_id, profile):
                return profile
        default = self.profiles["default"]
        if self.telemetry.pdu_up(ue_id, default):
            return default
        seen = set()
        for profile in self.profiles.values():
            if profile.pdu_session_id in seen:
                continue
            seen.add(profile.pdu_session_id)
            if self.telemetry.pdu_up(ue_id, profile):
                return profile
        return None

    def process(self, intent_id: str) -> None:
        snapshot = self.registry.snapshot()
        intent = snapshot["intents"].get(intent_id)
        if not intent or intent["state"] not in {"PENDING", "WAITING_FOR_UE", "WAITING_FOR_TELEMETRY"}:
            return
        ue_id = int(intent["ue_id"])
        with self._ue_lock(ue_id):
            source = self._source_profile(intent, snapshot)
            if source is None:
                self.registry.transition(intent_id, "WAITING_FOR_UE")
                return
            try:
                flow_id = intent.get("flow_id")
                if flow_id:
                    flow = self.registry.flow(flow_id)
                    if int(flow.get("sample_count", 0)) < self.min_flow_samples:
                        raise RuntimeError(
                            f"flow {flow_id} needs at least {self.min_flow_samples} samples"
                        )
                    first_sample = flow.get("first_sample_at")
                    if not first_sample or (
                        datetime.now(timezone.utc) - datetime.fromisoformat(first_sample)
                    ).total_seconds() < self.min_observation_seconds:
                        raise RuntimeError(
                            f"flow {flow_id} observation window is not complete"
                        )
                    observed, audit = self.telemetry.observe_flow(flow, source)
                else:
                    observed, audit = self.telemetry.observe(
                        ue_id, source, intent["service"]
                    )
            except RuntimeError as exc:
                self.registry.transition(
                    intent_id, "WAITING_FOR_TELEMETRY", error=str(exc)
                )
                return
            self.registry.transition(
                intent_id, "DECIDING", observed=audit, error=None
            )
            runtime = self.runtime_reader.read_slice_runtime(self.profiles)
            target, candidates, reason = evaluate_profiles(
                ServiceIntent(intent["service"], ue_id),
                observed,
                self.profiles,
                runtime,
            )
            self.registry.transition(
                intent_id,
                "ENSURING_SLICE",
                decision_reason=reason,
                candidates=[dataclasses.asdict(value) for value in candidates],
            )
            established = False
            try:
                active_pdus = {
                    profile.pdu_session_id
                    for profile in self.profiles.values()
                    if self.telemetry.pdu_up(ue_id, profile)
                }
                if (
                    target.pdu_session_id not in active_pdus
                    and len(active_pdus) >= self.max_pdu_sessions_per_ue
                ):
                    raise RuntimeError(
                        f"UE {ue_id} PDU admission limit reached: "
                        f"{len(active_pdus)}/{self.max_pdu_sessions_per_ue}"
                    )
                action = self.slices.ensure(target)
                self.registry.transition(intent_id, "ESTABLISHING", slice_action=action)
                target_interface = interface_for(target, ue_id)
                if not self.telemetry.pdu_up(ue_id, target):
                    self.oai.establish(target, ue_id - 1)
                    established = True
                self.registry.transition(intent_id, "VALIDATING")
                establish_deadline = (
                    time.monotonic() + max(1, self.establish_timeout_seconds)
                )
                while not self.telemetry.pdu_up(ue_id, target):
                    if time.monotonic() >= establish_deadline:
                        raise RuntimeError(
                            f"PDU {target.pdu_session_id} did not become observable "
                            f"within {self.establish_timeout_seconds}s"
                        )
                    time.sleep(1)

                # Require one uninterrupted healthy window. Interface races
                # during PDU creation are retried until the overall deadline.
                stable_since = time.monotonic()
                stable_required = max(0, self.stable_seconds)
                validated_once = False
                last_error: Exception | None = None
                while (
                    not validated_once
                    or time.monotonic() - stable_since < stable_required
                ):
                    try:
                        measured = measure_ping(
                            target_interface, target.gateway,
                            min(3, max(1, self.stable_seconds)), 56, 2,
                        )
                        if not sla_ok(measured, target):
                            raise RuntimeError(
                                "target validation failed: "
                                f"{dataclasses.asdict(measured)}"
                            )
                        last_error = None
                        validated_once = True
                    except (OSError, RuntimeError) as exc:
                        last_error = exc
                        stable_since = time.monotonic()
                        if time.monotonic() >= establish_deadline:
                            raise RuntimeError(
                                f"target PDU did not remain stable: {exc}"
                            ) from exc
                        time.sleep(1)
                if last_error:
                    raise last_error
                assessment = assess_service_sla(
                    measured,
                    target.service_class or target.name,
                    dimensions=("rtt", "jitter", "loss"),
                )
                self.registry.transition(
                    intent_id,
                    "VALIDATING",
                    validation={
                        "connectivity": "MET",
                        "service_sla": assessment,
                    },
                )
                self.registry.bind(intent_id, target, target_interface)
            except Exception as exc:
                # Allocation/reconciliation can briefly drop the UE. Keep the
                # intent retryable rather than converting a recoverable radio
                # event into a terminal orchestration failure.
                if self._source_profile(intent, self.registry.snapshot()) is None:
                    self.registry.transition(
                        intent_id, "WAITING_FOR_UE", error=str(exc)
                    )
                    return
                if established:
                    try:
                        self.oai.release(target.pdu_session_id, ue_id - 1)
                    except Exception:
                        pass
                self.registry.transition(intent_id, "FAILED", error=str(exc))

    def acknowledge(self, intent_id: str) -> list[dict[str, Any]]:
        release = self.registry.acknowledge_application(intent_id)
        self._release_sessions(release)
        return release

    def acknowledge_async(self, intent_id: str) -> list[dict[str, Any]]:
        """Commit the binding synchronously and drain old sessions in background."""
        release = self.registry.acknowledge_application(intent_id)
        self._release_in_background(release)
        return release

    def end(self, intent_id: str) -> list[dict[str, Any]]:
        release = self.registry.end(intent_id)
        self._release_sessions(release)
        return release

    def end_async(self, intent_id: str) -> list[dict[str, Any]]:
        release = self.registry.end(intent_id)
        self._release_in_background(release)
        return release

    def _release_in_background(self, sessions: list[dict[str, Any]]) -> None:
        if sessions:
            threading.Thread(
                target=self._release_sessions,
                args=(sessions,),
                daemon=True,
                name="pdu-release-worker",
            ).start()

    def _release_sessions(self, sessions: list[dict[str, Any]]) -> None:
        if sessions:
            time.sleep(max(0, self.drain_seconds))
        for session in sessions:
            ue_id = int(session["ue_id"])
            pdu_session_id = int(session["pdu_session_id"])
            try:
                self.oai.release(pdu_session_id, ue_id - 1)
            except Exception as exc:
                self.registry.mark_session_state(
                    ue_id, pdu_session_id, "RELEASE_FAILED", release_error=str(exc)
                )
            else:
                self.registry.mark_session_state(
                    ue_id, pdu_session_id, "RELEASE_REQUESTED"
                )

    def reap_idle_slices(self) -> list[dict[str, Any]]:
        """Deallocate only after refcount, live PDU and idle checks agree."""
        now = datetime.now(timezone.utc)
        actions = []
        snapshot = self.registry.snapshot()
        for session in snapshot.get("sessions", {}).values():
            if session.get("references") or not session.get("empty_since"):
                continue
            empty_since = datetime.fromisoformat(session["empty_since"])
            if (now - empty_since).total_seconds() < self.idle_timeout_seconds:
                continue
            profile = self.profiles.get(session["profile"])
            if not profile or profile.protected:
                continue
            if any(self.telemetry.pdu_up(ue_id, profile) for ue_id in self.ue_ids):
                continue
            action = self.slices.deallocate_if_idle(
                profile, active_pdu_count=0
            )
            self.registry.mark_session_state(
                int(session["ue_id"]),
                int(session["pdu_session_id"]),
                "IDLE",
                slice_lifecycle=action,
            )
            actions.append({"slice_id": profile.namespace, "action": action})
        return actions

    def reconcile_sessions(self) -> list[dict[str, Any]]:
        """Reconcile persisted session state with each UE's live PDU TUN.

        The registry is an orchestration audit log, not a source of radio
        truth. This prevents a UE/gNB restart from leaving stale ACTIVE
        sessions on the API and dashboard, while retaining references so an
        application intent can recover when the same PDU returns.
        """
        actions = []
        snapshot = self.registry.snapshot()
        for session in snapshot.get("sessions", {}).values():
            profile = self.profiles.get(session.get("profile"))
            if not profile:
                continue
            ue_id = int(session["ue_id"])
            pdu_session_id = int(session["pdu_session_id"])
            try:
                live = self.telemetry.pdu_up(ue_id, profile)
            except Exception:
                # Missing telemetry is unknown, not proof of a teardown.
                continue
            current = session.get("state", "UNKNOWN")
            desired = current
            if live and current in {"LOST", "RELEASED", "IDLE", "RELEASE_REQUESTED"}:
                desired = "ACTIVE" if session.get("references") else "ORPHANED"
            elif not live and current == "ACTIVE":
                desired = "LOST"
            elif not live and current == "RELEASE_REQUESTED":
                desired = "RELEASED"
            elif not live and current == "ORPHANED":
                desired = "IDLE"
            if desired != current:
                self.registry.mark_session_state(
                    ue_id,
                    pdu_session_id,
                    desired,
                    live_pdu=bool(live),
                    reconciled_at=datetime.now(timezone.utc).isoformat(),
                )
                actions.append({
                    "ue_id": ue_id,
                    "pdu_session_id": pdu_session_id,
                    "from": current,
                    "to": desired,
                })
        return actions

    def reconcile_qos(self) -> list[dict[str, Any]]:
        if not self.qos_enforcer:
            return []
        return self.qos_enforcer.reconcile(self.profiles)


def _prom_label(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_intent_metrics(document: dict[str, Any]) -> str:
    lines = []
    now = datetime.now(timezone.utc)
    for flow in document.get("flows", {}).values():
        if flow.get("state") == "ENDED":
            continue
        labels = (
            f'flow_id="{_prom_label(flow["flow_id"])}",ue_id="{flow["ue_id"]}",'
            f'service="{_prom_label(flow["service"])}",intent_source="{_prom_label(flow.get("intent_source", "application"))}",'
            f'state="{_prom_label(flow.get("state", "OBSERVING"))}",interface="{_prom_label(flow.get("interface", ""))}"'
        )
        lines.extend((
            "# TYPE ansa_application_flow_info gauge",
            f"ansa_application_flow_info{{{labels}}} 1",
        ))
        sample_at = flow.get("last_sample_at")
        if sample_at:
            age = max(0.0, (now - datetime.fromisoformat(sample_at)).total_seconds())
            lines.extend((
                "# TYPE ansa_application_flow_sample_age_seconds gauge",
                f'ansa_application_flow_sample_age_seconds{{flow_id="{_prom_label(flow["flow_id"])}",ue_id="{flow["ue_id"]}",service="{_prom_label(flow["service"])}"}} {age}',
                "# TYPE ansa_application_flow_sample_count gauge",
                f'ansa_application_flow_sample_count{{flow_id="{_prom_label(flow["flow_id"])}",ue_id="{flow["ue_id"]}",service="{_prom_label(flow["service"])}"}} {flow.get("sample_count", 0)}',
            ))
        metric_names = {
            "throughput_mbps": "ansa_application_throughput_mbps",
            "rtt_ms": "ansa_application_rtt_ms",
            "jitter_ms": "ansa_application_jitter_ms",
            "loss_percent": "ansa_application_packet_loss_percent",
            "packet_size_bytes": "ansa_application_packet_size_bytes",
            "message_rate_per_second": "ansa_application_message_rate_per_second",
        }
        for key, metric_name in metric_names.items():
            if flow.get("metrics") and key in flow["metrics"]:
                lines.extend((
                    f"# TYPE {metric_name} gauge",
                    f'{metric_name}{{flow_id="{_prom_label(flow["flow_id"])}",ue_id="{flow["ue_id"]}",service="{_prom_label(flow["service"])}",interface="{_prom_label(flow.get("interface", ""))}"}} {flow["metrics"][key]}',
                ))
    for intent in document.get("intents", {}).values():
        # Grafana panels describe current desired/runtime state. Terminal
        # records remain available from the state API as an audit trail.
        if intent.get("state") in TERMINAL_STATES:
            continue
        target = intent.get("target") or {}
        labels = (
            f'intent_id="{_prom_label(intent["intent_id"])}",ue_id="{intent["ue_id"]}",'
            f'service="{_prom_label(intent["service"])}",mode="{_prom_label(intent["mode"])}",'
            f'flow_id="{_prom_label(intent.get("flow_id") or "")}",intent_source="{_prom_label(intent.get("intent_source", "operator"))}",'
            f'state="{_prom_label(intent["state"])}",slice_id="{_prom_label(target.get("slice_id", ""))}",'
            f'pdu_session_id="{target.get("pdu_session_id", "")}"'
        )
        lines.extend((
            "# TYPE ansa_application_intent_info gauge",
            f"ansa_application_intent_info{{{labels}}} 1",
        ))
        for candidate in intent.get("candidates", []):
            candidate_labels = (
                f'intent_id="{_prom_label(intent["intent_id"])}",ue_id="{intent["ue_id"]}",'
                f'service="{_prom_label(intent["service"])}",profile="{_prom_label(candidate["profile"])}",'
                f'slice_id="{_prom_label(candidate["slice_id"])}",reason="{_prom_label(candidate["reason"])}",'
                f'evidence_status="{_prom_label(candidate.get("evidence_status", "UNVERIFIED"))}"'
            )
            lines.extend((
                "# TYPE ansa_intent_candidate_score gauge",
                f"ansa_intent_candidate_score{{{candidate_labels}}} {candidate['score']}",
                "# TYPE ansa_intent_candidate_effective_capacity_mbps gauge",
                f"ansa_intent_candidate_effective_capacity_mbps{{{candidate_labels}}} {candidate.get('effective_capacity_mbps', 0)}",
            ))
            for component in (
                "requirement_fit", "qos_headroom", "health_load", "intent_affinity",
                "evidence_confidence",
            ):
                lines.extend((
                    "# TYPE ansa_intent_candidate_score_component gauge",
                    f'ansa_intent_candidate_score_component{{{candidate_labels},component="{component}"}} {candidate.get(component, 0)}',
                ))
        validation = intent.get("validation", {}).get("service_sla", {})
        if validation:
            sla_labels = (
                f'intent_id="{intent["intent_id"]}",ue_id="{intent["ue_id"]}",'
                f'service="{intent["service"]}",service_class="{validation.get("service_class", "")}",'
                f'status="{validation.get("status", "UNKNOWN")}"'
            )
            lines.extend((
                "# TYPE ansa_intent_sla_met gauge",
                f'ansa_intent_sla_met{{{sla_labels}}} {1 if validation.get("status") == "MET" else 0}',
            ))
            for dimension, passed in validation.get("checks", {}).items():
                lines.extend((
                    "# TYPE ansa_intent_sla_dimension_met gauge",
                    f'ansa_intent_sla_dimension_met{{{sla_labels},dimension="{dimension}"}} {1 if passed else 0}',
                ))
    for session in document.get("sessions", {}).values():
        labels = (
            f'ue_id="{session["ue_id"]}",pdu_session_id="{session["pdu_session_id"]}",'
            f'slice_id="{session["slice_id"]}",interface="{session["interface"]}"'
        )
        lines.extend((
            "# TYPE ansa_pdu_reference_count gauge",
            f"ansa_pdu_reference_count{{{labels}}} {len(session['references'])}",
        ))
    return "\n".join(lines) + "\n"


class IntentService:
    def __init__(self, registry: IntentRegistry, executor: IntentExecutor, poll_seconds: float = 1.0):
        self.registry = registry
        self.executor = executor
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self.registry.recover_incomplete()

    def worker(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            snapshot = self.registry.snapshot()
            for intent in snapshot.get("intents", {}).values():
                if intent["state"] in {"PENDING", "WAITING_FOR_UE", "WAITING_FOR_TELEMETRY"}:
                    try:
                        self.executor.process(intent["intent_id"])
                    except Exception as exc:
                        self.registry.transition(
                            intent["intent_id"], "FAILED", error=str(exc)
                        )
            for intent_id in self.registry.stale_intent_ids():
                try:
                    self.executor.end_async(intent_id)
                except (KeyError, ValueError):
                    pass
            try:
                self.executor.reconcile_sessions()
            except Exception:
                # Reconciliation is best-effort during Prometheus outages.
                pass
            try:
                self.executor.reap_idle_slices()
            except Exception:
                # Reaping is best-effort and must never stop intent processing.
                pass
            try:
                self.executor.reconcile_qos()
            except Exception:
                # QoS reconciliation is idempotent and best-effort. A failure
                # is surfaced by the enforcement metric, not by stopping API work.
                pass

    def serve(self, host: str, port: int) -> None:
        service = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, status: int, value: Any):
                body = json.dumps(value, indent=2).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length) or b"{}")

            def do_GET(self):
                path = urlparse(self.path).path
                if path in {"/", "/health"}:
                    self._json(200, {"status": "ok"})
                elif path == "/api/v1/state":
                    self._json(200, service.registry.snapshot())
                elif __import__("re").fullmatch(r"/api/v1/flows/([^/]+)", path):
                    flow_id = path.rsplit("/", 1)[-1]
                    try:
                        flow = service.registry.flow(flow_id)
                    except KeyError as exc:
                        self._json(404, {"error": str(exc)})
                        return
                    intent_id = flow.get("intent_id")
                    intent = service.registry.snapshot().get("intents", {}).get(intent_id)
                    self._json(200, {**flow, "intent": intent})
                elif path == "/metrics":
                    body = render_intent_metrics(service.registry.snapshot()).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def do_POST(self):
                path = urlparse(self.path).path
                try:
                    if path == "/api/v1/intents":
                        payload = self._body()
                        ue_id = int(payload.get("ue_id", 1))
                        if ue_id not in service.executor.ue_ids:
                            raise ValueError(
                                f"ue_id {ue_id} is not configured; allowed: {service.executor.ue_ids}"
                            )
                        result = service.registry.submit(
                            payload["service"],
                            ue_id,
                            payload.get("mode", "auto"),
                            payload.get("replaces_intent_id"),
                            int(payload.get("lease_seconds", 60)),
                            payload.get("flow_id"),
                            payload.get("intent_source", "operator"),
                        )
                        self._json(202, result)
                        return
                    if path == "/api/v1/flows":
                        payload = self._body()
                        ue_id = int(payload.get("ue_id", 1))
                        if ue_id not in service.executor.ue_ids:
                            raise ValueError(
                                f"ue_id {ue_id} is not configured; allowed: {service.executor.ue_ids}"
                            )
                        result = service.registry.register_flow(
                            payload["service"],
                            ue_id,
                            payload.get("interface", ""),
                            payload.get("gateway", ""),
                            payload.get("flow_id"),
                            payload.get("intent_source", "application"),
                        )
                        self._json(201, result)
                        return
                    match = __import__("re").fullmatch(
                        r"/api/v1/flows/([^/]+)/samples", path
                    )
                    if match:
                        payload = self._body()
                        result = service.registry.update_flow(
                            match.group(1),
                            payload["metrics"],
                            payload.get("interface"),
                            payload.get("gateway"),
                        )
                        self._json(200, result)
                        return
                    match = __import__("re").fullmatch(
                        r"/api/v1/intents/([^/]+)/ack", path
                    )
                    if match:
                        released = service.executor.acknowledge_async(match.group(1))
                        self._json(200, {"released": released})
                        return
                    match = __import__("re").fullmatch(
                        r"/api/v1/intents/([^/]+)/heartbeat", path
                    )
                    if match:
                        result = service.registry.heartbeat(match.group(1))
                        self._json(200, result)
                        return
                    self.send_error(404)
                except (KeyError, ValueError) as exc:
                    self._json(400, {"error": str(exc)})

            def do_DELETE(self):
                path = urlparse(self.path).path
                flow_match = __import__("re").fullmatch(r"/api/v1/flows/([^/]+)", path)
                if flow_match:
                    try:
                        flow = service.registry.flow(flow_match.group(1))
                        intent_id = flow.get("intent_id")
                        intent = service.registry.snapshot().get("intents", {}).get(intent_id)
                        if intent and intent.get("state") not in TERMINAL_STATES:
                            service.executor.end_async(intent_id)
                        result = service.registry.close_flow(flow_match.group(1))
                        self._json(200, result)
                    except KeyError as exc:
                        self._json(404, {"error": str(exc)})
                    return
                match = __import__("re").fullmatch(r"/api/v1/intents/([^/]+)", path)
                if not match:
                    self.send_error(404)
                    return
                try:
                    released = service.executor.end_async(match.group(1))
                    self._json(200, {"released": released})
                except KeyError as exc:
                    self._json(404, {"error": str(exc)})

            def log_message(self, *_args):
                return

        thread = threading.Thread(target=self.worker, daemon=True)
        thread.start()
        try:
            ThreadingHTTPServer((host, port), Handler).serve_forever()
        finally:
            self._stop.set()
            thread.join(timeout=3)


def api_request(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            document = json.load(exc)
            message = document.get("error", str(exc))
        except (ValueError, AttributeError):
            message = str(exc)
        raise ValueError(message) from None
