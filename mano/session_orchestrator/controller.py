from __future__ import annotations

import dataclasses
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .metrics import PrometheusReader, SnapshotStore
from .models import FlowEvent, FlowResult, FlowState, ServiceIntent, SliceProfile, SliceRuntime, TrafficMetrics
from .oai_control import OaiTelnetControl
from .policy import evaluate_profiles, sla_ok
from .slice_manager import SliceManager
from .steering import BoundInterfaceSteering
from .telemetry import measure_ping


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionOrchestrator:
    def __init__(
        self,
        profiles: dict[str, SliceProfile],
        store: SnapshotStore,
        slices: SliceManager,
        oai: OaiTelnetControl,
        steering: BoundInterfaceSteering,
        stable_seconds: int = 15,
        drain_seconds: int = 3,
        measure: Callable[..., TrafficMetrics] = measure_ping,
        sleep: Callable[[float], None] = time.sleep,
        prometheus: PrometheusReader | None = None,
    ):
        self.profiles = profiles
        self.store = store
        self.slices = slices
        self.oai = oai
        self.steering = steering
        self.stable_seconds = stable_seconds
        self.drain_seconds = drain_seconds
        self.measure = measure
        self.sleep = sleep
        self.prometheus = prometheus

    def _publish(self, result: FlowResult) -> None:
        document = result.as_dict()
        document["service"] = result.intent.service
        document["ue_id"] = result.intent.ue_id
        self.store.write(document)

    def _transition(self, result: FlowResult, state: FlowState, detail: str) -> None:
        result.state = state
        result.events.append(FlowEvent(utc_now(), state.value, detail))
        self._publish(result)

    def _measure(self, profile: SliceProfile, intent: ServiceIntent, duration: int) -> TrafficMetrics:
        return self.measure(
            profile.tun_pattern,
            profile.gateway,
            duration,
            intent.packet_size_bytes,
            intent.message_rate_per_second,
        )

    def run(self, intent: ServiceIntent) -> FlowResult:
        source = self.profiles["default"]
        result = FlowResult(
            flow_id=f"flow-{uuid.uuid4().hex[:12]}",
            state=FlowState.DEFAULT_ACTIVE,
            intent=intent,
            source=source,
            active_interface=source.tun_pattern,
        )
        self._transition(result, FlowState.DEFAULT_ACTIVE, "default PDU remains active")
        target_established = False
        traffic_switched = False
        try:
            self._transition(result, FlowState.OBSERVING, "measuring real traffic on default PDU")
            result.metrics = self._measure(source, intent, intent.duration_seconds)
            self._publish(result)
            if self.prometheus:
                result.metrics = self.prometheus.read_traffic(
                    result.metrics, intent.ue_id, intent.service, result.flow_id
                )
                self._publish(result)

            self._transition(result, FlowState.DECIDING, "selecting S-NSSAI/DNN from intent and observed metrics")
            slice_runtime = (
                self.prometheus.read_slice_runtime(self.profiles)
                if self.prometheus else {}
            )
            target, candidates, reason = evaluate_profiles(
                intent, result.metrics, self.profiles, slice_runtime
            )
            result.target = target
            result.decision_candidates = candidates
            result.decision_reason = reason
            self._publish(result)
            if target.pdu_session_id == source.pdu_session_id:
                self._transition(result, FlowState.COMPLETED, "default PDU already satisfies selected profile")
                return result

            self._transition(result, FlowState.ENSURING_SLICE, f"checking {target.namespace}")
            result.slice_action = self.slices.ensure(target)
            self._publish(result)

            self._transition(result, FlowState.ESTABLISHING, f"establishing PDU session {target.pdu_session_id}; old PDU is retained")
            self.oai.establish(target, intent.ue_id - 1)
            target_established = True
            result.target_session_active = True

            self._transition(result, FlowState.VALIDATING, "validating TUN, gateway and SLA stability")
            deadline = time.monotonic() + max(1, self.stable_seconds)
            last_metrics = None
            while True:
                last_metrics = self._measure(target, intent, min(5, max(1, self.stable_seconds)))
                if not sla_ok(last_metrics, target):
                    raise RuntimeError(f"target PDU SLA validation failed: {dataclasses.asdict(last_metrics)}")
                if time.monotonic() >= deadline:
                    break
                self.sleep(min(1, max(0, deadline - time.monotonic())))
            result.metrics = last_metrics
            self._publish(result)

            self._transition(result, FlowState.STEERING, f"new service flows bind to {target.tun_pattern}")
            self.steering.switch_new_flows(target.tun_pattern)
            traffic_switched = True
            result.active_interface = target.tun_pattern

            self._transition(result, FlowState.DRAINING, "allowing old-interface flows to drain")
            self.steering.drain(self.drain_seconds)

            self._transition(result, FlowState.RELEASING, f"releasing old PDU session {source.pdu_session_id}")
            self.oai.release(source.pdu_session_id, intent.ue_id - 1)
            if not self.steering.wait_interface_down(source.tun_pattern, 10):
                raise RuntimeError(f"old PDU interface did not go down: {source.tun_pattern}")
            result.old_session_released = True
            result.source_session_active = False
            self._publish(result)

            post_release = self._measure(target, intent, 3)
            if not sla_ok(post_release, target):
                raise RuntimeError("target PDU failed after old-session release")
            result.metrics = post_release
            lifecycle = self.slices.deallocate_if_idle(source, active_pdu_count=0)
            result.events.append(FlowEvent(utc_now(), FlowState.RELEASING.value, f"source slice lifecycle: {lifecycle}"))

            self._transition(result, FlowState.COMPLETED, "make-before-break transition completed")
            return result
        except Exception as exc:
            result.error = str(exc)
            if target_established and not traffic_switched and result.target:
                try:
                    self.oai.release(result.target.pdu_session_id, intent.ue_id - 1)
                except Exception as rollback_exc:
                    result.error += f"; rollback release failed: {rollback_exc}"
            self._transition(result, FlowState.ROLLED_BACK, "old PDU retained; target transition was not committed")
            return result


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_profiles(config: dict) -> dict[str, SliceProfile]:
    return {name: SliceProfile(**value) for name, value in config["profiles"].items()}
