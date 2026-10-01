from __future__ import annotations

import time
from pathlib import Path


class BoundInterfaceSteering:
    """MVP steering: applications bind their new flows to the selected TUN.

    Existing flows on the old TUN are not re-routed. This is intentional: they
    can drain before the old session is released, matching make-before-break.
    """

    def __init__(self):
        self.active_interface: str | None = None

    def switch_new_flows(self, interface: str) -> None:
        self.active_interface = interface

    def drain(self, seconds: int) -> None:
        time.sleep(max(0, seconds))

    def wait_interface_down(self, interface: str, timeout_seconds: int) -> bool:
        """Wait until OAI confirms release by removing or lowering the TUN."""
        operstate = Path("/sys/class/net") / interface / "operstate"
        deadline = time.monotonic() + max(0, timeout_seconds)
        while True:
            if not operstate.exists() or operstate.read_text(encoding="utf-8").strip() == "down":
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)
