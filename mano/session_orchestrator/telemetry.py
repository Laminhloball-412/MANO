from __future__ import annotations

import re
import time
from pathlib import Path

from .command import run
from .models import TrafficMetrics


_LOSS = re.compile(r"([0-9.]+)% packet loss")
_RTT = re.compile(
    r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
    r"[0-9.]+/([0-9.]+)/[0-9.]+/([0-9.]+) ms"
)


def _interface_bytes(interface: str) -> int:
    root = Path("/sys/class/net") / interface / "statistics"
    return int((root / "rx_bytes").read_text()) + int((root / "tx_bytes").read_text())


def measure_ping(
    interface: str,
    gateway: str,
    duration_seconds: int,
    packet_size_bytes: int,
    message_rate_per_second: float,
) -> TrafficMetrics:
    count = max(3, int(duration_seconds * message_rate_per_second))
    interval = max(0.01, 1.0 / message_rate_per_second)
    before = _interface_bytes(interface)
    started = time.monotonic()
    result = run(
        [
            "ping", "-I", interface, "-c", str(count), "-i", f"{interval:.3f}",
            "-s", str(packet_size_bytes), "-W", "2", gateway,
        ],
        timeout=max(10, duration_seconds + count * 2 + 5),
        check=False,
    )
    elapsed = max(time.monotonic() - started, 0.001)
    after = _interface_bytes(interface)
    text = result.stdout + "\n" + result.stderr
    loss_match = _LOSS.search(text)
    rtt_match = _RTT.search(text)
    if not loss_match or not rtt_match:
        raise RuntimeError(f"unable to parse ping result on {interface}: {text[-800:]}")
    received = round(count * (1 - float(loss_match.group(1)) / 100.0))
    return TrafficMetrics(
        throughput_mbps=max(0, after - before) * 8 / elapsed / 1_000_000,
        rtt_ms=float(rtt_match.group(1)),
        jitter_ms=float(rtt_match.group(2)),
        loss_percent=float(loss_match.group(1)),
        packet_size_bytes=packet_size_bytes,
        message_rate_per_second=received / elapsed,
        samples=count,
        interface=interface,
        gateway=gateway,
    )
