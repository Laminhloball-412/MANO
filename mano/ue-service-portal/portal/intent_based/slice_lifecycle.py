from __future__ import annotations

import json
import os
import subprocess
import time
from copy import deepcopy

import requests

from .service_intent import load_catalog


AUTO_SLICE_STATUS_URL = os.environ.get(
    "AUTO_SLICE_STATUS_URL", "http://127.0.0.1:8888/api/auto-slice/status/"
)
AUTO_SLICE_CREATE_URL = os.environ.get(
    "AUTO_SLICE_CREATE_URL", "http://127.0.0.1:8888/api/auto-slice/create/"
)
INTENT_FLOW_CLI = os.environ.get(
    "INTENT_FLOW_CLI", "/home/ubuntu/mano/intent_flow_v2/intent_flow.py"
)


def run(command, timeout=30, check=True):
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    if check and completed.returncode:
        raise RuntimeError(
            completed.stderr.strip() or completed.stdout.strip() or "Command failed"
        )
    return completed


def _pod_and_endpoint_health(namespace):
    pods = json.loads(
        run(["kubectl", "get", "pods", "-n", namespace, "-o", "json"]).stdout
    ).get("items", [])
    components = {"pcf": False, "smf": False, "upf": False}
    pod_names = []
    for pod in pods:
        name = pod.get("metadata", {}).get("name", "")
        pod_names.append(name)
        statuses = pod.get("status", {}).get("containerStatuses", [])
        ready = bool(statuses) and all(item.get("ready") for item in statuses)
        for component in components:
            if component in name.lower() and ready:
                components[component] = True

    endpoints = json.loads(
        run(["kubectl", "get", "endpoints", "-n", namespace, "-o", "json"]).stdout
    ).get("items", [])
    endpoint_names = {
        endpoint.get("metadata", {}).get("name")
        for endpoint in endpoints
        if any(subset.get("addresses") for subset in endpoint.get("subsets") or [])
    }
    required_endpoints = {"pcf-svc", "smf-svc", "upf-svc"}
    smf_pods = [name for name in pod_names if "smf" in name.lower()]
    pfcp_associated = False
    if len(smf_pods) == 1:
        logs = run(
            ["kubectl", "logs", "-n", namespace, smf_pods[0], "--tail=500"],
            check=False,
        ).stdout
        associated_at = logs.rfind("PFCP associated")
        unhealthy_at = max(logs.rfind("PFCP de-associated"), logs.rfind("No UPF available"))
        pfcp_associated = associated_at >= 0 and associated_at > unhealthy_at
    infrastructure_ready = all(components.values()) and required_endpoints.issubset(
        endpoint_names
    )
    return {
        "operational": infrastructure_ready and pfcp_associated,
        "infrastructure_ready": infrastructure_ready,
        "pfcp_associated": pfcp_associated,
        "components": components,
        "pods": pod_names,
        "endpoints": sorted(name for name in endpoint_names if name),
    }


