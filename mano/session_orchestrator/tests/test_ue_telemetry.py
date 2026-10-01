from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from session_orchestrator.models import SliceProfile
from session_orchestrator.ue_telemetry import UeTelemetryCollector, interface_for


def profile(name: str, sst: int, pdu: int, tun: str) -> SliceProfile:
    return SliceProfile(
        name, f"slice-{sst}", sst, "000001", "srsapn",
        f"10.45.{sst}.1", pdu, tun,
    )


class UeTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.netns = self.root / "netns"
        self.netns.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write_interface(self, name: str, values: tuple[int, int, int, int]):
        root = self.root / name
        (root / "statistics").mkdir(parents=True, exist_ok=True)
        (root / "operstate").write_text("unknown\n", encoding="utf-8")
        for field, value in zip(
            ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets"), values
        ):
            (root / "statistics" / field).write_text(str(value), encoding="utf-8")

    def test_interface_name_is_scoped_to_ue(self):
        self.assertEqual(
            "oaitun_ue3",
            interface_for(profile("embb", 1, 1, "oaitun_ue1"), 3),
        )
        self.assertEqual(
            "oaitun_ue3p2",
            interface_for(profile("urllc", 2, 2, "oaitun_ue1p2"), 3),
        )

    def test_first_sample_does_not_invent_traffic_rate(self):
        self.write_interface("oaitun_ue1", (1000, 2000, 10, 20))
        collector = UeTelemetryCollector(
            {"embb": profile("embb", 1, 1, "oaitun_ue1")},
            [1], self.root, clock=lambda: 10.0, netns_path=self.netns,
        )
        sample = collector.collect()[0]
        self.assertTrue(sample.up)
        self.assertFalse(sample.sample_ready)
        self.assertEqual(0.0, sample.rx_mbps)

    def test_second_sample_reports_only_counter_delta(self):
        times = iter((10.0, 12.0))
        self.write_interface("oaitun_ue1", (1000, 2000, 10, 20))
        collector = UeTelemetryCollector(
            {"embb": profile("embb", 1, 1, "oaitun_ue1")},
            [1], self.root, clock=lambda: next(times), netns_path=self.netns,
        )
        collector.collect()
        self.write_interface("oaitun_ue1", (1_001_000, 502_000, 1010, 520))
        sample = collector.collect()[0]
        self.assertTrue(sample.sample_ready)
        self.assertEqual(4.0, sample.rx_mbps)
        self.assertEqual(2.0, sample.tx_mbps)
        self.assertEqual(500.0, sample.rx_packets_per_second)
        self.assertEqual(1000.0, sample.average_rx_packet_bytes)

    def test_missing_interface_is_explicitly_down(self):
        collector = UeTelemetryCollector(
            {"embb": profile("embb", 1, 1, "oaitun_ue1")},
            [1], self.root, netns_path=self.netns,
        )
        text = collector.render()
        self.assertIn("ansa_ue_pdu_up", text)
        self.assertIn("} 0", text)

    def test_web_interface_inside_ue_namespace_is_reported_up(self):
        payload = json.dumps([{
            "ifname": "tun_srsue11",
            "operstate": "UNKNOWN",
            "stats64": {
                "rx": {"bytes": 1000, "packets": 10},
                "tx": {"bytes": 2000, "packets": 20},
            },
        }])

        def run(command, **_kwargs):
            self.assertEqual(
                ["sudo", "-n", "ip", "netns", "exec", "ue1", "ip", "-j", "-s",
                 "link", "show", "dev", "tun_srsue11"],
                command,
            )
            return subprocess.CompletedProcess(command, 0, payload, "")

        collector = UeTelemetryCollector(
            {"embb": profile("embb", 1, 1, "oaitun_ue1")},
            [1], self.root, clock=lambda: 10.0, command_runner=run,
            netns_path=self.netns,
        )

        sample = collector.collect()[0]

        self.assertTrue(sample.up)
        self.assertEqual("tun_srsue11", sample.interface)

    def test_stopped_web_session_resets_the_same_metric_to_zero(self):
        payload = json.dumps([{
            "ifname": "tun_srsue11",
            "operstate": "UNKNOWN",
            "stats64": {
                "rx": {"bytes": 1000, "packets": 10},
                "tx": {"bytes": 2000, "packets": 20},
            },
        }])
        results = iter((
            subprocess.CompletedProcess([], 0, payload, ""),
            subprocess.CompletedProcess([], 1, "", "Cannot open network namespace"),
        ))
        collector = UeTelemetryCollector(
            {"embb": profile("embb", 1, 1, "oaitun_ue1")},
            [1], self.root, clock=lambda: 10.0,
            command_runner=lambda _command, **_kwargs: next(results),
            netns_path=self.netns,
        )
        collector.collect()

        stopped = collector.collect()[0]

        self.assertFalse(stopped.up)
        self.assertEqual("tun_srsue11", stopped.interface)

    def test_new_ue_namespace_is_discovered_without_config_change(self):
        payload = json.dumps([{
            "ifname": "tun_srsue41",
            "operstate": "UNKNOWN",
            "stats64": {
                "rx": {"bytes": 1000, "packets": 10},
                "tx": {"bytes": 2000, "packets": 20},
            },
        }])

        def run(command, **_kwargs):
            if "ue4" in command and "tun_srsue41" in command:
                return subprocess.CompletedProcess(command, 0, payload, "")
            return subprocess.CompletedProcess(command, 1, "", "not found")

        (self.netns / "ue4").touch()
        collector = UeTelemetryCollector(
            {"embb": profile("embb", 1, 1, "oaitun_ue1")},
            [1, 2, 3], self.root, clock=lambda: 10.0, command_runner=run,
            netns_path=self.netns,
        )

        samples = collector.collect()

        ue4 = [sample for sample in samples if sample.ue_id == 4]
        self.assertEqual(1, len(ue4))
        self.assertTrue(ue4[0].up)
        self.assertEqual("tun_srsue41", ue4[0].interface)


if __name__ == "__main__":
    unittest.main()
