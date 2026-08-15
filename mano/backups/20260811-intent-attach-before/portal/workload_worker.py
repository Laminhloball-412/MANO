#!/usr/bin/env python3
import argparse
import json
import math
import re
import statistics
import subprocess
import time

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ue_service_portal.settings")

import django
django.setup()

from django.conf import settings
from portal.workloads import write_ue_telemetry


def run(command, timeout=5):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def counter(namespace, interface, name):
    result = run(["sudo", "-n", "ip", "netns", "exec", namespace, "cat", "/sys/class/net/{}/statistics/{}".format(interface, name)])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Cannot read {}".format(name))
    return int(result.stdout.strip())


def base_values(rx, tx, **extra):
    values = {
        "rx_bytes": rx,
        "tx_bytes": tx,
        "throughput_bps": 0,
        "rtt_ms": 0,
        "jitter_ms": 0,
        "packet_loss_ratio": 0,
        "packet_size_bytes": 0,
        "message_rate": 0,
    }
    values.update(extra)
    return values


def video(ue, namespace, interface, duration):
    url = settings.PORTAL_DATA_URL.rstrip("/") + "/api/payload/?megabytes=512"
    curl = subprocess.Popen(["sudo", "-n", "ip", "netns", "exec", namespace, "curl", "-L", "-sS", "--max-time", str(duration), "-o", "/dev/null", url])
    previous_rx = counter(namespace, interface, "rx_bytes")
    previous_time = time.monotonic()
    deadline = previous_time + duration
    while time.monotonic() < deadline and curl.poll() is None:
        time.sleep(1)
        now = time.monotonic()
        rx = counter(namespace, interface, "rx_bytes")
        tx = counter(namespace, interface, "tx_bytes")
        bps = max(0, (rx - previous_rx) * 8 / max(now - previous_time, 0.001))
        write_ue_telemetry(ue, {"status": "RUNNING", **base_values(rx, tx, throughput_bps=bps, packet_size_bytes=1316)})
        previous_rx, previous_time = rx, now
    if curl.poll() is None:
        curl.terminate()
        curl.wait(timeout=3)


def control(ue, namespace, interface, duration):
    target = "192.168.180.35"
    sent = 0
    received = 0
    samples = []
    started = time.monotonic()
    while time.monotonic() - started < duration:
        sent += 1
        result = run(["sudo", "-n", "ip", "netns", "exec", namespace, "ping", "-n", "-c", "1", "-W", "1", target], timeout=3)
        match = re.search(r"time[=<]([0-9.]+)\s*ms", result.stdout)
        if match:
            received += 1
            samples.append(float(match.group(1)))
            samples = samples[-50:]
        rx = counter(namespace, interface, "rx_bytes")
        tx = counter(namespace, interface, "tx_bytes")
        write_ue_telemetry(ue, {"status": "RUNNING", **base_values(
            rx,
            tx,
            rtt_ms=statistics.fmean(samples) if samples else 0,
            jitter_ms=statistics.pstdev(samples) if len(samples) > 1 else 0,
            packet_loss_ratio=(sent - received) / sent,
            packet_size_bytes=64,
            message_rate=5,
        )})
        time.sleep(0.2)


def sensor(ue, namespace, interface, duration):
    url = settings.PORTAL_DATA_URL.rstrip("/") + "/api/sensor-ingest/"
    sent = 0
    received = 0
    samples = []
    started = time.monotonic()
    while time.monotonic() - started < duration:
        sent += 1
        payload = json.dumps({"device_id": ue, "sequence": sent, "temperature": 28.4})
        result = run([
            "sudo", "-n", "ip", "netns", "exec", namespace, "curl", "-sS", "-o", "/dev/null",
            "-w", "%{time_total}", "-H", "Content-Type: application/json", "-d", payload, url,
        ], timeout=5)
        if result.returncode == 0:
            received += 1
            try:
                samples.append(float(result.stdout.strip()) * 1000)
            except ValueError:
                pass
            samples = samples[-50:]
        rx = counter(namespace, interface, "rx_bytes")
        tx = counter(namespace, interface, "tx_bytes")
        write_ue_telemetry(ue, {"status": "RUNNING", **base_values(
            rx,
            tx,
            rtt_ms=statistics.fmean(samples) if samples else 0,
            jitter_ms=statistics.pstdev(samples) if len(samples) > 1 else 0,
            packet_loss_ratio=(sent - received) / sent,
            packet_size_bytes=len(payload.encode("utf-8")),
            message_rate=1,
        )})
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ue", required=True)
    parser.add_argument("--workload", choices=("video", "control", "sensor"), required=True)
    parser.add_argument("--duration", type=int, default=60)
    args = parser.parse_args()
    match = re.match(r"^ue-(\d+)$", args.ue)
    if not match:
        raise SystemExit("Invalid UE")
    ue_id = int(match.group(1))
    namespace = "ue{}".format(ue_id)
    interface = "tun_srsue{}".format(ue_id)
    try:
        {"video": video, "control": control, "sensor": sensor}[args.workload](args.ue, namespace, interface, args.duration)
        write_ue_telemetry(args.ue, {"active": 0, "status": "COMPLETED"})
    except Exception as error:
        write_ue_telemetry(args.ue, {"active": 0, "status": "FAILED", "error": str(error)})
        raise


if __name__ == "__main__":
    main()
