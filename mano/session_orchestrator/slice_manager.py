from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from .command import CommandError, run
from .models import SliceProfile


class SliceManager:
    def __init__(self, intent_flow: Path, protected_namespaces: set[str], dry_run_deallocate: bool = True):
        self.intent_flow = intent_flow
        self.protected_namespaces = protected_namespaces
        self.dry_run_deallocate = dry_run_deallocate
        # The daemon has a single worker today, but API requests and future
        # workers must not race two allocations for the same fixed slot.
        self._ensure_lock = threading.Lock()

    def exists(self, profile: SliceProfile) -> bool:
        result = run(["kubectl", "get", "namespace", profile.namespace, "-o", "name"], check=False)
        if result.returncode:
            return False
        pods = run(["kubectl", "get", "pods", "-n", profile.namespace, "-o", "json"])
        data = json.loads(pods.stdout)
        roles = {item.get("metadata", {}).get("labels", {}).get("app") for item in data.get("items", [])}
        ready = all(
            any(c.get("type") == "Ready" and c.get("status") == "True" for c in item.get("status", {}).get("conditions", []))
            for item in data.get("items", [])
        )
        return ready and len(data.get("items", [])) >= 3 and any("smf" in str(role).lower() for role in roles)

    def ensure(self, profile: SliceProfile) -> str:
        with self._ensure_lock:
            # Reconcile live state again after acquiring the lock.  A deleted
            # namespace may still have historical records in intent_flow_v2;
            # those records are an audit trail and must not block recreation.
            if self.exists(profile):
                return "reused"
            try:
                slot = int(profile.namespace.rsplit("-", 1)[1])
            except (IndexError, ValueError) as exc:
                raise ValueError(
                    f"profile namespace must end in a numeric slot: {profile.namespace}"
                ) from exc

            request_id = f"runtime-{profile.name}-{uuid.uuid4().hex[:12]}"
            payload = self.intent_flow.parent / "runtime" / f"{request_id}.json"
            payload.parent.mkdir(parents=True, exist_ok=True)
            payload.write_text(json.dumps({
                "intent_id": request_id,
                "slice_type": profile.name,
                "slot": slot,
                "sst": profile.sst,
                "sd": profile.sd,
            }, indent=2), encoding="utf-8")
            try:
                run(
                    ["python3", str(self.intent_flow), "create", str(payload), "--execute"],
                    timeout=900,
                )
            except CommandError:
                # A remote API can time out after committing.  Trust only the
                # reconciled Kubernetes health check, never the error text.
                if self.exists(profile):
                    return "recovered"
                raise
            if not self.exists(profile):
                raise RuntimeError(f"slice {profile.namespace} was allocated but is not healthy")
            return "allocated"

    def deallocate_if_idle(self, profile: SliceProfile, active_pdu_count: int) -> str:
        if active_pdu_count:
            return "active"
        if profile.namespace in self.protected_namespaces or profile.protected:
            return "protected"
        if self.dry_run_deallocate:
            return "dry-run"
        run(["python3", str(self.intent_flow), "delete", profile.namespace, "--execute"], timeout=900)
        return "deallocated"
