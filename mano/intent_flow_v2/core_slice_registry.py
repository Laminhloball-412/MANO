"""Reconcile managed S-NSSAI entries across the shared AMF and NSSF."""

from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any

import yaml


ACTIVE_SLICE_STATUSES = {
    "READY",
    "READY_FOR_UE",
    "VERIFYING",
    "REGISTERING_NSSF",
}
_SD_PATTERN = re.compile(r"^[0-9A-Fa-f]{1,6}$")


def normalize_snssai(value: dict) -> tuple[int, str]:
    """Return an S-NSSAI as a validated, stable ``(sst, sd)`` tuple."""

    if not isinstance(value, dict):
        raise ValueError("S-NSSAI must be an object")
    sst = value.get("sst")
    if isinstance(sst, bool):
        raise ValueError("SST must be an integer between 0 and 255")
    try:
        sst = int(sst)
    except (TypeError, ValueError) as exc:
        raise ValueError("SST must be an integer between 0 and 255") from exc
    if not 0 <= sst <= 255:
        raise ValueError("SST must be an integer between 0 and 255")

    sd = value.get("sd")
    if not isinstance(sd, str) or not _SD_PATTERN.fullmatch(sd):
        raise ValueError("SD must contain one to six hexadecimal characters")
    return sst, sd.upper().zfill(6)


def desired_snssai(
    state: dict,
    baseline: list[dict],
    include: set[tuple[int, str]] | None = None,
) -> list[tuple[int, str]]:
    """Build the sorted desired S-NSSAI set from baseline and active slices."""

    pairs = {normalize_snssai(item) for item in baseline}
    slices = state.get("slices", {}) if isinstance(state, dict) else {}
    records = slices.values() if isinstance(slices, dict) else slices
    for record in records:
        if not isinstance(record, dict) or record.get("status") not in ACTIVE_SLICE_STATUSES:
            continue
        candidate = record.get("snssai", record)
        pairs.add(normalize_snssai(candidate))
    for sst, sd in include or set():
        pairs.add(normalize_snssai({"sst": sst, "sd": sd}))
    return sorted(pairs, key=lambda pair: (pair[0], pair[1]))


def _snssai_documents(pairs: list[tuple[int, str]]) -> list[dict[str, Any]]:
    normalized = {
        normalize_snssai({"sst": sst, "sd": sd}) for sst, sd in pairs
    }
    return [
        {"sst": sst, "sd": sd}
        for sst, sd in sorted(normalized, key=lambda pair: (pair[0], pair[1]))
    ]


def render_amf_config(source: str, pairs: list[tuple[int, str]]) -> str:
    """Replace only AMF PLMN S-NSSAI lists while preserving other settings."""

    document = yaml.safe_load(source)
    try:
        plmn_support = document["amf"]["plmn_support"]
    except (KeyError, TypeError) as exc:
        raise ValueError("AMF configuration has no amf.plmn_support list") from exc
    if not isinstance(plmn_support, list) or not plmn_support:
        raise ValueError("AMF configuration has no amf.plmn_support list")
    rendered_pairs = _snssai_documents(pairs)
    for entry in plmn_support:
        entry["s_nssai"] = [dict(item) for item in rendered_pairs]
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def render_nssf_config(
    source: str,
    pairs: list[tuple[int, str]],
    nrf_uri: str,
) -> str:
    """Replace NSSF NSI mappings with deterministic shared-NRF mappings."""

    document = yaml.safe_load(source)
    try:
        client = document["nssf"]["sbi"]["client"]
    except (KeyError, TypeError) as exc:
        raise ValueError("NSSF configuration has no nssf.sbi.client object") from exc
    if not isinstance(client, dict):
        raise ValueError("NSSF configuration has no nssf.sbi.client object")
    client["nsi"] = [
        {"uri": nrf_uri, "s_nssai": item} for item in _snssai_documents(pairs)
    ]
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def run(command: list[str]) -> str:
    """Run one command and return stdout, raising with captured diagnostics."""

    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}: {detail}")
    return completed.stdout


