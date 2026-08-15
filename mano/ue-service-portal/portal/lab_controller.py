import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings


class LabControllerError(RuntimeError):
    pass


def _runtime():
    settings.UE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return settings.UE_RUNTIME_DIR


def _state_path():
    return _runtime() / "radio_state.json"


def _load_state():
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "DISCONNECTED", "processes": [], "ues": []}


def _save_state(state):
    target = _state_path()
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(target)


@contextmanager
def _lock():
    path = _runtime() / "radio.lock"
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _alive(pid):
    try:
        pid = int(pid)
        # A finished sudo wrapper can remain as a zombie until Popen is
        # reaped. os.kill(pid, 0) still reports that PID as alive, which made
        # the old stop loop escalate signals unnecessarily.
        try:
            if Path("/proc/{}/stat".format(pid)).read_text().split()[2] == "Z":
                return False
        except (OSError, IndexError):
            pass
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _process_exists(name):
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def _spawn(name, command, log_path):
    log_handle = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=str(log_path.parent),
    )
    return {"name": name, "pid": process.pid, "pgid": os.getpgid(process.pid), "log": str(log_path), "command": command}


def _gnb_command():
    command = [
        "sudo",
        "-n",
        str(settings.GNB_DIR / "gnb"),
        "-c",
        str(settings.GNB_CONFIG),
    ]
    slicing_config = getattr(settings, "GNB_SLICING_CONFIG", None)
    if slicing_config and slicing_config.exists():
        command.extend(["-c", str(slicing_config)])
    return command


def _ensure_namespace(name):
    if not re.fullmatch(r"ue[1-3]", name):
        raise LabControllerError("Refusing to manage unexpected network namespace {}".format(name))
    listed = subprocess.run(["sudo", "-n", "ip", "netns", "list"], capture_output=True, text=True, check=True).stdout
    exists = any(line.split()[0] == name for line in listed.splitlines() if line.strip())
    if exists:
        probe = subprocess.run(
            ["sudo", "-n", "ip", "netns", "exec", name, "true"],
            capture_output=True,
            text=True,
        )
        if probe.returncode:
            subprocess.run(["sudo", "-n", "ip", "netns", "del", name], check=True)
            exists = False
    if not exists:
        subprocess.run(["sudo", "-n", "ip", "netns", "add", name], check=True)
    subprocess.run(["sudo", "-n", "ip", "netns", "exec", name, "ip", "link", "set", "lo", "up"], check=True)


def _runtime_config(ue_id, direct, target_sst=None, target_sd=None, run_dir=None):
    source = settings.UE_CONFIG_DIR / "ue{}.conf".format(ue_id)
    if not source.exists():
        raise LabControllerError("Missing {}".format(source))
    target = (run_dir or _runtime()) / "ue{}.runtime.conf".format(ue_id)
    text = source.read_text(encoding="utf-8")
    if target_sst is not None:
        text, count = re.subn(
            r"(?m)^nssai-sst\s*=.*$",
            "nssai-sst = {}".format(int(target_sst)),
            text,
            count=1,
        )
        if count != 1:
            raise LabControllerError("UE config has no nssai-sst setting")
    if target_sd is not None:
        normalized_sd = str(target_sd).upper().zfill(6)
        if not re.fullmatch(r"[0-9A-F]{6}", normalized_sd):
            raise LabControllerError("target_sd must contain exactly 6 hexadecimal characters")
        text, count = re.subn(
            r"(?m)^nssai-sd\s*=.*$",
            "nssai-sd = {}".format(normalized_sd),
            text,
            count=1,
        )
        if count != 1:
            raise LabControllerError("UE config has no nssai-sd setting")
    if direct:
        text = re.sub(
            r"(?m)^device_args\s*=.*$",
            "device_args = tx_port=tcp://127.0.0.1:2001,rx_port=tcp://127.0.0.1:2000,base_srate=11.52e6",
            text,
            count=1,
        )
    target.write_text(text, encoding="utf-8")
    return target


