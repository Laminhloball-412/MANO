from __future__ import annotations

import json
import threading
import time

from .command import run
from .models import SliceProfile
from .slice_runtime import parse_rate_mbps


class QosEnforcer:
    """Idempotently reconcile the lab UPF downlink policy.

    This changes only the live qdisc inside generated UPF pods.  It does not
    edit Free5GMANO, Kubernetes or descriptor templates.
    """

    def __init__(self, runner=run, min_interval_seconds: int = 30):
        self.runner = runner
        self.min_interval_seconds = min_interval_seconds
        self._last_run = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _matches(text: str, profile: SliceProfile) -> bool:
        rate = parse_rate_mbps(text) if "qdisc tbf" in text else None
        return rate is not None and abs(rate - profile.downlink_limit_mbps) <= max(
            0.05, profile.downlink_limit_mbps * 0.02
        )

    def _upf_pod(self, profile: SliceProfile) -> str | None:
        result = self.runner(
            ["kubectl", "get", "pods", "-n", profile.namespace, "-o", "json"],
            timeout=10,
            check=False,
        )
        if result.returncode:
            return None
        for pod in json.loads(result.stdout).get("items", []):
            app = str(pod.get("metadata", {}).get("labels", {}).get("app", ""))
            if "upf" in app.lower() and pod.get("status", {}).get("phase") == "Running":
                return str(pod["metadata"]["name"])
        return None

    def reconcile_profile(self, profile: SliceProfile) -> dict:
        pod = self._upf_pod(profile)
        if not pod:
            return {"slice_id": profile.namespace, "status": "UPF_NOT_READY"}
        show = self.runner(
            ["kubectl", "exec", "-n", profile.namespace, pod, "--",
             "tc", "qdisc", "show", "dev", "ogstun"],
            timeout=10,
            check=False,
        )
        if show.returncode == 0 and self._matches(show.stdout, profile):
            return {"slice_id": profile.namespace, "status": "ALREADY_ENFORCED"}
        replace = self.runner(
            [
                "kubectl", "exec", "-n", profile.namespace, pod, "--",
                "tc", "qdisc", "replace", "dev", "ogstun", "root",
                "handle", "1:", "tbf", "rate", f"{profile.downlink_limit_mbps:g}mbit",
                "burst", f"{profile.downlink_burst_kb}kb", "latency", "100ms",
            ],
            timeout=15,
            check=False,
        )
        status = "APPLIED" if replace.returncode == 0 else "APPLY_FAILED"
        return {
            "slice_id": profile.namespace,
            "status": status,
            "error": replace.stderr.strip() if replace.returncode else None,
        }

    def reconcile(self, profiles: dict[str, SliceProfile], force: bool = False) -> list[dict]:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_run < self.min_interval_seconds:
                return []
            self._last_run = now
            unique = {}
            for profile in profiles.values():
                if profile.name != "default":
                    unique[profile.namespace] = profile
            return [self.reconcile_profile(profile) for profile in unique.values()]
