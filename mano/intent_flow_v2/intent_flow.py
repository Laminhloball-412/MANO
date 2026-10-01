#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import contextlib
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

from core_slice_registry import CoreSliceRegistry, desired_snssai
from renderer import (
    clean_failed_run,
    render_nrm_package,
    render_packages,
    validate_source_packages,
)


DEFAULT_QOS_BY_SLICE = {
    "embb": {"five_qi": 9, "arp_priority": 8, "resource_type": "NON_GBR"},
    "urllc": {"five_qi": 7, "arp_priority": 4, "resource_type": "NON_GBR"},
    "mmtc": {"five_qi": 9, "arp_priority": 12, "resource_type": "NON_GBR"},
}
# Accepted declarative 5QI profiles for this orchestration demo. These values are
# carried into evidence/manifests; acceptance does not claim core or radio enforcement.
SUPPORTED_5QI_BY_SLICE = {
    "embb": {1, 2, 7, 8, 9, 80},
    "urllc": {3, 7, 65, 67},
    "mmtc": {8, 9},
}


ROOT = Path(__file__).resolve().parent
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
    r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)


def normalize_uuid(value: str | None) -> str | None:
    if not value:
        return None
    compact = str(value).strip()
    if not UUID_RE.fullmatch(compact):
        return compact
    return str(uuid.UUID(compact))


def run(
    command: list[str],
    *,
    check: bool = True,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr.strip()}"
        )
    return result


class ApiError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} -> HTTP {status}: {body[:1000]}")
        self.method = method
        self.url = url
        self.status = status
        self.body = body


