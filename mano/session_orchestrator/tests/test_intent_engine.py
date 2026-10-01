from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_orchestrator.intent_engine import (
    IntentExecutor, LiveTelemetryReader, render_intent_metrics,
)
from session_orchestrator.intent_runtime import IntentRegistry, RuntimeStore
from session_orchestrator.models import SliceProfile, TrafficMetrics


def profile(name: str, sst: int, pdu: int) -> SliceProfile:
    return SliceProfile(
        name, f"slice-{sst}", sst, "000001", "srsapn",
        f"10.45.{sst}.1", pdu,
        "oaitun_ue1" if pdu == 1 else f"oaitun_ue1p{pdu}",
        service_class=name if name != "default" else "embb",
        protected=pdu == 1,
    )


class FakeTelemetry:
    def __init__(self, up=True, ready=True):
        self.up = up
        self.ready = ready
        self.active_pdus = {1} if up else set()

    def pdu_up(self, _ue_id, profile):
        return profile.pdu_session_id in self.active_pdus

    def observe(self, _ue_id, profile, service):
        if not self.ready:
            raise RuntimeError("no passive sample")
        return (
            TrafficMetrics(1, 0, 0, 0, 100, 20, 1, profile.tun_pattern, profile.gateway),
            {"source": "passive", "service": service, "throughput_mbps": 1},
        )

    def observe_flow(self, flow, profile):
        if not self.ready:
            raise RuntimeError("no flow sample")
        return (
            TrafficMetrics(0.05, 80, 10, 0, 120, 50, 2,
                           flow["interface"], flow["gateway"]),
            {"source": "application", "flow_id": flow["flow_id"],
             "service": flow["service"], "throughput_mbps": 0.05},
        )


class FakeRuntimeReader:
    def read_slice_runtime(self, _profiles):
        return {}


class FakeSlices:
    def __init__(self): self.calls = []
    def ensure(self, target): self.calls.append(target.name); return "reused"
    def deallocate_if_idle(self, target, active_pdu_count):
        self.calls.append(("deallocate", target.name, active_pdu_count))
        return "dry-run"


class FakeOai:
    def __init__(self): self.calls = []; self.telemetry = None
    def establish(self, target, ue_id):
        self.calls.append(("establish", target.pdu_session_id, ue_id))
        if self.telemetry:
            self.telemetry.active_pdus.add(target.pdu_session_id)
    def release(self, pdu, ue_id):
        self.calls.append(("release", pdu, ue_id))
        if self.telemetry:
            self.telemetry.active_pdus.discard(pdu)


class IntentExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.registry = IntentRegistry(RuntimeStore(Path(self.temporary.name) / "state.json"))
        self.profiles = {
            "default": profile("default", 1, 1),
            "embb": profile("embb", 1, 1),
            "urllc": profile("urllc", 2, 2),
            "mmtc": profile("mmtc", 3, 3),
        }
        self.slices = FakeSlices()
        self.oai = FakeOai()

    def tearDown(self): self.temporary.cleanup()

    def executor(self, telemetry):
        self.oai.telemetry = telemetry
        return IntentExecutor(
            self.registry, self.profiles, telemetry, FakeRuntimeReader(),
            self.slices, self.oai, stable_seconds=0, drain_seconds=0,
        )

    @patch("session_orchestrator.intent_engine.measure_ping")
    def test_concurrent_intent_establishes_target_without_releasing_source(self, measure):
        measure.return_value = TrafficMetrics(1, 10, 1, 0, 56, 2, 3, "oaitun_ue1p2", "10.45.2.1")
        intent = self.registry.submit("control", mode="concurrent")
        self.executor(FakeTelemetry()).process(intent["intent_id"])
        result = self.registry.snapshot()["intents"][intent["intent_id"]]
        self.assertEqual("ACTIVE", result["state"])
        self.assertEqual(2, result["target"]["pdu_session_id"])
        self.assertEqual([("establish", 2, 0)], self.oai.calls)

    def test_missing_ue_waits_without_allocating(self):
        intent = self.registry.submit("control")
        self.executor(FakeTelemetry(up=False)).process(intent["intent_id"])
        result = self.registry.snapshot()["intents"][intent["intent_id"]]
        self.assertEqual("WAITING_FOR_UE", result["state"])
        self.assertEqual([], self.slices.calls)

    def test_missing_passive_sample_waits_without_fabricated_metrics(self):
        intent = self.registry.submit("sensor")
        self.executor(FakeTelemetry(ready=False)).process(intent["intent_id"])
        result = self.registry.snapshot()["intents"][intent["intent_id"]]
        self.assertEqual("WAITING_FOR_TELEMETRY", result["state"])
        self.assertIsNone(result["observed"])

    def test_admission_limit_blocks_extra_pdu_before_slice_allocation(self):
        intent = self.registry.submit("control")
        telemetry = FakeTelemetry()
        executor = IntentExecutor(
            self.registry, self.profiles, telemetry, FakeRuntimeReader(),
            self.slices, self.oai, stable_seconds=0, drain_seconds=0,
            max_pdu_sessions_per_ue=1,
        )
        executor.process(intent["intent_id"])
        result = self.registry.snapshot()["intents"][intent["intent_id"]]
        self.assertEqual("FAILED", result["state"])
        self.assertIn("admission limit", result["error"])
        self.assertEqual([], self.slices.calls)
        self.assertEqual([], self.oai.calls)

    @patch("session_orchestrator.intent_engine.measure_ping")
    def test_idle_reaper_uses_real_refs_and_live_pdu_gate(self, measure):
        measure.return_value = TrafficMetrics(1, 10, 1, 0, 56, 2, 3, "oaitun_ue1p2", "10.45.2.1")
        intent = self.registry.submit("control", mode="concurrent")
        executor = IntentExecutor(
            self.registry, self.profiles, FakeTelemetry(), FakeRuntimeReader(),
            self.slices, self.oai, stable_seconds=0, drain_seconds=0,
            idle_timeout_seconds=0, ue_ids=[1],
        )
        self.oai.telemetry = executor.telemetry
        executor.process(intent["intent_id"])
        executor.end(intent["intent_id"])
        actions = executor.reap_idle_slices()
        self.assertEqual([{"slice_id": "slice-2", "action": "dry-run"}], actions)

    @patch("session_orchestrator.intent_engine.measure_ping")
    def test_migration_releases_replaced_pdu_only_after_ack(self, measure):
        measure.return_value = TrafficMetrics(1, 10, 1, 0, 56, 2, 3, "oaitun_ue1p2", "10.45.2.1")
        video = self.registry.submit("video", mode="concurrent")
        self.registry.bind(video["intent_id"], self.profiles["embb"], "oaitun_ue1")
        control = self.registry.submit(
            "control", mode="auto", replaces_intent_id=video["intent_id"]
        )
        executor = self.executor(FakeTelemetry())
        executor.process(control["intent_id"])
        self.assertNotIn(("release", 1, 0), self.oai.calls)
        executor.acknowledge(control["intent_id"])
        self.assertNotIn(("release", 1, 0), self.oai.calls)

    def test_reconcile_marks_stale_active_session_lost_and_recovers_it(self):
        intent = self.registry.submit("control", ue_id=2)
        self.registry.bind(intent["intent_id"], self.profiles["urllc"], "oaitun_ue2p2")
        telemetry = FakeTelemetry(up=False)
        executor = self.executor(telemetry)

        actions = executor.reconcile_sessions()
        self.assertEqual([{
            "ue_id": 2, "pdu_session_id": 2,
            "from": "ACTIVE", "to": "LOST",
        }], actions)
        self.assertEqual("LOST", self.registry.snapshot()["sessions"]["2:2"]["state"])

        telemetry.active_pdus.add(2)
        actions = executor.reconcile_sessions()
        self.assertEqual("ACTIVE", actions[0]["to"])
        self.assertEqual("ACTIVE", self.registry.snapshot()["sessions"]["2:2"]["state"])

    @patch("session_orchestrator.intent_engine.measure_ping")
    def test_observe_first_intent_uses_linked_flow_telemetry(self, measure):
        measure.return_value = TrafficMetrics(
            1, 10, 1, 0, 56, 2, 3, "oaitun_ue1p2", "10.45.2.1"
        )
        sample = {
            "throughput_mbps": 0.05, "rtt_ms": 80, "jitter_ms": 10,
            "loss_percent": 0, "packet_size_bytes": 120,
            "message_rate_per_second": 50,
        }
        self.registry.register_flow(
            "control", ue_id=1, interface="oaitun_ue1",
            gateway="10.45.1.1", flow_id="ue1-control",
        )
        self.registry.update_flow("ue1-control", sample)
        self.registry.update_flow("ue1-control", sample)
        intent = self.registry.submit(
            "control", ue_id=1, flow_id="ue1-control", mode="concurrent"
        )
        telemetry = FakeTelemetry()
        self.oai.telemetry = telemetry
        executor = IntentExecutor(
            self.registry, self.profiles, telemetry, FakeRuntimeReader(),
            self.slices, self.oai, stable_seconds=0, drain_seconds=0,
            min_flow_samples=2, min_observation_seconds=0,
        )
        executor.process(intent["intent_id"])
        state = self.registry.snapshot()
        result = state["intents"][intent["intent_id"]]
        self.assertEqual("AWAITING_APPLICATION_ACK", result["state"])
        self.assertEqual("application", result["observed"]["source"])
        self.assertEqual("ue1-control", result["observed"]["flow_id"])
        self.assertEqual("READY_FOR_BIND", state["flows"]["ue1-control"]["state"])

    def test_flow_waits_until_minimum_sample_count(self):
        sample = {
            "throughput_mbps": 0.01, "rtt_ms": 100, "jitter_ms": 10,
            "loss_percent": 0, "packet_size_bytes": 80,
            "message_rate_per_second": 10,
        }
        self.registry.register_flow(
            "sensor", interface="oaitun_ue1", gateway="10.45.1.1",
            flow_id="ue1-sensor",
        )
        self.registry.update_flow("ue1-sensor", sample)
        intent = self.registry.submit("sensor", flow_id="ue1-sensor")
        executor = IntentExecutor(
            self.registry, self.profiles, FakeTelemetry(), FakeRuntimeReader(),
            self.slices, self.oai, min_flow_samples=2, min_observation_seconds=0,
        )
        executor.process(intent["intent_id"])
        result = self.registry.snapshot()["intents"][intent["intent_id"]]
        self.assertEqual("WAITING_FOR_TELEMETRY", result["state"])
        self.assertIn("at least 2 samples", result["error"])


