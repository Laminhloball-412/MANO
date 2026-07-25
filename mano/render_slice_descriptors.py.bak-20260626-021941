#!/usr/bin/env python3
"""
Render Open5GS slice descriptor packages from reusable NSD/VNF zip templates.

The script keeps the original zip files untouched. It extracts them into an
output directory, generates fresh descriptor IDs, patches namespace references,
keeps NSD constituent_vnfd IDs aligned with the generated VNF descriptor IDs,
and creates new zip files ready to onboard/upload.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path


COMPONENTS = ("pcf", "smf", "upf")

DESCRIPTOR_ID_RE = re.compile(r"(?m)^(\s*descriptor_id:\s*)(\S+)\s*$")
NAMESPACE_RE = re.compile(r"(?m)^(\s*namespace:\s*)[^\r\n#]+")
VNFD_REF_RE = re.compile(r"(?m)^(\s*-\s*vnfd_id:\s*)(\S+)\s*$")
SERVICE_FQDN_RE = re.compile(r"\b(pcf-svc|smf-svc|upf-svc)\.slice-[A-Za-z0-9-]+\b")
STALE_SERVICE_FQDN_RE = re.compile(r"\b(?:pcf-svc|smf-svc|upf-svc)\.slice-[A-Za-z0-9-]+\.svc\.cluster\.local\b")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def extract_zip(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(dest)


def zip_dir(src_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(src_dir).as_posix())


def first_descriptor_id(text: str, path: Path) -> str:
    match = DESCRIPTOR_ID_RE.search(text)
    if not match:
        raise ValueError(f"Cannot find descriptor_id in {path}")
    return match.group(2)


def component_from_id(value: str) -> str | None:
    value_lower = value.lower()
    for component in COMPONENTS:
        if value_lower.endswith(component):
            return component
    return None


def new_vnfd_id(component: str) -> str:
    # Preserve the existing lab convention: UUID-shaped ID ending in pcf/smf/upf.
    return f"{str(uuid.uuid4())[:-3]}{component}"


def find_vnf_definition(vnf_root: Path, component: str) -> Path:
    preferred = vnf_root / component / "Definitions" / f"{component}.yaml"
    if preferred.exists():
        return preferred

    matches = sorted(vnf_root.glob(f"**/Definitions/{component}.yaml"))
    if matches:
        return matches[0]

    raise FileNotFoundError(f"Cannot find {component}/Definitions/{component}.yaml")


def find_nsd_definition(nsd_root: Path) -> Path:
    preferred = nsd_root / "Definitions" / "ns.yaml"
    if preferred.exists():
        return preferred

    matches = sorted(nsd_root.glob("**/Definitions/*.yaml"))
    if matches:
        return matches[0]

    raise FileNotFoundError("Cannot find NSD definition YAML under Definitions/")


def patch_service_fqdns(root: Path, slice_name: str) -> list[str]:
    changed: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = read_text(path)
        except UnicodeDecodeError:
            continue

        patched = SERVICE_FQDN_RE.sub(lambda m: f"{m.group(1)}.{slice_name}", text)
        if patched != text:
            write_text(path, patched)
            changed.append(str(path))
    return changed


def patch_vnf_descriptors(vnf_root: Path, slice_name: str) -> dict[str, dict[str, str]]:
    id_map: dict[str, dict[str, str]] = {}

    for component in COMPONENTS:
        definition = find_vnf_definition(vnf_root, component)
        text = read_text(definition)
        old_id = first_descriptor_id(text, definition)
        generated_id = new_vnfd_id(component)

        patched = DESCRIPTOR_ID_RE.sub(
            lambda m: f"{m.group(1)}{generated_id}",
            text,
            count=1,
        )
        patched = NAMESPACE_RE.sub(
            lambda m: f"{m.group(1)}{slice_name}",
            patched,
            count=1,
        )

        write_text(definition, patched)
        id_map[component] = {
            "old": old_id,
            "new": generated_id,
            "definition": str(definition),
        }

    return id_map


def patch_nsd_descriptor(nsd_root: Path, vnf_id_map: dict[str, dict[str, str]]) -> dict[str, object]:
    definition = find_nsd_definition(nsd_root)
    text = read_text(definition)
    old_nsd_id = first_descriptor_id(text, definition)
    new_nsd_id = str(uuid.uuid4())

    patched = DESCRIPTOR_ID_RE.sub(
        lambda m: f"{m.group(1)}{new_nsd_id}",
        text,
        count=1,
    )

    old_to_new = {entry["old"]: entry["new"] for entry in vnf_id_map.values()}
    unmatched_refs: list[str] = []
    generated_refs: list[str] = []

    def replace_vnfd_ref(match: re.Match[str]) -> str:
        current = match.group(2)
        replacement = old_to_new.get(current)
        if replacement is None:
            component = component_from_id(current)
            if component is not None:
                replacement = vnf_id_map[component]["new"]

        if replacement is None:
            unmatched_refs.append(current)
            return match.group(0)

        generated_refs.append(replacement)
        return f"{match.group(1)}{replacement}"

    patched = VNFD_REF_RE.sub(replace_vnfd_ref, patched)
    if unmatched_refs:
        refs = ", ".join(unmatched_refs)
        raise ValueError(
            f"NSD references VNF IDs that cannot be matched to pcf/smf/upf descriptors: {refs}"
        )

    expected_refs = {entry["new"] for entry in vnf_id_map.values()}
    actual_refs = set(generated_refs)
    if actual_refs != expected_refs:
        missing = ", ".join(sorted(expected_refs - actual_refs)) or "none"
        extra = ", ".join(sorted(actual_refs - expected_refs)) or "none"
        raise ValueError(f"NSD/VNF ID mismatch after rendering. Missing: {missing}. Extra: {extra}.")

    write_text(definition, patched)

    return {
        "old": old_nsd_id,
        "new": new_nsd_id,
        "definition": str(definition),
        "vnfd_refs": generated_refs,
    }


def validate_rendered_output(
    nsd_root: Path,
    vnf_root: Path,
    slice_name: str,
    vnf_id_map: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {
        "wrong_namespace": [],
        "stale_service_fqdn": [],
        "id_mismatch": [],
    }

    expected_vnf_ids = {entry["new"] for entry in vnf_id_map.values()}
    nsd_text = read_text(find_nsd_definition(nsd_root))
    nsd_refs = {match.group(2) for match in VNFD_REF_RE.finditer(nsd_text)}
    if nsd_refs != expected_vnf_ids:
        problems["id_mismatch"].append(
            f"NSD refs {sorted(nsd_refs)} do not match VNF descriptor IDs {sorted(expected_vnf_ids)}"
        )

    for component in COMPONENTS:
        definition = find_vnf_definition(vnf_root, component)
        text = read_text(definition)
        namespace_match = NAMESPACE_RE.search(text)
        if not namespace_match or namespace_match.group(0).split(":", 1)[1].strip() != slice_name:
            problems["wrong_namespace"].append(str(definition))

    for root in (nsd_root, vnf_root):
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                text = read_text(path)
            except UnicodeDecodeError:
                continue
            for match in STALE_SERVICE_FQDN_RE.finditer(text):
                if f".{slice_name}.svc.cluster.local" not in match.group(0):
                    problems["stale_service_fqdn"].append(f"{path}: {match.group(0)}")

    problems = {key: value for key, value in problems.items() if value}
    if problems:
        raise ValueError("Rendered descriptor validation failed: " + json.dumps(problems, indent=2))

    return {
        "nsd_vnfd_refs": sorted(nsd_refs),
        "vnf_descriptor_ids": sorted(expected_vnf_ids),
    }


def render_descriptor_packages(
    nsd_zip: Path,
    vnf_zip: Path,
    slice_name: str,
    out: Path = Path("generated_descriptors"),
    force: bool = False,
    run_id: str | None = None,
) -> dict[str, object]:
    nsd_zip = Path(nsd_zip).resolve()
    vnf_zip = Path(vnf_zip).resolve()
    out = Path(out)

    if not nsd_zip.exists():
        raise FileNotFoundError(nsd_zip)
    if not vnf_zip.exists():
        raise FileNotFoundError(vnf_zip)

    run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
    run_dir = (out / f"{slice_name}-{run_id}").resolve()

    if run_dir.exists():
        if not force:
            raise FileExistsError(f"{run_dir} already exists. Use --force to replace it.")
        shutil.rmtree(run_dir)

    nsd_root = run_dir / "NSD"
    vnf_root = run_dir / "VNF"
    nsd_root.mkdir(parents=True)
    vnf_root.mkdir(parents=True)

    extract_zip(nsd_zip, nsd_root)
    extract_zip(vnf_zip, vnf_root)

    vnf_id_map = patch_vnf_descriptors(vnf_root, slice_name)
    nsd_id_map = patch_nsd_descriptor(nsd_root, vnf_id_map)

    changed_fqdn_files = []
    changed_fqdn_files.extend(patch_service_fqdns(nsd_root, slice_name))
    changed_fqdn_files.extend(patch_service_fqdns(vnf_root, slice_name))
    validation = validate_rendered_output(nsd_root, vnf_root, slice_name, vnf_id_map)

    output_nsd_zip = run_dir / f"NSD-{slice_name}-{run_id}.zip"
    output_vnf_zip = run_dir / f"VNF-{slice_name}-{run_id}.zip"
    zip_dir(nsd_root, output_nsd_zip)
    zip_dir(vnf_root, output_vnf_zip)

    manifest = {
        "slice_name": slice_name,
        "run_id": run_id,
        "source": {
            "nsd_zip": str(nsd_zip),
            "vnf_zip": str(vnf_zip),
        },
        "ids": {
            "nsd": nsd_id_map,
            "vnf": vnf_id_map,
        },
        "changed_fqdn_files": changed_fqdn_files,
        "validation": validation,
        "outputs": {
            "run_dir": str(run_dir),
            "nsd_zip": str(output_nsd_zip),
            "vnf_zip": str(output_vnf_zip),
        },
    }

    manifest_path = run_dir / "render-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["outputs"]["manifest"] = str(manifest_path)

    return manifest


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate fresh slice descriptor zips from NSD/VNF zip templates.",
    )
    parser.add_argument(
        "--nsd-zip",
        required=True,
        type=Path,
        help="Path to the source NSD zip, for example NSD-1c.zip.",
    )
    parser.add_argument(
        "--vnf-zip",
        required=True,
        type=Path,
        help="Path to the source VNF bundle zip containing pcf/smf/upf.",
    )
    parser.add_argument(
        "--slice",
        required=True,
        dest="slice_name",
        help="Target Kubernetes namespace, for example slice-1 or slice-2.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("generated_descriptors"),
        help="Output base directory. Default: generated_descriptors.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete an existing run output directory if it already exists.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    manifest = render_descriptor_packages(
        nsd_zip=args.nsd_zip,
        vnf_zip=args.vnf_zip,
        slice_name=args.slice_name,
        out=args.out,
        force=args.force,
    )
    nsd_id_map = manifest["ids"]["nsd"]
    vnf_id_map = manifest["ids"]["vnf"]
    changed_fqdn_files = manifest["changed_fqdn_files"]

    print("Generated fresh descriptor packages")
    print(f"  Slice namespace: {args.slice_name}")
    print(f"  Run directory:   {manifest['outputs']['run_dir']}")
    print(f"  VNF zip:         {manifest['outputs']['vnf_zip']}")
    print(f"  NSD zip:         {manifest['outputs']['nsd_zip']}")
    print(f"  Manifest:        {manifest['outputs']['manifest']}")
    print()
    print("Descriptor ID map")
    print(f"  nsd: {nsd_id_map['old']} -> {nsd_id_map['new']}")
    for component in COMPONENTS:
        entry = vnf_id_map[component]
        print(f"  {component}: {entry['old']} -> {entry['new']}")
    print()
    if changed_fqdn_files:
        print("Patched service DNS references")
        for path in changed_fqdn_files:
            print(f"  {path}")
    else:
        print("Patched service DNS references: none found")
    print()
    print("Validation")
    print("  NSD constituent_vnfd IDs match VNF descriptor IDs")
    print(f"  VNF namespaces and service DNS references target {args.slice_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