def _wait_for_log(process, token, timeout):
    deadline = time.monotonic() + timeout
    log_path = Path(process["log"])
    while time.monotonic() < deadline:
        if not _alive(process.get("pid")):
            raise LabControllerError("{} exited during startup; inspect {}".format(process["name"], log_path))
        try:
            if token in log_path.read_text(encoding="utf-8", errors="ignore"):
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise LabControllerError("Timed out waiting for {} in {}".format(token, log_path))


def _log_status(ue_item):
    path = Path(ue_item["log"])
    text = ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[-100000:]
    except OSError:
        pass
    ip_match = re.findall(r"IP:\s*([0-9]+(?:\.[0-9]+){3})", text)
    if "PDU Session Establishment successful" in text:
        status = "CONNECTED"
    elif any(token in text.lower() for token in ("reject", "failed", "error")) and not _alive(ue_item.get("pid")):
        status = "FAILED"
    elif _alive(ue_item.get("pid")):
        status = "CONNECTING"
    else:
        status = "STOPPED"
    return {**ue_item, "status": status, "ip": ip_match[-1] if ip_match else None}


def radio_status():
    state = _load_state()
    ues = [_log_status(item) for item in state.get("ues", [])]
    active_processes = [item for item in state.get("processes", []) if _alive(item.get("pid"))]
    if not active_processes:
        overall = "DISCONNECTED" if state.get("status") == "DISCONNECTED" else "STOPPED"
    elif ues and all(item["status"] == "CONNECTED" for item in ues):
        overall = "CONNECTED"
    elif any(item["status"] == "FAILED" for item in ues):
        overall = "DEGRADED"
    else:
        overall = "CONNECTING"
    return {**state, "status": overall, "ues": ues, "active_processes": len(active_processes)}


