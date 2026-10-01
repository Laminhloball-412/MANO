from __future__ import annotations

import json
import unittest

from session_orchestrator.command import CommandResult
from session_orchestrator.models import SliceProfile
from session_orchestrator.slice_runtime import (
    SliceRuntimeCollector, parse_cpu, parse_memory, parse_rate_mbps,
)


class RuntimeCollectorTests(unittest.TestCase):
    def test_resource_parsers(self):
        self.assertEqual(0.125, parse_cpu("125m"))
        self.assertEqual(256 * 1024**2, parse_memory("256Mi"))
        self.assertEqual(5.0, parse_rate_mbps("qdisc tbf 1: rate 5Mbit burst 64Kb"))

    def test_collector_exports_health_and_load(self):
        pod = {
            "metadata": {"namespace": "slice-1", "name": "upf-1"},
            "status": {"phase": "Running", "containerStatuses": [{"ready": True, "restartCount": 2}]},
            "spec": {"containers": [{"resources": {"limits": {"cpu": "200m", "memory": "200Mi"}}}]},
        }

        def runner(argv, **_kwargs):
            if argv[1:3] == ["get", "pods"]:
                return CommandResult(tuple(argv), 0, json.dumps({"items": [pod]}), "")
            return CommandResult(tuple(argv), 0, "slice-1 upf-1 50m 100Mi\n", "")

        profile = SliceProfile("embb", "slice-1", 1, "000001", "srsapn", "10.45.1.1", 1, "tun1", service_class="embb")
        collector = SliceRuntimeCollector({"embb": profile}, runner=runner)
        snapshot = collector.collect()["slice-1"]
        self.assertTrue(snapshot.healthy)
        self.assertEqual(0.25, snapshot.cpu_load_ratio)
        self.assertEqual(0.5, snapshot.memory_load_ratio)
        text = collector.render()
        self.assertIn("ansa_slice_health", text)
        self.assertIn("ansa_slice_capacity_mbps", text)
        self.assertIn("ansa_slice_five_qi", text)
        self.assertIn("ansa_slice_session_ambr_mbps", text)
        self.assertIn("ansa_slice_downlink_limit_mbps", text)


if __name__ == "__main__":
    unittest.main()
