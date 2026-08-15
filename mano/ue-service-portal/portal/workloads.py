import fcntl
import json
import os
import re
import signal
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

from .lab_controller import LabControllerError, radio_status


METRIC_NAMES = (
    "throughput_bps",
    "rtt_ms",
    "jitter_ms",
    "packet_loss_ratio",
    "packet_size_bytes",
    "message_rate",
    "rx_bytes",
    "tx_bytes",
)


def _path():
    settings.UE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return settings.UE_RUNTIME_DIR / "telemetry.json"


@contextmanager
def telemetry_lock():
    lock_path = settings.UE_RUNTIME_DIR / "telemetry.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def read_telemetry():
    try:
        value = json.loads(_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"ues": {}}
    except (OSError, ValueError):
        return {"ues": {}}


def write_ue_telemetry(ue, values):
    with telemetry_lock():
        state = read_telemetry()
        state.setdefault("ues", {})
        current = state["ues"].get(ue, {})
        current.update(values)
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        state["ues"][ue] = current
        temporary = _path().with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(_path())


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def start_workload(ue, workload, duration):
    if workload not in ("video", "control", "sensor"):
        raise LabControllerError("Unknown workload")
    match = re.match(r"^ue-(\d+)$", ue)
    if not match:
        raise LabControllerError("Invalid UE name")
    duration = max(10, min(int(duration), 300))
    radio = radio_status()
    connected = {item["ue"] for item in radio.get("ues", []) if item.get("status") == "CONNECTED"}
    if ue not in connected:
        raise LabControllerError("{} must be CONNECTED before starting a workload".format(ue))
    current = read_telemetry().get("ues", {}).get(ue, {})
    if current.get("active") and _pid_alive(current.get("worker_pid")):
        raise LabControllerError("{} already has an active workload".format(ue))

    log_dir = Path(radio.get("run_dir") or settings.UE_RUNTIME_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "{}-{}.workload.log".format(ue, workload)
    log_handle = log_path.open("ab", buffering=0)
    worker = Path(settings.UE_PORTAL_ROOT) / "portal" / "workload_worker.py"
    process = subprocess.Popen(
        [sys.executable, str(worker), "--ue", ue, "--workload", workload, "--duration", str(duration)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(settings.UE_PORTAL_ROOT),
    )
    write_ue_telemetry(ue, {
        "active": 1,
        "status": "STARTING",
        "workload": workload,
        "duration_seconds": duration,
        "worker_pid": process.pid,
        "worker_pgid": os.getpgid(process.pid),
        "log": str(log_path),
        **{name: 0 for name in METRIC_NAMES},
    })
    return read_telemetry()["ues"][ue]


def stop_workload(ue):
    current = read_telemetry().get("ues", {}).get(ue, {})
    pid = current.get("worker_pid")
    pgid = current.get("worker_pgid")
    if _pid_alive(pid):
        target = -int(pgid or pid)
        try:
            os.kill(target, signal.SIGTERM)
        except OSError:
            pass
    write_ue_telemetry(ue, {"active": 0, "status": "STOPPED"})
    return read_telemetry().get("ues", {}).get(ue, {})


def prometheus_text():
    telemetry = read_telemetry().get("ues", {})
    radio = radio_status()
    connected = {item["ue"]: item for item in radio.get("ues", [])}
    lines = [
        "# HELP ansa_ue_connected Whether the UE has an established PDU session.",
        "# TYPE ansa_ue_connected gauge",
    ]
    all_ues = sorted(set(telemetry) | set(connected))
    for ue in all_ues:
        session = connected.get(ue, {})
        lines.append('ansa_ue_connected{{ue="{}",ip="{}"}} {}'.format(ue, session.get("ip") or "", 1 if session.get("status") == "CONNECTED" else 0))
    descriptions = {
        "throughput_bps": "Observed UE tunnel throughput in bits per second.",
        "rtt_ms": "Observed application round-trip time in milliseconds.",
        "jitter_ms": "Observed round-trip time variation in milliseconds.",
        "packet_loss_ratio": "Observed packet or message loss ratio.",
        "packet_size_bytes": "Application payload size in bytes.",
        "message_rate": "Application messages per second.",
        "rx_bytes": "UE tunnel received bytes.",
        "tx_bytes": "UE tunnel transmitted bytes.",
    }
    for name in METRIC_NAMES:
        metric = "ansa_ue_{}".format(name)
        lines.extend(["# HELP {} {}".format(metric, descriptions[name]), "# TYPE {} gauge".format(metric)])
        for ue, values in sorted(telemetry.items()):
            workload = str(values.get("workload") or "none")
            lines.append('{}{{ue="{}",workload="{}"}} {}'.format(metric, ue, workload, float(values.get(name) or 0)))
    lines.extend(["# HELP ansa_ue_workload_active Whether a real UE workload is active.", "# TYPE ansa_ue_workload_active gauge"])
    for ue, values in sorted(telemetry.items()):
        lines.append('ansa_ue_workload_active{{ue="{}",workload="{}"}} {}'.format(ue, values.get("workload") or "none", 1 if values.get("active") else 0))
    return "\n".join(lines) + "\n"
