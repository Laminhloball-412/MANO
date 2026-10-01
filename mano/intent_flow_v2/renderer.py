from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any


DESCRIPTOR_ID_RE = re.compile(r"(?m)^(\s*descriptor_id:\s*)(\S+)\s*$")
VNFD_REF_RE = re.compile(r"(?m)^(\s*-\s*vnfd_id:\s*)(\S+)\s*$")
NAMESPACE_RE = re.compile(r"(?m)^(\s*namespace:\s*)[^\r\n#]+")
SLICE_FQDN_RE = re.compile(
    r"\b([a-z0-9-]+-svc)\.slice-[A-Za-z0-9-]+\.svc\.cluster\.local\b",
    re.IGNORECASE,
)
IPV4_SUBNET_RE = re.compile(r"\b10\.45\.\d+\.0/(?:16|24)\b")
IPV4_GATEWAY_RE = re.compile(r"\b10\.45\.\d+\.1(?:/(?:16|24))?\b")
IPV6_SUBNET_RE = re.compile(r"\b2001:db8:[0-9a-fA-F:]+/(?:48|64)\b")
IPV6_GATEWAY_RE = re.compile(r"\b2001:db8:[0-9a-fA-F:]+::1\b")
SST_RE = re.compile(r"(?m)^(\s*-\s*sst:\s*)\d+\s*$")
SD_RE = re.compile(r'(?m)^(\s*sd:\s*)["\']?[0-9A-Fa-f]{6}["\']?\s*$')
SLICE_NAME_RE = re.compile(r"\bslice-\d+\b")
EXPORT_SERVICE_RE = re.compile(
    r"(?m)^(\s*)is_export_service:\s*(?:True|true)\s*$"
)
PFCP_SERVER_RE = re.compile(
    r"(?m)^(\s{2}pfcp:\s*\n"
    r"\s{4}server:\s*\n)"
    r"\s{6}-\s+(?:address:\s*0\.0\.0\.0|dev:\s*eth0)\s*\n"
    r"(?:\s{8}advertise:\s*[^\r\n]+\s*\n)?"
)
GTPU_SERVER_RE = re.compile(
    r"(?m)^(\s{2}gtpu:\s*\n"
    r"\s{4}server:\s*\n)"
    r"\s{6}-\s+(?:address:\s*0\.0\.0\.0|dev:\s*eth0)\s*\n"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if destination != target and destination not in target.parents:
            raise ValueError(f"Unsafe zip member: {member.filename}")
    archive.extractall(destination)


def _zip_tree(source: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())


def _definition_files(vnf_root: Path) -> list[Path]:
    definitions = sorted(vnf_root.glob("*/Definitions/*.yaml"))
    if not definitions:
        definitions = sorted(vnf_root.glob("**/Definitions/*.yaml"))
    return [path for path in definitions if DESCRIPTOR_ID_RE.search(_read(path))]


def _nsd_definition(nsd_root: Path) -> Path:
    preferred = nsd_root / "Definitions" / "ns.yaml"
    if preferred.exists():
        return preferred
    matches = sorted(nsd_root.glob("**/Definitions/*.yaml"))
    if not matches:
        raise FileNotFoundError("NSD package has no Definitions/*.yaml")
    return matches[0]


def validate_source_packages(*, nsd_zip: Path, vnf_zip: Path) -> dict[str, Any]:
    """Validate that an NSD references exactly the VNF IDs in a source bundle."""
    try:
        with zipfile.ZipFile(vnf_zip) as archive:
            with tempfile.TemporaryDirectory(prefix="auto-slice-vnf-check-") as temp:
                vnf_root = Path(temp)
                _safe_extract(archive, vnf_root)
                definitions = _definition_files(vnf_root)
                vnf_ids = {
                    match.group(2)
                    for path in definitions
                    if (match := DESCRIPTOR_ID_RE.search(_read(path)))
                }
    except zipfile.BadZipFile as error:
        raise ValueError(f"Invalid VNF ZIP: {error}") from error
    if not vnf_ids:
        raise ValueError("VNF package has no descriptor_id entries")

    try:
        with zipfile.ZipFile(nsd_zip) as archive:
            with tempfile.TemporaryDirectory(prefix="auto-slice-nsd-check-") as temp:
                nsd_root = Path(temp)
                _safe_extract(archive, nsd_root)
                definition = _nsd_definition(nsd_root)
                text = _read(definition)
                nsd_match = DESCRIPTOR_ID_RE.search(text)
                if not nsd_match:
                    raise ValueError(f"NSD descriptor_id missing in {definition}")
                nsd_id = nsd_match.group(2)
                referenced_ids = {
                    match.group(2) for match in VNFD_REF_RE.finditer(text)
                }
    except zipfile.BadZipFile as error:
        raise ValueError(f"Invalid NSD ZIP: {error}") from error
    if not referenced_ids:
        raise ValueError("NSD package has no vnfd_id references")

    missing = sorted(referenced_ids - vnf_ids)
    unused = sorted(vnf_ids - referenced_ids)
    if missing or unused:
        details = []
        if missing:
            details.append(f"NSD references unknown VNF IDs: {missing}")
        if unused:
            details.append(f"VNF IDs not referenced by NSD: {unused}")
        raise ValueError("; ".join(details))
    return {
        "nsd_descriptor_id": nsd_id,
        "vnf_descriptor_ids": sorted(vnf_ids),
        "nsd_vnfd_ids": sorted(referenced_ids),
        "status": "MATCHED",
    }


def _patch_text(
    text: str,
    *,
    namespace: str,
    slot: int,
    sst: int,
    sd: str,
    ipv4_subnet: str,
    ipv4_gateway: str,
    ipv6_subnet: str | None,
    ipv6_gateway: str | None,
) -> str:
    text = SLICE_FQDN_RE.sub(
        lambda match: f"{match.group(1)}.{namespace}.svc.cluster.local",
        text,
    )
    text = SLICE_NAME_RE.sub(namespace, text)

    text = IPV4_SUBNET_RE.sub(ipv4_subnet, text)

    def replace_gateway(match: re.Match[str]) -> str:
        # Definition commands can contain the gateway with a prefix while the
        # Open5GS YAML contains the same gateway without one.  Preserve that
        # distinction, but always use the prefix of the selected slice pool.
        suffix = f"/{ipv4_subnet.rsplit('/', 1)[1]}" if "/" in match.group(0) else ""
        return f"{ipv4_gateway}{suffix}"

    text = IPV4_GATEWAY_RE.sub(replace_gateway, text)
    if ipv6_subnet and ipv6_gateway:
        text = IPV6_SUBNET_RE.sub(ipv6_subnet, text)
        text = IPV6_GATEWAY_RE.sub(ipv6_gateway, text)
    text = SST_RE.sub(lambda match: f"{match.group(1)}{sst}", text)
    text = SD_RE.sub(lambda match: f'{match.group(1)}"{sd}"', text)
    return text


def _ensure_pfcp_kubernetes_identity(
    text: str,
    *,
    component: str | None,
    namespace: str,
) -> str:
    """Keep PFCP peer identity on direct pod DNS, not a UDP ClusterIP/NAT."""
    if component == "upf" and "headless_service:" not in text:
        match = EXPORT_SERVICE_RE.search(text)
        if match:
            indent = match.group(1)
            insertion = (
                f"{match.group(0)}\n"
                f"{indent}headless_service: True\n"
                f"{indent}publish_not_ready_addresses: True"
            )
            text = text[:match.start()] + insertion + text[match.end():]

    if component in {"smf", "upf"}:
        text = PFCP_SERVER_RE.sub(
            lambda match: f"{match.group(1)}      - dev: eth0\n",
            text,
            count=1,
        )
    if component == "upf":
        # Binding GTP-U to 0.0.0.0 starts the socket, but Open5GS can then put
        # an unusable/local address in the N2 tunnel information.  Resolving
        # eth0 at pod start advertises the real routable pod IP to the gNB.
        text = GTPU_SERVER_RE.sub(
            lambda match: f"{match.group(1)}      - dev: eth0\n",
            text,
            count=1,
        )
    return text


def render_packages(
    *,
    nsd_zip: Path,
    vnf_zip: Path,
    namespace: str,
    slot: int,
    slice_type: str,
    sst: int,
    sd: str,
    ipv4_subnet: str,
    ipv4_gateway: str,
    ipv6_subnet: str | None,
    ipv6_gateway: str | None,
    output_root: Path,
    default_qos: dict[str, Any] | None = None,
    service_qos: dict[str, Any] | None = None,
    session_ambr_mbps: float | None = None,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    run_dir = output_root / f"{namespace}-{run_id}"
    nsd_root = run_dir / "NSD"
    vnf_root = run_dir / "VNF"
    nsd_root.mkdir(parents=True)
    vnf_root.mkdir(parents=True)

    with zipfile.ZipFile(nsd_zip) as archive:
        _safe_extract(archive, nsd_root)
    with zipfile.ZipFile(vnf_zip) as archive:
        _safe_extract(archive, vnf_root)

    id_map: dict[str, str] = {}
    definition_records: list[dict[str, str]] = []
    for definition in _definition_files(vnf_root):
        original = _read(definition)
        match = DESCRIPTOR_ID_RE.search(original)
        if not match:
            continue
        old_id = match.group(2)
        relative_parts = [
            part.lower() for part in definition.relative_to(vnf_root).parts
        ]
        component = next(
            (
                name
                for name in ("pcf", "smf", "upf")
                if name in relative_parts
                or definition.stem.lower().startswith(name)
                or old_id.lower().endswith(name)
            ),
            None,
        )
        generated_id = str(uuid.uuid4())
        new_id = (
            f"{generated_id[:-len(component)]}{component}"
            if component
            else generated_id
        )
        patched = DESCRIPTOR_ID_RE.sub(
            lambda value: f"{value.group(1)}{new_id}",
            original,
            count=1,
        )
        patched = NAMESPACE_RE.sub(
            lambda value: f"{value.group(1)}{namespace}",
            patched,
            count=1,
        )
        patched = _patch_text(
            patched,
            namespace=namespace,
            slot=slot,
            sst=sst,
            sd=sd,
            ipv4_subnet=ipv4_subnet,
            ipv4_gateway=ipv4_gateway,
            ipv6_subnet=ipv6_subnet,
            ipv6_gateway=ipv6_gateway,
        )
        patched = _ensure_pfcp_kubernetes_identity(
            patched,
            component=component,
            namespace=namespace,
        )
        _write(definition, patched)
        id_map[old_id] = new_id
        definition_records.append(
            {
                "path": definition.relative_to(vnf_root).as_posix(),
                "old_id": old_id,
                "new_id": new_id,
                "component": component,
            }
        )

    if not id_map:
        raise ValueError("VNF package has no descriptor_id entries")

    nsd_definition = _nsd_definition(nsd_root)
    nsd_text = _read(nsd_definition)
    nsd_match = DESCRIPTOR_ID_RE.search(nsd_text)
    if not nsd_match:
        raise ValueError(f"NSD descriptor_id missing in {nsd_definition}")
    old_nsd_id = nsd_match.group(2)
    new_nsd_id = str(uuid.uuid4())
    nsd_text = DESCRIPTOR_ID_RE.sub(
        lambda match: f"{match.group(1)}{new_nsd_id}",
        nsd_text,
        count=1,
    )

    unresolved: list[str] = []

    def replace_ref(match: re.Match[str]) -> str:
        old_ref = match.group(2)
        if old_ref not in id_map:
            unresolved.append(old_ref)
            return match.group(0)
        return f"{match.group(1)}{id_map[old_ref]}"

    nsd_text = VNFD_REF_RE.sub(replace_ref, nsd_text)
    if unresolved:
        raise ValueError(f"NSD references unknown VNF IDs: {sorted(set(unresolved))}")
    nsd_text = _patch_text(
        nsd_text,
        namespace=namespace,
        slot=slot,
        sst=sst,
        sd=sd,
        ipv4_subnet=ipv4_subnet,
        ipv4_gateway=ipv4_gateway,
        ipv6_subnet=ipv6_subnet,
        ipv6_gateway=ipv6_gateway,
    )
    _write(nsd_definition, nsd_text)

    for root in (vnf_root, nsd_root):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".zip"}:
                continue
            try:
                text = _read(path)
            except UnicodeDecodeError:
                continue
            patched = _patch_text(
                text,
                namespace=namespace,
                slot=slot,
                sst=sst,
                sd=sd,
                ipv4_subnet=ipv4_subnet,
                ipv4_gateway=ipv4_gateway,
                ipv6_subnet=ipv6_subnet,
                ipv6_gateway=ipv6_gateway,
            )
            relative_parts = {
                part.lower() for part in path.relative_to(vnf_root).parts
            } if path.is_relative_to(vnf_root) else set()
            component = next(
                (name for name in ("pcf", "smf", "upf") if name in relative_parts),
                None,
            )
            patched = _ensure_pfcp_kubernetes_identity(
                patched,
                component=component,
                namespace=namespace,
            )
            if patched != text:
                _write(path, patched)

    rendered_refs = {
        match.group(2) for match in VNFD_REF_RE.finditer(_read(nsd_definition))
    }
    expected_refs = set(id_map.values())
    if rendered_refs != expected_refs:
        raise ValueError(
            f"NSD/VNF mismatch: NSD={sorted(rendered_refs)}, VNF={sorted(expected_refs)}"
        )

    stale_namespaces: list[str] = []
    for path in vnf_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = _read(path)
        except UnicodeDecodeError:
            continue
        for match in SLICE_FQDN_RE.finditer(text):
            if f".{namespace}.svc.cluster.local" not in match.group(0):
                stale_namespaces.append(f"{path}: {match.group(0)}")
    if stale_namespaces:
        raise ValueError(f"Stale slice FQDNs: {stale_namespaces}")

    nsd_output = run_dir / f"NSD-{namespace}.zip"
    vnf_output = run_dir / f"VNF-{namespace}.zip"
    _zip_tree(nsd_root, nsd_output)
    _zip_tree(vnf_root, vnf_output)

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "namespace": namespace,
        "slot": slot,
        "slice_type": slice_type,
        "network": {
            "sst": sst,
            "sd": sd,
            "ipv4_subnet": ipv4_subnet,
            "ipv4_gateway": ipv4_gateway,
            "ipv6_subnet": ipv6_subnet,
            "ipv6_gateway": ipv6_gateway,
        },
        "qos": {
            "default": default_qos,
            "service": service_qos,
            "session_ambr_mbps": session_ambr_mbps,
            "core_policy_status": (
                "DECLARED_NOT_ENFORCED"
                if default_qos is not None or service_qos is not None
                else "NOT_REQUESTED"
            ),
            "radio_status": "NOT_VERIFIED",
        },
        "descriptors": {
            "nsd": {"old_id": old_nsd_id, "new_id": new_nsd_id},
            "vnf": definition_records,
        },
        "outputs": {
            "run_dir": str(run_dir.resolve()),
            "nsd_zip": str(nsd_output.resolve()),
            "vnf_zip": str(vnf_output.resolve()),
        },
        "sha256": {
            "nsd_zip": hashlib.sha256(nsd_output.read_bytes()).hexdigest(),
            "vnf_zip": hashlib.sha256(vnf_output.read_bytes()).hexdigest(),
        },
    }
    (run_dir / "render-manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return manifest


def clean_failed_run(manifest: dict[str, Any] | None) -> None:
    if not manifest:
        return
    run_dir = Path(manifest.get("outputs", {}).get("run_dir", ""))
    if run_dir.exists() and run_dir.name:
        shutil.rmtree(run_dir)


def render_nrm_package(
    *,
    nrm_zip: Path,
    run_dir: Path,
    namespace: str,
    sst: int,
    sd: str,
    service_type: str,
    characteristics: str,
    performance_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nrm_root = run_dir / "NRM"
    nrm_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(nrm_zip) as archive:
        _safe_extract(archive, nrm_root)
    json_files = sorted(nrm_root.rglob("*.json"))
    if not json_files:
        raise FileNotFoundError("NRM package has no JSON definition")

    definition = json_files[0]
    document = json.loads(definition.read_text(encoding="utf-8"))
    definitions = document.setdefault("definitions", {})
    snssai = definitions.setdefault("SnssaiList", {})
    sst_document = snssai.setdefault("sst", {})
    sst_document["value"] = str(sst)
    sst_document["type"] = service_type
    sst_document["characteristics"] = characteristics
    snssai["sd"] = sd

    performance = definitions.setdefault("PrefReq", {}).setdefault(
        "properties", {}
    )
    if performance_overrides:
        performance.update(performance_overrides)

    definition.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    output = run_dir / f"NRM-{namespace}.zip"
    _zip_tree(nrm_root, output)
    return {
        "definition": definition.relative_to(nrm_root).as_posix(),
        "sst": sst,
        "sd": sd,
        "service_type": service_type,
        "output": str(output.resolve()),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
