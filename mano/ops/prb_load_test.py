#!/usr/bin/env python3
"""Generate sustained per-UE or aggregate downlink traffic for PRB demos."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NamedTuple
from urllib.request import urlopen


STATUS_URL = "http://127.0.0.1:5000/status"
KPM_METRICS_URL = "http://127.0.0.1:9108/metrics"
GRAFANA_URL = (
    "http://192.168.180.35:30030/"
    "d/auto-slice-operations?orgId=1&refresh=5s"
)

UDP_GENERATOR = r'''
import socket
import sys
import time

destination = sys.argv[1]
duration = float(sys.argv[2])
packets_per_second = float(sys.argv[3])
payload_bytes = int(sys.argv[4])

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
payload = b"P" * payload_bytes
deadline = time.monotonic() + duration
next_send = time.monotonic()
sent = 0

while time.monotonic() < deadline:
    sock.sendto(payload, (destination, 5001))
    sent += 1
    next_send += 1.0 / packets_per_second
    delay = next_send - time.monotonic()
    if delay > 0:
        time.sleep(delay)

print("sent={} destination={}".format(sent, destination))
'''


class Target(NamedTuple):
    ue_id: int
    ran_ue_id: str
    namespace: str
    ip: str
    interface: str


def read_url(url: str, timeout: float = 5) -> str:
    with urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8")


def build_targets(status: dict) -> list[Target]:
    if status.get("gnb") != "running" or status.get("scenario") != "running":
        raise RuntimeError("gNB and the four-UE scenario must be running")

    targets = []
    ues = status.get("ues", {})
    for ue_id in range(1, 5):
        ue = ues.get(str(ue_id), {})
        if ue.get("registration_state") != "registered" or ue.get("state") != "connected":
            raise RuntimeError(f"UE{ue_id} is not connected and registered")
        active = [
            session
            for session in ue.get("pdu_sessions", [])
            if session.get("state") == "active"
        ]
        if len(active) != 1:
            raise RuntimeError(
                f"UE{ue_id} must have exactly one active PDU session; found {len(active)}"
            )
        session = active[0]
        targets.append(
            Target(
                ue_id=ue_id,
                ran_ue_id=str(ue_id - 1),
                namespace=str(session["slice_id"]),
                ip=str(session["ip"]),
                interface=str(session["interface"]),
            )
        )
    return targets


def build_batches(mode: str, targets: list[Target]) -> list[list[Target]]:
    if mode == "all":
        return [targets]
    if mode == "sequential":
        return [[target] for target in targets]
    match = re.fullmatch(r"ue([1-4])", mode)
    if match:
        ue_id = int(match.group(1))
        return [[[target for target in targets if target.ue_id == ue_id][0]]]
    raise ValueError("mode must be all, sequential, or ue1..ue4")


def parse_prb_used(text: str) -> dict[tuple[str, str, str], float]:
    result = {}
    for line in text.splitlines():
        if not line.startswith("srsran_prb_used{"):
            continue
        labels = dict(re.findall(r'(\w+)="([^"]*)"', line))
        try:
            value = float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
        scope = labels.get("scope", "")
        direction = labels.get("direction", "")
        identity = labels.get("ran_ue_id", "cell")
        result[(scope, identity, direction)] = value
    return result


def require_live_kpm(metrics: str) -> None:
    if not re.search(r"^srsran_kpm_exporter_up 1(?:\.0+)?$", metrics, re.MULTILINE):
        raise RuntimeError("E2SM-KPM PRB feed is not live")


def find_upf(namespace: str) -> str:
    completed = subprocess.run(
        [
            "kubectl", "get", "pod", "-n", namespace, "-l", "app=upf-svc",
            "-o", "jsonpath={.items[0].metadata.name}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    pod = completed.stdout.strip()
    if completed.returncode or not pod:
        detail = completed.stderr.strip() or "no UPF pod returned"
        raise RuntimeError(f"cannot find UPF in {namespace}: {detail}")
    return pod


def run_sender(
    target: Target,
    pod: str,
    duration: float,
    pps: float,
    payload_bytes: int,
) -> str:
    completed = subprocess.run(
        [
            "kubectl", "exec", "-i", "-n", target.namespace, pod, "--",
            "python3", "-", target.ip, str(duration), str(pps), str(payload_bytes),
        ],
        input=UDP_GENERATOR,
        capture_output=True,
        text=True,
        timeout=duration + 30,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"UE{target.ue_id} traffic failed: {detail}")
    return completed.stdout.strip()


class PrbMonitor:
    def __init__(self):
        self.stop_event = threading.Event()
        self.maxima: dict[tuple[str, str, str], float] = {}
        self.nonzero_samples: dict[tuple[str, str, str], int] = {}
        self.samples = 0
        self.error: Exception | None = None

    def run(self) -> None:
        while not self.stop_event.wait(0.25):
            try:
                values = parse_prb_used(read_url(KPM_METRICS_URL, timeout=2))
            except Exception as exc:  # surfaced after traffic stops
                self.error = exc
                continue
            self.samples += 1
            for key, value in values.items():
                self.maxima[key] = max(self.maxima.get(key, 0.0), value)
                if value > 0:
                    self.nonzero_samples[key] = self.nonzero_samples.get(key, 0) + 1

    def stop(self) -> None:
        self.stop_event.set()


def print_summary(monitor: PrbMonitor, batch: list[Target]) -> None:
    cell_dl = monitor.maxima.get(("cell", "cell", "dl"), 0.0)
    cell_ul = monitor.maxima.get(("cell", "cell", "ul"), 0.0)
    print(f"Cell maximum: DL={cell_dl:g} PRB, UL={cell_ul:g} PRB")
    for target in batch:
        dl_key = ("ue", target.ran_ue_id, "dl")
        ul_key = ("ue", target.ran_ue_id, "ul")
        dl = monitor.maxima.get(dl_key, 0.0)
        ul = monitor.maxima.get(ul_key, 0.0)
        observed = max(
            monitor.nonzero_samples.get(dl_key, 0),
            monitor.nonzero_samples.get(ul_key, 0),
        )
        print(
            f"UE{target.ue_id} / RAN UE {target.ran_ue_id}: "
            f"DL max={dl:g}, UL max={ul:g}, nonzero samples={observed}/{monitor.samples}"
        )


def run_batch(
    batch: list[Target],
    upfs: dict[str, str],
    duration: float,
    pps: float,
    payload_bytes: int,
) -> None:
    names = ", ".join(f"UE{target.ue_id}" for target in batch)
    print(f"\nStarting {names} for {duration:g}s at {pps:g} packet/s per UE")
    monitor = PrbMonitor()
    monitor_thread = threading.Thread(target=monitor.run, daemon=True)
    monitor_thread.start()
    try:
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {
                executor.submit(
                    run_sender,
                    target,
                    upfs[target.namespace],
                    duration,
                    pps,
                    payload_bytes,
                ): target
                for target in batch
            }
            for future in as_completed(futures):
                target = futures[future]
                print(f"UE{target.ue_id}: {future.result()}")
    finally:
        time.sleep(1.5)
        monitor.stop()
        monitor_thread.join(timeout=3)
    if monitor.error and monitor.samples == 0:
        raise RuntimeError(f"cannot sample KPM metrics: {monitor.error}")
    print_summary(monitor, batch)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hold srsRAN PRB load using the four connected lab UEs"
    )
    parser.add_argument("mode", choices=("all", "sequential", "ue1", "ue2", "ue3", "ue4"))
    parser.add_argument("--duration", type=float, default=60, help="seconds per batch")
    parser.add_argument("--pps", type=float, default=100, help="1200-byte UDP packets/s per UE")
    parser.add_argument("--payload-bytes", type=int, default=1200)
    parser.add_argument("--gap", type=float, default=5, help="seconds between sequential UEs")
    args = parser.parse_args(argv)
    if args.duration <= 0 or args.pps <= 0 or args.payload_bytes < 64 or args.gap < 0:
        parser.error("duration/pps must be positive, payload >= 64, and gap >= 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    status = json.loads(read_url(STATUS_URL))
    targets = build_targets(status)
    metrics = read_url(KPM_METRICS_URL)
    require_live_kpm(metrics)
    upfs = {target.namespace: find_upf(target.namespace) for target in targets}

    print("Grafana:", GRAFANA_URL)
    for target in targets:
        print(
            f"UE{target.ue_id}: {target.ip} {target.namespace} "
            f"{target.interface} -> RAN UE {target.ran_ue_id}"
        )

    batches = build_batches(args.mode, targets)
    try:
        for index, batch in enumerate(batches):
            run_batch(batch, upfs, args.duration, args.pps, args.payload_bytes)
            if index + 1 < len(batches) and args.gap:
                print(f"Waiting {args.gap:g}s before the next UE...")
                time.sleep(args.gap)
    except KeyboardInterrupt:
        print("\nInterrupted; active kubectl senders are being terminated.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
