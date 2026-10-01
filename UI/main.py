from copy import deepcopy
from flask import Flask, render_template, request, jsonify, Response
import ipaddress
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time

try:
    from pymongo import MongoClient
except ImportError:  # Keep the web UI available and report the missing dependency.
    MongoClient = None


app = Flask(__name__)

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)


# ── Runtime configuration ─────────────────────────────────────
APP_DIR = os.path.dirname(os.path.abspath(__file__))

SRSUE_DIR = "/home/ubuntu/ran/srsRAN_4G/build/srsue/src"
SRSUE_BIN = "./srsue"

GNB_DIR = "/home/ubuntu/ran/srsRAN_project/build/apps/gnb"
GNB_BIN = "./gnb"
GNB_CONF = "gnb1.yaml"

SCENARIO_DIR = APP_DIR
SCENARIO_BIN = "/usr/bin/python3"
SCENARIO_FILE = "dynamic_ue_scenario.py"

UE_MULTI_CONF_TEMPLATE = os.path.join(SRSUE_DIR, "ue{}_multi.conf")
UE_CONF_TEMPLATE = os.path.join(SRSUE_DIR, "ue{}.conf")
UE_LIMIT = int(os.getenv("UI_MAX_UES", "8"))
NETNS_PREFIX = "ue"