class Api:
    def __init__(self, config: dict[str, Any]):
        self.base_url = config["free5gmano_url"].rstrip("/") + "/"
        self.nfvo_url = config["nfvo_url"].rstrip("/") + "/"
        self.timeout = int(config.get("http_timeout_seconds", 60))
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def request(
        self,
        method: str,
        url: str,
        *,
        expected: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> requests.Response:
        response = self.session.request(
            method,
            url,
            timeout=kwargs.pop("timeout", self.timeout),
            **kwargs,
        )
        if response.status_code not in expected:
            raise ApiError(method, url, response.status_code, response.text)
        return response

    def free5g(self, path: str) -> str:
        return self.base_url + path.lstrip("/")

    def nfvo(self, path: str) -> str:
        return self.nfvo_url + path.lstrip("/")

    def generic_templates(self) -> list[dict[str, Any]]:
        return self.request(
            "GET", self.free5g("ObjectManagement/GenericTemplate/")
        ).json()

    def generic_template(self, template_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            self.free5g(f"ObjectManagement/GenericTemplate/{template_id}/"),
        ).json()

    def slice_templates(self) -> list[dict[str, Any]]:
        return self.request(
            "GET", self.free5g("ObjectManagement/SliceTemplate/")
        ).json()

    def slice_template(self, template_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            self.free5g(f"ObjectManagement/SliceTemplate/{template_id}/"),
        ).json()

    def create_generic(
        self,
        template_type: str,
        name: str,
        description: str,
        nfvo_type: str,
    ) -> dict[str, Any]:
        payload = {
            "nfvoType": nfvo_type,
            "templateType": template_type,
            "name": name,
            "description": description,
        }
        return self.request(
            "POST",
            self.free5g("ObjectManagement/GenericTemplate/"),
            expected=(200, 201),
            data=payload,
        ).json()

    def upload_generic(
        self,
        template_id: str,
        template_type: str,
        path: Path,
        nfvo_type: str,
    ) -> dict[str, Any]:
        with path.open("rb") as stream:
            self.request(
                "PUT",
                self.free5g(f"ObjectManagement/GenericTemplate/{template_id}/"),
                expected=(200, 201, 204),
                data={"nfvoType": nfvo_type, "templateType": template_type},
                files={"templateFile": (path.name, stream, "application/zip")},
            )
        detail = self.generic_template(template_id)
        if detail.get("operationStatus") != "UPLOAD":
            raise RuntimeError(
                f"GenericTemplate {template_id} is not UPLOAD: "
                f"{detail.get('operationStatus')}"
            )
        return detail

    def create_slice_template(
        self,
        *,
        generic_ids: list[str],
        nfvo_type: str,
        description: str,
    ) -> dict[str, Any]:
        form: list[tuple[str, str]] = [
            ("nfvoType", nfvo_type),
            ("description", description),
        ]
        form.extend(("genericTemplates", value) for value in generic_ids)
        return self.request(
            "POST",
            self.free5g("ObjectManagement/SliceTemplate/"),
            expected=(200, 201),
            data=form,
        ).json()

    def allocate(self, template_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            self.free5g("ObjectManagement/NSS/SliceProfiles/"),
            expected=(200, 201),
            json={
                "attributeListIn": {
                    "nsstid": template_id,
                    "using_existed": False,
                }
            },
            timeout=max(self.timeout, 180),
        ).json()

    def delete_nssi(self, nssi_id: str) -> requests.Response:
        return self.request(
            "DELETE",
            self.free5g(f"ObjectManagement/NSS/SliceProfiles/{nssi_id}/"),
            expected=(200, 202, 204, 404),
            timeout=max(self.timeout, 180),
        )

    def delete_slice_template(self, template_id: str) -> None:
        self.request(
            "DELETE",
            self.free5g(f"ObjectManagement/SliceTemplate/{template_id}/"),
            expected=(200, 202, 204, 404),
        )

    def delete_generic(self, template_id: str) -> None:
        self.request(
            "DELETE",
            self.free5g(f"ObjectManagement/GenericTemplate/{template_id}/"),
            expected=(200, 202, 204, 404),
        )

    def download_generic(self, template_id: str, output: Path) -> Path:
        detail = self.generic_template(template_id)
        file_url = detail.get("templateFile")
        if not file_url:
            raise RuntimeError(f"GenericTemplate {template_id} has no templateFile")
        parsed = urlparse(file_url)
        broken_prefix = (
            f"/ObjectManagement/GenericTemplate/{template_id}/download/"
        )
        if broken_prefix in parsed.path:
            parsed = parsed._replace(
                path=parsed.path.replace(
                    broken_prefix,
                    "/ObjectManagement/GenericTemplate/download/",
                    1,
                )
            )
            file_url = parsed.geturl()
        if parsed.hostname in {"127.0.0.1", "localhost"}:
            base = urlparse(self.base_url)
            parsed = parsed._replace(
                scheme=base.scheme,
                netloc=base.netloc,
            )
            file_url = parsed.geturl()
        response = self.request("GET", file_url, expected=(200,))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(response.content)
        return output

    def get_moi(self, nssi_id: str) -> dict[str, Any] | None:
        response = self.session.get(
            self.free5g(f"ObjectManagement/NetworkSliceSubnet/{nssi_id}/"),
            params={"scope": '["BASE_NTH_LEVEL", 1]'},
            timeout=self.timeout,
        )
        if response.status_code == 404:
            return None
        if response.status_code not in (200, 201):
            raise ApiError("GET", response.url, response.status_code, response.text)
        return response.json()

    def nfvo_instance(self, instance_id: str) -> dict[str, Any] | None:
        response = self.session.get(
            self.nfvo(f"nslcm/v1/ns_instances/{instance_id}/"),
            timeout=self.timeout,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ApiError("GET", response.url, response.status_code, response.text)
        return response.json()

    def terminate_nfvo_instance(self, instance_id: str) -> None:
        self.request(
            "POST",
            self.nfvo(f"nslcm/v1/ns_instances/{instance_id}/terminate/"),
            expected=(200, 202, 204, 404, 409),
        )

    def delete_nfvo_instance(self, instance_id: str) -> None:
        self.request(
            "DELETE",
            self.nfvo(f"nslcm/v1/ns_instances/{instance_id}/"),
            expected=(200, 202, 204, 404),
        )


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.data: dict[str, Any] = {}

    @contextlib.contextmanager
    def locked(self, timeout: int = 30) -> Iterator["StateStore"]:
        deadline = time.monotonic() + timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                descriptor = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(descriptor, f"{os.getpid()} {now()}".encode())
                os.close(descriptor)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"State lock is busy: {self.lock_path}")
                if time.time() - self.lock_path.stat().st_mtime > 600:
                    self.lock_path.unlink(missing_ok=True)
                    continue
                time.sleep(0.2)
        try:
            self.data = self.load()
            yield self
        finally:
            self.lock_path.unlink(missing_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "slices": {}, "intents": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(dump(self.data), encoding="utf-8")
        os.replace(temporary, self.path)


@dataclass
class Plan:
    intent_id: str
    slice_type: str
    slot: int
    namespace: str
    source_vnf: str
    source_nsd: str
    nrm: str
    sst: int
    sd: str
    ipv4_subnet: str
    ipv4_gateway: str
    ipv6_subnet: str | None
    ipv6_gateway: str | None
    deployment_configuration: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def classify_intent(intent: dict[str, Any]) -> tuple[str, list[str]]:
    explicit = str(intent.get("slice_type", "")).lower().replace("-", "")
    if explicit in {"embb", "urllc", "mmtc"}:
        return explicit, ["explicit slice_type"]

    reasons: list[str] = []
    latency = intent.get("latency_ms", intent.get("max_latency_ms"))
    reliability = intent.get("reliability_percent")
    bandwidth = intent.get("bandwidth_mbps", intent.get("min_bandwidth_mbps"))
    if latency is not None and float(latency) <= 10:
        reasons.append(f"latency_ms={latency} <= 10")
    if reliability is not None and float(reliability) >= 99.999:
        reasons.append(f"reliability_percent={reliability} >= 99.999")
    text = json.dumps(intent, ensure_ascii=False).lower()
    if any(
        keyword in text
        for keyword in (
            "mmtc",
            "massive machine",
            "massive iot",
            "sensor",
            "smart meter",
        )
    ):
        return "mmtc", ["mMTC keyword"]
    if any(
        keyword in text
        for keyword in (
            "low latency",
            "ultra reliable",
            "robot",
            "industrial control",
            "độ trễ thấp",
        )
    ):
        reasons.append("URLLC keyword")
    if reasons:
        return "urllc", reasons
    if bandwidth is not None and float(bandwidth) >= 50:
        return "embb", [f"bandwidth_mbps={bandwidth} >= 50"]
    if any(
        keyword in text
        for keyword in ("video", "streaming", "broadband", "high bandwidth", "4k")
    ):
        return "embb", ["eMBB keyword"]
    raise ValueError(
        "Cannot classify intent. Add slice_type=embb|urllc|mmtc or measurable SLA fields."
    )


def _finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return number


def _exact_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be an integer")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"{field} must be a finite integer")
    return int(number)


def normalize_runtime_qos(
    value: dict[str, Any], slice_type: str, field: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    five_qi = _exact_integer(value.get("five_qi"), f"{field}.five_qi")
    arp_priority = _exact_integer(
        value.get("arp_priority"), f"{field}.arp_priority"
    )
    if five_qi not in SUPPORTED_5QI_BY_SLICE[slice_type]:
        raise ValueError(f"{field}.five_qi {five_qi} is not supported by {slice_type}")
    if not 1 <= arp_priority <= 15:
        raise ValueError(f"{field}.arp_priority must be in 1..15")
    resource_type = str(value.get("resource_type", "")).upper()
    if resource_type not in {"GBR", "NON_GBR"}:
        raise ValueError(f"{field}.resource_type must be GBR or NON_GBR")
    normalized = copy.deepcopy(value)
    normalized.update(
        five_qi=five_qi,
        arp_priority=arp_priority,
        resource_type=resource_type,
    )
    if resource_type == "GBR":
        gfbr = _finite_nonnegative(value.get("gfbr_mbps"), f"{field}.gfbr_mbps")
        mfbr = _finite_nonnegative(value.get("mfbr_mbps"), f"{field}.mfbr_mbps")
        if gfbr <= 0 or mfbr < gfbr:
            raise ValueError(f"{field} requires positive GFBR and MFBR >= GFBR")
        normalized.update(gfbr_mbps=gfbr, mfbr_mbps=mfbr)
    return normalized


def deployment_configuration(
    intent: dict[str, Any], slice_type: str
) -> dict[str, Any]:
    technical = intent.get("technical_sla")
    policy = intent.get("deployment_policy")
    if technical is None and policy is None:
        return {
            "template_family": slice_type,
            "evidence_status": "DECLARED",
        }
    if not isinstance(technical, dict) or not isinstance(policy, dict):
        raise ValueError("technical_sla and deployment_policy must be supplied together")
    if policy.get("template_family") != slice_type:
        raise ValueError("deployment_policy.template_family does not match slice_type")
    numeric_technical = (
        "min_throughput_mbps",
        "target_throughput_mbps",
        "peak_throughput_mbps",
        "max_latency_ms",
        "max_jitter_ms",
        "max_packet_loss_percent",
        "min_reliability_percent",
    )
    configuration = {
        name: _finite_nonnegative(technical.get(name), f"technical_sla.{name}")
        for name in numeric_technical
    }
    expected_sessions = _finite_nonnegative(
        technical.get("expected_sessions"), "technical_sla.expected_sessions"
    )
    if not expected_sessions.is_integer():
        raise ValueError("technical_sla.expected_sessions must be an integer")
    default_qos = normalize_runtime_qos(
        policy.get("default_qos", DEFAULT_QOS_BY_SLICE[slice_type]),
        slice_type,
        "deployment_policy.default_qos",
    )
    legacy_service_qos = {
        "five_qi": policy.get("five_qi"),
        "arp_priority": policy.get("arp_priority"),
        "resource_type": policy.get("resource_type", "NON_GBR"),
        "gfbr_mbps": policy.get("gfbr_mbps"),
        "mfbr_mbps": policy.get("mfbr_mbps"),
    }
    service_qos = normalize_runtime_qos(
        policy.get("service_qos", legacy_service_qos),
        slice_type,
        "deployment_policy.service_qos",
    )
    session_ambr = _finite_nonnegative(
        policy.get("session_ambr_mbps"),
        "deployment_policy.session_ambr_mbps",
    )
    if session_ambr <= 0:
        raise ValueError("deployment_policy.session_ambr_mbps must be positive")
    configuration.update(
        {
            "template_family": slice_type,
            "dnn": str(policy.get("dnn", "")).strip(),
            "five_qi": service_qos["five_qi"],
            "arp_priority": service_qos["arp_priority"],
            "session_ambr_mbps": session_ambr,
            "downlink_limit_mbps": _finite_nonnegative(
                policy.get("downlink_limit_mbps"),
                "deployment_policy.downlink_limit_mbps",
            ),
            "expected_sessions": int(expected_sessions),
            "evidence_status": str(policy.get("evidence_status", "DECLARED")),
            "default_qos": default_qos,
            "service_qos": service_qos,
            "qos_evidence": copy.deepcopy(
                policy.get(
                    "qos_evidence",
                    {
                        "core_policy_status": "DECLARED_NOT_ENFORCED",
                        "radio_status": "NOT_VERIFIED",
                    },
                )
            ),
        }
    )
    if configuration["evidence_status"] not in {
        "DECLARED",
        "ENFORCED",
        "BENCHMARKED",
    }:
        raise ValueError("deployment_policy.evidence_status is invalid")
    if not configuration["dnn"]:
        raise ValueError("deployment_policy.dnn is required")
    return configuration


def nrm_overrides_from_configuration(configuration: dict[str, Any]) -> dict[str, str]:
    required = {
        "target_throughput_mbps",
        "peak_throughput_mbps",
        "max_latency_ms",
        "max_jitter_ms",
        "max_packet_loss_percent",
        "min_reliability_percent",
    }
    if not required.issubset(configuration):
        return {}

    def number(name: str) -> str:
        return format(float(configuration[name]), "g")

    return {
        "expDataRateDL": f"{number('peak_throughput_mbps')} Mbps",
        "expDataRateUL": f"{number('peak_throughput_mbps')} Mbps",
        "areaTrafficCapDL": f"{number('target_throughput_mbps')} Mbps",
        "areaTrafficCapUL": f"{number('target_throughput_mbps')} Mbps",
        "latency": f"{number('max_latency_ms')} ms",
        "jitter": f"{number('max_jitter_ms')} ms",
        "packetLoss": f"{number('max_packet_loss_percent')}%",
        "reliability": f"{number('min_reliability_percent')}%",
    }


class Orchestrator:
    def __init__(
        self,
        config: dict[str, Any],
        config_path: Path,
        core_registry: CoreSliceRegistry | None = None,
    ):
        self.config = config
        self.config_path = config_path
        self.api = Api(config)
        self.state = StateStore((ROOT / config["state_file"]).resolve())
        self.output_root = (ROOT / config["output_dir"]).resolve()
        self.prefix = config.get("namespace_prefix", "slice-")
        self.max_slots = int(config.get("max_slots", 10))
        self.core_registry = core_registry or CoreSliceRegistry(config)

    def register_core_slice(
        self,
        store: StateStore,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """Register an allocated slice in AMF/NSSF without deleting it on failure."""

        record["status"] = "REGISTERING_NSSF"
        record.setdefault("events", []).append(
            {"at": now(), "event": "core_registration_started"}
        )
        store.save()
        try:
            pairs = desired_snssai(
                store.data,
                self.config.get("shared_amf_baseline_snssai", []),
                {(record["sst"], record["sd"])},
            )
            evidence = self.core_registry.reconcile(pairs)
            record["status"] = "VERIFYING_NSSF"
            record["core_registration"] = evidence
            store.save()
            record["events"].append(
                {
                    "at": now(),
                    "event": "nssf_registration_verified",
                    "snssai": evidence["snssai"],
                }
            )
            record["status"] = "READY_FOR_UE"
            record.pop("core_registration_error", None)
            store.save()
        except Exception as error:
            record["status"] = "REGISTRATION_FAILED"
            record["core_registration_error"] = str(error)
            backup_dir = getattr(self.core_registry, "last_backup_dir", None)
            if backup_dir:
                record["core_registration_backup_dir"] = str(backup_dir)
            record["events"].append(
                {
                    "at": now(),
                    "event": "nssf_registration_failed",
                    "reason": str(error),
                }
            )
            store.save()
        return record

    def core_pairs(self, state: dict[str, Any] | None = None) -> list[tuple[int, str]]:
        return desired_snssai(
            state if state is not None else self.state.load(),
            self.config.get("shared_amf_baseline_snssai", []),
        )

    def core_status(self) -> dict[str, Any]:
        """Read AMF/NSSF/NRF state without changing cluster resources."""

        pairs = self.core_pairs()
        try:
            evidence = self.core_registry.verify(pairs)
        except Exception as error:
            return {
                "verified": False,
                "snssai": [
                    {"sst": sst, "sd": sd} for sst, sd in pairs
                ],
                "error": str(error),
            }
        return evidence

    def core_reconcile(self) -> dict[str, Any]:
        """Apply the desired S-NSSAI set and return complete verification evidence."""

        return self.core_registry.reconcile(self.core_pairs())

    def all_namespace_slots(self) -> dict[int, str]:
        result = run(
            ["kubectl", "get", "namespace", "-o", "json"],
            check=False,
        )
        if result.returncode:
            return {}
        namespaces = json.loads(result.stdout).get("items", [])
        occupied: dict[int, str] = {}
        pattern = re.compile(rf"^{re.escape(self.prefix)}(\d+)$")
        for item in namespaces:
            name = item.get("metadata", {}).get("name", "")
            match = pattern.fullmatch(name)
            if match:
                occupied[int(match.group(1))] = name
        return occupied

    def namespace_has_workloads(self, namespace: str) -> bool:
        result = run(
            [
                "kubectl",
                "get",
                "pods,deployments,statefulsets,daemonsets,jobs",
                "-n",
                namespace,
                "-o",
                "json",
            ],
            check=False,
        )
        if result.returncode:
            return False
        return bool(json.loads(result.stdout).get("items", []))

    def namespace_slots(self) -> dict[int, str]:
        return {
            slot: namespace
            for slot, namespace in self.all_namespace_slots().items()
            if self.namespace_has_workloads(namespace)
        }

    def orphan_namespaces(self) -> list[str]:
        active = set(self.namespace_slots().values())
        return sorted(
            set(self.all_namespace_slots().values()) - active,
            key=lambda name: int(name.removeprefix(self.prefix)),
        )

    def occupied_slots(self, state: dict[str, Any]) -> dict[int, str]:
        occupied = self.namespace_slots()
        for namespace, record in state.get("slices", {}).items():
            if record.get("status") not in {"DELETED", "ROLLED_BACK"}:
                slot = record.get("slot")
                if slot:
                    occupied[int(slot)] = namespace
        return occupied

    def choose_slot(self, state: dict[str, Any]) -> int:
        occupied = self.occupied_slots(state)
        for slot in range(1, self.max_slots + 1):
            if slot not in occupied:
                return slot
        raise RuntimeError(f"No free slot in 1..{self.max_slots}: {occupied}")

    def choose_requested_slot(
        self,
        intent: dict[str, Any],
        state: dict[str, Any],
    ) -> int:
        """Use an explicit slot when a caller has a stable data-plane mapping.

        Interactive Auto Slice requests keep the original first-free behavior.
        The session orchestrator supplies a slot because its UE PDU ID, TUN,
        gateway and subscriber S-NSSAI are intentionally pre-provisioned as one
        consistent lab profile.
        """
        requested = intent.get("slot")
        if requested is None:
            return self.choose_slot(state)
        try:
            slot = int(requested)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"slot must be an integer: {requested}") from exc
        if slot < 1 or slot > self.max_slots:
            raise ValueError(f"slot must be in 1..{self.max_slots}: {slot}")
        occupied = self.occupied_slots(state)
        if slot in occupied:
            raise RuntimeError(
                f"Requested slot {slot} is already occupied by {occupied[slot]}"
            )
        return slot

    def make_plan(
        self,
        intent: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[Plan, list[str]]:
        slice_type, reasons = classify_intent(intent)
        profile = self.config["profiles"][slice_type]
        slot = self.choose_requested_slot(intent, state)
        namespace = f"{self.prefix}{slot}"
        intent_id = str(intent.get("intent_id") or f"intent-{uuid.uuid4()}")
        ipv4_subnet = profile.get("ipv4_subnet", "10.45.{slot}.0/24").format(
            slot=slot
        )
        ipv4_gateway = profile.get("ipv4_gateway", "10.45.{slot}.1").format(
            slot=slot
        )
        ipv6_subnet = profile.get("ipv6_subnet")
        ipv6_gateway = profile.get("ipv6_gateway")
        if ipv6_subnet:
            ipv6_subnet = ipv6_subnet.format(slot=slot)
        if ipv6_gateway:
            ipv6_gateway = ipv6_gateway.format(slot=slot)
        sst = int(intent.get("sst", profile["sst"]))
        configured_sd = intent.get("sd")
        if configured_sd is None:
            configured_sd = f"{self.first_free_sd(sst, state):06X}"
        sd = str(configured_sd).upper()
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", sd):
            raise ValueError(f"SD must be exactly 6 hexadecimal digits: {sd}")
        return (
            Plan(
                intent_id=intent_id,
                slice_type=slice_type,
                slot=slot,
                namespace=namespace,
                source_vnf=profile["source_vnf_template_id"],
                source_nsd=profile["source_nsd_template_id"],
                nrm=profile["nrm_template_id"],
                sst=sst,
                sd=sd.upper(),
                ipv4_subnet=ipv4_subnet,
                ipv4_gateway=ipv4_gateway,
                ipv6_subnet=ipv6_subnet,
                ipv6_gateway=ipv6_gateway,
                deployment_configuration=deployment_configuration(
                    intent, slice_type
                ),
            ),
            reasons,
        )

    def active_snssai_pairs(self) -> set[tuple[int, str]]:
        pairs: set[tuple[int, str]] = set()
        for template in self.api.slice_templates():
            if not template.get("instanceId"):
                continue
            detail = self.api.slice_template(template["templateId"])
            generic_templates = detail.get("genericTemplates") or {}
            if isinstance(generic_templates, dict):
                nrm_ids = generic_templates.get("NRM") or []
            else:
                nrm_ids = [
                    item["templateId"]
                    for item in generic_templates
                    if item.get("templateType") == "NRM"
                ]
            for nrm_id in nrm_ids:
                nrm = self.api.generic_template(nrm_id)
                contents = nrm.get("content") or []
                if not contents:
                    continue
                topology = contents[0].get("topology_template")
                if isinstance(topology, str):
                    with contextlib.suppress(ValueError, SyntaxError):
                        topology = ast.literal_eval(topology)
                if not isinstance(topology, dict):
                    continue
                snssai = topology.get("definitions", {}).get(
                    "SnssaiList", {}
                )
                try:
                    pairs.add(
                        (
                            int(snssai["sst"]["value"]),
                            str(snssai["sd"]).upper().zfill(6),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        return pairs

    def first_free_sd(self, sst: int, state: dict[str, Any]) -> int:
        used = {
            int(sd, 16)
            for pair_sst, sd in self.active_snssai_pairs()
            if pair_sst == sst
        }
        for record in state.get("slices", {}).values():
            if record.get("status") in {"DELETED", "ROLLED_BACK"}:
                continue
            if int(record.get("sst", -1)) == sst and record.get("sd"):
                used.add(int(str(record["sd"]), 16))
        for candidate in range(1, 0x1000000):
            if candidate not in used:
                return candidate
        raise RuntimeError(f"No free SD remains for SST {sst:02d}")

    def validate_sources(self, plan: Plan) -> None:
        local = self.local_base_template_paths()
        if local:
            self._validate_base_template_files(
                plan.slice_type,
                vnf_zip=local["VNF"],
                nsd_zip=local["NSD"],
                nrm_zip=local["NRM"],
            )
            return
        expected = {
            plan.source_vnf: "VNF",
            plan.source_nsd: "NSD",
            plan.nrm: "NRM",
        }
        for template_id, template_type in expected.items():
            detail = self.api.generic_template(template_id)
            if detail.get("templateType") != template_type:
                raise RuntimeError(
                    f"{template_id}: expected {template_type}, "
                    f"got {detail.get('templateType')}"
                )
            if detail.get("operationStatus") != "UPLOAD":
                raise RuntimeError(
                    f"{template_id}: operationStatus={detail.get('operationStatus')}"
                )

    def local_base_template_paths(self) -> dict[str, Path] | None:
        configured = self.config.get("local_base_templates")
        if not configured:
            return None
        keys = {"VNF": "vnf_zip", "NSD": "nsd_zip", "NRM": "nrm_zip"}
        paths: dict[str, Path] = {}
        for kind, key in keys.items():
            value = configured.get(key)
            if not value:
                raise ValueError(f"local_base_templates.{key} is required")
            path = Path(value)
            if not path.is_absolute():
                path = ROOT / path
            path = path.resolve()
            if not path.is_file():
                raise ValueError(f"Local {kind} base template is missing: {path}")
            paths[kind] = path
        return paths

    def materialize_source_archives(
        self, plan: Plan, destination: Path
    ) -> tuple[Path, Path, Path]:
        local = self.local_base_template_paths()
        if not local:
            return (
                self.api.download_generic(
                    plan.source_vnf, destination / "VNF-source.zip"
                ),
                self.api.download_generic(
                    plan.source_nsd, destination / "NSD-source.zip"
                ),
                self.api.download_generic(
                    plan.nrm, destination / "NRM-source.zip"
                ),
            )
        destination.mkdir(parents=True, exist_ok=True)
        outputs = {
            kind: destination / f"{kind}-source.zip"
            for kind in ("VNF", "NSD", "NRM")
        }
        for kind, source in local.items():
            shutil.copyfile(source, outputs[kind])
        return outputs["VNF"], outputs["NSD"], outputs["NRM"]

    def _validate_base_template_files(
        self,
        slice_type: str,
        *,
        vnf_zip: Path,
        nsd_zip: Path,
        nrm_zip: Path,
    ) -> dict[str, Any]:
        if slice_type not in self.config["profiles"]:
            raise ValueError(f"Unknown profile: {slice_type}")
        profile = self.config["profiles"][slice_type]
        source_match = validate_source_packages(
            nsd_zip=nsd_zip,
            vnf_zip=vnf_zip,
        )
        with tempfile.TemporaryDirectory(prefix="auto-slice-base-check-") as temp:
            root = Path(temp)
            manifest = render_packages(
                nsd_zip=nsd_zip,
                vnf_zip=vnf_zip,
                namespace="slice-999",
                slot=999,
                slice_type=slice_type,
                sst=int(profile["sst"]),
                sd="000001",
                ipv4_subnet="10.45.99.0/24",
                ipv4_gateway="10.45.99.1",
                ipv6_subnet="2001:db8:99::/48",
                ipv6_gateway="2001:db8:99::1",
                output_root=root / "rendered",
            )
            nrm_profile = profile.get("nrm", {})
            nrm_manifest = render_nrm_package(
                nrm_zip=nrm_zip,
                run_dir=Path(manifest["outputs"]["run_dir"]),
                namespace="slice-999",
                sst=int(profile["sst"]),
                sd="000001",
                service_type=nrm_profile.get("service_type", slice_type),
                characteristics=nrm_profile.get(
                    "characteristics", f"Base {slice_type} template"
                ),
                performance_overrides=nrm_profile.get("performance_overrides"),
            )
        return {
            "status": "VALID",
            "profile": slice_type,
            "source_match": source_match,
            "components": sorted(
                item.get("component") or "unknown"
                for item in manifest["descriptors"]["vnf"]
            ),
            "nrm_sha256": nrm_manifest["sha256"],
        }

    def _write_profile_sources(
        self,
        slice_type: str,
        *,
        vnf_template_id: str,
        nsd_template_id: str,
        nrm_template_id: str,
    ) -> None:
        lock_path = self.config_path.with_suffix(self.config_path.suffix + ".lock")
        deadline = time.monotonic() + 30
        while True:
            try:
                descriptor = os.open(
                    lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                os.write(descriptor, f"{os.getpid()} {now()}".encode())
                os.close(descriptor)
                break
            except FileExistsError:
                if lock_path.exists() and time.time() - lock_path.stat().st_mtime > 600:
                    lock_path.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Config lock is busy: {lock_path}")
                time.sleep(0.2)
        try:
            current = load_json(self.config_path)
            if slice_type not in current.get("profiles", {}):
                raise ValueError(f"Unknown profile: {slice_type}")
            profile = current["profiles"][slice_type]
            profile["source_vnf_template_id"] = vnf_template_id
            profile["source_nsd_template_id"] = nsd_template_id
            profile["nrm_template_id"] = nrm_template_id
            temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
            temporary.write_text(dump(current) + "\n", encoding="utf-8")
            os.replace(temporary, self.config_path)
            self.config = current
        finally:
            lock_path.unlink(missing_ok=True)

    def validate_base_template_ids(
        self,
        slice_type: str,
        *,
        vnf_template_id: str,
        nsd_template_id: str,
        nrm_template_id: str,
    ) -> dict[str, Any]:
        selected = {
            "VNF": vnf_template_id,
            "NSD": nsd_template_id,
            "NRM": nrm_template_id,
        }
        with tempfile.TemporaryDirectory(prefix="auto-slice-source-") as temp:
            root = Path(temp)
            paths: dict[str, Path] = {}
            details: dict[str, dict[str, Any]] = {}
            for kind, template_id in selected.items():
                detail = self.api.generic_template(template_id)
                if detail.get("templateType") != kind:
                    raise ValueError(
                        f"{template_id}: expected {kind}, got {detail.get('templateType')}"
                    )
                if detail.get("operationStatus") != "UPLOAD":
                    raise ValueError(
                        f"{template_id}: operationStatus={detail.get('operationStatus')}"
                    )
                paths[kind] = self.api.download_generic(
                    template_id, root / f"{kind}.zip"
                )
                details[kind] = detail
            validation = self._validate_base_template_files(
                slice_type,
                vnf_zip=paths["VNF"],
                nsd_zip=paths["NSD"],
                nrm_zip=paths["NRM"],
            )
        validation["templates"] = {
            kind.lower(): {
                "template_id": selected[kind],
                "name": details[kind].get("name"),
            }
            for kind in ("VNF", "NSD", "NRM")
        }
        return validation

    def set_base_templates(
        self,
        slice_type: str,
        *,
        vnf_template_id: str,
        nsd_template_id: str,
        nrm_template_id: str,
    ) -> dict[str, Any]:
        validation = self.validate_base_template_ids(
            slice_type,
            vnf_template_id=vnf_template_id,
            nsd_template_id=nsd_template_id,
            nrm_template_id=nrm_template_id,
        )
        self._write_profile_sources(
            slice_type,
            vnf_template_id=vnf_template_id,
            nsd_template_id=nsd_template_id,
            nrm_template_id=nrm_template_id,
        )
        validation["activated"] = True
        return validation

    def template_catalog(self) -> dict[str, Any]:
        templates = self.api.generic_templates()
        catalog: dict[str, list[dict[str, Any]]] = {
            "VNF": [], "NSD": [], "NRM": []
        }
        for item in templates:
            kind = item.get("templateType")
            if kind not in catalog or item.get("operationStatus") != "UPLOAD":
                continue
            catalog[kind].append({
                "template_id": item.get("templateId"),
                "name": item.get("name") or "Unnamed",
                "description": item.get("description") or "",
            })
        for values in catalog.values():
            values.sort(key=lambda item: (item["name"].lower(), item["template_id"]))
        available_ids = {
            item["template_id"] for values in catalog.values() for item in values
        }
        profiles = {}
        storage = ROOT / self.config.get("base_template_dir", "base_templates")
        for name, profile in self.config["profiles"].items():
            selected = {
                "vnf_template_id": profile.get("source_vnf_template_id"),
                "nsd_template_id": profile.get("source_nsd_template_id"),
                "nrm_template_id": profile.get("nrm_template_id"),
            }
            profiles[name] = {
                **selected,
                "stored_bundle": all(
                    (storage / name / f"{kind}-source.zip").is_file()
                    for kind in ("VNF", "NSD", "NRM")
                ),
                "status": (
                    "AVAILABLE"
                    if all(value in available_ids for value in selected.values())
                    else "MISSING"
                ),
            }
        return {"templates": catalog, "profiles": profiles}

    def restore_base_templates(self, slice_type: str) -> dict[str, Any]:
        storage = ROOT / self.config.get("base_template_dir", "base_templates")
        profile_dir = storage / slice_type
        paths = {
            kind: profile_dir / f"{kind}-source.zip"
            for kind in ("VNF", "NSD", "NRM")
        }
        missing = [kind for kind, path in paths.items() if not path.is_file()]
        if missing:
            raise ValueError(
                f"No complete stored bundle for {slice_type}: missing {missing}"
            )
        return self.install_base_templates(
            slice_type,
            vnf_zip=paths["VNF"],
            nsd_zip=paths["NSD"],
            nrm_zip=paths["NRM"],
        )

    def install_base_templates(
        self,
        slice_type: str,
        *,
        vnf_zip: Path,
        nsd_zip: Path,
        nrm_zip: Path,
    ) -> dict[str, Any]:
        validation = self._validate_base_template_files(
            slice_type,
            vnf_zip=vnf_zip,
            nsd_zip=nsd_zip,
            nrm_zip=nrm_zip,
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        storage = ROOT / self.config.get("base_template_dir", "base_templates")
        profile_dir = storage / slice_type
        profile_dir.mkdir(parents=True, exist_ok=True)
        stored = {
            "VNF": profile_dir / "VNF-source.zip",
            "NSD": profile_dir / "NSD-source.zip",
            "NRM": profile_dir / "NRM-source.zip",
        }
        for kind, source in (("VNF", vnf_zip), ("NSD", nsd_zip), ("NRM", nrm_zip)):
            temporary = stored[kind].with_suffix(".zip.tmp")
            shutil.copyfile(source, temporary)
            os.replace(temporary, stored[kind])

        nfvo_type = self.config.get("nfvo_type", "kube5gnfvo")
        created_ids: list[str] = []
        ids: dict[str, str] = {}
        try:
            for kind in ("VNF", "NSD", "NRM"):
                created = self.api.create_generic(
                    kind,
                    f"base-{slice_type}-{kind.lower()}-{stamp}",
                    f"AUTO_SLICE_BASE:{slice_type}:{stamp}:PROTECTED",
                    nfvo_type,
                )
                template_id = created["templateId"]
                created_ids.append(template_id)
                self.api.upload_generic(
                    template_id, kind, stored[kind], nfvo_type
                )
                ids[kind] = template_id
            self._write_profile_sources(
                slice_type,
                vnf_template_id=ids["VNF"],
                nsd_template_id=ids["NSD"],
                nrm_template_id=ids["NRM"],
            )
        except Exception:
            for template_id in reversed(created_ids):
                try:
                    self.api.delete_generic(template_id)
                except Exception:
                    pass
            raise
        validation.update({
            "activated": True,
            "stored_at": str(profile_dir),
            "templates": {
                kind.lower(): {"template_id": ids[kind]}
                for kind in ("VNF", "NSD", "NRM")
            },
        })
        return validation

    def create_namespace(self, namespace: str) -> bool:
        existed = (
            run(
                ["kubectl", "get", "namespace", namespace],
                check=False,
            ).returncode
            == 0
        )
        if existed:
            if self.namespace_has_workloads(namespace):
                raise RuntimeError(
                    f"Namespace has active workloads: {namespace}"
                )
            run(
                [
                    "kubectl",
                    "delete",
                    "namespace",
                    namespace,
                    "--wait=true",
                    "--timeout=90s",
                ]
            )
        run(["kubectl", "create", "namespace", namespace])
        source_ns = self.config.get("ovs_source_namespace", "default")
        network_name = self.config.get("network_attachment", "ovs-net")
        source = run(
            [
                "kubectl",
                "get",
                "network-attachment-definition",
                network_name,
                "-n",
                source_ns,
                "-o",
                "json",
            ]
        )
        document = json.loads(source.stdout)
        metadata = document["metadata"]
        for key in (
            "creationTimestamp",
            "resourceVersion",
            "uid",
            "generation",
            "managedFields",
        ):
            metadata.pop(key, None)
        metadata["namespace"] = namespace
        applied = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=json.dumps(document),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if applied.returncode:
            raise RuntimeError(applied.stderr.strip())
        return True

    def delete_namespace(self, namespace: str) -> None:
        run(
            [
                "kubectl",
                "delete",
                "namespace",
                namespace,
                "--ignore-not-found",
                "--wait=false",
            ],
            check=False,
        )

    def sync_monitoring(self, state: dict[str, Any]) -> dict[str, Any]:
        """Reconcile Prometheus/exporter discovery with active Auto Slice state."""
        if not self.config.get("monitoring_sync_enabled", False):
            return {"enabled": False, "changed": False, "slices": []}

        monitoring_namespace = self.config.get(
            "monitoring_namespace", "monitoring"
        )
        active: list[dict[str, Any]] = []
        for namespace, record in state.get("slices", {}).items():
            if record.get("status") in {
                "DELETED",
                "DELETING",
                "ROLLED_BACK",
                "ROLLING_BACK",
                "DELETE_FAILED",
            }:
                continue
            if not re.fullmatch(
                rf"{re.escape(self.prefix)}\d+", namespace
            ):
                continue
            active.append(
                {
                    "namespace": namespace,
                    "slice_type": str(record.get("slice_type") or "unknown"),
                    "sst": int(record["sst"]),
                    "sd": str(record["sd"]).upper().zfill(6),
                    "slot": int(
                        record.get("slot")
                        or namespace.removeprefix(self.prefix)
                    ),
                }
            )
        active.sort(key=lambda item: item["slot"])

        service_list = {
            "apiVersion": "v1",
            "kind": "List",
            "items": [
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {
                        "name": "smf-metrics",
                        "namespace": item["namespace"],
                        "labels": {
                            "app.kubernetes.io/name": "open5gs-smf-metrics",
                            "app.kubernetes.io/part-of": "auto-slice-monitoring",
                        },
                    },
                    "spec": {
                        "selector": {"app": "smf-svc"},
                        "ports": [
                            {
                                "name": "metrics",
                                "port": 9090,
                                "targetPort": 9090,
                                "protocol": "TCP",
                            }
                        ],
                    },
                }
                for item in active
            ],
        }
        if service_list["items"]:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                encoding="utf-8",
                delete=False,
            ) as handle:
                json.dump(service_list, handle)
                service_manifest = Path(handle.name)
            try:
                run(["kubectl", "apply", "-f", str(service_manifest)])
            finally:
                service_manifest.unlink(missing_ok=True)

        type_labels = {
            "embb": "eMBB",
            "urllc": "URLLC",
            "mmtc": "mMTC",
        }
        exporter_slices = [
            {
                "slice_id": item["namespace"],
                "slice_type": type_labels.get(
                    item["slice_type"].lower(), item["slice_type"]
                ),
                "sst": item["sst"],
                "sd": item["sd"],
                "amf": "amf-metrics.share.svc.cluster.local:9090",
                "smf": (
                    f"smf-metrics.{item['namespace']}"
                    ".svc.cluster.local:9090"
                ),
            }
            for item in active
        ]
        exporter_data = (
            json.dumps(exporter_slices, indent=2, ensure_ascii=False) + "\n"
        )

        prometheus_lines = [
            "global:",
            "  scrape_interval: 15s",
            "  evaluation_interval: 15s",
            "",
            "scrape_configs:",
            "  - job_name: open5gs-slice-exporter",
            "    static_configs:",
            "      - targets:",
            "          - slice-exporter:8080",
            "",
            "  - job_name: shared-amf",
            "    static_configs:",
            "      - targets:",
            "          - amf-metrics.share.svc.cluster.local:9090",
            "        labels:",
            "          nf_type: amf",
            "          scope: shared",
        ]
        for item in active:
            display_type = type_labels.get(
                item["slice_type"].lower(), item["slice_type"]
            )
            prometheus_lines.extend(
                [
                    "",
                    f"  - job_name: {item['namespace']}-smf",
                    "    static_configs:",
                    "      - targets:",
                    (
                        "          - smf-metrics."
                        f"{item['namespace']}.svc.cluster.local:9090"
                    ),
                    "        labels:",
                    f"          slice_id: {item['namespace']}",
                    f"          slice_type: {display_type}",
                    f'          sst: "{item["sst"]}"',
                    f'          sd: "{item["sd"]}"',
                    "          nf_type: smf",
                ]
            )
        for job in self.config.get("monitoring_extra_scrape_jobs", []):
            job_name = str(job["job_name"])
            targets = [str(target) for target in job.get("targets", [])]
            if not targets:
                raise ValueError(
                    f"monitoring extra scrape job has no targets: {job_name}"
                )
            prometheus_lines.extend(["", f"  - job_name: {job_name}"])
            if job.get("scrape_interval"):
                prometheus_lines.append(
                    f"    scrape_interval: {job['scrape_interval']}"
                )
            prometheus_lines.extend(
                ["    static_configs:", "      - targets:"]
            )
            prometheus_lines.extend(
                f"          - {target}" for target in targets
            )
        prometheus_data = "\n".join(prometheus_lines) + "\n"

        changed_deployments: list[str] = []
        for name, key, value, deployment in (
            (
                "slice-exporter-config",
                "slices.json",
                exporter_data,
                "slice-exporter",
            ),
            (
                "prometheus-config",
                "prometheus.yml",
                prometheus_data,
                "prometheus",
            ),
        ):
            current = run(
                [
                    "kubectl",
                    "get",
                    "configmap",
                    name,
                    "-n",
                    monitoring_namespace,
                    "-o",
                    "json",
                ],
                check=False,
            )
            current_value = None
            if current.returncode == 0:
                current_value = (
                    json.loads(current.stdout).get("data", {}).get(key)
                )
            if current_value == value:
                continue
            if current.returncode == 0:
                run(
                    [
                        "kubectl",
                        "patch",
                        "configmap",
                        name,
                        "-n",
                        monitoring_namespace,
                        "--type=merge",
                        "-p",
                        json.dumps({"data": {key: value}}),
                    ]
                )
            else:
                document = {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": name,
                        "namespace": monitoring_namespace,
                    },
                    "data": {key: value},
                }
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".json",
                    encoding="utf-8",
                    delete=False,
                ) as handle:
                    json.dump(document, handle)
                    config_manifest = Path(handle.name)
                try:
                    run(["kubectl", "apply", "-f", str(config_manifest)])
                finally:
                    config_manifest.unlink(missing_ok=True)
            changed_deployments.append(deployment)

        for deployment in changed_deployments:
            run(
                [
                    "kubectl",
                    "rollout",
                    "restart",
                    f"deployment/{deployment}",
                    "-n",
                    monitoring_namespace,
                ]
            )
        return {
            "enabled": True,
            "changed": bool(changed_deployments),
            "restarted": changed_deployments,
            "slices": [item["namespace"] for item in active],
        }

    def record_monitoring_sync(
        self,
        store: StateStore,
        record: dict[str, Any],
    ) -> None:
        try:
            result = self.sync_monitoring(store.data)
            record["monitoring_sync"] = result
            record.setdefault("events", []).append(
                {
                    "at": now(),
                    "event": "monitoring_synced",
                    "slices": result.get("slices", []),
                    "changed": result.get("changed", False),
                }
            )
        except Exception as error:
            record["monitoring_sync_error"] = str(error)
            record.setdefault("events", []).append(
                {
                    "at": now(),
                    "event": "monitoring_sync_failed",
                    "reason": str(error),
                }
            )
        store.save()

    def template_instances(self, template_id: str) -> set[str]:
        detail = self.api.slice_template(template_id)
        return {
            normalize_uuid(value) or value
            for value in (detail.get("instanceId") or [])
        }

    def detect_nssi(
        self,
        template_id: str,
        before: set[str],
        response: dict[str, Any],
    ) -> str:
        def search(value: Any) -> str | None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() in {"nssiid", "nssi_id"}:
                        return normalize_uuid(str(item))
                    found = search(item)
                    if found:
                        return found
            if isinstance(value, list):
                for item in value:
                    found = search(item)
                    if found:
                        return found
            return None

        from_response = search(response)
        deadline = time.monotonic() + int(
            self.config.get("instance_detection_timeout_seconds", 60)
        )
        while time.monotonic() < deadline:
            after = self.template_instances(template_id)
            difference = after - before
            if len(difference) == 1:
                return next(iter(difference))
            if from_response and from_response in after:
                return from_response
            if len(difference) > 1:
                raise RuntimeError(
                    f"Concurrent allocations detected for {template_id}: {difference}"
                )
            time.sleep(3)
        if from_response:
            return from_response
        raise RuntimeError("Allocation returned no detectable NSSI ID")

    def wait_ready(self, namespace: str) -> list[str]:
        deadline = time.monotonic() + int(
            self.config.get("pod_ready_timeout_seconds", 240)
        )
        last = "No pods"
        while time.monotonic() < deadline:
            result = run(
                ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
                check=False,
            )
            if result.returncode == 0:
                items = json.loads(result.stdout).get("items", [])
                if items:
                    not_ready: list[str] = []
                    for item in items:
                        statuses = item.get("status", {}).get(
                            "containerStatuses", []
                        )
                        if not statuses or not all(
                            status.get("ready") for status in statuses
                        ):
                            not_ready.append(item["metadata"]["name"])
                    if not not_ready:
                        return [item["metadata"]["name"] for item in items]
                    last = f"Not ready: {not_ready}"
            time.sleep(5)
        raise RuntimeError(f"Pods did not become ready in {namespace}: {last}")

    def ensure_pfcp_recreate_strategy(self, namespace: str) -> list[str]:
        """Persist Recreate on the single-replica SMF/UPF deployments.

        RollingUpdate can briefly run an old and a new PFCP endpoint together.
        That is undesirable for this lab because each slice has exactly one
        stateful SMF and one stateful UPF.
        """
        result = run(
            [
                "kubectl",
                "get",
                "deployments",
                "-n",
                namespace,
                "-o",
                "json",
            ]
        )
        items = json.loads(result.stdout).get("items", [])
        targets: dict[str, str] = {}
        for item in items:
            name = item.get("metadata", {}).get("name", "")
            lowered = name.lower()
            for component in ("smf", "upf"):
                if component in lowered:
                    if component in targets:
                        raise RuntimeError(
                            f"{namespace}: multiple {component.upper()} "
                            f"deployments: {targets[component]}, {name}"
                        )
                    targets[component] = name

        missing = sorted({"smf", "upf"} - set(targets))
        if missing:
            raise RuntimeError(
                f"{namespace}: missing PFCP deployments: {', '.join(missing)}"
            )

        patched: list[str] = []
        for component in ("smf", "upf"):
            name = targets[component]
            item = next(
                deployment
                for deployment in items
                if deployment.get("metadata", {}).get("name") == name
            )
            strategy = item.get("spec", {}).get("strategy", {})
            if strategy.get("type") != "Recreate":
                run(
                    [
                        "kubectl",
                        "patch",
                        "deployment",
                        name,
                        "-n",
                        namespace,
                        "--type=merge",
                        "-p",
                        json.dumps(
                            {
                                "spec": {
                                    "strategy": {
                                        "type": "Recreate",
                                        "rollingUpdate": None,
                                    }
                                }
                            }
                        ),
                    ]
                )
                patched.append(name)

            verified = run(
                [
                    "kubectl",
                    "get",
                    "deployment",
                    name,
                    "-n",
                    namespace,
                    "-o",
                    "jsonpath={.spec.strategy.type}",
                ]
            ).stdout.strip()
            if verified != "Recreate":
                raise RuntimeError(
                    f"{namespace}/{name}: expected Recreate, got {verified}"
                )
        return patched

    def runtime_checks(self, namespace: str) -> list[str]:
        issues: list[str] = []
        pods_result = run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            check=False,
        )
        if pods_result.returncode:
            return [f"Cannot list pods in {namespace}: {pods_result.stderr.strip()}"]
        items = json.loads(pods_result.stdout).get("items", [])
        smf_pods = [
            item["metadata"]["name"]
            for item in items
            if "smf" in item["metadata"]["name"].lower()
        ]
        for pod in smf_pods:
            logs = run(
                ["kubectl", "logs", "-n", namespace, pod, "--tail=500"],
                check=False,
            ).stdout
            # A newly allocated slice can be healthy before any UE creates a
            # PDU session.  Also, SMF logs every PCF discovered through NRF;
            # those "NF EndPoint associated" lines do not mean that SMF
            # selected a foreign PCF for a session.  Session/PFCP state is
            # monitored separately and must not downgrade infrastructure
            # health while no UE is attached.
        return issues

    def create(self, intent: dict[str, Any], execute: bool) -> dict[str, Any]:
        with self.state.locked() as store:
            plan, reasons = self.make_plan(intent, store.data)
            self.validate_sources(plan)
            preview = {
                "mode": "execute" if execute else "plan",
                "classification_reasons": reasons,
                **plan.as_dict(),
            }
            if not execute:
                return preview
            if plan.intent_id in store.data["intents"]:
                raise RuntimeError(f"intent_id already exists: {plan.intent_id}")
            record: dict[str, Any] = {
                **plan.as_dict(),
                "status": "RESERVED",
                "created_at": now(),
                "events": [{"at": now(), "event": "slot_reserved"}],
                "owned_resources": {
                    "namespace": False,
                    "generic_templates": [],
                    "slice_template": None,
                    "nssi": None,
                },
            }
            store.data["slices"][plan.namespace] = record
            store.data["intents"][plan.intent_id] = plan.namespace
            store.save()

            manifest: dict[str, Any] | None = None
            allocation_complete = False
            try:
                record["status"] = "PREPARING"
                work = self.output_root / plan.intent_id
                source_dir = work / "source"
                source_vnf, source_nsd, source_nrm = (
                    self.materialize_source_archives(plan, source_dir)
                )
                manifest = render_packages(
                    nsd_zip=source_nsd,
                    vnf_zip=source_vnf,
                    namespace=plan.namespace,
                    slot=plan.slot,
                    slice_type=plan.slice_type,
                    sst=plan.sst,
                    sd=plan.sd,
                    ipv4_subnet=plan.ipv4_subnet,
                    ipv4_gateway=plan.ipv4_gateway,
                    ipv6_subnet=plan.ipv6_subnet,
                    ipv6_gateway=plan.ipv6_gateway,
                    output_root=work / "rendered",
                    default_qos=plan.deployment_configuration.get("default_qos"),
                    service_qos=plan.deployment_configuration.get("service_qos"),
                    session_ambr_mbps=plan.deployment_configuration.get(
                        "session_ambr_mbps"
                    ),
                )
                nrm_profile = self.config["profiles"][plan.slice_type].get(
                    "nrm", {}
                )
                performance_overrides = dict(
                    nrm_profile.get("performance_overrides") or {}
                )
                performance_overrides.update(
                    nrm_overrides_from_configuration(
                        plan.deployment_configuration
                    )
                )
                nrm_manifest = render_nrm_package(
                    nrm_zip=source_nrm,
                    run_dir=Path(manifest["outputs"]["run_dir"]),
                    namespace=plan.namespace,
                    sst=plan.sst,
                    sd=plan.sd,
                    service_type=nrm_profile.get(
                        "service_type", plan.slice_type
                    ),
                    characteristics=nrm_profile.get(
                        "characteristics",
                        f"Automatically rendered {plan.slice_type} slice.",
                    ),
                    performance_overrides=performance_overrides or None,
                )
                manifest["nrm"] = nrm_manifest
                manifest["outputs"]["nrm_zip"] = nrm_manifest["output"]
                manifest["sha256"]["nrm_zip"] = nrm_manifest["sha256"]
                Path(manifest["outputs"]["run_dir"]).joinpath(
                    "render-manifest.json"
                ).write_text(dump(manifest), encoding="utf-8")
                record["render_manifest"] = manifest
                self.create_namespace(plan.namespace)
                record["owned_resources"]["namespace"] = True
                store.save()

                nfvo_type = self.config.get("nfvo_type", "kube5gnfvo")
                created_generics: list[str] = []
                for kind, path_key in (
                    ("VNF", "vnf_zip"),
                    ("NSD", "nsd_zip"),
                    ("NRM", "nrm_zip"),
                ):
                    created = self.api.create_generic(
                        kind,
                        f"{plan.namespace}-{kind.lower()}",
                        f"{plan.slice_type}:{plan.namespace}:{plan.intent_id}",
                        nfvo_type,
                    )
                    template_id = created["templateId"]
                    created_generics.append(template_id)
                    record["owned_resources"]["generic_templates"] = created_generics
                    store.save()
                    self.api.upload_generic(
                        template_id,
                        kind,
                        Path(manifest["outputs"][path_key]),
                        nfvo_type,
                    )

                slice_template = self.api.create_slice_template(
                    generic_ids=created_generics,
                    nfvo_type=nfvo_type,
                    description=f"{plan.slice_type}:{plan.namespace}:{plan.intent_id}",
                )
                slice_template_id = slice_template["templateId"]
                record["owned_resources"]["slice_template"] = slice_template_id
                record["events"].append(
                    {"at": now(), "event": "slice_template_created"}
                )
                store.save()

                before = self.template_instances(slice_template_id)
                allocation = self.api.allocate(slice_template_id)
                nssi_id = self.detect_nssi(
                    slice_template_id, before, allocation
                )
                allocation_complete = True
                record["owned_resources"]["nssi"] = nssi_id
                record["nssi_id"] = nssi_id
                record["allocation_response"] = allocation
                with contextlib.suppress(Exception):
                    record["moi"] = self.api.get_moi(nssi_id)
                record["status"] = "VERIFYING"
                record["events"].append(
                    {"at": now(), "event": "nssi_allocated", "nssi_id": nssi_id}
                )
                store.save()

                record["pods"] = self.wait_ready(plan.namespace)
                recreate_patched = self.ensure_pfcp_recreate_strategy(
                    plan.namespace
                )
                record["deployment_strategy"] = {
                    "smf": "Recreate",
                    "upf": "Recreate",
                    "patched": recreate_patched,
                }
                record["events"].append(
                    {
                        "at": now(),
                        "event": "pfcp_recreate_strategy_verified",
                        "patched": recreate_patched,
                    }
                )
                store.save()
                issues = self.runtime_checks(plan.namespace)
                record["verification_issues"] = issues
                record["status"] = "DEGRADED" if issues else "VERIFYING"
                record["events"].append(
                    {
                        "at": now(),
                        "event": "verification_complete",
                        "issues": issues,
                    }
                )
                store.save()
                if not issues:
                    self.register_core_slice(store, record)
                record["completed_at"] = now()
                store.save()
                self.record_monitoring_sync(store, record)
                return record
            except Exception as error:
                record["error"] = str(error)
                record["failed_at"] = now()
                if allocation_complete:
                    record["status"] = "FAILED_AFTER_ALLOCATION"
                    record["events"].append(
                        {
                            "at": now(),
                            "event": "manual_review_required",
                            "reason": str(error),
                        }
                    )
                else:
                    record["status"] = "ROLLING_BACK"
                    record["events"].append(
                        {"at": now(), "event": "rollback_started"}
                    )
                    self.rollback_owned(record)
                    record["status"] = "ROLLED_BACK"
                    clean_failed_run(manifest)
                store.save()
                raise

    def rollback_owned(self, record: dict[str, Any]) -> None:
        owned = record["owned_resources"]
        slice_template = owned.get("slice_template")
        if slice_template:
            with contextlib.suppress(Exception):
                self.api.delete_slice_template(slice_template)
        for template_id in reversed(owned.get("generic_templates", [])):
            with contextlib.suppress(Exception):
                self.api.delete_generic(template_id)
        if owned.get("namespace"):
            self.delete_namespace(record["namespace"])

    def find_record(
        self, state: dict[str, Any], target: str
    ) -> tuple[str | None, dict[str, Any] | None]:
        if target in state.get("slices", {}):
            return target, state["slices"][target]
        namespace = state.get("intents", {}).get(target)
        if namespace:
            return namespace, state["slices"].get(namespace)
        wanted = normalize_uuid(target)
        for name, record in state.get("slices", {}).items():
            if normalize_uuid(record.get("nssi_id")) == wanted:
                return name, record
        return None, None

    def locate_template_for_nssi(self, nssi_id: str) -> str | None:
        wanted = normalize_uuid(nssi_id)
        for template in self.api.slice_templates():
            instances = {
                normalize_uuid(value)
                for value in template.get("instanceId") or []
            }
            if wanted in instances:
                return template["templateId"]
        return None

    def extract_ns_instance_id(self, moi: dict[str, Any] | None) -> str | None:
        if not moi:
            return None
        values = moi.get("attributeListOut")
        if isinstance(values, list) and values:
            ns_info = values[0].get("nsInfo") or {}
            return normalize_uuid(ns_info.get("id"))
        if isinstance(values, dict):
            ns_info = values.get("nsInfo") or {}
            return normalize_uuid(ns_info.get("id"))
        return None

    def nssi_still_attached(self, nssi_id: str) -> bool:
        return self.locate_template_for_nssi(nssi_id) is not None

    def wait_nfvo_not_instantiated(self, ns_instance_id: str) -> None:
        deadline = time.monotonic() + int(
            self.config.get("delete_timeout_seconds", 120)
        )
        while time.monotonic() < deadline:
            detail = self.api.nfvo_instance(ns_instance_id)
            if detail is None or detail.get("nsState") == "NOT_INSTANTIATED":
                return
            time.sleep(3)
        raise RuntimeError(
            f"NFVO instance {ns_instance_id} did not reach NOT_INSTANTIATED"
        )

    def direct_nfvo_cleanup(self, ns_instance_id: str) -> None:
        detail = self.api.nfvo_instance(ns_instance_id)
        if detail and detail.get("nsState") == "INSTANTIATED":
            self.api.terminate_nfvo_instance(ns_instance_id)
            self.wait_nfvo_not_instantiated(ns_instance_id)
        self.api.delete_nfvo_instance(ns_instance_id)

    def force_db_detach(self, nssi_id: str) -> None:
        normalized = normalize_uuid(nssi_id)
        if not normalized or not UUID_RE.fullmatch(normalized):
            raise ValueError(f"Refusing DB detach for invalid UUID: {nssi_id}")
        deployment = self.config.get(
            "free5gmano_deployment", "deployment/free5gmano"
        )
        namespace = self.config.get("free5gmano_namespace", "default")
        script = (
            "from nssmf.models import SliceTemplate;"
            "from moi.models import NetworkSliceSubnet,NsInfo;"
            f"sid='{normalized}';"
            "rows=list(NetworkSliceSubnet.objects.filter(nssiId=sid));"
            "nsinfo_ids=[str(x.nsInfo_id) for x in rows if x.nsInfo_id];"
            "templates=list(SliceTemplate.objects.filter(instanceId=sid));"
            "[t.instanceId.remove(sid) for t in templates];"
            "NetworkSliceSubnet.objects.filter(nssiId=sid).delete();"
            "[NsInfo.objects.filter(id=x,nsInfo__isnull=True).delete() "
            "for x in nsinfo_ids];"
            "print({'detached_templates':len(templates),"
            "'deleted_nssi_rows':len(rows),'nsinfo_ids':nsinfo_ids})"
        )
        result = run(
            [
                "kubectl",
                "exec",
                "-n",
                namespace,
                deployment,
                "--",
                "python3",
                "manage.py",
                "shell",
                "-c",
                script,
            ],
            timeout=60,
        )
        print(result.stdout.strip())

    def delete(
        self,
        target: str,
        *,
        execute: bool,
        force_fallback: bool,
        force_db_detach: bool,
        purge_namespace: bool,
        cleanup_templates: bool,
    ) -> dict[str, Any]:
        with self.state.locked() as store:
            namespace, record = self.find_record(store.data, target)
            nssi_id = normalize_uuid(
                record.get("nssi_id") if record else target
            )
            if not nssi_id or not UUID_RE.fullmatch(nssi_id):
                raise RuntimeError(
                    "Target is not a tracked namespace/intent or valid NSSI UUID"
                )
            template_id = (
                record.get("owned_resources", {}).get("slice_template")
                if record
                else None
            ) or self.locate_template_for_nssi(nssi_id)
            moi = self.api.get_moi(nssi_id)
            ns_instance_id = self.extract_ns_instance_id(moi)
            if namespace is None and ns_instance_id:
                nfvo_detail = self.api.nfvo_instance(ns_instance_id) or {}
                candidate_text = " ".join(
                    str(nfvo_detail.get(key) or "")
                    for key in ("nsInstanceName", "nsInstanceDescription")
                )
                match = re.search(
                    rf"\b{re.escape(self.prefix)}\d+\b",
                    candidate_text,
                )
                if match:
                    namespace = match.group(0)
            plan = {
                "mode": "execute" if execute else "plan",
                "target": target,
                "namespace": namespace,
                "nssi_id": nssi_id,
                "slice_template_id": template_id,
                "ns_instance_id": ns_instance_id,
                "tracked": record is not None,
                "force_fallback": force_fallback,
                "force_db_detach": force_db_detach,
                "purge_namespace": purge_namespace,
                "cleanup_templates": cleanup_templates,
            }
            if not execute:
                return plan
            if record:
                record["status"] = "DELETING"
                record.setdefault("events", []).append(
                    {"at": now(), "event": "delete_started"}
                )
                store.save()

            normal_error: str | None = None
            try:
                self.api.delete_nssi(nssi_id)
            except Exception as error:
                normal_error = str(error)

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and self.nssi_still_attached(
                nssi_id
            ):
                time.sleep(2)
            stuck = self.nssi_still_attached(nssi_id)
            nfvo_left = bool(
                ns_instance_id and self.api.nfvo_instance(ns_instance_id)
            )

            if (normal_error or stuck or nfvo_left) and force_fallback:
                if ns_instance_id:
                    with contextlib.suppress(ApiError):
                        self.direct_nfvo_cleanup(ns_instance_id)
                if namespace:
                    self.delete_namespace(namespace)
                if force_db_detach and self.nssi_still_attached(nssi_id):
                    self.force_db_detach(nssi_id)
                stuck = self.nssi_still_attached(nssi_id)
                nfvo_left = bool(
                    ns_instance_id and self.api.nfvo_instance(ns_instance_id)
                )

            if namespace and (
                purge_namespace
                or (record and record.get("owned_resources", {}).get("namespace"))
            ):
                self.delete_namespace(namespace)

            if cleanup_templates and record:
                owned = record.get("owned_resources", {})
                if not stuck and owned.get("slice_template"):
                    with contextlib.suppress(Exception):
                        self.api.delete_slice_template(owned["slice_template"])
                if not stuck:
                    for generic_id in reversed(
                        owned.get("generic_templates", [])
                    ):
                        with contextlib.suppress(Exception):
                            self.api.delete_generic(generic_id)

            if stuck:
                if record:
                    record["status"] = "DELETE_FAILED"
                    record["delete_error"] = normal_error or "NSSI still attached"
                    store.save()
                raise RuntimeError(
                    "NSSI is still attached in Free5GMANO. Re-run with "
                    "--force-fallback --force-db-detach after reviewing the plan."
                )
            if nfvo_left:
                if record:
                    record["status"] = "DELETE_FAILED"
                    record["delete_error"] = (
                        f"NFVO instance still exists: {ns_instance_id}"
                    )
                    store.save()
                raise RuntimeError(
                    f"Free5GMANO detached the NSSI but NFVO instance "
                    f"{ns_instance_id} still exists. Re-run with --force-fallback."
                )

            if record:
                record["status"] = "DELETED"
                record["deleted_at"] = now()
                record.setdefault("events", []).append(
                    {"at": now(), "event": "delete_complete"}
                )
                store.save()
                self.record_monitoring_sync(store, record)
                try:
                    core_evidence = self.core_registry.reconcile(
                        self.core_pairs(store.data)
                    )
                    record["events"].append(
                        {
                            "at": now(),
                            "event": "core_registration_reconciled_after_delete",
                            "snssai": core_evidence["snssai"],
                        }
                    )
                    record["core_registration_after_delete"] = core_evidence
                except Exception as error:
                    record["core_reconcile_error"] = str(error)
                    record["events"].append(
                        {
                            "at": now(),
                            "event": "core_registration_reconcile_failed_after_delete",
                            "reason": str(error),
                        }
                    )
                store.save()
            return {
                **plan,
                "status": "DELETED",
                "normal_error": normal_error,
                "core_registration": (
                    record.get("core_registration_after_delete") if record else None
                ),
            }

    def status(self) -> dict[str, Any]:
        state = self.state.load()
        templates = self.api.slice_templates()
        namespaces = self.namespace_slots()
        tracked_nssi = {
            normalize_uuid(record.get("nssi_id"))
            for record in state.get("slices", {}).values()
            if record.get("nssi_id")
            and record.get("status") not in {"DELETED", "ROLLED_BACK"}
        }
        api_nssi = {
            normalize_uuid(instance)
            for template in templates
            for instance in (template.get("instanceId") or [])
        }
        return {
            "state_file": str(self.state.path),
            "tracked_slices": state.get("slices", {}),
            "kubernetes_namespaces": namespaces,
            "orphan_namespaces": self.orphan_namespaces(),
            "api_instances": sorted(value for value in api_nssi if value),
            "untracked_api_instances": sorted(api_nssi - tracked_nssi),
            "tracked_but_missing_api": sorted(tracked_nssi - api_nssi),
        }

    def web_status(self) -> dict[str, Any]:
        state = self.state.load()
        status = self.status()
        status["tracked_slices"] = {
            namespace: {
                key: record.get(key)
                for key in (
                    "intent_id",
                    "slice_type",
                    "sst",
                    "sd",
                    "status",
                    "nssi_id",
                    "core_registration",
                    "core_registration_error",
                )
            }
            for namespace, record in status["tracked_slices"].items()
        }
        profiles: list[dict[str, Any]] = []
        for slice_type in ("embb", "urllc", "mmtc"):
            plan, _ = self.make_plan(
                {
                    "slice_type": slice_type,
                    "intent_id": f"preview-{slice_type}",
                },
                state,
            )
            profiles.append(
                {
                    "slice_type": slice_type,
                    "sst": plan.sst,
                    "next_sd": plan.sd,
                    "next_namespace": plan.namespace,
                }
            )
        status["profiles"] = profiles
        status["shared_core"] = self.core_status()
        return status

    def doctor(self) -> dict[str, Any]:
        state = self.state.load()
        checks: dict[str, Any] = {
            "free5gmano": len(self.api.slice_templates()),
            "kubectl": run(
                ["kubectl", "version", "--client"],
                check=False,
            ).returncode
            == 0,
            "profiles": {},
        }
        nssf_manifest = Path(
            self.config.get("shared_nssf_manifest", "shared_core/open5gs-nssf.yaml")
        )
        if not nssf_manifest.is_absolute():
            nssf_manifest = ROOT / nssf_manifest
        shared_core = self.core_status()
        checks["shared_core"] = {
            **shared_core,
            "manifest": str(nssf_manifest),
            "manifest_valid": nssf_manifest.is_file(),
            "status": (
                "OK"
                if nssf_manifest.is_file() and shared_core.get("verified")
                else "FAILED"
            ),
        }
        with tempfile.TemporaryDirectory(prefix="intent-flow-doctor-") as temp:
            temporary = Path(temp)
            for slice_type in ("embb", "urllc", "mmtc"):
                plan, reasons = self.make_plan(
                    {"slice_type": slice_type, "intent_id": f"doctor-{slice_type}"},
                    state,
                )
                self.validate_sources(plan)
                source = temporary / slice_type / "source"
                vnf, nsd, nrm = self.materialize_source_archives(plan, source)
                manifest = render_packages(
                    nsd_zip=nsd,
                    vnf_zip=vnf,
                    namespace=plan.namespace,
                    slot=plan.slot,
                    slice_type=plan.slice_type,
                    sst=plan.sst,
                    sd=plan.sd,
                    ipv4_subnet=plan.ipv4_subnet,
                    ipv4_gateway=plan.ipv4_gateway,
                    ipv6_subnet=plan.ipv6_subnet,
                    ipv6_gateway=plan.ipv6_gateway,
                    output_root=temporary / slice_type / "rendered",
                )
                nrm_profile = self.config["profiles"][slice_type].get(
                    "nrm", {}
                )
                nrm_manifest = render_nrm_package(
                    nrm_zip=nrm,
                    run_dir=Path(manifest["outputs"]["run_dir"]),
                    namespace=plan.namespace,
                    sst=plan.sst,
                    sd=plan.sd,
                    service_type=nrm_profile.get("service_type", slice_type),
                    characteristics=nrm_profile.get(
                        "characteristics",
                        f"Automatically rendered {slice_type} slice.",
                    ),
                    performance_overrides=nrm_profile.get(
                        "performance_overrides"
                    ),
                )
                checks["profiles"][slice_type] = {
                    "classification": reasons,
                    "namespace": plan.namespace,
                    "descriptor_ids": [
                        {
                            "component": item.get("component"),
                            "new_id": item["new_id"],
                        }
                        for item in manifest["descriptors"]["vnf"]
                    ],
                    "render_sha256": manifest["sha256"],
                    "nrm_sha256": nrm_manifest["sha256"],
                    "status": "OK",
                }
        checks["status"] = (
            "OK"
            if checks["kubectl"]
            and checks["shared_core"]["status"] == "OK"
            and all(
                value["status"] == "OK"
                for value in checks["profiles"].values()
            )
            else "FAILED"
        )
        return checks


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="External intent-to-slice orchestrator for Free5GMANO"
    )
    result.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config.json",
        help="JSON configuration file",
    )
    commands = result.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="Classify and show a read-only plan")
    plan.add_argument("intent", type=Path)

    create = commands.add_parser("create", help="Create a slice from an intent")
    create.add_argument("intent", type=Path)
    create.add_argument(
        "--execute",
        action="store_true",
        help="Perform writes; without this flag create is a dry-run",
    )

    delete = commands.add_parser("delete", help="Delete or repair-delete a slice")
    delete.add_argument("target", help="namespace, intent ID, or NSSI UUID")
    delete.add_argument("--execute", action="store_true")
    delete.add_argument(
        "--force-fallback",
        action="store_true",
        help="If Free5GMANO delete fails, directly terminate/delete NFVO instance",
    )
    delete.add_argument(
        "--force-db-detach",
        action="store_true",
        help="Last resort: detach the exact NSSI UUID using Free5GMANO ORM",
    )
    delete.add_argument(
        "--purge-namespace",
        action="store_true",
        help="Delete the tracked Kubernetes namespace even if it is already orphaned",
    )
    delete.add_argument(
        "--cleanup-templates",
        action="store_true",
        help="Delete Generic/SliceTemplate resources created by this orchestrator",
    )

    commands.add_parser("status", help="Reconcile state, API instances and namespaces")
    commands.add_parser(
        "core-status",
        help="Read-only verification of shared AMF/NSSF S-NSSAI state",
    )
    commands.add_parser(
        "core-reconcile",
        help="Reconcile desired S-NSSAI entries into shared AMF and NSSF",
    )
    commands.add_parser(
        "web-status",
        help="WebUI status plus next namespace/SD preview for each profile",
    )
    commands.add_parser(
        "sync-monitoring",
        help="Reconcile Prometheus and slice exporter with active slices",
    )
    commands.add_parser(
        "doctor",
        help="Read-only API/download/render validation for both profiles",
    )
    commands.add_parser(
        "template-catalog",
        help="List uploaded templates and the active source IDs",
    )
    select_templates = commands.add_parser(
        "set-base-templates",
        help="Validate and activate an existing VNF/NSD/NRM source set",
    )
    select_templates.add_argument("profile", choices=("embb", "urllc", "mmtc"))
    select_templates.add_argument("--vnf", required=True)
    select_templates.add_argument("--nsd", required=True)
    select_templates.add_argument("--nrm", required=True)
    install_templates = commands.add_parser(
        "install-base-templates",
        help="Validate, upload, persist, and activate a VNF/NSD/NRM source set",
    )
    install_templates.add_argument("profile", choices=("embb", "urllc", "mmtc"))
    install_templates.add_argument("--vnf", type=Path, required=True)
    install_templates.add_argument("--nsd", type=Path, required=True)
    install_templates.add_argument("--nrm", type=Path, required=True)
    restore_templates = commands.add_parser(
        "restore-base-templates",
        help="Re-upload and activate the persisted source bundle",
    )
    restore_templates.add_argument("profile", choices=("embb", "urllc", "mmtc"))
    return result


