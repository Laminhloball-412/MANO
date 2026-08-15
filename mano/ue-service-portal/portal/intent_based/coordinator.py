from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
from django.conf import settings

from portal.lab_controller import (
    LabControllerError,
    connect_selected_ue,
    radio_status,
)

from .policy import SLICE_PROFILES, choose_slice, choose_slice_from_observed


AUTO_SLICE_STATUS_URL = os.environ.get(
    "AUTO_SLICE_STATUS_URL", "http://127.0.0.1:8888/api/auto-slice/status/"
)
AUTO_SLICE_CREATE_URL = os.environ.get(
    "AUTO_SLICE_CREATE_URL", "http://127.0.0.1:8888/api/auto-slice/create/"
)
MONGO_NAMESPACE = os.environ.get("OPEN5GS_MONGO_NAMESPACE", "share")
MONGO_LABEL = os.environ.get("OPEN5GS_MONGO_LABEL", "app=mongodb-svc")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def run(command, timeout=30, check=True):
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    if check and completed.returncode:
        raise RuntimeError(
            completed.stderr.strip() or completed.stdout.strip() or "Command failed"
        )
    return completed


def _read_ue_config(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(str(path), encoding="utf-8")
    match = re.fullmatch(r"ue(\d+)\.conf", path.name)
    if not match or not parser.has_option("usim", "imsi"):
        return None
    return {
        "ue_id": int(match.group(1)),
        "name": "ue-{}".format(match.group(1)),
        "imsi": parser.get("usim", "imsi").strip(),
        "dnn": parser.get("nas", "apn", fallback="srsapn").strip(),
        "configured_sst": parser.getint("slicing", "nssai-sst", fallback=1),
        "configured_sd": parser.get(
            "slicing", "nssai-sd", fallback="000001"
        ).strip().upper().zfill(6),
        "config_path": str(path),
    }


def configured_ues():
    values = []
    for path in sorted(settings.UE_CONFIG_DIR.glob("ue[0-9]*.conf")):
        item = _read_ue_config(path)
        if item:
            values.append(item)
    return values


def _mongo_pod():
    result = run(
        [
            "kubectl",
            "get",
            "pod",
            "-n",
            MONGO_NAMESPACE,
            "-l",
            MONGO_LABEL,
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ]
    )
    pod = result.stdout.strip()
    if not pod:
        raise RuntimeError("Open5GS MongoDB pod was not found")
    return pod


def subscriber_for_imsi(imsi):
    expression = (
        "var d=db.subscribers.findOne({imsi:'%s'},{_id:0,imsi:1,slice:1});"
        "print(JSON.stringify(d));" % imsi
    )
    result = run(
        [
            "kubectl",
            "exec",
            "-n",
            MONGO_NAMESPACE,
            _mongo_pod(),
            "--",
            "mongo",
            "open5gs",
            "--quiet",
            "--eval",
            expression,
        ],
        timeout=30,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines or lines[-1] in {"null", "undefined"}:
        return None
    return json.loads(lines[-1])


def allowed_pairs(subscriber):
    pairs = []
    for item in (subscriber or {}).get("slice", []):
        sst = int(item.get("sst", 0))
        sd = str(item.get("sd") or "000000").upper().zfill(6)
        dnns = [session.get("name") for session in item.get("session", [])]
        pairs.append({"sst": sst, "sd": sd, "dnns": [d for d in dnns if d]})
    return pairs


def ue_inventory():
    inventory = []
    for ue in configured_ues():
        try:
            subscriber = subscriber_for_imsi(ue["imsi"])
            pairs = allowed_pairs(subscriber)
            error = None
        except Exception as exc:
            subscriber = None
            pairs = []
            error = str(exc)
        allowed_types = []
        for name, profile in SLICE_PROFILES.items():
            if any(
                pair["sst"] == profile["sst"]
                and pair["sd"] == profile["sd"]
                and ue["dnn"] in pair["dnns"]
                for pair in pairs
            ):
                allowed_types.append(name)
        inventory.append(
            {
                **ue,
                "subscriber_registered": subscriber is not None,
                "allowed_pairs": pairs,
                "allowed_slice_types": allowed_types,
                "inventory_error": error,
            }
        )
    return inventory


def _pod_and_endpoint_health(namespace):
    pods = json.loads(
        run(["kubectl", "get", "pods", "-n", namespace, "-o", "json"]).stdout
    ).get("items", [])
    expected = {"pcf": False, "smf": False, "upf": False}
    pod_names = []
    for pod in pods:
        name = pod.get("metadata", {}).get("name", "")
        pod_names.append(name)
        statuses = pod.get("status", {}).get("containerStatuses", [])
        ready = bool(statuses) and all(item.get("ready") for item in statuses)
        for component in expected:
            if component in name.lower() and ready:
                expected[component] = True

    endpoints = json.loads(
        run(
            ["kubectl", "get", "endpoints", "-n", namespace, "-o", "json"]
        ).stdout
    ).get("items", [])
    endpoint_names = set()
    for endpoint in endpoints:
        subsets = endpoint.get("subsets") or []
        if any(subset.get("addresses") for subset in subsets):
            endpoint_names.add(endpoint.get("metadata", {}).get("name"))
    required_endpoints = {"pcf-svc", "smf-svc", "upf-svc"}
    smf_pods = [name for name in pod_names if "smf" in name.lower()]
    pfcp_associated = False
    if len(smf_pods) == 1:
        logs = run(
            ["kubectl", "logs", "-n", namespace, smf_pods[0], "--tail=500"],
            check=False,
        ).stdout
        associated_at = logs.rfind("PFCP associated")
        unhealthy_at = max(
            logs.rfind("PFCP de-associated"),
            logs.rfind("No UPF available"),
        )
        pfcp_associated = associated_at >= 0 and associated_at > unhealthy_at
    infrastructure_ready = all(expected.values()) and required_endpoints.issubset(
        endpoint_names
    )
    return {
        "operational": infrastructure_ready and pfcp_associated,
        "infrastructure_ready": infrastructure_ready,
        "pfcp_associated": pfcp_associated,
        "components": expected,
        "pods": pod_names,
        "endpoints": sorted(endpoint_names),
    }


def _wait_slice_health(namespace, timeout=90):
    deadline = time.monotonic() + timeout
    health = None
    while time.monotonic() < deadline:
        health = _pod_and_endpoint_health(namespace)
        if health["operational"]:
            return health
        time.sleep(3)
    return health or _pod_and_endpoint_health(namespace)


def _restart_smf_for_pfcp(namespace):
    deployments = json.loads(
        run(["kubectl", "get", "deployments", "-n", namespace, "-o", "json"]).stdout
    ).get("items", [])
    names = [
        item.get("metadata", {}).get("name", "")
        for item in deployments
        if "smf" in item.get("metadata", {}).get("name", "").lower()
    ]
    if len(names) != 1:
        raise RuntimeError(
            "Expected exactly one SMF deployment in {}, found {}".format(
                namespace, names
            )
        )
    run(["kubectl", "rollout", "restart", "deployment/{}".format(names[0]), "-n", namespace])
    run(
        [
            "kubectl",
            "rollout",
            "status",
            "deployment/{}".format(names[0]),
            "-n",
            namespace,
            "--timeout=120s",
        ],
        timeout=130,
    )


def _auto_slice_status():
    response = requests.get(AUTO_SLICE_STATUS_URL, timeout=90)
    response.raise_for_status()
    document = response.json()
    if not document.get("ok"):
        raise RuntimeError(document.get("error") or "Auto Slice status failed")
    return document["data"]


def _find_target_slice(status, selected):
    for namespace, item in (status.get("tracked_slices") or {}).items():
        if item.get("status") in {"DELETED", "ROLLED_BACK"}:
            continue
        if (
            int(item.get("sst", -1)) == int(selected["sst"])
            and str(item.get("sd", "")).upper().zfill(6) == selected["sd"]
        ):
            return {**item, "namespace": namespace}
    return None


def ensure_slice(selected):
    status = _auto_slice_status()
    existing = _find_target_slice(status, selected)
    if existing:
        health = _wait_slice_health(existing["namespace"], timeout=15)
        if health["operational"]:
            return {"action": "REUSED", "slice": existing, "health": health}
        # A headless UPF service resolves to the pod IP. If the UPF pod was
        # recreated, an already-running SMF can retain the old PFCP address.
        # Refresh only the SMF, then require a real PFCP association before UE
        # attach; pod Running/Ready alone is not sufficient.
        if health.get("infrastructure_ready") and not health.get("pfcp_associated"):
            _restart_smf_for_pfcp(existing["namespace"])
            health = _wait_slice_health(existing["namespace"], timeout=90)
            if health["operational"]:
                return {
                    "action": "REUSED_REPAIRED_PFCP",
                    "slice": existing,
                    "health": health,
                }
        raise RuntimeError(
            "Target slice {} exists but is not operational: {}".format(
                existing["namespace"], health
            )
        )

    response = requests.post(
        AUTO_SLICE_CREATE_URL,
        json={
            "slice_type": selected["slice_type"],
            "sst": selected["sst"],
            "sd": selected["sd"],
        },
        timeout=420,
    )
    response.raise_for_status()
    document = response.json()
    if not document.get("ok"):
        raise RuntimeError(document.get("error") or "Auto Slice create failed")
    created = document["data"]
    if (
        int(created.get("sst", -1)) != selected["sst"]
        or str(created.get("sd", "")).upper().zfill(6) != selected["sd"]
    ):
        raise RuntimeError("Auto Slice created a different S-NSSAI: {}".format(created))
    health = _wait_slice_health(created["namespace"], timeout=90)
    if created.get("status") != "READY" or not health["operational"]:
        raise RuntimeError("Created slice did not become operational: {}".format(health))
    return {"action": "CREATED", "slice": created, "health": health}


class JobStore:
    def __init__(self):
        self.path = settings.UE_RUNTIME_DIR / "intent_jobs.json"
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

    def create(self, payload):
        with self.lock:
            jobs = self._read()
            job_id = "intent-{}".format(uuid.uuid4().hex[:12])
            job = {
                "job_id": job_id,
                "status": "QUEUED",
                "stage": "QUEUED",
                "created_at": utcnow(),
                "input": payload,
                "events": [{"at": utcnow(), "stage": "QUEUED"}],
            }
            jobs[job_id] = job
            self._write(jobs)
            return job

    def get(self, job_id):
        with self.lock:
            return self._read().get(job_id)

    def update(self, job_id, **values):
        with self.lock:
            jobs = self._read()
            job = jobs[job_id]
            stage = values.get("stage")
            job.update(values)
            if stage:
                job.setdefault("events", []).append({"at": utcnow(), "stage": stage})
            jobs[job_id] = job
            self._write(jobs)
            return job


STORE = JobStore()
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="intent-attach")


def _selected_ue(ue_id):
    for ue in ue_inventory():
        if int(ue["ue_id"]) == int(ue_id):
            return ue
    raise ValueError("Configured UE{} was not found".format(ue_id))


def _verify_subscriber(ue, selected):
    if not ue["subscriber_registered"]:
        raise RuntimeError("Subscriber {} is not registered in Open5GS".format(ue["imsi"]))
    target = (selected["sst"], selected["sd"])
    for pair in ue["allowed_pairs"]:
        if (pair["sst"], pair["sd"]) == target and ue["dnn"] in pair["dnns"]:
            return pair
    raise RuntimeError(
        "Subscriber {} does not allow SST {} / SD {} / DNN {}".format(
            ue["imsi"], selected["sst"], selected["sd"], ue["dnn"]
        )
    )


def _wait_for_pdu(ue_id, timeout=75):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        state = radio_status()
        matches = [item for item in state.get("ues", []) if int(item["id"]) == int(ue_id)]
        if matches:
            last = matches[0]
            if last.get("status") == "CONNECTED":
                return state, last
            if last.get("status") == "FAILED":
                raise RuntimeError("UE attach failed; inspect {}".format(last.get("log")))
        time.sleep(1)
    raise RuntimeError("Timed out waiting for UE PDU session: {}".format(last))


def _execute(job_id):
    try:
        job = STORE.get(job_id)
        payload = job["input"]
        STORE.update(job_id, status="RUNNING", stage="VALIDATING_UE")
        ue_id = int((payload.get("ue") or {}).get("ue_id"))
        ue = _selected_ue(ue_id)

        STORE.update(job_id, stage="CLASSIFYING_INTENT", ue=ue)
        if payload.get("input_source") == "prometheus_simulation":
            decision = choose_slice_from_observed(
                payload.get("observed_kpis"),
                allowed_slice_types=ue["allowed_slice_types"],
            )
        else:
            decision = choose_slice(
                payload.get("qos_requirements"),
                allowed_slice_types=ue["allowed_slice_types"],
            )
        selected = decision["selected_slice"]
        STORE.update(job_id, stage="VALIDATING_SUBSCRIBER", decision=decision)
        _verify_subscriber(ue, selected)

        STORE.update(job_id, stage="RESOLVING_SLICE")
        slice_result = ensure_slice(selected)
        STORE.update(
            job_id,
            stage=("REUSING_SLICE" if slice_result["action"] == "REUSED" else "SLICE_CREATED"),
            slice_result=slice_result,
        )

        STORE.update(job_id, stage="RENDERING_UE_CONFIG")
        radio = connect_selected_ue(ue_id, selected["sst"], selected["sd"])
        STORE.update(job_id, stage="WAITING_PDU", radio=radio)
        state, attached = _wait_for_pdu(ue_id)

        STORE.update(job_id, stage="PDU_SESSION_ACTIVE", radio=state, attached_ue=attached)
        STORE.update(
            job_id,
            status="COMPLETED",
            stage="COMPLETED",
            completed_at=utcnow(),
            verification={"attach": True, "pdu_session": True, "ip_assigned": attached.get("ip")},
        )
    except Exception as exc:
        STORE.update(
            job_id,
            status="FAILED",
            stage="FAILED",
            failed_at=utcnow(),
            error=str(exc),
        )


def submit_job(payload):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    ue = payload.get("ue") or {}
    try:
        int(ue.get("ue_id"))
    except (TypeError, ValueError):
        raise ValueError("ue.ue_id must be an integer")
    # Validate before queueing so malformed inputs receive HTTP 400 immediately.
    if payload.get("input_source") == "prometheus_simulation":
        choose_slice_from_observed(payload.get("observed_kpis"))
    else:
        choose_slice(payload.get("qos_requirements"))
    job = STORE.create(payload)
    EXECUTOR.submit(_execute, job["job_id"])
    return job


def get_job(job_id):
    return STORE.get(job_id)
