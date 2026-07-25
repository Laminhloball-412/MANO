#!/usr/bin/env python3
"""Repair Open5GS PFCP identity and UDP service routing for all lab slices."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone


def run(*args: str, input_data: str | None = None) -> str:
    result = subprocess.run(
        args,
        input=input_data,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}")
    return result.stdout


def kubectl_json(*args: str) -> dict:
    return json.loads(run("kubectl", *args, "-o", "json"))


def discover_slices() -> tuple[str, ...]:
    namespaces = kubectl_json("get", "namespaces").get("items", [])
    slices = [
        item["metadata"]["name"]
        for item in namespaces
        if re.fullmatch(r"slice-\d+", item["metadata"]["name"])
    ]
    return tuple(sorted(slices, key=lambda name: int(name.split("-")[1])))


def make_service_headless(namespace: str, name: str) -> None:
    existing = kubectl_json("get", "service", name, "-n", namespace)
    if existing["spec"].get("clusterIP") == "None":
        print(f"{namespace}/{name}: already headless")
        return

    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": existing["metadata"].get("labels", {}),
        },
        "spec": {
            "clusterIP": "None",
            "publishNotReadyAddresses": True,
            "selector": existing["spec"].get("selector", {}),
            "ports": existing["spec"]["ports"],
        },
    }
    run("kubectl", "delete", "service", name, "-n", namespace, "--wait=true")
    run("kubectl", "apply", "-f", "-", input_data=json.dumps(service))
    print(f"{namespace}/{name}: recreated as headless")


def make_service_clusterip(namespace: str, name: str) -> None:
    existing = kubectl_json("get", "service", name, "-n", namespace)
    if existing["spec"].get("clusterIP") != "None":
        print(f"{namespace}/{name}: already ClusterIP")
        return
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": existing["metadata"].get("labels", {}),
        },
        "spec": {
            "selector": existing["spec"].get("selector", {}),
            "ports": existing["spec"]["ports"],
        },
    }
    run("kubectl", "delete", "service", name, "-n", namespace, "--wait=true")
    run("kubectl", "apply", "-f", "-", input_data=json.dumps(service))
    print(f"{namespace}/{name}: recreated as ClusterIP")


PFCP_SERVER_RE = re.compile(
    r"(?m)^(\s{2}pfcp:\s*\n"
    r"\s{4}server:\s*\n)"
    r"\s{6}-\s+(?:address:\s*0\.0\.0\.0|dev:\s*eth0)\s*\n"
    r"(?:\s{8}advertise:\s*[^\r\n]+\s*\n)?"
)


def patch_pfcp_config(namespace: str, component: str) -> None:
    config_map_name = f"open5gs-{component}"
    config_map = kubectl_json(
        "get", "configmap", config_map_name, "-n", namespace
    )
    key = config_map_name
    config = config_map["data"][key]
    patched, count = PFCP_SERVER_RE.subn(
        lambda match: f"{match.group(1)}      - dev: eth0\n",
        config,
        count=1,
    )
    if count != 1:
        raise RuntimeError(
            f"{namespace}/{config_map_name}: expected one PFCP server block"
        )
    if patched == config:
        print(f"{namespace}/{config_map_name}: PFCP already binds eth0")
        return
    payload = json.dumps({"data": {key: patched}})
    run(
        "kubectl",
        "patch",
        "configmap",
        config_map_name,
        "-n",
        namespace,
        "--type=merge",
        "-p",
        payload,
    )
    print(f"{namespace}/{config_map_name}: PFCP now binds dev eth0")


def deployments_for_app(namespace: str, app: str) -> list[str]:
    deployments = kubectl_json("get", "deployment", "-n", namespace)
    return [
        item["metadata"]["name"]
        for item in deployments["items"]
        if item["spec"]["template"]["metadata"]["labels"].get("app") == app
    ]


def restart_deployment(namespace: str, name: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(
        {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": timestamp
                        }
                    }
                }
            }
        }
    )
    run(
        "kubectl",
        "patch",
        "deployment",
        name,
        "-n",
        namespace,
        "--type=merge",
        "-p",
        payload,
    )
    run(
        "kubectl",
        "rollout",
        "status",
        f"deployment/{name}",
        "-n",
        namespace,
        "--timeout=180s",
    )
    print(f"{namespace}/{name}: rollout complete")


def use_recreate_strategy(namespace: str, name: str) -> None:
    payload = json.dumps(
        {"spec": {"strategy": {"type": "Recreate", "rollingUpdate": None}}}
    )
    run(
        "kubectl",
        "patch",
        "deployment",
        name,
        "-n",
        namespace,
        "--type=merge",
        "-p",
        payload,
    )
    print(f"{namespace}/{name}: rollout strategy is Recreate")


def main() -> int:
    slices = discover_slices()
    if not slices:
        raise RuntimeError("No slice-N namespaces were found")

    for namespace in slices:
        make_service_headless(namespace, "upf-svc")
        make_service_clusterip(namespace, "smf-svc")
        patch_pfcp_config(namespace, "smf")
        patch_pfcp_config(namespace, "upf")

    for namespace in slices:
        for app in ("upf-svc", "smf-svc"):
            names = deployments_for_app(namespace, app)
            if len(names) != 1:
                raise RuntimeError(
                    f"{namespace}: expected one {app} deployment, got {names}"
                )
            use_recreate_strategy(namespace, names[0])
            restart_deployment(namespace, names[0])

    amfs = deployments_for_app("share", "amf-svc")
    if len(amfs) != 1:
        raise RuntimeError(f"share: expected one AMF deployment, got {amfs}")
    restart_deployment("share", amfs[0])

    for namespace in slices:
        for service_name in ("upf-svc",):
            service = kubectl_json(
                "get", "service", service_name, "-n", namespace
            )
            endpoint = kubectl_json(
                "get", "endpoints", service_name, "-n", namespace
            )
            addresses = (
                endpoint.get("subsets", [{}])[0].get("addresses", [])
                if endpoint.get("subsets")
                else []
            )
            if service["spec"].get("clusterIP") != "None" or not addresses:
                raise RuntimeError(
                    f"{namespace}/{service_name}: invalid headless endpoint"
                )
            print(
                f"PASS {namespace}/{service_name}: "
                f"{addresses[0]['ip']} (headless)"
            )
        smf_service = kubectl_json(
            "get", "service", "smf-svc", "-n", namespace
        )
        if smf_service["spec"].get("clusterIP") in (None, "None"):
            raise RuntimeError(f"{namespace}/smf-svc: expected ClusterIP")
        print(
            f"PASS {namespace}/smf-svc: "
            f"{smf_service['spec']['clusterIP']} (ClusterIP)"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
