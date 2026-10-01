from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from session_orchestrator.intent_runtime import IntentRegistry, RuntimeStore
from session_orchestrator.models import SliceProfile


def profile(name: str, sst: int, pdu: int) -> SliceProfile:
    return SliceProfile(
        name, f"slice-{sst}", sst, "000001", "srsapn",
        f"10.45.{sst}.1", pdu,
        "oaitun_ue1" if pdu == 1 else f"oaitun_ue1p{pdu}",
    )


class IntentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.registry = IntentRegistry(
            RuntimeStore(Path(self.temporary.name) / "runtime.json")
        )
        self.embb = profile("embb", 1, 1)
        self.urllc = profile("urllc", 2, 2)
        self.mmtc = profile("mmtc", 3, 3)

    def tearDown(self):
        self.temporary.cleanup()

    def active(self, service, profile, mode="auto", replaces=None):
        intent = self.registry.submit(service, mode=mode, replaces_intent_id=replaces)
        return self.registry.bind(intent["intent_id"], profile, profile.tun_pattern)

    def test_auto_without_replacement_is_safe_concurrent(self):
        intent = self.registry.submit("control")
        self.assertEqual("concurrent", intent["mode"])

    def test_concurrent_services_keep_two_pdu_references(self):
        video = self.active("video", self.embb)
        control = self.active("control", self.urllc)
        state = self.registry.snapshot()
        self.assertEqual("ACTIVE", video["state"])
        self.assertEqual("ACTIVE", control["state"])
        self.assertEqual([video["intent_id"]], state["sessions"]["1:1"]["references"])
        self.assertEqual([control["intent_id"]], state["sessions"]["1:2"]["references"])

    def test_migration_requires_application_ack_before_old_release(self):
        video = self.active("video", self.embb)
        control = self.active(
            "control", self.urllc, mode="auto", replaces=video["intent_id"]
        )
        self.assertEqual("AWAITING_APPLICATION_ACK", control["state"])
        before = self.registry.snapshot()
        self.assertEqual([video["intent_id"]], before["sessions"]["1:1"]["references"])
        release = self.registry.acknowledge_application(control["intent_id"])
        after = self.registry.snapshot()
        self.assertEqual([], release)
        self.assertEqual("ENDED", after["intents"][video["intent_id"]]["state"])
        self.assertEqual([], after["sessions"]["1:1"]["references"])
        self.assertEqual("ACTIVE", after["intents"][control["intent_id"]]["state"])

    def test_specialized_to_specialized_migration_releases_old_pdu(self):
        control = self.active("control", self.urllc)
        sensor = self.active(
            "sensor", self.mmtc, mode="auto", replaces=control["intent_id"]
        )
        release = self.registry.acknowledge_application(sensor["intent_id"])
        self.assertEqual([2], [item["pdu_session_id"] for item in release])

    def test_active_intent_lease_expires_without_heartbeat(self):
        intent = self.registry.submit("sensor", lease_seconds=1)
        self.registry.bind(intent["intent_id"], self.mmtc, "oaitun_ue1p3")

        def age(document):
            document["intents"][intent["intent_id"]]["last_heartbeat_at"] = (
                "2000-01-01T00:00:00+00:00"
            )

        self.registry.store.mutate(age)
        self.assertEqual([intent["intent_id"]], self.registry.stale_intent_ids())

    def test_shared_pdu_is_not_released_until_last_intent_ends(self):
        first = self.active("control-a", self.urllc)
        second = self.active("control-b", self.urllc)
        self.assertEqual([], self.registry.end(first["intent_id"]))
        release = self.registry.end(second["intent_id"])
        self.assertEqual(1, len(release))
        self.assertEqual(2, release[0]["pdu_session_id"])

    def test_replacement_must_belong_to_same_ue(self):
        original = self.registry.submit("video", ue_id=1)
        with self.assertRaisesRegex(ValueError, "same UE"):
            self.registry.submit(
                "control", ue_id=2, replaces_intent_id=original["intent_id"]
            )

    def test_same_pdu_id_is_isolated_per_ue(self):
        first = self.registry.submit("control", ue_id=1)
        second = self.registry.submit("control", ue_id=2)
        self.registry.bind(first["intent_id"], self.urllc, "oaitun_ue1p2")
        self.registry.bind(second["intent_id"], self.urllc, "oaitun_ue2p2")

        state = self.registry.snapshot()
        self.assertEqual([first["intent_id"]], state["sessions"]["1:2"]["references"])
        self.assertEqual([second["intent_id"]], state["sessions"]["2:2"]["references"])
        release = self.registry.end(first["intent_id"])
        self.assertEqual([(1, 2)], [
            (item["ue_id"], item["pdu_session_id"]) for item in release
        ])
        self.assertEqual(
            [second["intent_id"]],
            self.registry.snapshot()["sessions"]["2:2"]["references"],
        )

    def test_restart_requeues_only_pre_binding_work(self):
        pending = self.registry.submit("control")
        self.registry.transition(pending["intent_id"], "VALIDATING")
        active = self.active("sensor", self.mmtc)
        recovered = self.registry.recover_incomplete()
        state = self.registry.snapshot()
        self.assertEqual([pending["intent_id"]], recovered)
        self.assertEqual("PENDING", state["intents"][pending["intent_id"]]["state"])
        self.assertEqual("ACTIVE", state["intents"][active["intent_id"]]["state"])

    def test_observe_first_flow_links_intent_and_waits_for_application_ack(self):
        flow = self.registry.register_flow(
            "control", ue_id=1, interface="oaitun_ue1",
            gateway="10.45.1.1", flow_id="ue1-control",
        )
        self.assertEqual("OBSERVING", flow["state"])
        sample = {
            "throughput_mbps": 0.05, "rtt_ms": 80, "jitter_ms": 10,
            "loss_percent": 0, "packet_size_bytes": 120,
            "message_rate_per_second": 50,
        }
        self.registry.update_flow("ue1-control", sample)
        self.registry.update_flow("ue1-control", sample)
        intent = self.registry.submit(
            "control", ue_id=1, flow_id="ue1-control", mode="concurrent"
        )
        self.registry.bind(
            intent["intent_id"], self.urllc, "oaitun_ue1p2"
        )
        state = self.registry.snapshot()
        self.assertEqual(
            "AWAITING_APPLICATION_ACK",
            state["intents"][intent["intent_id"]]["state"],
        )
        self.assertEqual("READY_FOR_BIND", state["flows"]["ue1-control"]["state"])
        self.registry.acknowledge_application(intent["intent_id"])
        self.assertEqual(
            "STEERED", self.registry.snapshot()["flows"]["ue1-control"]["state"]
        )

    def test_migration_audit_orders_ack_before_old_release_authorization(self):
        old = self.active("sensor", self.mmtc)
        new = self.registry.submit(
            "control", mode="migrate", replaces_intent_id=old["intent_id"]
        )
        self.registry.bind(new["intent_id"], self.urllc, "oaitun_ue1p2")
        releases = self.registry.acknowledge_application(new["intent_id"])
        relevant = [
            event["event"] for event in self.registry.snapshot()["events"]
            if event.get("intent_id") == new["intent_id"]
        ]
        self.assertLess(
            relevant.index("APPLICATION_BINDING_ACKNOWLEDGED"),
            relevant.index("OLD_SESSION_RELEASE_AUTHORIZED_AFTER_ACK"),
        )
        self.assertEqual([3], [item["pdu_session_id"] for item in releases])

    def test_flow_intent_must_use_same_ue_and_only_one_active_intent(self):
        self.registry.register_flow("sensor", ue_id=2, flow_id="ue2-sensor")
        with self.assertRaisesRegex(ValueError, "same UE"):
            self.registry.submit("sensor", ue_id=1, flow_id="ue2-sensor")
        first = self.registry.submit("sensor", ue_id=2, flow_id="ue2-sensor")
        with self.assertRaisesRegex(ValueError, "already has an active intent"):
            self.registry.submit("sensor", ue_id=2, flow_id="ue2-sensor")
        self.registry.end(first["intent_id"])
        second = self.registry.submit("sensor", ue_id=2, flow_id="ue2-sensor")
        self.assertNotEqual(first["intent_id"], second["intent_id"])

    def test_application_intent_service_must_match_registered_flow(self):
        self.registry.register_flow("video", ue_id=1, flow_id="ue1-video")
        with self.assertRaisesRegex(ValueError, "must match"):
            self.registry.submit(
                "control", ue_id=1, flow_id="ue1-video",
                intent_source="application",
            )

    def test_old_runtime_document_is_upgraded_with_flow_registry(self):
        self.registry.store.path.write_text(
            '{"version": 2, "intents": {}, "sessions": {}, "events": []}',
            encoding="utf-8",
        )
        state = self.registry.snapshot()
        self.assertEqual(3, state["version"])
        self.assertEqual({}, state["flows"])


if __name__ == "__main__":
    unittest.main()
