from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import SliceProfile


def interface_for(profile: SliceProfile, ue_id: int) -> str:
    """Resolve the OAI TUN name for a one-based UE identifier."""
    return re.sub(r"ue1(?=p\d+$|$)", f"ue{ue_id}", profile.tun_pattern)


@dataclass(frozen=True)
class InterfaceCounters:
    at: float
    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int


@dataclass(frozen=True)
class InterfaceSample:
    ue_id: int
    profile: SliceProfile
    interface: str
    up: bool
    sample_ready: bool = False
    rx_mbps: float = 0.0
    tx_mbps: float = 0.0
    rx_packets_per_second: float = 0.0
    tx_packets_per_second: float = 0.0
    average_rx_packet_bytes: float = 0.0
    average_tx_packet_bytes: float = 0.0


class UeTelemetryCollector:
    """Collect passive UE traffic rates from Linux OAI TUN counters.

    The collector does not generate packets. RTT, jitter and loss therefore
    require a separately labelled active probe or application instrumentation.
    """

    def __init__(
        self,
        profiles: dict[str, SliceProfile],
        ue_ids: list[int],
        sys_class_net: Path = Path("/sys/class/net"),
        clock: Callable[[], float] = time.monotonic,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        netns_path: Path = Path("/var/run/netns"),
    ):
        self.profiles = {
            profile.pdu_session_id: profile
            for profile in profiles.values()
            if profile.name != "default"
        }
        self.ue_ids = sorted(set(ue_ids))
        self._known_ue_ids = set(self.ue_ids)
        self.netns_path = netns_path
        self.sys_class_net = sys_class_net
        self.clock = clock
        self.command_runner = command_runner
        self._previous: dict[tuple[int, int], InterfaceCounters] = {}
        self._last_interface: dict[tuple[int, int], str] = {}
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._last_render = ""

    def _discover_ue_ids(self) -> None:
        try:
            namespaces = self.netns_path.iterdir()
            for namespace in namespaces:
                match = re.fullmatch(r"ue(\d+)", namespace.name)
                if match:
                    self._known_ue_ids.add(int(match.group(1)))
        except OSError:
            return

    def _read(self, interface: str) -> InterfaceCounters | None:
        root = self.sys_class_net / interface
        operstate = root / "operstate"
        if not operstate.exists():
            return None
        if operstate.read_text(encoding="utf-8").strip() == "down":
            return None
        statistics = root / "statistics"
        try:
            return InterfaceCounters(
                self.clock(),
                int((statistics / "rx_bytes").read_text(encoding="utf-8")),
                int((statistics / "tx_bytes").read_text(encoding="utf-8")),
                int((statistics / "rx_packets").read_text(encoding="utf-8")),
                int((statistics / "tx_packets").read_text(encoding="utf-8")),
            )
        except (FileNotFoundError, ValueError):
            return None

    def _read_namespaced(
        self, ue_id: int, pdu_id: int
    ) -> tuple[str, InterfaceCounters] | None:
        interface = f"tun_srsue{ue_id}{pdu_id}"
        command = [
            "sudo", "-n", "ip", "netns", "exec", f"ue{ue_id}",
            "ip", "-j", "-s", "link", "show", "dev", interface,
        ]
        try:
            result = self.command_runner(
                command, capture_output=True, text=True, timeout=2, check=False,
            )
            if result.returncode != 0:
                return None
            links = json.loads(result.stdout)
            if not links or str(links[0].get("operstate", "")).lower() == "down":
                return None
            statistics = links[0].get("stats64") or links[0].get("stats") or {}
            rx = statistics.get("rx") or {}
            tx = statistics.get("tx") or {}
            return interface, InterfaceCounters(
                self.clock(),
                int(rx["bytes"]),
                int(tx["bytes"]),
                int(rx["packets"]),
                int(tx["packets"]),
            )
        except (
            json.JSONDecodeError, KeyError, OSError, subprocess.SubprocessError,
            TypeError, ValueError,
        ):
            return None

    def collect(self) -> list[InterfaceSample]:
        samples = []
        with self._lock:
            self._discover_ue_ids()
            for ue_id in sorted(self._known_ue_ids):
                for pdu_id, profile in sorted(self.profiles.items()):
                    interface = interface_for(profile, ue_id)
                    current = self._read(interface)
                    key = (ue_id, pdu_id)
                    if current is None:
                        namespaced = self._read_namespaced(ue_id, pdu_id)
                        if namespaced is not None:
                            interface, current = namespaced
                    previous = self._previous.get(key)
                    if current is None:
                        self._previous.pop(key, None)
                        interface = self._last_interface.get(key, interface)
                        samples.append(InterfaceSample(ue_id, profile, interface, False))
                        continue
                    self._last_interface[key] = interface
                    self._previous[key] = current
                    if previous is None or current.at <= previous.at:
                        samples.append(InterfaceSample(ue_id, profile, interface, True))
                        continue
                    elapsed = current.at - previous.at
                    rx_bytes = max(0, current.rx_bytes - previous.rx_bytes)
                    tx_bytes = max(0, current.tx_bytes - previous.tx_bytes)
                    rx_packets = max(0, current.rx_packets - previous.rx_packets)
                    tx_packets = max(0, current.tx_packets - previous.tx_packets)
                    samples.append(InterfaceSample(
                        ue_id=ue_id,
                        profile=profile,
                        interface=interface,
                        up=True,
                        sample_ready=True,
                        rx_mbps=rx_bytes * 8 / elapsed / 1_000_000,
                        tx_mbps=tx_bytes * 8 / elapsed / 1_000_000,
                        rx_packets_per_second=rx_packets / elapsed,
                        tx_packets_per_second=tx_packets / elapsed,
                        average_rx_packet_bytes=(rx_bytes / rx_packets if rx_packets else 0.0),
                        average_tx_packet_bytes=(tx_bytes / tx_packets if tx_packets else 0.0),
                    ))
        return samples

    def render(self) -> str:
        if not self._render_lock.acquire(blocking=False):
            if self._last_render:
                return self._last_render
            return "# TYPE ansa_ue_collector_busy gauge\nansa_ue_collector_busy 1\n"
        try:
            lines = []
            for sample in self.collect():
                profile = sample.profile
                labels = (
                    f'ue_id="{sample.ue_id}",pdu_session_id="{profile.pdu_session_id}",'
                    f'slice_id="{profile.namespace}",sst="{profile.sst}",sd="{profile.sd}",'
                    f'dnn="{profile.dnn}",interface="{sample.interface}",source="passive"'
                )
                values = {
                    "ansa_ue_pdu_up": int(sample.up),
                    "ansa_ue_passive_sample_ready": int(sample.sample_ready),
                    "ansa_ue_rx_mbps": sample.rx_mbps,
                    "ansa_ue_tx_mbps": sample.tx_mbps,
                    "ansa_ue_rx_packets_per_second": sample.rx_packets_per_second,
                    "ansa_ue_tx_packets_per_second": sample.tx_packets_per_second,
                    "ansa_ue_average_rx_packet_bytes": sample.average_rx_packet_bytes,
                    "ansa_ue_average_tx_packet_bytes": sample.average_tx_packet_bytes,
                }
                for metric, value in values.items():
                    lines.extend(
                        (f"# TYPE {metric} gauge", f"{metric}{{{labels}}} {value}")
                    )
            rendered = "\n".join(lines) + "\n"
            self._last_render = rendered
            return rendered
        finally:
            self._render_lock.release()
