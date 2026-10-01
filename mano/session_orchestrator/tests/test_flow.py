from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_orchestrator.controller import SessionOrchestrator
from session_orchestrator.metrics import PrometheusReader, SnapshotStore, render_metrics
from session_orchestrator.models import FlowState, ServiceIntent, SliceProfile, SliceRuntime, TrafficMetrics
from session_orchestrator.policy import assess_service_sla, evaluate_profiles
from session_orchestrator.steering import BoundInterfaceSteering


def profile(name, sst, psi, tun):
    return SliceProfile(name, f"slice-{sst}", sst, "000001", "srsapn", f"10.45.{sst}.1", psi, tun)


class FakeSlices:
    def __init__(self): self.calls = []
    def ensure(self, selected): self.calls.append(selected); return "reused"
    def deallocate_if_idle(self, selected, active_pdu_count):
        self.calls.append((selected, active_pdu_count))
        return "protected"


class FakeOai:
    def __init__(self): self.calls = []
    def establish(self, selected, ue_id=0): self.calls.append(("establish", selected.pdu_session_id, ue_id))
    def release(self, psi, ue_id=0): self.calls.append(("release", psi, ue_id))


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SnapshotStore(Path(self.temp.name) / "state.json")
        self.profiles = {
            "default": profile("default", 1, 1, "tun1"),
            "embb": profile("embb", 1, 1, "tun1"),
            "urllc": profile("urllc", 2, 2, "tun2"),
            "mmtc": profile("mmtc", 3, 3, "tun3"),
        }
        self.slices, self.oai, self.steering = FakeSlices(), FakeOai(), BoundInterfaceSteering()

    def tearDown(self): self.temp.cleanup()

    @staticmethod
    def metrics(interface, gateway, *_args):
        return TrafficMetrics(1.2, 12, 2, 0, 120, 10, 20, interface, gateway)

    def controller(self, measure=None):
        return SessionOrchestrator(self.profiles, self.store, self.slices, self.oai, self.steering, 0, 0, measure or self.metrics, lambda _: None)

    def test_control_is_make_before_break(self):
        result = self.controller().run(ServiceIntent("robot control", duration_seconds=1))
        self.assertEqual(FlowState.COMPLETED, result.state)
        self.assertEqual([("establish", 2, 0), ("release", 1, 0)], self.oai.calls)
        states = [event.state for event in result.events]
        self.assertLess(states.index("ESTABLISHING"), states.index("RELEASING"))
        self.assertEqual("tun2", self.steering.active_interface)

    def test_default_profile_does_not_create_second_pdu(self):
        result = self.controller().run(ServiceIntent("video", duration_seconds=1))
        self.assertEqual(FlowState.COMPLETED, result.state)
        self.assertEqual([], self.oai.calls)

    def test_bad_target_sla_rolls_back_without_releasing_old(self):
        calls = {"count": 0}
        def bad(interface, gateway, *_args):
            calls["count"] += 1
            loss = 0 if calls["count"] == 1 else 100
            return TrafficMetrics(1, 10, 1, loss, 120, 10, 3, interface, gateway)
        result = self.controller(bad).run(ServiceIntent("control", duration_seconds=1))
        self.assertEqual(FlowState.ROLLED_BACK, result.state)
        self.assertEqual([("establish", 2, 0), ("release", 2, 0)], self.oai.calls)
        self.assertFalse(result.old_session_released)

    def test_prometheus_output_contains_required_kpis(self):
        text = render_metrics({"ue_id": 1, "service": "control", "metrics": {"throughput_mbps": 1, "rtt_ms": 2, "jitter_ms": 3, "loss_percent": 4, "packet_size_bytes": 120, "message_rate_per_second": 10}})
        for name in ("throughput", "rtt", "jitter", "packet_loss", "packet_size", "message_rate"):
            self.assertIn(f"ansa_ue_{name}", text)

    def test_missing_runtime_telemetry_does_not_break_existing_flow(self):
        reader = PrometheusReader("http://prometheus.invalid", 0)
        with patch("session_orchestrator.metrics.urlopen", side_effect=OSError("offline")):
            runtime = reader.read_slice_runtime(self.profiles)
        self.assertTrue(all(item.healthy for item in runtime.values()))
        self.assertTrue(all(item.cpu_load_ratio == 0 for item in runtime.values()))

    def test_explicit_intent_wins_and_explains_slice_choice(self):
        selected, candidates, reason = evaluate_profiles(
            ServiceIntent("robot control"), self.metrics("tun1", "10.45.1.1"), self.profiles
        )
        self.assertEqual("urllc", selected.name)
        self.assertEqual("urllc", candidates[0].profile)
        self.assertIn("explicit", reason)
        self.assertGreater(candidates[0].score, candidates[1].score)

    def test_metrics_describe_multi_pdu_and_candidate_scores(self):
        result = self.controller().run(ServiceIntent("control", duration_seconds=1))
        text = render_metrics(result.as_dict() | {"ue_id": 1, "service": "control"})
        self.assertIn('pdu_session_id="1"', text)
        self.assertIn('pdu_session_id="2"', text)
        self.assertIn('state="inactive"', text)
        self.assertIn('state="active"', text)
        self.assertIn("ansa_intent_slice_score", text)
        self.assertIn("ansa_intent_slice_selected_info", text)

    @patch("session_orchestrator.metrics.interface_is_active")
    def test_live_interface_overrides_stale_session_snapshot(self, active):
        active.side_effect = lambda interface: interface == "tun1"
        snapshot = self.controller().run(
            ServiceIntent("control", duration_seconds=1)
        ).as_dict() | {"ue_id": 1, "service": "control"}
        text = render_metrics(snapshot, verify_interfaces=True)
        self.assertIn('interface="tun1",role="source",state="active"', text)
        self.assertIn('interface="tun2",role="target",state="inactive"', text)

    def test_multiple_instances_of_one_service_class_are_ranked_per_slice(self):
        profiles = dict(self.profiles)
        profiles["urllc-secondary"] = SliceProfile(
            "urllc-secondary", "slice-4", 2, "000002", "srsapn",
            "10.45.4.1", 4, "tun4", service_class="urllc",
        )
        selected, candidates, _ = evaluate_profiles(
            ServiceIntent("robot control"), self.metrics("tun1", "10.45.1.1"), profiles
        )
        urllc_slices = [item.slice_id for item in candidates if item.profile == "urllc"]
        self.assertEqual(["slice-2", "slice-4"], urllc_slices)
        self.assertEqual("slice-2", selected.namespace)

    def test_unhealthy_slice_is_ineligible(self):
        runtime = {"slice-2": SliceRuntime(healthy=False)}
        selected, candidates, _ = evaluate_profiles(
            ServiceIntent("control"), self.metrics("tun1", "10.45.1.1"),
            self.profiles, runtime,
        )
        urllc = next(item for item in candidates if item.profile == "urllc")
        self.assertFalse(urllc.eligible)
        self.assertEqual(0, urllc.score)
        self.assertNotEqual("urllc", selected.name)

    def test_absent_slice_remains_eligible_for_allocation(self):
        runtime = {"slice-2": SliceRuntime(exists=False, healthy=False)}
        selected, candidates, _ = evaluate_profiles(
            ServiceIntent("control"), self.metrics("tun1", "10.45.1.1"),
            self.profiles, runtime,
        )
        urllc = next(item for item in candidates if item.profile == "urllc")
        self.assertTrue(urllc.eligible)
        self.assertEqual("allocatable_absent_intent_match", urllc.reason)
        self.assertEqual("urllc", selected.name)

    def test_runtime_load_reduces_health_load_component(self):
        runtime = {
            "slice-2": SliceRuntime(
                exists=True,
                healthy=True,
                pdu_sessions=0,
                cpu_load_ratio=0.9,
                memory_load_ratio=0.2,
            )
        }
        _, candidates, _ = evaluate_profiles(
            ServiceIntent("control"), self.metrics("tun1", "10.45.1.1"),
            self.profiles, runtime,
        )
        urllc = next(item for item in candidates if item.profile == "urllc")
        self.assertEqual(10.0, urllc.health_load)

    def test_fixed_lab_classes_select_their_matching_slice(self):
        capable = {
            "default": self.profiles["default"],
            "embb": SliceProfile(
                "embb", "slice-1", 1, "000001", "srsapn", "10.45.1.1", 1, "tun1",
                service_class="embb", capacity_mbps=100, max_pdu_sessions=64,
                max_message_rate_per_second=2000, baseline_rtt_ms=100,
                baseline_jitter_ms=50, baseline_loss_percent=2,
            ),
            "urllc": SliceProfile(
                "urllc", "slice-2", 2, "000001", "srsapn", "10.45.2.1", 2, "tun2",
                service_class="urllc", capacity_mbps=20, max_pdu_sessions=32,
                max_message_rate_per_second=5000, baseline_rtt_ms=50,
                baseline_jitter_ms=20, baseline_loss_percent=0.5,
            ),
            "mmtc": SliceProfile(
                "mmtc", "slice-3", 3, "000001", "srsapn", "10.45.3.1", 3, "tun3",
                service_class="mmtc", capacity_mbps=10, max_pdu_sessions=1000,
                max_message_rate_per_second=10000, baseline_rtt_ms=200,
                baseline_jitter_ms=100, baseline_loss_percent=2,
            ),
        }
        observed = self.metrics("tun1", "10.45.1.1")
        for service, expected in (("video", "embb"), ("control", "urllc"), ("sensor", "mmtc")):
            with self.subTest(service=service):
                selected, candidates, _ = evaluate_profiles(
                    ServiceIntent(service), observed, capable,
                )
                self.assertEqual(expected, selected.name)
                self.assertEqual(expected, candidates[0].profile)

    def test_effective_capacity_uses_real_benchmark(self):
        profiles = dict(self.profiles)
        profiles["embb"] = SliceProfile(
            "embb", "slice-1", 1, "000001", "srsapn", "10.45.1.1", 1, "tun1",
            service_class="embb", capacity_mbps=100, session_ambr_mbps=100,
            downlink_limit_mbps=100, benchmark_downlink_mbps=11.19,
        )
        _, candidates, _ = evaluate_profiles(
            ServiceIntent("video"), self.metrics("tun1", "10.45.1.1"), profiles,
        )
        embb = next(item for item in candidates if item.profile == "embb")
        self.assertEqual(11.19, embb.effective_capacity_mbps)
        self.assertLess(embb.requirement_fit, 100)

    def test_active_probe_assesses_only_latency_dimensions(self):
        result = assess_service_sla(
            TrafficMetrics(0.001, 90, 40, 0, 56, 2, 3, "tun2", "10.45.2.1"),
            "urllc",
            dimensions=("rtt", "jitter", "loss"),
        )
        self.assertEqual("DEGRADED", result["status"])
        self.assertNotIn("throughput", result["checks"])
        self.assertFalse(result["checks"]["rtt"])


if __name__ == "__main__":
    unittest.main()