def _wait_slice_health(namespace, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = _pod_and_endpoint_health(namespace)
        if last["operational"]:
            return last
        time.sleep(3)
    return last or _pod_and_endpoint_health(namespace)


def _auto_slice_status():
    response = requests.get(AUTO_SLICE_STATUS_URL, timeout=90)
    response.raise_for_status()
    document = response.json()
    if not document.get("ok"):
        raise RuntimeError(document.get("error") or "Auto Slice status failed")
    return document["data"]


def verify_core_slice_registration(sst, sd):
    completed = run(
        ["python3", INTENT_FLOW_CLI, "core-status"], timeout=90
    )
    document = json.loads(completed.stdout)
    target = (int(sst), str(sd).upper().zfill(6))
    registered = {
        (int(item["sst"]), str(item["sd"]).upper().zfill(6))
        for item in document.get("snssai", [])
    }
    if not document.get("verified") or target not in registered:
        raise RuntimeError(
            "Shared AMF/NSSF verification does not contain SST {} SD {}".format(
                target[0], target[1]
            )
        )
    return document


def _find_target_slice(status, selected):
    for namespace, item in (status.get("tracked_slices") or {}).items():
        if item.get("status") in {"DELETED", "ROLLED_BACK"}:
            continue
        if selected.get("namespace") and namespace == selected["namespace"]:
            return dict(item, namespace=namespace)
        sd_matches = selected.get("sd") is None or (
            str(item.get("sd", "")).upper().zfill(6)
            == str(selected["sd"]).upper().zfill(6)
        )
        if int(item.get("sst", -1)) == int(selected["sst"]) and sd_matches:
            return dict(item, namespace=namespace)
    return None


def plan_slice(selected, technical_sla, deployment_policy, service_intent=None):
    legacy_qos = {
        "five_qi": int(deployment_policy["five_qi"]),
        "arp_priority": int(deployment_policy["arp_priority"]),
        "resource_type": "NON_GBR",
    }
    default_qos = deployment_policy.get("default_qos") or legacy_qos
    service_qos = deployment_policy.get("service_qos") or legacy_qos
    configuration = {
        "template_family": selected["template_family"],
        "namespace": selected.get("namespace"),
        "sst": int(selected["sst"]),
        "sd": selected.get("sd"),
        "dnn": deployment_policy["dnn"],
        "default_qos": deepcopy(default_qos),
        "service_qos": deepcopy(service_qos),
        "five_qi": int(deployment_policy["five_qi"]),
        "arp_priority": int(deployment_policy["arp_priority"]),
        "session_ambr_mbps": float(deployment_policy["session_ambr_mbps"]),
        "downlink_limit_mbps": float(deployment_policy["downlink_limit_mbps"]),
        "min_throughput_mbps": float(technical_sla["min_throughput_mbps"]),
        "target_throughput_mbps": float(technical_sla["target_throughput_mbps"]),
        "peak_throughput_mbps": float(technical_sla["peak_throughput_mbps"]),
        "max_latency_ms": float(technical_sla["max_latency_ms"]),
        "max_jitter_ms": float(technical_sla["max_jitter_ms"]),
        "max_packet_loss_percent": float(
            technical_sla["max_packet_loss_percent"]
        ),
        "min_reliability_percent": float(
            technical_sla["min_reliability_percent"]
        ),
        "expected_sessions": int(technical_sla["expected_sessions"]),
        "evidence_status": deployment_policy["evidence_status"],
        "qos_evidence": {
            "core_policy": "DECLARED_NOT_ENFORCED",
            "radio": "NOT_VERIFIED",
        },
    }
    return {
        "action": selected["action"],
        "selected_candidate": deepcopy(selected),
        "service_intent": deepcopy(service_intent),
        "technical_sla": deepcopy(technical_sla),
        "deployment_policy": deepcopy(deployment_policy),
        "deployment_configuration": configuration,
    }


def inspect_slice(namespace, plan=None, allocation=None):
    configuration = deepcopy((plan or {}).get("deployment_configuration") or {})
    configuration["namespace"] = namespace
    if allocation:
        if allocation.get("sst") is not None:
            configuration["sst"] = int(allocation["sst"])
        if allocation.get("sd"):
            configuration["sd"] = str(allocation["sd"]).upper().zfill(6)
    return {
        "namespace": namespace,
        "configuration": configuration,
        "health": _pod_and_endpoint_health(namespace),
    }


def list_slice_instances():
    status = _auto_slice_status()
    catalog = load_catalog()
    instances = []
    for namespace, item in sorted((status.get("tracked_slices") or {}).items()):
        if item.get("status") in {"DELETED", "ROLLED_BACK"}:
            continue
        family = str(item.get("slice_type") or item.get("profile") or "").lower()
        if family not in catalog["templates"]:
            continue
        template = catalog["templates"][family]
        try:
            health = _pod_and_endpoint_health(namespace)
        except Exception as error:
            health = {
                "operational": False,
                "infrastructure_ready": False,
                "pfcp_associated": False,
                "components": {"pcf": False, "smf": False, "upf": False},
                "pods": [],
                "endpoints": [],
                "error": str(error),
            }
        instances.append(
            {
                "candidate_id": namespace,
                "namespace": namespace,
                "template_family": family,
                "sst": int(item.get("sst", template["sst"])),
                "sd": str(item.get("sd") or "").upper().zfill(6) or None,
                "healthy": health["operational"],
                "health": health,
                "residual_throughput_mbps": float(
                    item.get("residual_throughput_mbps", template["max_throughput_mbps"])
                ),
                "remaining_sessions": int(
                    item.get("remaining_sessions", template["max_sessions"])
                ),
                "evidence_status": item.get(
                    "evidence_status", template["evidence_status"]
                ),
                "allocation": deepcopy(item),
            }
        )
    return instances


def ensure_slice(selected_or_plan):
    plan = selected_or_plan if "selected_candidate" in selected_or_plan else None
    selected = (plan or {}).get("selected_candidate") or selected_or_plan
    status = _auto_slice_status()
    existing = _find_target_slice(status, selected)
    if existing:
        inspected = inspect_slice(existing["namespace"], plan, allocation=existing)
        if not inspected["health"]["operational"]:
            raise RuntimeError(
                "Target slice {} exists but is not operational".format(
                    existing["namespace"]
                )
            )
        return {
            "action": "REUSED",
            "slice": existing,
            "inspection": inspected,
        }

    payload = {
        "slice_type": selected["template_family"]
        if "template_family" in selected
        else selected["slice_type"],
        "sst": int(selected["sst"]),
    }
    if selected.get("sd"):
        payload["sd"] = selected["sd"]
    if plan:
        payload.update(
            {
                "service_intent": plan.get("service_intent"),
                "technical_sla": plan["technical_sla"],
                "deployment_policy": plan["deployment_policy"],
            }
        )
    try:
        response = requests.post(AUTO_SLICE_CREATE_URL, json=payload, timeout=420)
    except requests.RequestException:
        reconciled = _find_target_slice(_auto_slice_status(), selected)
        if reconciled:
            inspection = inspect_slice(
                reconciled["namespace"], plan, allocation=reconciled
            )
            if inspection["health"]["operational"]:
                return {
                    "action": "RECOVERED",
                    "slice": reconciled,
                    "inspection": inspection,
                }
        raise
    response.raise_for_status()
    document = response.json()
    if not document.get("ok"):
        raise RuntimeError(document.get("error") or "Auto Slice create failed")
    created = document["data"]
    namespace = created["namespace"]
    health = _wait_slice_health(namespace, timeout=90)
    if created.get("status") not in {"READY", "READY_FOR_UE"} or not health[
        "operational"
    ]:
        raise RuntimeError("Created slice did not become operational")
    inspection = inspect_slice(namespace, plan, allocation=created)
    return {"action": "CREATED", "slice": created, "inspection": inspection}
