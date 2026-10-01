from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_orchestrator.command import CommandError, CommandResult
from session_orchestrator.models import SliceProfile
from session_orchestrator.slice_manager import SliceManager


def profile() -> SliceProfile:
    return SliceProfile(
        "urllc", "slice-2", 2, "000001", "srsapn", "10.45.2.1", 2,
        "oaitun_ue1p2", service_class="urllc",
    )


class SliceManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.flow = root / "intent_flow_v2" / "intent_flow.py"
        self.flow.parent.mkdir(parents=True)
        self.manager = SliceManager(self.flow, set())

    def tearDown(self):
        self.temporary.cleanup()

    def test_recreation_uses_unique_intent_id(self):
        with patch.object(self.manager, "exists", side_effect=[False, True]), patch(
            "session_orchestrator.slice_manager.run",
            return_value=CommandResult(tuple(), 0, "", ""),
        ):
            self.assertEqual("allocated", self.manager.ensure(profile()))
        payloads = list((self.flow.parent / "runtime").glob("runtime-urllc-*.json"))
        self.assertEqual(1, len(payloads))
        payload = json.loads(payloads[0].read_text(encoding="utf-8"))
        self.assertRegex(payload["intent_id"], r"^runtime-urllc-[0-9a-f]{12}$")
        self.assertEqual(2, payload["slot"])

    def test_timeout_after_commit_is_reconciled(self):
        with patch.object(self.manager, "exists", side_effect=[False, True]), patch(
            "session_orchestrator.slice_manager.run",
            side_effect=CommandError("timeout after remote commit"),
        ):
            self.assertEqual("recovered", self.manager.ensure(profile()))

    def test_failure_without_live_slice_is_not_hidden(self):
        with patch.object(self.manager, "exists", side_effect=[False, False]), patch(
            "session_orchestrator.slice_manager.run",
            side_effect=CommandError("allocation failed"),
        ):
            with self.assertRaises(CommandError):
                self.manager.ensure(profile())


if __name__ == "__main__":
    unittest.main()
