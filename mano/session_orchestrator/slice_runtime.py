from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .command import CommandResult, run
from .models import SliceProfile


def parse_cpu(value: str) -> float:
    value = value.strip()
    return float(value[:-1]) / 1000.0 if value.endswith("m") else float(value)


def parse_memory(value: str) -> float:
    value = value.strip()
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
    for suffix, multiplier in units.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * multiplier
    return float(value)


def parse_rate_mbps(text: str) -> float | None:
    match = re.search(r"\brate\s+([0-9.]+)([KMG])?bit\b", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "").upper()
    return value * {"": 0.000001, "K": 0.001, "M": 1.0, "G": 1000.0}[unit]


@dataclass(frozen=True)
class SliceResourceSnapshot:
    healthy: bool
    ready_pods: int
    total_pods: int
    restarts: int
    cpu_usage_cores: float
    cpu_limit_cores: float
    memory_usage_bytes: float
    memory_limit_bytes: float
    downlink_enforcement_up: bool = False
    enforced_downlink_mbps: float = 0.0

    @property
    def cpu_load_ratio(self) -> float:
        return self.cpu_usage_cores / self.cpu_limit_cores if self.cpu_limit_cores else 0.0

    @property
    def memory_load_ratio(self) -> float:
        return self.memory_usage_bytes / self.memory_limit_bytes if self.memory_limit_bytes else 0.0


class SliceRuntimeCollector:
    def __init__(
        self,
        profiles: dict[str, SliceProfile],
        ttl_seconds: int = 10,
        runner: Callable[..., CommandResult] = run,
    ):
        self.profiles = {
            profile.namespace: profile for profile in profiles.values()
            if profile.name != "default"
        }
        self.ttl_seconds = ttl_seconds
        self.runner = runner
        self._lock = threading.Lock()
        self._updated = 0.0
        self._cache: dict[str, SliceResourceSnapshot] = {}

    def collect(self) -> dict[str, SliceResourceSnapshot]:
        with self._lock:
            if self._cache and time.monotonic() - self._updated < self.ttl_seconds:
                return self._cache
            pods_result = self.runner(
                ["kubectl", "get", "pods", "-A", "-o", "json"], timeout=10
            )
            top_result = self.runner(
                ["kubectl", "top", "pods", "-A", "--no-headers"],
                timeout=10, check=False,
            )
            usage = {}
            if top_result.returncode == 0:
                for line in top_result.stdout.splitlines():
                    fields = line.split()
                    if len(fields) >= 4:
                        usage[(fields[0], fields[1])] = (
                            parse_cpu(fields[2]), parse_memory(fields[3])
                        )
            document = json.loads(pods_result.stdout)
            collected = {}
            for namespace in self.profiles:
                pods = [
                    pod for pod in document.get("items", [])
                    if pod.get("metadata", {}).get("namespace") == namespace
                ]
                ready = 0
                restarts = 0
                cpu_usage = memory_usage = cpu_limit = memory_limit = 0.0
                for pod in pods:
                    statuses = pod.get("status", {}).get("containerStatuses", [])
                    pod_ready = bool(statuses) and all(item.get("ready", False) for item in statuses)
                    ready += int(pod_ready and pod.get("status", {}).get("phase") == "Running")
                    restarts += sum(item.get("restartCount", 0) for item in statuses)
                    pod_usage = usage.get((namespace, pod["metadata"]["name"]), (0.0, 0.0))
                    cpu_usage += pod_usage[0]
                    memory_usage += pod_usage[1]
                    for container in pod.get("spec", {}).get("containers", []):
                        limits = container.get("resources", {}).get("limits", {})
                        if "cpu" in limits:
                            cpu_limit += parse_cpu(limits["cpu"])
                        if "memory" in limits:
                            memory_limit += parse_memory(limits["memory"])
                upf_pods = [
                    pod for pod in pods
                    if "upf" in str(
                        pod.get("metadata", {}).get("labels", {}).get("app", "")
                    ).lower()
                ]
                enforced_rate = None
                if upf_pods:
                    qdisc = self.runner(
                        [
                            "kubectl", "exec", "-n", namespace,
                            upf_pods[0]["metadata"]["name"], "--",
                            "tc", "qdisc", "show", "dev", "ogstun",
                        ],
                        timeout=10,
                        check=False,
                    )
                    if qdisc.returncode == 0 and "qdisc tbf" in qdisc.stdout:
                        enforced_rate = parse_rate_mbps(qdisc.stdout)
                collected[namespace] = SliceResourceSnapshot(
                    healthy=bool(pods) and ready == len(pods),
                    ready_pods=ready,
                    total_pods=len(pods),
                    restarts=restarts,
                    cpu_usage_cores=cpu_usage,
                    cpu_limit_cores=cpu_limit,
                    memory_usage_bytes=memory_usage,
                    memory_limit_bytes=memory_limit,
                    downlink_enforcement_up=(
                        enforced_rate is not None
                        and abs(enforced_rate - self.profiles[namespace].downlink_limit_mbps)
                        <= max(0.05, self.profiles[namespace].downlink_limit_mbps * 0.02)
                    ),
                    enforced_downlink_mbps=enforced_rate or 0.0,
                )
            self._cache, self._updated = collected, time.monotonic()
            return collected

    def render(self) -> str:
        lines = []
        for slice_id, runtime in self.collect().items():
            profile = self.profiles[slice_id]
            service_class = profile.service_class or profile.name
            labels = (
                f'slice_id="{slice_id}",profile="{service_class}",sst="{profile.sst}",'
                f'sd="{profile.sd}",dnn="{profile.dnn}"'
            )
            values = {
                "ansa_slice_health": int(runtime.healthy),
                "ansa_slice_ready_pods": runtime.ready_pods,
                "ansa_slice_total_pods": runtime.total_pods,
                "ansa_slice_container_restarts_total": runtime.restarts,
                "ansa_slice_cpu_usage_cores": runtime.cpu_usage_cores,
                "ansa_slice_cpu_limit_cores": runtime.cpu_limit_cores,
                "ansa_slice_cpu_load_ratio": runtime.cpu_load_ratio,
                "ansa_slice_memory_usage_bytes": runtime.memory_usage_bytes,
                "ansa_slice_memory_limit_bytes": runtime.memory_limit_bytes,
                "ansa_slice_memory_load_ratio": runtime.memory_load_ratio,
                "ansa_slice_capacity_mbps": profile.capacity_mbps,
                "ansa_slice_max_pdu_sessions": profile.max_pdu_sessions,
                "ansa_slice_max_message_rate_per_second": profile.max_message_rate_per_second,
                "ansa_slice_baseline_rtt_ms": profile.baseline_rtt_ms,
                "ansa_slice_baseline_jitter_ms": profile.baseline_jitter_ms,
                "ansa_slice_baseline_loss_percent": profile.baseline_loss_percent,
                "ansa_slice_five_qi": profile.five_qi,
                "ansa_slice_session_ambr_mbps": profile.session_ambr_mbps,
                "ansa_slice_arp_priority": profile.arp_priority,
                "ansa_slice_downlink_limit_mbps": profile.downlink_limit_mbps,
                "ansa_slice_benchmark_downlink_mbps": profile.benchmark_downlink_mbps,
                "ansa_slice_downlink_enforcement_up": int(runtime.downlink_enforcement_up),
                "ansa_slice_enforced_downlink_mbps": runtime.enforced_downlink_mbps,
            }
            for metric, value in values.items():
                lines.extend((f"# TYPE {metric} gauge", f"{metric}{{{labels}}} {value}"))
        return "\n".join(lines) + "\n"