def connect_radio(count):
    count = int(count)
    if count not in (1, 2, 3):
        raise LabControllerError("This testbed currently supports 1, 2, or 3 UEs")
    with _lock():
        current = radio_status()
        if current.get("active_processes"):
            raise LabControllerError("A portal-managed radio session is already active")
        if _process_exists("gnb") or _process_exists("srsue"):
            raise LabControllerError("A gNB or srsUE process is already running outside the portal; stop it first")
        run_dir = _runtime() / datetime.now().strftime("run-%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=False)
        processes = []
        ue_items = []
        try:
            if count > 1:
                relay_source = settings.RAN_ROOT / ("multi_ue_scenario_2ue.py" if count == 2 else "multi_ue_scenario.py")
                relay = _spawn(
                    "relay",
                    ["/usr/bin/python3", str(settings.UE_PORTAL_ROOT / "portal" / "relay_runner.py"), str(relay_source)],
                    run_dir / "relay.log",
                )
                processes.append(relay)
                time.sleep(2)
                if not _alive(relay["pid"]):
                    raise LabControllerError("GNU Radio relay exited during startup")

            gnb = _spawn(
                "gnb",
                _gnb_command(),
                run_dir / "gnb.log",
            )
            processes.append(gnb)
            time.sleep(5)
            if not _alive(gnb["pid"]):
                raise LabControllerError("gNB exited during startup")

            for ue_id in range(1, count + 1):
                _ensure_namespace("ue{}".format(ue_id))
                config = _runtime_config(ue_id, direct=count == 1)
                item = _spawn(
                    "ue{}".format(ue_id),
                    ["sudo", "-n", str(settings.UE_BINARY), str(config)],
                    run_dir / "ue{}.log".format(ue_id),
                )
                item.update({"id": ue_id, "ue": "ue-{}".format(ue_id), "config": str(config)})
                ue_items.append(item)
                processes.append(item)
                if ue_id < count:
                    time.sleep(3)

            state = {
                "status": "CONNECTING",
                "mode": "direct" if count == 1 else "relay-{}ue".format(count),
                "requested_count": count,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "run_dir": str(run_dir),
                "processes": processes,
                "ues": ue_items,
            }
            _save_state(state)
            return radio_status()
        except Exception:
            for item in reversed(processes):
                _stop_process(item, 1)
            raise


def connect_selected_ue(ue_id, target_sst, target_sd):
    """Start one selected UE with a job-local S-NSSAI runtime configuration.

    The source ueN.conf remains unchanged. A controlled radio restart is used
    because this lab has one direct ZeroMQ path between srsUE and srsRAN gNB.
    """
    ue_id = int(ue_id)
    target_sst = int(target_sst)
    target_sd = str(target_sd).upper().zfill(6)
    if ue_id not in (1, 2, 3):
        raise LabControllerError("This testbed currently has UE1, UE2, and UE3")
    if target_sst not in (1, 2, 3) or not re.fullmatch(r"[0-9A-F]{6}", target_sd):
        raise LabControllerError("Unsupported target S-NSSAI")

    with _lock():
        previous = _load_state()
        for item in reversed(previous.get("processes", [])):
            _stop_process(item)
        if previous.get("processes"):
            time.sleep(2)

        if _process_exists("gnb") or _process_exists("srsue"):
            raise LabControllerError(
                "A gNB or srsUE process is still running outside portal control; stop it before attaching"
            )

        run_dir = _runtime() / datetime.now().strftime("intent-%Y%m%d-%H%M%S-%f")
        run_dir.mkdir(parents=True, exist_ok=False)
        processes = []
        try:
            # Keep the three fixed lab namespaces ready. Only the selected UE
            # process is started, so this does not create extra PDU sessions.
            for namespace_id in (1, 2, 3):
                _ensure_namespace("ue{}".format(namespace_id))
            config = _runtime_config(
                ue_id,
                direct=True,
                target_sst=target_sst,
                target_sd=target_sd,
                run_dir=run_dir,
            )
            gnb = _spawn(
                "gnb",
                _gnb_command(),
                run_dir / "gnb.log",
            )
            processes.append(gnb)
            # srsRAN buffers this console output when stdout is a regular file,
            # so log-token polling creates a false timeout. Use the lab's proven
            # startup delay here; the PDU-session wait remains the final check.
            time.sleep(5)
            if not _alive(gnb["pid"]):
                raise LabControllerError("gNB exited during startup; inspect {}".format(gnb["log"]))

            ue_item = _spawn(
                "ue{}".format(ue_id),
                ["sudo", "-n", str(settings.UE_BINARY), str(config)],
                run_dir / "ue{}.log".format(ue_id),
            )
            ue_item.update(
                {
                    "id": ue_id,
                    "ue": "ue-{}".format(ue_id),
                    "config": str(config),
                    "target_sst": target_sst,
                    "target_sd": target_sd,
                }
            )
            processes.append(ue_item)
            state = {
                "status": "CONNECTING",
                "mode": "intent-direct",
                "requested_count": 1,
                "selected_ue_id": ue_id,
                "target_sst": target_sst,
                "target_sd": target_sd,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "run_dir": str(run_dir),
                "processes": processes,
                "ues": [ue_item],
            }
            _save_state(state)
            return radio_status()
        except Exception:
            for item in reversed(processes):
                _stop_process(item, 1)
            raise


def _stop_process(item, grace=3):
    pid = item.get("pid")
    pgid = item.get("pgid")
    if not _alive(pid):
        return
    target = "-{}".format(pgid or pid)
    for signal_name, wait_seconds in (("INT", grace), ("TERM", 1), ("KILL", 0)):
        subprocess.run(
            ["sudo", "-n", "kill", "-{}".format(signal_name), "--", target],
            capture_output=True,
        )
        if wait_seconds:
            deadline = time.time() + wait_seconds
            while time.time() < deadline and _alive(pid):
                time.sleep(0.2)
        if not _alive(pid):
            break


def disconnect_radio():
    with _lock():
        state = _load_state()
        for item in reversed(state.get("processes", [])):
            _stop_process(item)
        state.update({
            "status": "DISCONNECTED",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "processes": [],
            "ues": [],
        })
        _save_state(state)
        return radio_status()