OPEN5GS_MONGO_URI = os.getenv(
    "OPEN5GS_MONGO_URI", "mongodb://10.105.204.200:27017/open5gs"
)
OPEN5GS_MONGO_DB = os.getenv("OPEN5GS_MONGO_DB", "open5gs")
OPEN5GS_AUTO_SYNC = os.getenv("OPEN5GS_AUTO_SYNC", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
OPEN5GS_SYNC_INTERVAL = max(5, int(os.getenv("OPEN5GS_SYNC_INTERVAL", "10")))

KUBECTL_BIN = shutil.which("kubectl")
KUBE_CONFIG = os.getenv("KUBECONFIG", "/home/ubuntu/.kube/config")


# Verified against gNB, AMF, NSSF and per-slice SMF/UPF ConfigMaps.
SLICE_CATALOG = {
    "slice-1": {
        "id": "slice-1",
        "name": "eMBB",
        "sst": 1,
        "sd": "000001",
        "dnn": "srsapn",
        "ipv4_subnet": "10.45.1.0/24",
        "core_namespace": "slice-1",
        "description": "Enhanced mobile broadband",
    },
    "slice-2": {
        "id": "slice-2",
        "name": "URLLC",
        "sst": 2,
        "sd": "000001",
        "dnn": "srsapn",
        "ipv4_subnet": "10.45.2.0/24",
        "core_namespace": "slice-2",
        "description": "Ultra-reliable low-latency communication",
    },
    "slice-3": {
        "id": "slice-3",
        "name": "mMTC",
        "sst": 3,
        "sd": "000001",
        "dnn": "srsapn",
        "ipv4_subnet": "10.45.3.0/24",
        "core_namespace": "slice-3",
        "description": "Massive machine-type communication",
    },
}


def _initial_ue_state(ue_id):
    slice_ids = list(SLICE_CATALOG)
    default_slice_id = slice_ids[(ue_id - 1) % len(slice_ids)]
    return {
        "state": "disconnected",
        "registration_state": "disconnected",
        "ip": None,
        "selected_slices": [deepcopy(SLICE_CATALOG[default_slice_id])],
        "pdu_sessions": [],
    }


# ── Shared state ──────────────────────────────────────────────
running_processes = {"ues": {}, "gnb": None, "scenario": None}
ue_states = {}

start_lock = threading.Lock()
profile_lock = threading.Lock()
state_lock = threading.RLock()
log_lock = threading.Lock()
slice_health_lock = threading.Lock()
open5gs_sync_lock = threading.Lock()

is_starting = False
start_cancel_event = threading.Event()
log_queue = queue.Queue()
all_logs = []
slice_health_cache = {"checked_at": 0.0, "values": {}}
open5gs_sync_status = {
    "state": "waiting" if OPEN5GS_AUTO_SYNC else "disabled",
    "message": "Waiting for the first Open5GS subscriber sync"
    if OPEN5GS_AUTO_SYNC else "Automatic Open5GS sync is disabled",
    "last_sync": None,
    "core_subscribers": 0,
    "local_profiles": 0,
    "imported": 0,
    "skipped": 0,
}


# ── Helpers ───────────────────────────────────────────────────
def add_log(prefix, line):
    entry = f"{prefix} {line}"
    with log_lock:
        all_logs.append(entry)
        if len(all_logs) > 500:
            all_logs.pop(0)
    log_queue.put(entry)


def clear_logs():
    with log_lock:
        all_logs.clear()
    while True:
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break


def process_running(proc):
    return proc is not None and proc.poll() is None


def require_running(proc, label):
    exit_code = proc.poll()
    if exit_code is not None:
        raise RuntimeError(
            f"{label} exited during startup (code {exit_code}); check Live logs for the cause"
        )


def normalize_slice_id(value):
    value = str(value or "").strip().lower()
    if value in SLICE_CATALOG:
        return value
    if value.isdigit() and f"slice-{value}" in SLICE_CATALOG:
        return f"slice-{value}"
    return None


def configured_ue_ids():
    """Return local srsUE profiles. ueN_multi.conf alone does not define a UE."""
    try:
        names = os.listdir(SRSUE_DIR)
    except OSError:
        return sorted(ue_states)
    ids = []
    for name in names:
        match = re.fullmatch(r"ue(\d+)\.conf", name)
        if match:
            ue_id = int(match.group(1))
            if 1 <= ue_id <= UE_LIMIT:
                ids.append(ue_id)
    return sorted(set(ids))


def sync_ue_states():
    ids = configured_ue_ids()
    with state_lock:
        for ue_id in ids:
            ue_states.setdefault(ue_id, _initial_ue_state(ue_id))
    return ids


def update_key_in_section(content, section, key, value):
    section_pattern = rf"(?ims)(^\s*\[{re.escape(section)}\]\s*$\n?)(.*?)(?=^\s*\[|\Z)"
    if not re.search(section_pattern, content):
        raise ValueError(f"Missing [{section}] section")

    def replace_in_section(match):
        heading, body = match.group(1), match.group(2)
        key_pattern = rf"(?im)^\s*{re.escape(key)}\s*=.*$"
        replacement = f"{key} = {value}"
        if re.search(key_pattern, body):
            body = re.sub(key_pattern, replacement, body, count=1)
        else:
            body = f"{replacement}\n{body}"
        return f"{heading}{body}"

    return re.sub(section_pattern, replace_in_section, content, count=1)


def remove_key_from_section(content, section, key):
    """Remove an active key from one INI section without touching other sections."""
    section_pattern = rf"(?ims)(^\s*\[{re.escape(section)}\]\s*$\n?)(.*?)(?=^\s*\[|\Z)"
    if not re.search(section_pattern, content):
        raise ValueError(f"Missing [{section}] section")

    def remove_from_section(match):
        heading, body = match.group(1), match.group(2)
        key_pattern = rf"(?im)^\s*{re.escape(key)}\s*=.*(?:\n|\Z)"
        return f"{heading}{re.sub(key_pattern, '', body)}"

    return re.sub(section_pattern, remove_from_section, content, count=1)


def configure_usim_and_nas(
    content, imsi, k, operator_key_type, operator_key, dnn, pdu_type, write_nas=True
):
    """Write one soft-USIM identity and, when present, legacy NAS settings."""
    other_key = "op" if operator_key_type == "opc" else "opc"
    content = update_key_in_section(content, "usim", "mode", "soft")
    content = update_key_in_section(content, "usim", "algo", "milenage")
    content = update_key_in_section(content, "usim", "imsi", imsi)
    content = update_key_in_section(content, "usim", "k", k)
    content = remove_key_from_section(content, "usim", other_key)
    content = update_key_in_section(content, "usim", operator_key_type, operator_key)
    if write_nas:
        content = update_key_in_section(content, "nas", "apn", dnn)
        content = update_key_in_section(content, "nas", "apn_protocol", pdu_type)
    return content


def write_conf_atomic(conf_path, content):
    conf_dir = os.path.dirname(conf_path)
    original_mode = os.stat(conf_path).st_mode & 0o777 if os.path.exists(conf_path) else 0o600
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=conf_dir, prefix=".ue-ui-", delete=False
    ) as temp_file:
        temp_file.write(content)
        temp_path = temp_file.name
    try:
        os.chmod(temp_path, original_mode)
        os.replace(temp_path, conf_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def configure_ue_slices(ue_id, slice_ids):
    normalized_ids = list(dict.fromkeys(sid for sid in slice_ids if sid in SLICE_CATALOG))
    slices_info = [SLICE_CATALOG[sid] for sid in normalized_ids]
    if not slices_info:
        raise ValueError(f"No valid slices found for UE {ue_id}")

    # Keep the legacy single-session profile aligned with the first selection.
    single_path = UE_CONF_TEMPLATE.format(ue_id)
    if not os.path.exists(single_path):
        raise ValueError(f"Missing local profile: {single_path}")
    with open(single_path, "r", encoding="utf-8") as conf_file:
        single_content = conf_file.read()
    single_content = update_key_in_section(single_content, "nas", "apn", slices_info[0]["dnn"])
    single_content = update_key_in_section(single_content, "slicing", "nssai-sst", slices_info[0]["sst"])
    single_content = update_key_in_section(single_content, "slicing", "nssai-sd", slices_info[0]["sd"])
    write_conf_atomic(single_path, single_content)

    # The custom srsUE reads comma-separated lists for multi-PDU. Write the exact
    # dashboard selection instead of launching a file permanently fixed to 1,2,3.
    multi_path = UE_MULTI_CONF_TEMPLATE.format(ue_id)
    if len(slices_info) > 1 and not os.path.exists(multi_path):
        raise ValueError(f"Missing multi-PDU profile: {multi_path}")
    if os.path.exists(multi_path):
        with open(multi_path, "r", encoding="utf-8") as conf_file:
            multi_content = conf_file.read()
        multi_content = update_key_in_section(
            multi_content, "slicing", "apn_list", ", ".join(item["dnn"] for item in slices_info)
        )
        multi_content = update_key_in_section(
            multi_content, "slicing", "apn_protocol_list", ", ".join("ipv4" for _ in slices_info)
        )
        multi_content = update_key_in_section(
            multi_content, "slicing", "nssai_sst_list", ", ".join(str(item["sst"]) for item in slices_info)
        )
        multi_content = update_key_in_section(
            multi_content, "slicing", "nssai_sd_list", ", ".join(item["sd"] for item in slices_info)
        )
        write_conf_atomic(multi_path, multi_content)

    with state_lock:
        state = ue_states.setdefault(ue_id, _initial_ue_state(ue_id))
        state["selected_slices"] = deepcopy(slices_info)
        if state.get("state") == "disconnected":
            state["pdu_sessions"] = []

def configure_subscriber(ue_id, imsi, k, operator_key_type, operator_key, dnn, pdu_type):
    # Update both single-session and multi-PDU local profiles.
    conf_paths = [
        UE_CONF_TEMPLATE.format(ue_id),
        UE_MULTI_CONF_TEMPLATE.format(ue_id)
    ]
    
    for conf_path in conf_paths:
        if not os.path.exists(conf_path):
            continue # Bỏ qua nếu file multi chưa được tạo/không tồn tại

        with open(conf_path, "r", encoding="utf-8") as conf_file:
            content = conf_file.read()
        is_multi = conf_path.endswith("_multi.conf")
        content = configure_usim_and_nas(
            content, imsi, k, operator_key_type, operator_key, dnn, pdu_type,
            write_nas=not is_multi,
        )
        if is_multi:
            content = update_key_in_section(content, "slicing", "apn_list", dnn)
            content = update_key_in_section(content, "slicing", "apn_protocol_list", pdu_type)
        write_conf_atomic(conf_path, content)


def _replace_device_ports(content, ue_id):
    downlink_port = 2000 + ue_id * 100
    uplink_port = downlink_port + 1
    device_pattern = r"(?im)^(\s*device_args\s*=\s*).*$"
    match = re.search(device_pattern, content)
    if not match:
        raise ValueError("Template is missing rf.device_args")
    old_value = match.group(0).split("=", 1)[1].strip()
    old_value = re.sub(r"tx_port=tcp://127\.0\.0\.1:\d+", f"tx_port=tcp://127.0.0.1:{uplink_port}", old_value)
    old_value = re.sub(r"rx_port=tcp://127\.0\.0\.1:\d+", f"rx_port=tcp://127.0.0.1:{downlink_port}", old_value)
    return re.sub(device_pattern, lambda m: f"{m.group(1)}{old_value}", content, count=1)


def _template_path(multi=False):
    suffix = "_multi.conf" if multi else ".conf"
    candidates = []
    try:
        for name in os.listdir(SRSUE_DIR):
            if re.fullmatch(rf"ue\d+{re.escape(suffix)}", name):
                candidates.append(os.path.join(SRSUE_DIR, name))
    except OSError:
        pass
    if not candidates:
        raise ValueError(f"No {'multi-PDU ' if multi else ''}UE configuration template found")
    return sorted(candidates)[0]


def _read_config_value(path, section, key):
    try:
        with open(path, "r", encoding="utf-8") as config_file:
            content = config_file.read()
    except OSError:
        return None
    section_match = re.search(
        rf"(?ims)^\s*\[{re.escape(section)}\]\s*$\n(.*?)(?=^\s*\[|\Z)", content
    )
    if not section_match:
        return None
    key_match = re.search(rf"(?im)^\s*{re.escape(key)}\s*=\s*(.*?)\s*$", section_match.group(1))
    return key_match.group(1) if key_match else None


def create_local_ue_profile(imsi, k, operator_key_type, operator_key, dnn, pdu_type):
    ids = configured_ue_ids()
    if len(ids) >= UE_LIMIT:
        raise ValueError(f"Local UE limit reached ({UE_LIMIT})")
    ue_id = next(candidate for candidate in range(1, UE_LIMIT + 1) if candidate not in ids)

    for existing_id in ids:
        existing_imsi = _read_config_value(UE_CONF_TEMPLATE.format(existing_id), "usim", "imsi")
        if existing_imsi == imsi:
            raise ValueError(f"IMSI is already assigned to UE {existing_id}")

    created_paths = []
    try:
        for multi, target_template in (
            (False, UE_CONF_TEMPLATE),
            (True, UE_MULTI_CONF_TEMPLATE),
        ):
            source_path = _template_path(multi=multi)
            target_path = target_template.format(ue_id)
            if os.path.exists(target_path):
                raise ValueError(f"Refusing to overwrite existing profile: {target_path}")
            with open(source_path, "r", encoding="utf-8") as source_file:
                content = source_file.read()
            content = _replace_device_ports(content, ue_id)
            content = update_key_in_section(content, "gw", "netns", f"{NETNS_PREFIX}{ue_id}")
            content = update_key_in_section(content, "gw", "ip_devname", f"tun_srsue{ue_id}")
            content = configure_usim_and_nas(
                content, imsi, k, operator_key_type, operator_key, dnn, pdu_type,
                write_nas=not multi,
            )
            if multi:
                content = update_key_in_section(content, "slicing", "apn_list", dnn)
                content = update_key_in_section(content, "slicing", "apn_protocol_list", pdu_type)
            write_conf_atomic(target_path, content)
            created_paths.append(target_path)
    except Exception:
        for created_path in created_paths:
            try:
                os.unlink(created_path)
            except OSError:
                pass
        raise

    try:
        with state_lock:
            ue_states[ue_id] = _initial_ue_state(ue_id)
        configure_ue_slices(ue_id, [list(SLICE_CATALOG)[(ue_id - 1) % len(SLICE_CATALOG)]])
    except Exception:
        with state_lock:
            ue_states.pop(ue_id, None)
        for created_path in created_paths:
            try:
                os.unlink(created_path)
            except OSError:
                pass
        raise
    return ue_id


def _lab_is_active():
    return is_starting or any(
        process_running(proc)
        for proc in [
            running_processes["gnb"],
            running_processes["scenario"],
            *running_processes["ues"].values(),
        ]
    )


def _safe_sync_error(error):
    """Remove MongoDB credentials if a driver error includes the configured URI."""
    message = str(error)
    return re.sub(r"(mongodb(?:\+srv)?://)[^/@\s]+@", r"\1***@", message)[:500]


def _set_open5gs_sync_status(**values):
    with open5gs_sync_lock:
        open5gs_sync_status.update(values)
        return deepcopy(open5gs_sync_status)


def _normalize_sd(value):
    if value is None:
        return None
    if isinstance(value, int):
        return f"{value:06x}"
    raw = str(value).strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    return raw.zfill(6) if re.fullmatch(r"[0-9a-f]{1,6}", raw) else None


def _catalog_slices_from_subscriber(document):
    selected = []
    for item in document.get("slice") or []:
        if not isinstance(item, dict):
            continue
        try:
            sst = int(item.get("sst"))
        except (TypeError, ValueError):
            continue
        sd = _normalize_sd(item.get("sd"))
        for slice_id, catalog_item in SLICE_CATALOG.items():
            if catalog_item["sst"] == sst and (
                sd is None or catalog_item["sd"].lower() == sd
            ):
                selected.append(slice_id)
    return list(dict.fromkeys(selected))


def _subscriber_dnn(document):
    allowed_dnns = {item["dnn"] for item in SLICE_CATALOG.values()}
    for slice_item in document.get("slice") or []:
        if not isinstance(slice_item, dict):
            continue
        for session in slice_item.get("session") or []:
            if not isinstance(session, dict):
                continue
            dnn = str(session.get("name") or session.get("dnn") or "").strip().lower()
            if dnn in allowed_dnns:
                return dnn
    return next(iter(allowed_dnns))


def _local_imsi_map():
    result = {}
    for ue_id in configured_ue_ids():
        imsi = _read_config_value(UE_CONF_TEMPLATE.format(ue_id), "usim", "imsi")
        if imsi:
            result[str(imsi).strip()] = ue_id
    return result


def _fetch_open5gs_subscribers():
    if MongoClient is None:
        raise RuntimeError(
            "PyMongo is not installed; run ./venv/bin/pip install -r requirements.txt"
        )
    client = MongoClient(
        OPEN5GS_MONGO_URI,
        serverSelectionTimeoutMS=2500,
        connectTimeoutMS=2500,
        socketTimeoutMS=5000,
        appname="5g-slice-ue-manager",
    )
    try:
        client.admin.command("ping")
        projection = {
            "_id": 0,
            "imsi": 1,
            "security.k": 1,
            "security.op": 1,
            "security.opc": 1,
            "slice.sst": 1,
            "slice.sd": 1,
            "slice.session.name": 1,
            "slice.session.dnn": 1,
        }
        return list(client[OPEN5GS_MONGO_DB]["subscribers"].find({}, projection))
    finally:
        client.close()


def sync_open5gs_subscribers():
    """Import missing core subscribers as local UE profiles, without changing Open5GS."""
    if _lab_is_active():
        return _set_open5gs_sync_status(
            state="paused",
            message="Stop the lab before importing new Open5GS subscribers",
        )
    if not open5gs_sync_lock.acquire(blocking=False):
        with open5gs_sync_lock:
            return deepcopy(open5gs_sync_status)

    try:
        # We hold the sync lock here, so update the shared object directly.
        open5gs_sync_status.update(state="syncing", message="Reading Open5GS subscribers")
        documents = _fetch_open5gs_subscribers()
        imported = 0
        skipped = 0

        with profile_lock:
            local_imsis = _local_imsi_map()
            for document in sorted(documents, key=lambda item: str(item.get("imsi", ""))):
                imsi = str(document.get("imsi", "")).strip()
                if not re.fullmatch(r"\d{5,15}", imsi) or imsi in local_imsis:
                    continue
                if len(configured_ue_ids()) >= UE_LIMIT:
                    skipped += 1
                    continue

                security = document.get("security") or {}
                k = str(security.get("k") or "").strip()
                opc = str(security.get("opc") or "").strip()
                op = str(security.get("op") or "").strip()
                if not re.fullmatch(r"[0-9a-fA-F]{32}", k):
                    skipped += 1
                    continue
                if re.fullmatch(r"[0-9a-fA-F]{32}", opc):
                    operator_key_type, operator_key = "opc", opc
                elif re.fullmatch(r"[0-9a-fA-F]{32}", op):
                    operator_key_type, operator_key = "op", op
                else:
                    skipped += 1
                    continue

                ue_id = create_local_ue_profile(
                    imsi,
                    k,
                    operator_key_type,
                    operator_key,
                    _subscriber_dnn(document),
                    "ipv4",
                )
                selected_slices = _catalog_slices_from_subscriber(document)
                if selected_slices:
                    configure_ue_slices(ue_id, selected_slices)
                local_imsis[imsi] = ue_id
                imported += 1
                add_log(
                    "[SYNC]",
                    f"Imported Open5GS subscriber as UE {ue_id} with {len(selected_slices) or 1} slice profile(s)",
                )

        local_count = len(configured_ue_ids())
        message = (
            f"Imported {imported} new UE profile(s)"
            if imported else "Local UE profiles are up to date"
        )
        if skipped:
            message += f"; skipped {skipped} invalid or over-limit subscriber(s)"
        open5gs_sync_status.update(
            state="ok",
            message=message,
            last_sync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            core_subscribers=len(documents),
            local_profiles=local_count,
            imported=imported,
            skipped=skipped,
        )
        return deepcopy(open5gs_sync_status)
    except Exception as error:
        message = _safe_sync_error(error)
        open5gs_sync_status.update(
            state="error",
            message=message,
            last_sync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        add_log("[SYNC]", f"Open5GS subscriber sync failed: {message}")
        return deepcopy(open5gs_sync_status)
    finally:
        open5gs_sync_lock.release()


def open5gs_auto_sync_worker():
    while True:
        sync_open5gs_subscribers()
        time.sleep(OPEN5GS_SYNC_INTERVAL)


def validate_ue_ids(values):
    available_ids = sync_ue_states()
    if values is None:
        return available_ids
    try:
        ue_ids = sorted({int(value) for value in values})
    except (TypeError, ValueError):
        raise ValueError("ue_ids must be a list of integers")
    if not ue_ids or any(ue_id not in available_ids for ue_id in ue_ids):
        raise ValueError(f"UE IDs must be configured local profiles: {available_ids}")
    return ue_ids


def read_core_pods():
    if not KUBECTL_BIN or not os.path.isfile(KUBE_CONFIG):
        return None
    try:
        result = subprocess.run(
            [KUBECTL_BIN, f"--kubeconfig={KUBE_CONFIG}", "get", "pods", "-A", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout).get("items", [])
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def get_slice_health():
    now = time.monotonic()
    with slice_health_lock:
        if now - slice_health_cache["checked_at"] < 10:
            return deepcopy(slice_health_cache["values"])
        pods = read_core_pods()
        values = {}
        for slice_id, slice_info in SLICE_CATALOG.items():
            namespace = slice_info["core_namespace"]
            if pods is None:
                smf_ready = None
                upf_ready = None
            else:
                namespace_pods = [
                    pod for pod in pods if pod.get("metadata", {}).get("namespace") == namespace
                ]

                def component_ready(component):
                    matches = []
                    for pod in namespace_pods:
                        metadata = pod.get("metadata", {})
                        app_label = metadata.get("labels", {}).get("app", "").lower()
                        pod_name = metadata.get("name", "").lower()
                        if component in app_label or component in pod_name:
                            statuses = pod.get("status", {}).get("containerStatuses") or []
                            matches.append(bool(statuses) and all(item.get("ready") for item in statuses))
                    return bool(matches) and all(matches)

                smf_ready = component_ready("smf")
                upf_ready = component_ready("upf")
            if smf_ready is True and upf_ready is True:
                status = "available"
            elif smf_ready is False or upf_ready is False:
                status = "unavailable"
            else:
                status = "configured"
            values[slice_id] = {
                "status": status,
                "smf_ready": smf_ready,
                "upf_ready": upf_ready,
            }
        slice_health_cache["checked_at"] = now
        slice_health_cache["values"] = values
        return deepcopy(values)


def public_slice_catalog():
    health = get_slice_health()
    slices = []
    for slice_id, configured in SLICE_CATALOG.items():
        item = deepcopy(configured)
        item.update(health.get(slice_id, {"status": "configured"}))
        slices.append(item)
    return slices


# ── Process and log handling ──────────────────────────────────
def run_gnb():
    proc = running_processes["gnb"]
    if not proc:
        return
    for line in proc.stdout:
        line = line.strip()
        if line:
            add_log("[gNB]", line)
    add_log("[gNB]", "process ended")


def run_scenario():
    proc = running_processes["scenario"]
    if not proc:
        return
    for line in proc.stdout:
        line = line.strip()
        if line:
            add_log("[SCENARIO]", line)
    add_log("[SCENARIO]", "process ended")


def _slice_for_ip(ip_address, selected_slices):
    try:
        address = ipaddress.ip_address(ip_address)
    except ValueError:
        return None
    for selected in selected_slices:
        try:
            if address in ipaddress.ip_network(selected["ipv4_subnet"], strict=False):
                return selected
        except (KeyError, ValueError):
            continue
    return None


def _session_id_from_interface(ue_id, tun_interface):
    base_name = f"tun_srsue{ue_id}"
    if tun_interface == base_name:
        return 1
    if tun_interface.startswith(base_name):
        suffix = tun_interface[len(base_name):]
        if suffix.isdigit():
            return int(suffix)
    return None


def _find_interface_for_ip(ue_id, ip_address):
    netns = f"{NETNS_PREFIX}{ue_id}"
    fallback = f"tun_srsue{ue_id}"
    for attempt in range(5):
        try:
            result = subprocess.run(
                ["sudo", "ip", "netns", "exec", netns, "ip", "-o", "-4", "addr", "show"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            for output_line in result.stdout.splitlines():
                fields = output_line.split()
                if len(fields) >= 4 and fields[2] == "inet" and fields[3].split("/", 1)[0] == ip_address:
                    return fields[1].strip(":")
        except (OSError, subprocess.SubprocessError):
            break
        if attempt < 4:
            time.sleep(0.15)
    return fallback


def _session_from_state(ue_id, ip_address, pdu_id, tun_interface):
    state = ue_states.setdefault(ue_id, _initial_ue_state(ue_id))
    selected_slices = state.get("selected_slices") or [deepcopy(SLICE_CATALOG["slice-1"])]
    selected = _slice_for_ip(ip_address, selected_slices)
    if selected is None:
        slice_index = max(0, min(pdu_id - 1, len(selected_slices) - 1))
        selected = selected_slices[slice_index]
    return {
        "session_id": pdu_id,
        "slice_id": selected["id"],
        "slice_name": selected["name"],
        "sst": selected["sst"],
        "sd": selected["sd"],
        "dnn": selected["dnn"],
        "ip": ip_address,
        "interface": tun_interface, # Lấy tên TUN thật
        "state": "active",
    }


def _upsert_pdu_session(ue_id, ip_address, tun_interface):
    state = ue_states.setdefault(ue_id, _initial_ue_state(ue_id))
    sessions = state.setdefault("pdu_sessions", [])

    # A repeated console line for the same IP/interface updates the existing
    # session instead of inflating the dashboard counter.
    base_interface = f"tun_srsue{ue_id}"
    interface_is_specific = bool(tun_interface and tun_interface != base_interface)
    for existing in sessions:
        if existing.get("ip") == ip_address or (
            interface_is_specific and existing.get("interface") == tun_interface
        ):
            session_id = existing.get("session_id") or 1
            existing.update(_session_from_state(ue_id, ip_address, session_id, tun_interface))
            return existing

    session_id = _session_id_from_interface(ue_id, tun_interface)
    used_ids = {item.get("session_id") for item in sessions}
    if session_id is None or session_id in used_ids:
        session_id = next(candidate for candidate in range(1, len(sessions) + 2) if candidate not in used_ids)
    new_session = _session_from_state(ue_id, ip_address, session_id, tun_interface)
    sessions.append(new_session)
    sessions.sort(key=lambda item: (item.get("session_id", 999), item.get("ip") or ""))
    return new_session


def count_active_pdu_sessions(states):
    unique_sessions = set()
    for ue_id, state in states.items():
        for session in state.get("pdu_sessions", []):
            if session.get("state") != "active":
                continue
            identity = session.get("ip") or session.get("interface") or f"id:{session.get('session_id')}"
            unique_sessions.add((int(ue_id), identity))
    return len(unique_sessions)

def run_ue(ue_id):
    prefix = f"[UE-{ue_id}]"
    proc = running_processes["ues"].get(ue_id)
    if not proc:
        return
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        add_log(prefix, line)
        if "PDU Session Establishment successful" in line:
            ip_match = re.search(r"(?:IP(?:v4)?(?: address)?\s*[:=]\s*)(\d+\.\d+\.\d+\.\d+)", line, re.I)
            if ip_match:
                ip_address = ip_match.group(1)
                tun_interface = _find_interface_for_ip(ue_id, ip_address)

                with state_lock:
                    state = ue_states.setdefault(ue_id, _initial_ue_state(ue_id))
                    state["state"] = "connected"
                    state["registration_state"] = "registered"
                    if not state.get("ip"):
                        state["ip"] = ip_address
                    _upsert_pdu_session(ue_id, ip_address, tun_interface)
            continue
        with state_lock:
            state = ue_states.setdefault(ue_id, _initial_ue_state(ue_id))
            if "Attaching UE" in line:
                state["state"] = "attaching"
                state["registration_state"] = "registering"
            elif "Random Access Complete" in line:
                state["state"] = "random_access"
            elif "RRC Connected" in line:
                state["state"] = "authenticating"
            elif "RRC NR reconfiguration successful" in line:
                state["registration_state"] = "registered"
                if state.get("pdu_sessions"):
                    state["state"] = "connected"
            elif "Received RRC Release" in line:
                was_connected = state.get("state") == "connected"
                state["state"] = "disconnected" if was_connected else "failed"
                state["registration_state"] = "disconnected"
                state["ip"] = None
                for session in state.get("pdu_sessions", []):
                    session["state"] = "inactive"
                    session["ip"] = None
                if not was_connected:
                    add_log(prefix, "[ERROR] RRC released before PDU session")

    with state_lock:
        state = ue_states.setdefault(ue_id, _initial_ue_state(ue_id))
        state["state"] = "disconnected"
        state["registration_state"] = "disconnected"
        state["ip"] = None
        state["pdu_sessions"] = []
    add_log(prefix, "process ended")


def stop_process(proc, label):
    if not process_running(proc):
        return
    try:
        subprocess.run(
            ["sudo", "kill", "-TERM", "--", f"-{proc.pid}"], capture_output=True, timeout=2
        )
        proc.wait(timeout=4)
    except (OSError, subprocess.SubprocessError):
        try:
            subprocess.run(
                ["sudo", "kill", "-KILL", "--", f"-{proc.pid}"], capture_output=True, timeout=2
            )
        except (OSError, subprocess.SubprocessError):
            pass
    add_log("[SYS]", f"{label} stopped")


def delete_namespace(ue_id):
    subprocess.run(
        ["sudo", "ip", "netns", "delete", f"{NETNS_PREFIX}{ue_id}"],
        capture_output=True,
        text=True,
    )


def stop_all():
    add_log("[SYS]", "Stopping UI-managed processes...")
    for ue_id, proc in list(running_processes["ues"].items()):
        stop_process(proc, f"UE {ue_id}")
    stop_process(running_processes["gnb"], "gNB")
    stop_process(running_processes["scenario"], "GNU Radio scenario")

    running_processes["ues"].clear()
    running_processes["gnb"] = None
    running_processes["scenario"] = None
    known_ue_ids = sorted(set(sync_ue_states()) | set(running_processes["ues"]) | set(ue_states))
    for ue_id in known_ue_ids:
        delete_namespace(ue_id)

    with state_lock:
        for ue_id in known_ue_ids:
            # Lưu lại danh sách slices đã chọn trước khi reset state
            selected_slices = deepcopy(ue_states[ue_id].get("selected_slices"))
            ue_states[ue_id] = _initial_ue_state(ue_id)
            if selected_slices:
                ue_states[ue_id]["selected_slices"] = selected_slices
    add_log("[SYS]", "UI-managed processes and UE namespaces cleaned")

def signal_handler(_sig, _frame):
    print("\n[INFO] Stopping UI-managed lab processes...")
    start_cancel_event.set()
    stop_all()
    raise SystemExit(0)


# ── Routes ────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/slices")
def api_slices():
    return jsonify({
        "slices": public_slice_catalog(),
        "source": "gNB + AMF + NSSF + per-slice SMF/UPF",
        "note": "Availability is network-level; subscriber authorization is confirmed when a PDU session succeeds.",
    })


@app.route("/api/ue-slices", methods=["POST"])
def save_ue_slices():
    if any(process_running(proc) for proc in running_processes["ues"].values()):
        return jsonify({"success": False, "error": "Stop active UEs before changing slice selection"}), 409
    data = request.get_json(silent=True) or {}
    selections = data.get("selections") or {}
    available_ids = sync_ue_states()
    saved = []
    try:
        for raw_ue_id, raw_slice_data in selections.items():
            ue_id = int(raw_ue_id)
            if ue_id not in available_ids:
                raise ValueError(f"Invalid UE ID: {ue_id}")
            
            # Handle both single string and array of strings from frontend
            if isinstance(raw_slice_data, list):
                slice_ids = [normalize_slice_id(s) for s in raw_slice_data if normalize_slice_id(s)]
            else:
                s_id = normalize_slice_id(raw_slice_data)
                slice_ids = [s_id] if s_id else []

            if not slice_ids:
                raise ValueError(f"Invalid slice for UE {ue_id}")
                
            configure_ue_slices(ue_id, slice_ids)
            saved.append({"ue_id": ue_id, "slice_ids": slice_ids})
    except (OSError, ValueError) as error:
        return jsonify({"success": False, "error": str(error)}), 400
    if not saved:
        return jsonify({"success": False, "error": "No UE slice selections supplied"}), 400
    return jsonify({"success": True, "saved": saved})

def _validated_subscriber_payload():
    data = request.get_json(silent=True) or {}
    imsi = str(data.get("imsi", "")).strip()
    k = str(data.get("k", "")).strip()
    operator_key_type = str(data.get("operator_key_type", "")).strip().lower()
    operator_key = str(data.get("operator_key", "")).strip()
    dnn = str(data.get("dnn", "")).strip().lower()
    pdu_type = str(data.get("pdu_type", "")).strip().lower()

    if not re.fullmatch(r"\d{5,15}", imsi):
        raise ValueError("IMSI must contain 5 to 15 digits")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", k):
        raise ValueError("K must contain exactly 32 hexadecimal characters")
    if operator_key_type not in {"op", "opc"}:
        raise ValueError("USIM operator key type must be OPc or OP")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", operator_key):
        raise ValueError(f"{operator_key_type.upper()} must contain exactly 32 hexadecimal characters")
    allowed_dnns = {item["dnn"] for item in SLICE_CATALOG.values()}
    if dnn not in allowed_dnns:
        raise ValueError(f"DNN/APN must match the configured slice catalog: {sorted(allowed_dnns)}")
    if pdu_type != "ipv4":
        raise ValueError("Only IPv4 PDU sessions are enabled on this testbed")
    return imsi, k, operator_key_type, operator_key, dnn, pdu_type


@app.route("/api/open5gs-sync", methods=["GET", "POST"])
def api_open5gs_sync():
    if request.method == "POST":
        result = sync_open5gs_subscribers()
    else:
        with open5gs_sync_lock:
            result = deepcopy(open5gs_sync_status)
    result.update({
        "enabled": OPEN5GS_AUTO_SYNC,
        "interval_seconds": OPEN5GS_SYNC_INTERVAL,
    })
    if result["state"] == "paused":
        return jsonify(result), 409
    if result["state"] == "error":
        return jsonify(result), 503
    return jsonify(result)


@app.route("/api/ues", methods=["GET", "POST"])
def api_ues():
    if request.method == "GET":
        ids = sync_ue_states()
        return jsonify({
            "ue_ids": ids,
            "limit": UE_LIMIT,
            "profiles": [
                {
                    "ue_id": ue_id,
                    "single_config": os.path.basename(UE_CONF_TEMPLATE.format(ue_id)),
                    "multi_config": os.path.basename(UE_MULTI_CONF_TEMPLATE.format(ue_id)),
                    "multi_ready": os.path.exists(UE_MULTI_CONF_TEMPLATE.format(ue_id)),
                }
                for ue_id in ids
            ],
            "core_provisioning": False,
            "open5gs_auto_sync": OPEN5GS_AUTO_SYNC,
        })
    if is_starting or any(
        process_running(proc)
        for proc in [running_processes["gnb"], running_processes["scenario"], *running_processes["ues"].values()]
    ):
        return jsonify({"success": False, "error": "Stop the lab before adding a local UE profile"}), 409
    try:
        imsi, k, operator_key_type, operator_key, dnn, pdu_type = _validated_subscriber_payload()
        with profile_lock:
            ue_id = create_local_ue_profile(
                imsi, k, operator_key_type, operator_key, dnn, pdu_type
            )
    except (OSError, ValueError) as error:
        return jsonify({"success": False, "error": str(error)}), 400
    add_log("[SYS]", f"Local UE {ue_id} profile created ({operator_key_type.upper()}, {dnn}, {pdu_type})")
    return jsonify({
        "success": True,
        "ue_id": ue_id,
        "message": f"UE {ue_id} created locally from the manual fallback form.",
    }), 201


@app.route("/save-config", methods=["POST"])
def save_config():
    """Compatibility alias: the old endpoint now adds one UE and never overwrites UE 1."""
    return api_ues()

@app.route("/attach", methods=["POST"])
def attach():
    global is_starting
    data = request.get_json(silent=True) or {}
    try:
        ue_ids = validate_ue_ids(data.get("ue_ids"))
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 400

    with start_lock:
        infrastructure_running = process_running(running_processes["gnb"]) or process_running(running_processes["scenario"])
        if is_starting or infrastructure_running:
            return jsonify({"success": False, "error": "Lab scenario is already running"}), 409
        is_starting = True
        start_cancel_event.clear()

    def start_sequence():
        global is_starting
        try:
            stop_all()
            clear_logs()
            if start_cancel_event.is_set():
                raise RuntimeError("Start cancelled")
            for ue_id in ue_ids:
                netns = f"{NETNS_PREFIX}{ue_id}"
                result = subprocess.run(
                    ["sudo", "ip", "netns", "add", netns], capture_output=True, text=True
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or f"Unable to create {netns}")
                add_log("[SYS]", f"netns {netns} ready")

            if start_cancel_event.is_set():
                raise RuntimeError("Start cancelled")
            selected_ues = ",".join(str(ue_id) for ue_id in ue_ids)
            scenario_command = [
                SCENARIO_BIN, "-s", SCENARIO_FILE, "--ues", selected_ues,
            ]
            add_log(
                "[SCENARIO]",
                f"Starting dynamic RF paths for UE(s): {selected_ues}",
            )
            scenario_proc = subprocess.Popen(
                scenario_command, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, cwd=SCENARIO_DIR, start_new_session=True
            )
            running_processes["scenario"] = scenario_proc
            threading.Thread(target=run_scenario, daemon=True).start()
            time.sleep(1)
            require_running(scenario_proc, "GNU Radio scenario")

            if start_cancel_event.is_set():
                raise RuntimeError("Start cancelled")
            add_log("[gNB]", f"Starting: sudo {GNB_BIN} -c {GNB_CONF}")
            gnb_proc = subprocess.Popen(
                ["sudo", GNB_BIN, "-c", GNB_CONF], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, cwd=GNB_DIR, start_new_session=True
            )
            running_processes["gnb"] = gnb_proc
            threading.Thread(target=run_gnb, daemon=True).start()
            add_log("[SYS]", "Waiting 3 seconds for gNB to connect to AMF...")
            time.sleep(3)
            require_running(gnb_proc, "gNB")
            for ue_id in ue_ids:
                if start_cancel_event.is_set():
                    raise RuntimeError("Start cancelled")
                
                selected_slices = ue_states[ue_id]["selected_slices"]
                is_multi_pdu = len(selected_slices) > 1
                
                # Choose configuration file based on PDU count
                if is_multi_pdu:
                    conf_name = f"ue{ue_id}_multi.conf"
                    slice_names = ", ".join([s['name'] for s in selected_slices])
                    add_log(f"[UE-{ue_id}]", f"Starting MULTI-PDU mode on slices: {slice_names}")
                else:
                    conf_name = f"ue{ue_id}.conf"
                    selected = selected_slices[0]
                    add_log(
                        f"[UE-{ue_id}]",
                        f"Starting on {selected['name']} (SST {selected['sst']}, SD {selected['sd']})",
                    )
                
                proc = subprocess.Popen(
                    ["sudo", SRSUE_BIN, conf_name], stdout=subprocess.PIPE, # uses the chosen conf_name
                    stderr=subprocess.STDOUT, text=True, cwd=SRSUE_DIR, start_new_session=True
                )
                running_processes["ues"][ue_id] = proc
                with state_lock:
                    ue_states[ue_id]["state"] = "attaching"
                    ue_states[ue_id]["registration_state"] = "registering"
                    ue_states[ue_id]["ip"] = None
                    ue_states[ue_id]["pdu_sessions"] = []
                threading.Thread(target=run_ue, args=(ue_id,), daemon=True).start()
                time.sleep(1)
                require_running(proc, f"UE {ue_id}")
            require_running(scenario_proc, "GNU Radio scenario")
            require_running(gnb_proc, "gNB")
            add_log("[SYS]", f"Lab started with {len(ue_ids)} UE(s)")
        except Exception as error:
            add_log("[SYS]", f"[ERROR] Start failed: {error}")
            stop_all()
        finally:
            with start_lock:
                is_starting = False

    threading.Thread(target=start_sequence, daemon=True).start()
    return jsonify({"success": True, "ue_ids": ue_ids})


@app.route("/detach/<int:ue_id>", methods=["POST"])
def detach_ue(ue_id):
    if ue_id not in sync_ue_states():
        return jsonify({"success": False, "error": "Invalid UE ID"}), 404
    other_active_ues = [
        active_ue_id
        for active_ue_id, proc in running_processes["ues"].items()
        if active_ue_id != ue_id and process_running(proc)
    ]
    if other_active_ues and process_running(running_processes["scenario"]):
        add_log(
            "[SYS]",
            "Changing the active UE set requires rebuilding RF paths; stopping the lab safely",
        )
        stop_all()
        return jsonify({
            "success": True,
            "message": "Lab stopped safely. Select the remaining UEs and start again.",
        })
    stop_process(running_processes["ues"].pop(ue_id, None), f"UE {ue_id}")
    delete_namespace(ue_id)
    with state_lock:
        # Lưu lại danh sách slices đã chọn trước khi reset state của UE này
        selected_slices = deepcopy(ue_states[ue_id].get("selected_slices"))
        ue_states[ue_id] = _initial_ue_state(ue_id)
        if selected_slices:
            ue_states[ue_id]["selected_slices"] = selected_slices
    add_log(f"[UE-{ue_id}]", "Detached")
    return jsonify({"success": True})

@app.route("/stop-scenario", methods=["POST"])
def stop_scenario():
    start_cancel_event.set()
    stop_all()
    return jsonify({"success": True, "message": "Lab stopped"})


@app.route("/status")
def status():
    configured_ids = sync_ue_states()
    with state_lock:
        public_states = {ue_id: deepcopy(ue_states[ue_id]) for ue_id in configured_ids}
    active_sessions = count_active_pdu_sessions(public_states)
    return jsonify({
        "ues": public_states,
        "configured_ue_ids": configured_ids,
        "ue_limit": UE_LIMIT,
        "gnb": "running" if process_running(running_processes["gnb"]) else "stopped",
        "scenario": "running" if process_running(running_processes["scenario"]) else "stopped",
        "starting": is_starting,
        "summary": {
            "registered_ues": sum(
                1 for state in public_states.values() if state.get("registration_state") == "registered"
            ),
            "active_pdu_sessions": active_sessions,
            "configured_slices": len(SLICE_CATALOG),
            "configured_ues": len(configured_ids),
        },
    })


@app.route("/logs/stream")
def logs_stream():
    def generate():
        with log_lock:
            history = list(all_logs)
        for line in history:
            yield f"data: {line}\n\n"
        while True:
            try:
                yield f"data: {log_queue.get(timeout=30)}\n\n"
            except queue.Empty:
                yield "data: [PING]\n\n"
    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal_handler)
    if OPEN5GS_AUTO_SYNC:
        threading.Thread(
            target=open5gs_auto_sync_worker,
            name="open5gs-subscriber-sync",
            daemon=True,
        ).start()
    app_port = int(os.getenv("PORT", "5000"))
    print(f"5G UE Manager running at http://0.0.0.0:{app_port}")
    app.run(host="0.0.0.0", port=app_port, debug=False, threaded=True)
