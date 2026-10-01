from __future__ import annotations

import copy
import json
import os
import threading
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import SliceProfile


VALID_MODES = {"auto", "migrate", "concurrent"}
TERMINAL_STATES = {"ENDED", "FAILED"}
RECOVERABLE_IN_PROGRESS_STATES = {
    "DECIDING", "ENSURING_SLICE", "ESTABLISHING", "VALIDATING"
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeStore:
    """Atomic process-local state store for the intent daemon."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"version": 3, "flows": {}, "intents": {}, "sessions": {}, "events": []}

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self.empty()
            document = json.loads(self.path.read_text(encoding="utf-8"))
            document.setdefault("flows", {})
            document.setdefault("intents", {})
            document.setdefault("sessions", {})
            document.setdefault("events", [])
            document["version"] = 3
            return document

    def mutate(self, change: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock:
            document = self.read()
            result = change(document)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
            return result


def session_key(ue_id: int, pdu_session_id: int) -> str:
    return f"{ue_id}:{pdu_session_id}"


class IntentRegistry:
    """Reference-count PDU bindings independently from radio execution."""

    def __init__(self, store: RuntimeStore, default_pdu_session_id: int = 1):
        self.store = store
        self.default_pdu_session_id = default_pdu_session_id

    def register_flow(
        self,
        service: str,
        ue_id: int = 1,
        interface: str = "",
        gateway: str = "",
        flow_id: str | None = None,
        intent_source: str = "application",
    ) -> dict[str, Any]:
        flow_id = flow_id or f"flow-{uuid.uuid4().hex[:12]}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", flow_id):
            raise ValueError("flow_id must be 1-64 safe identifier characters")
        if intent_source not in {"application", "inferred", "operator"}:
            raise ValueError("intent_source must be application, inferred or operator")

        def change(document):
            existing = document["flows"].get(flow_id)
            if existing and existing.get("state") != "ENDED":
                raise ValueError(f"flow_id is already active: {flow_id}")
            flow = {
                "flow_id": flow_id,
                "ue_id": int(ue_id),
                "service": service,
                "intent_source": intent_source,
                "state": "OBSERVING",
                "interface": interface,
                "gateway": gateway,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "first_sample_at": None,
                "last_sample_at": None,
                "sample_count": 0,
                "metrics": None,
                "intent_id": None,
                "binding": None,
            }
            document["flows"][flow_id] = flow
            document["events"].append({
                "at": utc_now(), "flow_id": flow_id, "event": "FLOW_REGISTERED",
            })
            return copy.deepcopy(flow)
        return self.store.mutate(change)

    def update_flow(
        self,
        flow_id: str,
        metrics: dict[str, Any],
        interface: str | None = None,
        gateway: str | None = None,
    ) -> dict[str, Any]:
        required = {
            "throughput_mbps", "rtt_ms", "jitter_ms", "loss_percent",
            "packet_size_bytes", "message_rate_per_second",
        }
        missing = sorted(required - set(metrics))
        if missing:
            raise ValueError(f"flow sample is missing metrics: {missing}")

        def change(document):
            flow = document["flows"].get(flow_id)
            if not flow:
                raise KeyError(flow_id)
            if flow.get("state") == "ENDED":
                raise ValueError("flow is ended")
            now = utc_now()
            if not flow.get("first_sample_at"):
                flow["first_sample_at"] = now
            flow["last_sample_at"] = now
            flow["updated_at"] = now
            flow["sample_count"] = int(flow.get("sample_count", 0)) + 1
            flow["metrics"] = {
                "throughput_mbps": max(0.0, float(metrics["throughput_mbps"])),
                "rtt_ms": max(0.0, float(metrics["rtt_ms"])),
                "jitter_ms": max(0.0, float(metrics["jitter_ms"])),
                "loss_percent": min(100.0, max(0.0, float(metrics["loss_percent"]))),
                "packet_size_bytes": max(0, int(metrics["packet_size_bytes"])),
                "message_rate_per_second": max(0.0, float(metrics["message_rate_per_second"])),
            }
            if interface:
                flow["interface"] = interface
            if gateway:
                flow["gateway"] = gateway
            return copy.deepcopy(flow)
        return self.store.mutate(change)

    def flow(self, flow_id: str) -> dict[str, Any]:
        flow = self.snapshot().get("flows", {}).get(flow_id)
        if not flow:
            raise KeyError(flow_id)
        return flow

    def close_flow(self, flow_id: str) -> dict[str, Any]:
        def change(document):
            flow = document["flows"].get(flow_id)
            if not flow:
                raise KeyError(flow_id)
            flow["state"] = "ENDED"
            flow["updated_at"] = utc_now()
            document["events"].append({
                "at": utc_now(), "flow_id": flow_id, "event": "FLOW_ENDED",
            })
            return copy.deepcopy(flow)
        return self.store.mutate(change)

    def submit(
        self,
        service: str,
        ue_id: int = 1,
        mode: str = "auto",
        replaces_intent_id: str | None = None,
        lease_seconds: int = 60,
        flow_id: str | None = None,
        intent_source: str = "operator",
    ) -> dict[str, Any]:
        mode = mode.lower()
        if mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
        effective = (
            "migrate" if mode == "auto" and replaces_intent_id
            else "concurrent" if mode == "auto"
            else mode
        )
        intent_id = f"intent-{uuid.uuid4().hex[:12]}"

        def change(document):
            resolved_intent_source = intent_source
            flow = None
            if flow_id:
                flow = document["flows"].get(flow_id)
                if not flow or flow.get("state") == "ENDED":
                    raise ValueError(f"flow_id is not active: {flow_id}")
                if int(flow["ue_id"]) != int(ue_id):
                    raise ValueError("intent and flow must belong to the same UE")
                if (
                    intent_source == "application"
                    and str(flow["service"]).lower() != str(service).lower()
                ):
                    raise ValueError(
                        "application intent service must match the registered flow"
                    )
                active_intent_id = flow.get("intent_id")
                active_intent = document["intents"].get(active_intent_id) if active_intent_id else None
                if active_intent and active_intent.get("state") not in TERMINAL_STATES:
                    raise ValueError(f"flow already has an active intent: {active_intent_id}")
                resolved_intent_source = flow.get(
                    "intent_source", resolved_intent_source
                )
            if replaces_intent_id:
                replaced = document["intents"].get(replaces_intent_id)
                if not replaced or replaced["state"] in TERMINAL_STATES:
                    raise ValueError(
                        f"replaces_intent_id is not active: {replaces_intent_id}"
                    )
                if int(replaced["ue_id"]) != int(ue_id):
                    raise ValueError("replacement intents must belong to the same UE")
            intent = {
                "intent_id": intent_id,
                "ue_id": int(ue_id),
                "service": service,
                "requested_mode": mode,
                "mode": effective,
                "replaces_intent_id": replaces_intent_id,
                "state": "PENDING",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "target": None,
                "observed": None,
                "error": None,
                "lease_seconds": max(0, int(lease_seconds)),
                "last_heartbeat_at": utc_now(),
                "flow_id": flow_id,
                "intent_source": resolved_intent_source,
            }
            document["intents"][intent_id] = intent
            if flow:
                flow["intent_id"] = intent_id
                flow["state"] = "INTENT_RECEIVED"
                flow["updated_at"] = utc_now()
            document["events"].append({
                "at": utc_now(), "intent_id": intent_id,
                "event": "INTENT_SUBMITTED", "mode": effective,
            })
            return copy.deepcopy(intent)

        return self.store.mutate(change)

    def heartbeat(self, intent_id: str) -> dict[str, Any]:
        def change(document):
            intent = document["intents"].get(intent_id)
            if not intent:
                raise KeyError(intent_id)
            if intent["state"] in TERMINAL_STATES:
                raise ValueError(f"intent is terminal: {intent['state']}")
            intent["last_heartbeat_at"] = utc_now()
            return copy.deepcopy(intent)
        return self.store.mutate(change)

    def stale_intent_ids(self) -> list[str]:
        now = datetime.now(timezone.utc)
        stale = []
        for intent in self.snapshot().get("intents", {}).values():
            if intent.get("state") not in {"ACTIVE", "AWAITING_APPLICATION_ACK"}:
                continue
            lease = int(intent.get("lease_seconds", 0))
            heartbeat = intent.get("last_heartbeat_at")
            if lease <= 0 or not heartbeat:
                continue
            if (now - datetime.fromisoformat(heartbeat)).total_seconds() > lease:
                stale.append(intent["intent_id"])
        return stale

    def recover_incomplete(self) -> list[str]:
        """Return interrupted pre-binding work to the idempotent worker queue."""
        def change(document):
            recovered = []
            for intent in document.get("intents", {}).values():
                if intent.get("state") not in RECOVERABLE_IN_PROGRESS_STATES:
                    continue
                intent["state"] = "PENDING"
                intent["updated_at"] = utc_now()
                intent["error"] = "recovered after intent-engine restart"
                recovered.append(intent["intent_id"])
                document["events"].append({
                    "at": utc_now(),
                    "intent_id": intent["intent_id"],
                    "event": "RECOVERED_AFTER_RESTART",
                })
            return recovered
        return self.store.mutate(change)

    def transition(
        self,
        intent_id: str,
        state: str,
        **fields: Any,
    ) -> dict[str, Any]:
        def change(document):
            intent = document["intents"].get(intent_id)
            if not intent:
                raise KeyError(intent_id)
            intent.update(fields)
            intent["state"] = state
            intent["updated_at"] = utc_now()
            document["events"].append({
                "at": utc_now(), "intent_id": intent_id, "event": state,
            })
            return copy.deepcopy(intent)
        return self.store.mutate(change)

    def bind(self, intent_id: str, profile: SliceProfile, interface: str) -> dict[str, Any]:
        def change(document):
            intent = document["intents"].get(intent_id)
            if not intent:
                raise KeyError(intent_id)
            key = session_key(intent["ue_id"], profile.pdu_session_id)
            session = document["sessions"].setdefault(key, {
                "ue_id": intent["ue_id"],
                "pdu_session_id": profile.pdu_session_id,
                "profile": profile.name,
                "slice_id": profile.namespace,
                "sst": profile.sst,
                "sd": profile.sd,
                "dnn": profile.dnn,
                "interface": interface,
                "state": "ACTIVE",
                "references": [],
                "empty_since": None,
            })
            if intent_id not in session["references"]:
                session["references"].append(intent_id)
                session["references"].sort()
            session["state"] = "ACTIVE"
            session["empty_since"] = None
            intent["target"] = {
                "profile": profile.name,
                "slice_id": profile.namespace,
                "pdu_session_id": profile.pdu_session_id,
                "interface": interface,
                "gateway": profile.gateway,
            }
            intent["state"] = (
                "AWAITING_APPLICATION_ACK"
                if intent["mode"] == "migrate" or intent.get("flow_id") else "ACTIVE"
            )
            intent["updated_at"] = utc_now()
            flow_id = intent.get("flow_id")
            flow = document.get("flows", {}).get(flow_id) if flow_id else None
            if flow:
                flow["binding"] = copy.deepcopy(intent["target"])
                flow["state"] = "READY_FOR_BIND"
                flow["updated_at"] = utc_now()
            return copy.deepcopy(intent)
        return self.store.mutate(change)

    def _unbind_in_document(
        self,
        document: dict[str, Any],
        intent: dict[str, Any],
        allow_default_release: bool = False,
    ) -> list[dict[str, Any]]:
        release = []
        target = intent.get("target")
        if target:
            key = session_key(intent["ue_id"], target["pdu_session_id"])
            session = document["sessions"].get(key)
            if session and intent["intent_id"] in session["references"]:
                session["references"].remove(intent["intent_id"])
                if not session["references"]:
                    session["empty_since"] = utc_now()
                    if (
                        session["pdu_session_id"] != self.default_pdu_session_id
                        or allow_default_release
                    ):
                        release.append(copy.deepcopy(session))
        intent["state"] = "ENDED"
        intent["updated_at"] = utc_now()
        return release

    def acknowledge_application(self, intent_id: str) -> list[dict[str, Any]]:
        """Commit a migration only after the application opened its new flow."""
        def change(document):
            intent = document["intents"].get(intent_id)
            if not intent:
                raise KeyError(intent_id)
            if intent["state"] != "AWAITING_APPLICATION_ACK":
                raise ValueError(f"intent is not awaiting ACK: {intent['state']}")
            release = []
            # Record the commit point before authorizing any old-session
            # release. The ordered events are audit evidence for
            # establish-before-release at the orchestration layer.
            document["events"].append({
                "at": utc_now(), "intent_id": intent_id,
                "event": "APPLICATION_BINDING_ACKNOWLEDGED",
            })
            replaced_id = intent.get("replaces_intent_id")
            if replaced_id:
                replaced = document["intents"].get(replaced_id)
                if replaced and replaced["state"] not in TERMINAL_STATES:
                    # Keep the protected default PDU as the always-available
                    # management and fallback path.
                    release.extend(self._unbind_in_document(document, replaced))
                    replaced_flow_id = replaced.get("flow_id")
                    replaced_flow = (
                        document.get("flows", {}).get(replaced_flow_id)
                        if replaced_flow_id else None
                    )
                    if replaced_flow:
                        replaced_flow["intent_id"] = None
                        replaced_flow["binding"] = None
                        if replaced_flow.get("state") != "ENDED":
                            replaced_flow["state"] = "SUPERSEDED"
                        replaced_flow["updated_at"] = utc_now()
                    document["events"].append({
                        "at": utc_now(), "intent_id": intent_id,
                        "replaced_intent_id": replaced_id,
                        "event": "OLD_SESSION_RELEASE_AUTHORIZED_AFTER_ACK",
                        "pdu_session_ids": [
                            item["pdu_session_id"] for item in release
                        ],
                    })
            intent["state"] = "ACTIVE"
            intent["updated_at"] = utc_now()
            flow_id = intent.get("flow_id")
            flow = document.get("flows", {}).get(flow_id) if flow_id else None
            if flow:
                flow["state"] = "STEERED"
                flow["updated_at"] = utc_now()
            return release
        return self.store.mutate(change)

    def end(self, intent_id: str) -> list[dict[str, Any]]:
        def change(document):
            intent = document["intents"].get(intent_id)
            if not intent:
                raise KeyError(intent_id)
            if intent["state"] in TERMINAL_STATES:
                return []
            release = self._unbind_in_document(document, intent)
            flow_id = intent.get("flow_id")
            flow = document.get("flows", {}).get(flow_id) if flow_id else None
            if flow and flow.get("intent_id") == intent_id:
                flow["intent_id"] = None
                flow["binding"] = None
                if flow.get("state") != "ENDED":
                    flow["state"] = "OBSERVING"
                flow["updated_at"] = utc_now()
            document["events"].append({
                "at": utc_now(), "intent_id": intent_id,
                "event": "INTENT_ENDED",
            })
            return release
        return self.store.mutate(change)

    def snapshot(self) -> dict[str, Any]:
        return self.store.read()

    def mark_session_state(
        self,
        ue_id: int,
        pdu_session_id: int,
        state: str,
        **fields: Any,
    ) -> None:
        def change(document):
            session = document["sessions"].get(
                session_key(ue_id, pdu_session_id)
            )
            if session:
                session["state"] = state
                session.update(fields)
        self.store.mutate(change)
