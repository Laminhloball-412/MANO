from __future__ import annotations

import json
import unittest

from session_orchestrator.command import CommandResult
from session_orchestrator.models import SliceProfile
from session_orchestrator.qos_enforcement import QosEnforcer


class QosEnforcerTests(unittest.TestCase):
    def profile(self):
        return SliceProfile(
            "urllc", "slice-2", 2, "000001", "srsapn", "10.45.2.1", 2,
            "oaitun_ue1p2", service_class="urllc", downlink_limit_mbps=5,
            downlink_burst_kb=64,
        )

    def test_applies_missing_tbf_without_touching_templates(self):
        calls = []

        def runner(argv, **_kwargs):
            calls.append(tuple(argv))
            if argv[1:3] == ["get", "pods"]:
                pod = {"metadata": {"name": "upf-1", "labels": {"app": "upf-svc"}},
                       "status": {"phase": "Running"}}
                return CommandResult(tuple(argv), 0, json.dumps({"items": [pod]}), "")
            if "show" in argv:
                return CommandResult(tuple(argv), 0, "qdisc fq_codel 0: root", "")
            return CommandResult(tuple(argv), 0, "", "")

        result = QosEnforcer(runner=runner).reconcile_profile(self.profile())
        self.assertEqual("APPLIED", result["status"])
        replace = next(call for call in calls if "replace" in call)
        self.assertIn("5mbit", replace)
        self.assertIn("64kb", replace)

    def test_matching_tbf_is_idempotent(self):
        calls = []

        def runner(argv, **_kwargs):
            calls.append(tuple(argv))
            if argv[1:3] == ["get", "pods"]:
                pod = {"metadata": {"name": "upf-1", "labels": {"app": "upf-svc"}},
                       "status": {"phase": "Running"}}
                return CommandResult(tuple(argv), 0, json.dumps({"items": [pod]}), "")
            return CommandResult(tuple(argv), 0, "qdisc tbf 1: root rate 5Mbit burst 64Kb", "")

        result = QosEnforcer(runner=runner).reconcile_profile(self.profile())
        self.assertEqual("ALREADY_ENFORCED", result["status"])
        self.assertFalse(any("replace" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