def main() -> int:
    args = parser().parse_args()
    config_path = args.config.resolve()
    config = load_json(config_path)
    orchestrator = Orchestrator(config, config_path)
    try:
        if args.command in {"plan", "create"}:
            intent = load_json(args.intent.resolve())
            output = orchestrator.create(
                intent,
                execute=args.command == "create" and args.execute,
            )
        elif args.command == "delete":
            if args.force_db_detach and not args.force_fallback:
                raise ValueError("--force-db-detach requires --force-fallback")
            output = orchestrator.delete(
                args.target,
                execute=args.execute,
                force_fallback=args.force_fallback,
                force_db_detach=args.force_db_detach,
                purge_namespace=args.purge_namespace,
                cleanup_templates=args.cleanup_templates,
            )
        elif args.command == "status":
            output = orchestrator.status()
        elif args.command == "core-status":
            output = orchestrator.core_status()
        elif args.command == "core-reconcile":
            output = orchestrator.core_reconcile()
        elif args.command == "web-status":
            output = orchestrator.web_status()
        elif args.command == "sync-monitoring":
            output = orchestrator.sync_monitoring(orchestrator.state.load())
        elif args.command == "template-catalog":
            output = orchestrator.template_catalog()
        elif args.command == "set-base-templates":
            output = orchestrator.set_base_templates(
                args.profile,
                vnf_template_id=args.vnf,
                nsd_template_id=args.nsd,
                nrm_template_id=args.nrm,
            )
        elif args.command == "install-base-templates":
            output = orchestrator.install_base_templates(
                args.profile,
                vnf_zip=args.vnf.resolve(),
                nsd_zip=args.nsd.resolve(),
                nrm_zip=args.nrm.resolve(),
            )
        elif args.command == "restore-base-templates":
            output = orchestrator.restore_base_templates(args.profile)
        else:
            output = orchestrator.doctor()
        print(dump(output))
        return 0
    except (ApiError, RuntimeError, ValueError, TimeoutError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
