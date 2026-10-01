#!/usr/bin/env python3
"""Add or remove the ANSA OCUDU RAN scrape job in Prometheus YAML."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


BEGIN_MARKER = "  # BEGIN ANSA OCUDU RAN"
END_MARKER = "  # END ANSA OCUDU RAN"
_TARGET_PATTERN = re.compile(
    r"(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?):(?P<port>[0-9]{1,5})"
)
_SCRAPE_CONFIGS_PATTERN = re.compile(r"scrape_configs:\s*(?:#.*)?")
_SERVER_METADATA_FIELDS = (
    "creationTimestamp",
    "generation",
    "managedFields",
    "resourceVersion",
    "uid",
)


def _validate_target(target: str) -> None:
    match = _TARGET_PATTERN.fullmatch(target)
    if match is None:
        raise ValueError("target must be a safe HOST:PORT value")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise ValueError("target port must be between 1 and 65535")


def _owned_block(target: str) -> str:
    return (
        f"{BEGIN_MARKER}\n"
        "  - job_name: ocudu-ran-metrics\n"
        "    scrape_interval: 1s\n"
        "    static_configs:\n"
        f'      - targets: ["{target}"]\n'
        f"{END_MARKER}\n"
    )


def _owned_span(prometheus_yaml: str) -> tuple[int, int] | None:
    lines = prometheus_yaml.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == BEGIN_MARKER]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == END_MARKER]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise ValueError("Prometheus YAML has malformed ANSA OCUDU RAN markers")

    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets[starts[0]], offsets[ends[0] + 1]


def remove_job(prometheus_yaml: str) -> str:
    """Remove only the scrape block owned by this deployment."""

    span = _owned_span(prometheus_yaml)
    if span is None:
        return prometheus_yaml
    start, end = span
    return prometheus_yaml[:start] + prometheus_yaml[end:]


def _scrape_section_end(prometheus_yaml: str) -> int:
    lines = prometheus_yaml.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    scrape_line = None
    for index, line in enumerate(lines):
        if _SCRAPE_CONFIGS_PATTERN.fullmatch(line.rstrip("\r\n")):
            scrape_line = index
            break
    if scrape_line is None:
        raise ValueError("Prometheus YAML does not contain a top-level scrape_configs section")

    for index in range(scrape_line + 1, len(lines)):
        content = lines[index].rstrip("\r\n")
        if content and not content[0].isspace() and not content.startswith("#"):
            return offsets[index]
    return len(prometheus_yaml)


def add_job(prometheus_yaml: str, target: str) -> str:
    """Add or replace the owned scrape job without changing unrelated text."""

    _validate_target(target)
    cleaned = remove_job(prometheus_yaml)
    insertion = _scrape_section_end(cleaned)
    prefix = cleaned[:insertion]
    suffix = cleaned[insertion:]
    separator = "" if not prefix or prefix.endswith(("\n", "\r")) else "\n"
    return prefix + separator + _owned_block(target) + suffix


def replace_configmap_data(configmap_json: str, data_key: str, value: str) -> str:
    """Replace one ConfigMap data value while retaining all user-owned fields."""

    try:
        configmap = json.loads(configmap_json)
        data = configmap["data"]
        metadata = configmap["metadata"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("input must be a ConfigMap JSON object with data and metadata") from error
    if not isinstance(data, dict) or not isinstance(metadata, dict) or data_key not in data:
        raise ValueError(f"ConfigMap does not contain data key {data_key!r}")

    data[data_key] = value
    for key in _SERVER_METADATA_FIELDS:
        metadata.pop(key, None)
    return json.dumps(configmap, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("--target", required=True)
    subparsers.add_parser("remove")
    configmap_parser = subparsers.add_parser("replace-configmap-data")
    configmap_parser.add_argument("--key", required=True)
    configmap_parser.add_argument("--value-file", required=True, type=Path)
    args = parser.parse_args(argv)

    source = sys.stdin.read()
    try:
        if args.command == "add":
            output = add_job(source, args.target)
        elif args.command == "remove":
            output = remove_job(source)
        else:
            output = replace_configmap_data(
                source,
                args.key,
                args.value_file.read_text(encoding="utf-8"),
            )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
