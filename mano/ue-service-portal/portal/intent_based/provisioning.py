from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

from .business_intent import translate_business_requirement
from .policy import NoFeasibleTemplate, decide_slice
from .service_intent import load_catalog, translate_service_intent
from .slice_lifecycle import (
    ensure_slice,
    list_slice_instances,
    plan_slice,
    verify_core_slice_registration,
)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def _event(events, stage, detail=None):
    event = {"at": utcnow(), "stage": stage}
    if detail:
        event["detail"] = detail
    events.append(event)


def process_intent(
    payload,
    instances=None,
    inventory_provider=list_slice_instances,
    ensure_provider=ensure_slice,
    core_registration_provider=verify_core_slice_registration,
):
    events = []
    _event(events, "RECEIVED")
    translated = translate_service_intent(payload)
    _event(events, "VALIDATED")
    _event(events, "TRANSLATED")
    available = inventory_provider() if instances is None else instances
    decision = decide_slice(translated["technical_sla"], instances=available)
    _event(events, "DECIDING")
    plan = plan_slice(
        decision["selected_candidate"],
        translated["technical_sla"],
        translated["deployment_policy"],
        service_intent=translated["intent"],
    )
    _event(events, "PLANNED")
    result = {
        "status": "RUNNING",
        "stage": "PLANNED",
        "input": translated["intent"],
        "translation": translated,
        "decision": decision,
        "plan": plan,
        "configuration": deepcopy(plan["deployment_configuration"]),
        "events": events,
    }
    if translated["intent"]["execution_mode"] == "dry_run":
        _event(events, "DRY_RUN")
        result.update(
            status="COMPLETED", stage="DRY_RUN", completed_at=utcnow()
        )
        return result

    _event(events, "EXECUTING")
    slice_result = ensure_provider(plan)
    action_stage = "REUSED" if slice_result["action"] == "REUSED" else "ALLOCATING"
    _event(events, action_stage)
    _event(events, "INSPECTING")
    inspection = slice_result["inspection"]
    if not inspection.get("health", {}).get("operational"):
        raise RuntimeError("Provisioned slice is not operational")
    allocated = slice_result.get("slice") or {}
    allocated_status = allocated.get("status")
    core_registration = allocated.get("core_registration")
    if allocated_status == "READY_FOR_UE":
        if not core_registration or not core_registration.get("verified"):
            raise RuntimeError("Allocator reported READY_FOR_UE without verified core registration")
        _event(events, "REGISTERING_NSSF")
        _event(events, "VERIFYING_NSSF", core_registration)
        _event(events, "READY_FOR_UE")
        final_stage = "READY_FOR_UE"
    elif slice_result.get("action") == "REUSED":
        _event(events, "REGISTERING_NSSF")
        core_registration = core_registration_provider(
            inspection["configuration"]["sst"],
            inspection["configuration"]["sd"],
        )
        if not core_registration.get("verified"):
            raise RuntimeError("Reused slice is missing verified core registration")
        _event(events, "VERIFYING_NSSF", core_registration)
        _event(events, "READY_FOR_UE")
        final_stage = "READY_FOR_UE"
    else:
        _event(events, "READY")
        final_stage = "READY"
    result.update(
        status="COMPLETED",
        stage=final_stage,
        slice_result=slice_result,
        core_registration=deepcopy(core_registration),
        configuration=inspection["configuration"],
        completed_at=utcnow(),
    )
    return result


class JobStore:
    def __init__(self, path=None):
        if path is None:
            runtime = getattr(
                settings, "UE_RUNTIME_DIR", Path("/var/lib/ansa-slice-provisioning")
            )
            path = Path(runtime) / "slice_provisioning_jobs.json"
        self.path = Path(path)
        self.lock = threading.RLock()

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, jobs):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def create(self, payload, context=None):
        with self.lock:
            jobs = self._read()
            intent_id = "slice-intent-{}".format(uuid.uuid4().hex[:12])
            job = {
                "intent_id": intent_id,
                "status": "QUEUED",
                "stage": "RECEIVED",
                "created_at": utcnow(),
                "input": deepcopy(payload),
                "events": [{"at": utcnow(), "stage": "RECEIVED"}],
            }
            safe_context = {
                key: deepcopy((context or {})[key])
                for key in ("business_input", "business_translation")
                if key in (context or {})
            }
            job.update(safe_context)
            jobs[intent_id] = job
            self._write(jobs)
            return job

    def get(self, intent_id):
        with self.lock:
            return self._read().get(intent_id)

    def update(self, intent_id, **values):
        with self.lock:
            jobs = self._read()
            job = jobs[intent_id]
            job.update(deepcopy(values))
            jobs[intent_id] = job
            self._write(jobs)
            return job


STORE = JobStore()
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slice-provisioning")


def _execute(intent_id):
    job = STORE.get(intent_id)
    try:
        STORE.update(intent_id, status="RUNNING", stage="VALIDATED")
        result = process_intent(job["input"])
        STORE.update(intent_id, **result)
    except NoFeasibleTemplate as error:
        STORE.update(
            intent_id,
            status="FAILED",
            stage="FAILED",
            error=str(error),
            candidates=error.candidates,
            failed_at=utcnow(),
        )
    except Exception as error:
        STORE.update(
            intent_id,
            status="FAILED",
            stage="FAILED",
            error=str(error),
            failed_at=utcnow(),
        )


def submit_job(payload):
    translated = translate_service_intent(payload)
    job = STORE.create(translated["intent"])
    EXECUTOR.submit(_execute, job["intent_id"])
    return job


def submit_business_job(payload):
    translated = translate_business_requirement(payload)
    structured = translated["structured_intent"]
    if (
        translated["trace"]["confidence"] == "low"
        and structured["execution_mode"] == "execute"
    ):
        raise ValueError(
            "Low-confidence business intent must be clarified before execute"
        )
    job = STORE.create(
        structured,
        context={
            "business_input": translated["business_requirement"],
            "business_translation": translated["trace"],
        },
    )
    EXECUTOR.submit(_execute, job["intent_id"])
    return job


def get_job(intent_id):
    return STORE.get(intent_id)


def catalog_document():
    catalog = load_catalog()
    return {
        "catalog_version": catalog["catalog_version"],
        "services": catalog["services"],
        "priorities": catalog["priorities"],
        "templates": catalog["templates"],
    }


def slice_inventory():
    return list_slice_instances()
