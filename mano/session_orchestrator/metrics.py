from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from .models import SliceProfile, SliceRuntime, TrafficMetrics
from .slice_runtime import SliceRuntimeCollector
from .ue_telemetry import UeTelemetryCollector


def _label(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def interface_is_active(interface: str) -> bool:
    operstate = Path("/sys/class/net") / interface / "operstate"
    return (
        operstate.exists()
        and operstate.read_text(encoding="utf-8").strip() != "down"
    )


def render_metrics(snapshot: dict[str, Any], verify_interfaces: bool = False) -> str:
    labels = 'ue_id="{}",service="{}",flow_id="{}"'.format(
        _label(snapshot.get("ue_id", 1)), _label(snapshot.get("service", "unknown")),
        _label(snapshot.get("flow_id", "")),
    )
    values = snapshot.get("metrics") or {}
    names = {
        "throughput_mbps": "ansa_ue_throughput_mbps",
        "rtt_ms": "ansa_ue_rtt_ms",
        "jitter_ms": "ansa_ue_jitter_ms",
        "loss_percent": "ansa_ue_packet_loss_percent",
        "packet_size_bytes": "ansa_ue_packet_size_bytes",
        "message_rate_per_second": "ansa_ue_message_rate_per_second",
    }
    lines = []
    for key, metric in names.items():
        if key in values:
            lines.extend((f"# TYPE {metric} gauge", f"{metric}{{{labels}}} {values[key]}"))
    state = snapshot.get("state")
    if state:
        lines.extend(("# TYPE ansa_intent_flow_info gauge", f'ansa_intent_flow_info{{{labels},state="{_label(state)}"}} 1'))

    source, target = snapshot.get("source"), snapshot.get("target")
    sessions = (
        (source, "source", snapshot.get(
            "source_session_active", not snapshot.get("old_session_released", False)
        )),
        (target, "target", snapshot.get(
            "target_session_active", bool(target) and snapshot.get("old_session_released", False)
        )),
    )
    for profile, role, active in sessions:
        if not profile:
            continue
        if verify_interfaces:
            # The snapshot records the last orchestrated transition, while a UE
            # can be restarted independently. Linux TUN state is authoritative
            # for the current dashboard view in the single-host OAI lab.
            active = interface_is_active(profile["tun_pattern"])
        session_labels = (
            f'{labels},pdu_session_id="{profile["pdu_session_id"]}",'
            f'slice_id="{_label(profile["namespace"])}",sst="{profile["sst"]}",'
            f'sd="{_label(profile["sd"])}",dnn="{_label(profile["dnn"])}",'
            f'interface="{_label(profile["tun_pattern"])}",role="{role}",'
            f'state="{"active" if active else "inactive"}"'
        )
        lines.extend(("# TYPE ansa_pdu_session_info gauge", f"ansa_pdu_session_info{{{session_labels}}} 1"))

    if target:
        decision_labels = (
            f'{labels},profile="{_label(target["name"])}",'
            f'slice_id="{_label(target["namespace"])}",sst="{target["sst"]}",'
            f'sd="{_label(target["sd"])}",dnn="{_label(target["dnn"])}",'
            f'reason="{_label(snapshot.get("decision_reason", ""))}"'
        )
        lines.extend(("# TYPE ansa_intent_slice_selected_info gauge", f"ansa_intent_slice_selected_info{{{decision_labels}}} 1"))
    for candidate in snapshot.get("decision_candidates", []):
        candidate_labels = (
            f'{labels},profile="{_label(candidate["profile"])}",'
            f'slice_id="{_label(candidate["slice_id"])}",sst="{candidate["sst"]}",'
            f'sd="{_label(candidate["sd"])}",dnn="{_label(candidate["dnn"])}",'
            f'reason="{_label(candidate["reason"])}",'
            f'evidence_status="{_label(candidate.get("evidence_status", "UNVERIFIED"))}"'
        )
        lines.extend(("# TYPE ansa_intent_slice_score gauge", f'ansa_intent_slice_score{{{candidate_labels}}} {candidate["score"]}'))
        lines.extend((
            "# TYPE ansa_intent_slice_eligible gauge",
            f'ansa_intent_slice_eligible{{{candidate_labels}}} {1 if candidate.get("eligible", True) else 0}',
        ))
        for component in (
            "requirement_fit", "qos_headroom", "health_load", "intent_affinity",
            "evidence_confidence",
        ):
            lines.extend((
                "# TYPE ansa_intent_slice_score_component gauge",
                f'ansa_intent_slice_score_component{{{candidate_labels},component="{component}"}} {candidate.get(component, 0)}',
            ))
    return "\n".join(lines) + "\n"


class SnapshotStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def write(self, value: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temp.replace(self.path)

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            return json.loads(self.path.read_text(encoding="utf-8"))


class PrometheusReader:
    def __init__(self, base_url: str, wait_seconds: int = 10):
        self.base_url = base_url.rstrip("/")
        self.wait_seconds = wait_seconds

    def _value(self, metric: str, ue_id: int, service: str, flow_id: str) -> float | None:
        selector = f'{metric}{{ue_id="{ue_id}",service="{service}",flow_id="{flow_id}"}}'
        url = self.base_url + "/api/v1/query?" + urlencode({"query": selector})
        with urlopen(url, timeout=4) as response:
            document = json.load(response)
        values = document.get("data", {}).get("result", [])
        return float(values[0]["value"][1]) if values else None

    def _slice_value(self, metric: str, slice_id: str) -> float | None:
        selector = f'{metric}{{slice_id="{slice_id}"}}'
        url = self.base_url + "/api/v1/query?" + urlencode({"query": selector})
        try:
            with urlopen(url, timeout=4) as response:
                document = json.load(response)
            values = document.get("data", {}).get("result", [])
            return float(values[0]["value"][1]) if values else None
        except (OSError, TimeoutError, ValueError, KeyError, IndexError):
            # Runtime telemetry enriches ranking but must not break the existing
            # PDU flow when Prometheus is temporarily unavailable.
            return None

    def read_slice_runtime(
        self, profiles: dict[str, SliceProfile]
    ) -> dict[str, SliceRuntime]:
        result = {}
        for profile in profiles.values():
            if profile.name == "default" or profile.namespace in result:
                continue
            scrape = self._slice_value("open5gs_scrape_success", profile.namespace)
            exporter_health = self._slice_value("ansa_slice_health", profile.namespace)
            pdu = self._slice_value("open5gs_pdu_count", profile.namespace)
            total_pods = self._slice_value(
                "ansa_slice_total_pods", profile.namespace
            )
            cpu = self._slice_value("ansa_slice_cpu_load_ratio", profile.namespace)
            memory = self._slice_value("ansa_slice_memory_load_ratio", profile.namespace)
            enforcement = self._slice_value(
                "ansa_slice_downlink_enforcement_up", profile.namespace
            )
            enforced_rate = self._slice_value(
                "ansa_slice_enforced_downlink_mbps", profile.namespace
            )
            exists = total_pods is None or total_pods > 0
            result[profile.namespace] = SliceRuntime(
                exists=exists,
                healthy=exists and (scrape is None or scrape > 0) and (
                    exporter_health is None or exporter_health > 0
                ),
                pdu_sessions=pdu or 0.0,
                cpu_load_ratio=cpu or 0.0,
                memory_load_ratio=memory or 0.0,
                downlink_enforcement_up=(
                    None if enforcement is None else enforcement > 0
                ),
                enforced_downlink_mbps=enforced_rate,
            )
        return result

    def read_traffic(
        self, measured: TrafficMetrics, ue_id: int, service: str, flow_id: str
    ) -> TrafficMetrics:
        names = {
            "throughput_mbps": "ansa_ue_throughput_mbps",
            "rtt_ms": "ansa_ue_rtt_ms",
            "jitter_ms": "ansa_ue_jitter_ms",
            "loss_percent": "ansa_ue_packet_loss_percent",
            "packet_size_bytes": "ansa_ue_packet_size_bytes",
            "message_rate_per_second": "ansa_ue_message_rate_per_second",
        }
        deadline = time.monotonic() + self.wait_seconds
        while True:
            values = {
                key: self._value(metric, ue_id, service, flow_id)
                for key, metric in names.items()
            }
            if all(value is not None for value in values.values()):
                return TrafficMetrics(
                    values["throughput_mbps"], values["rtt_ms"], values["jitter_ms"],
                    values["loss_percent"], int(values["packet_size_bytes"]),
                    values["message_rate_per_second"], measured.samples,
                    measured.interface, measured.gateway,
                )
            if time.monotonic() >= deadline:
                missing = [key for key, value in values.items() if value is None]
                raise RuntimeError(f"Prometheus did not scrape required metrics: {missing}")
            time.sleep(1)


def serve(
    store: SnapshotStore,
    host: str,
    port: int,
    collector: SliceRuntimeCollector | None = None,
    ue_collector: UeTelemetryCollector | None = None,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/metrics", "/state"):
                self.send_error(404)
                return
            payload = store.read()
            if self.path == "/metrics":
                text = render_metrics(payload, verify_interfaces=True)
                if collector:
                    try:
                        text += collector.render()
                    except Exception:
                        text += "# TYPE ansa_slice_collector_success gauge\nansa_slice_collector_success 0\n"
                    else:
                        text += "# TYPE ansa_slice_collector_success gauge\nansa_slice_collector_success 1\n"
                if ue_collector:
                    try:
                        text += ue_collector.render()
                    except Exception:
                        text += "# TYPE ansa_ue_collector_success gauge\nansa_ue_collector_success 0\n"
                    else:
                        text += "# TYPE ansa_ue_collector_success gauge\nansa_ue_collector_success 1\n"
                body = text.encode()
                content_type = "text/plain; version=0.0.4"
            else:
                body = json.dumps(payload, indent=2).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()