class CoreSliceRegistry:
    """Own the managed S-NSSAI lists in the shared AMF and NSSF."""

    def __init__(self, config: dict, runner=run):
        self.config = config
        self.runner = runner
        self.root = Path(__file__).resolve().parent
        self.last_backup_dir: str | None = None

    def _command(self, command: list[str]) -> str:
        return self.runner(command)

    def _resource(self, prefix: str, field: str) -> str:
        value = self.config.get(f"shared_{prefix}_{field}")
        if not value:
            raise ValueError(f"missing shared_{prefix}_{field} configuration")
        return str(value)

    def ensure_nssf(self) -> dict:
        if not self.config.get("shared_nssf_enabled", False):
            raise RuntimeError("shared NSSF reconciliation is disabled")
        namespace = self._resource("nssf", "namespace")
        deployment = self._resource("nssf", "deployment")
        try:
            existing = self._command(
                [
                    "kubectl",
                    "get",
                    "deployment",
                    deployment,
                    "-n",
                    namespace,
                    "-o",
                    "name",
                ]
            ).strip()
        except Exception:
            existing = ""
        if existing:
            return {"manifest": None, "applied": False, "output": existing}
        manifest = Path(self._resource("nssf", "manifest"))
        if not manifest.is_absolute():
            manifest = self.root / manifest
        if not manifest.is_file():
            raise FileNotFoundError(f"shared NSSF manifest not found: {manifest}")
        output = self._command(["kubectl", "apply", "-f", str(manifest)])
        return {"manifest": str(manifest), "applied": True, "output": output.strip()}

    def _get_configmap(self, prefix: str) -> tuple[dict, str]:
        namespace = self._resource(prefix, "namespace")
        name = self._resource(prefix, "configmap")
        key = self._resource(prefix, "config_key")
        raw = self._command(
            ["kubectl", "get", "configmap", name, "-n", namespace, "-o", "json"]
        )
        document = json.loads(raw)
        try:
            return document, document["data"][key]
        except KeyError as exc:
            raise RuntimeError(f"ConfigMap {namespace}/{name} has no data key {key}") from exc

    def snapshot(self) -> dict:
        amf_document, amf_source = self._get_configmap("amf")
        nssf_document, nssf_source = self._get_configmap("nssf")
        backup_root = Path(self.config.get("core_registry_backup_dir", "runtime/core-config-backups"))
        if not backup_root.is_absolute():
            backup_root = self.root / backup_root
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup_dir = backup_root / stamp
        backup_dir.mkdir(parents=True, exist_ok=False)
        (backup_dir / "open5gs-amf-configmap.json").write_text(
            json.dumps(amf_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (backup_dir / "open5gs-nssf-configmap.json").write_text(
            json.dumps(nssf_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "backup_dir": str(backup_dir),
            "amf_source": amf_source,
            "nssf_source": nssf_source,
        }

    def _patch_configmap(self, prefix: str, content: str) -> None:
        namespace = self._resource(prefix, "namespace")
        name = self._resource(prefix, "configmap")
        key = self._resource(prefix, "config_key")
        payload = json.dumps({"data": {key: content}}, separators=(",", ":"))
        self._command(
            [
                "kubectl",
                "patch",
                "configmap",
                name,
                "-n",
                namespace,
                "--type",
                "merge",
                "-p",
                payload,
            ]
        )

    def _restart(self, prefix: str) -> None:
        namespace = self._resource(prefix, "namespace")
        deployment = self._resource(prefix, "deployment")
        timeout = int(self.config.get("core_rollout_timeout_seconds", 120))
        self._command(
            ["kubectl", "rollout", "restart", f"deployment/{deployment}", "-n", namespace]
        )
        self._command(
            [
                "kubectl",
                "rollout",
                "status",
                f"deployment/{deployment}",
                "-n",
                namespace,
                f"--timeout={timeout}s",
            ]
        )

    def _deployment_available(self, prefix: str) -> str:
        namespace = self._resource(prefix, "namespace")
        deployment = self._resource(prefix, "deployment")
        raw = self._command(
            ["kubectl", "get", "deployment", deployment, "-n", namespace, "-o", "json"]
        )
        status = json.loads(raw).get("status", {})
        replicas = int(status.get("replicas", 0))
        if replicas < 1 or int(status.get("availableReplicas", 0)) < replicas:
            raise RuntimeError(f"Deployment {namespace}/{deployment} is not Available")
        return "Available"

    def _nssf_endpoints(self) -> list[str]:
        namespace = self._resource("nssf", "namespace")
        service = self._resource("nssf", "service")
        raw = self._command(
            ["kubectl", "get", "endpoints", service, "-n", namespace, "-o", "json"]
        )
        endpoints = []
        for subset in json.loads(raw).get("subsets", []):
            for address in subset.get("addresses", []):
                for port in subset.get("ports", []):
                    endpoints.append(f"{address['ip']}:{port['port']}")
        if not endpoints:
            raise RuntimeError(f"Service {namespace}/{service} has no ready endpoint")
        return sorted(set(endpoints))

    def _nrf_nssf_registration(self) -> dict:
        namespace = self._resource("nssf", "namespace")
        pod = self._command(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                namespace,
                "-l",
                "app=nssf-svc",
                "--field-selector=status.phase=Running",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ]
        ).strip()
        if not pod:
            raise RuntimeError("no Running NSSF pod found")
        uri = self._resource("nrf", "service_uri")
        discovery_uri = (
            f"{uri}/nnrf-disc/v1/nf-instances"
            "?target-nf-type=NSSF&requester-nf-type=AMF"
        )
        raw = self._command(
            [
                "kubectl",
                "exec",
                "-n",
                namespace,
                pod,
                "--",
                "curl",
                "--http2-prior-knowledge",
                "-sS",
                discovery_uri,
            ]
        )
        instances = json.loads(raw).get("nfInstances", [])
        registered = [
            item for item in instances
            if item.get("nfType") == "NSSF" and item.get("nfStatus") == "REGISTERED"
        ]
        usable = [
            endpoint
            for item in registered
            for service in item.get("nfServices", [])
            for endpoint in service.get("ipEndPoints", [])
        ]
        if not registered or not usable:
            raise RuntimeError("NRF has no REGISTERED NSSF with a usable endpoint")
        return {"nf_types": sorted({item["nfType"] for item in registered}), "status": "REGISTERED"}

    def verify(self, pairs: list[tuple[int, str]]) -> dict:
        expected = _snssai_documents(pairs)
        _, amf_source = self._get_configmap("amf")
        _, nssf_source = self._get_configmap("nssf")
        amf = yaml.safe_load(amf_source)
        nssf = yaml.safe_load(nssf_source)
        actual_amf = amf["amf"]["plmn_support"][0].get("s_nssai", [])
        actual_nssf = [
            item.get("s_nssai")
            for item in nssf["nssf"]["sbi"]["client"].get("nsi", [])
        ]
        if actual_amf != expected or actual_nssf != expected:
            raise RuntimeError(
                f"S-NSSAI verification mismatch: expected={expected}, "
                f"amf={actual_amf}, nssf={actual_nssf}"
            )
        return {
            "verified": True,
            "snssai": expected,
            "deployments": {
                "amf": self._deployment_available("amf"),
                "nssf": self._deployment_available("nssf"),
            },
            "nssf_endpoint": self._nssf_endpoints(),
            "nrf_registration": self._nrf_nssf_registration(),
        }

    def _restore(self, snapshot: dict) -> None:
        self._patch_configmap("nssf", snapshot["nssf_source"])
        self._patch_configmap("amf", snapshot["amf_source"])
        self._restart("nssf")
        self._restart("amf")

    def reconcile(self, pairs: list[tuple[int, str]]) -> dict:
        normalized = [normalize_snssai({"sst": sst, "sd": sd}) for sst, sd in pairs]
        self.ensure_nssf()
        snapshot = self.snapshot()
        self.last_backup_dir = snapshot["backup_dir"]
        rendered_amf = render_amf_config(snapshot["amf_source"], normalized)
        rendered_nssf = render_nssf_config(
            snapshot["nssf_source"],
            normalized,
            self._resource("nrf", "service_uri"),
        )
        changed = {
            "amf": yaml.safe_load(rendered_amf) != yaml.safe_load(snapshot["amf_source"]),
            "nssf": yaml.safe_load(rendered_nssf) != yaml.safe_load(snapshot["nssf_source"]),
        }
        try:
            if changed["nssf"]:
                self._patch_configmap("nssf", rendered_nssf)
                self._restart("nssf")
            if changed["amf"]:
                self._patch_configmap("amf", rendered_amf)
                self._restart("amf")
            evidence = self.verify(normalized)
        except Exception:
            self._restore(snapshot)
            raise
        evidence["changed"] = changed
        evidence["backup_dir"] = snapshot["backup_dir"]
        return evidence