class LiveTelemetryReaderTests(unittest.TestCase):
    def test_stale_prometheus_sample_is_rejected(self):
        reader = LiveTelemetryReader("http://prometheus", max_age_seconds=5)
        with patch.object(reader, "_query", return_value=(1.0, 99.0)), patch(
            "session_orchestrator.intent_engine.time.time", return_value=10.0
        ):
            self.assertIsNone(reader.value("metric", 1, 1))

    def test_terminal_intents_are_not_exported_as_current_state(self):
        document = {
            "intents": {
                "old": {"intent_id": "old", "ue_id": 1, "service": "sensor",
                        "mode": "concurrent", "state": "ENDED", "target": {}},
                "live": {"intent_id": "live", "ue_id": 1, "service": "control",
                         "mode": "concurrent", "state": "ACTIVE", "target": {}},
            },
            "sessions": {},
        }
        metrics = render_intent_metrics(document)
        self.assertNotIn('intent_id="old"', metrics)
        self.assertIn('intent_id="live"', metrics)

    def test_session_metrics_do_not_repeat_label_names(self):
        document = {
            "intents": {},
            "sessions": {
                "2:3": {
                    "ue_id": 2, "pdu_session_id": 3,
                    "slice_id": "slice-3", "interface": "oaitun_ue2p3",
                    "references": ["intent-a"],
                }
            },
        }
        metrics = render_intent_metrics(document)
        self.assertEqual(1, metrics.count('ue_id="2"'))
        self.assertEqual(1, metrics.count('pdu_session_id="3"'))

    def test_flow_metrics_are_scoped_by_flow_id_and_source(self):
        document = {
            "flows": {
                "ue1-control": {
                    "flow_id": "ue1-control", "ue_id": 1,
                    "service": "control", "intent_source": "application",
                    "state": "OBSERVING", "interface": "oaitun_ue1",
                    "last_sample_at": "2026-08-21T00:00:00+00:00",
                    "sample_count": 2,
                    "metrics": {
                        "throughput_mbps": 0.05, "rtt_ms": 80,
                        "jitter_ms": 10, "loss_percent": 0,
                        "packet_size_bytes": 120,
                        "message_rate_per_second": 50,
                    },
                }
            },
            "intents": {}, "sessions": {},
        }
        metrics = render_intent_metrics(document)
        self.assertIn('ansa_application_flow_info{flow_id="ue1-control"', metrics)
        self.assertIn('intent_source="application"', metrics)
        self.assertIn('ansa_application_throughput_mbps{flow_id="ue1-control"', metrics)

    def test_prometheus_label_values_are_escaped(self):
        document = {
            "flows": {
                "safe-id": {
                    "flow_id": "safe-id", "ue_id": 1,
                    "service": 'video"test\\path', "state": "OBSERVING",
                    "interface": "oaitun_ue1", "metrics": {},
                }
            },
            "intents": {}, "sessions": {},
        }
        metrics = render_intent_metrics(document)
        self.assertIn('service="video\\"test\\\\path"', metrics)


if __name__ == "__main__":
    unittest.main()
