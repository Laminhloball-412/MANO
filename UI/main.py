from flask import Flask, render_template, request, jsonify, Response
import subprocess
import threading
import signal
import re
import os
import queue
import time

app = Flask(__name__)

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ── Config ────────────────────────────────────────────────────
SRSUE_DIR   = "/home/ubuntu/ran/srsRAN_4G/build/srsue/src"
SRSUE_BIN   = "./srsue"

GNB_DIR     = "/home/ubuntu/ran/srsRAN_project/build/apps/gnb"
GNB_BIN     = "./gnb"
GNB_CONF    = "gnb1.yaml"

SCENARIO_DIR = "/home/ubuntu/ran"
SCENARIO_BIN = "python3"
SCENARIO_FILE = "multi_ue_scenario.py"

# UE config files — ue1.conf, ue2.conf, ue3.conf
UE_CONF_TEMPLATE = "/home/ubuntu/ran/srsRAN_4G/build/srsue/src/ue{}.conf"

MAX_UES = 3
NETNS_PREFIX = "ue"  # ue1, ue2, ue3
SST_NAMES = {1: "eMBB", 2: "URLLC", 3: "mMTC"}

# ── State ─────────────────────────────────────────────────────
running_processes = {
    "ues":      {},    # {ue_id: subprocess.Popen}
    "gnb":      None,  # subprocess.Popen
    "scenario": None,  # subprocess.Popen
}

ue_states = {}  # {ue_id: {state, ip, imsi, sst, sd, slice}}

start_lock = threading.Lock()
is_starting = False

log_queue = queue.Queue()
all_logs  = []

# ── Log helpers ───────────────────────────────────────────────
def add_log(prefix, line):
    entry = f"{prefix} {line}"
    all_logs.append(entry)
    log_queue.put(entry)
    if len(all_logs) > 500:
        all_logs.pop(0)

def clear_log_queue():
    while True:
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break


# ── Config writer ─────────────────────────────────────────────
def update_conf(key, value, content):
    pattern = rf'(?im)^\s*{key}\s*=.*'
    if re.search(pattern, content):
        return re.sub(pattern, f"{key}  = {value}", content)
    else:
        return re.sub(r'(?im)(\[usim\])', rf'\1\n{key}  = {value}', content)

# ── Process runners ───────────────────────────────────────────
def run_gnb():
    proc = running_processes["gnb"]
    if not proc: return
    for line in proc.stdout:
        line = line.strip()
        if line: add_log("[gNB]", line)
    add_log("[gNB]", "process ended")

def run_scenario():
    proc = running_processes["scenario"]
    if not proc: return
    for line in proc.stdout:
        line = line.strip()
        if line: add_log("[SCENARIO]", line)
    add_log("[SCENARIO]", "process ended")

def run_ue(ue_id: int):
    prefix = f"[UE-{ue_id}]"
    proc   = running_processes["ues"].get(ue_id)
    if not proc: return

    for line in proc.stdout:
        line = line.strip()
        if not line: continue
        add_log(prefix, line)

        st = ue_states.get(ue_id, {})
        if "Attaching UE" in line:
            st["state"] = "attaching"
        elif "Random Access Complete" in line:
            st["state"] = "random_access"
        elif "RRC Connected" in line:
            st["state"] = "authenticating"
        elif "PDU Session Establishment successful" in line:
            m = re.search(r"IP:\s*(\d+\.\d+\.\d+\.\d+)", line)
            st["state"] = "connected"
            if m: st["ip"] = m.group(1)
        elif "RRC NR reconfiguration successful" in line:
            st["state"] = "connected"
        elif "Received RRC Release" in line:
            if st.get("state") != "connected":
                st["state"] = "failed"
                add_log(prefix, "[ERROR] RRC released before PDU session")
        elif "Received RRC Release" in line and st.get("state") == "connected":
            st["state"] = "disconnected"
            st["ip"]    = None
        ue_states[ue_id] = st

    if ue_id in ue_states:
        ue_states[ue_id]["state"] = "disconnected"
        ue_states[ue_id]["ip"]    = None
    add_log(prefix, "process ended")

# ── Core stop function (Dùng cho API /stop-scenario) ─────────────────
def _stop_all():
    add_log("[SYS]", "Stopping all processes...")
    
    # 1. Dùng sudo pkill để ép tắt bằng quyền root (thay vì proc.kill vô dụng)
    subprocess.run(["sudo", "pkill", "-9", "-f", GNB_BIN], capture_output=True)
    subprocess.run(["sudo", "pkill", "-9", "-f", SRSUE_BIN], capture_output=True)
    subprocess.run(["sudo", "pkill", "-9", "-f", SCENARIO_FILE], capture_output=True)
    
    # 2. Xóa toàn bộ namespaces
    subprocess.run(["sudo", "ip", "-all", "netns", "delete"], capture_output=True)
    
    # 3. Xóa sổ log và trạng thái
    running_processes["scenario"] = None
    running_processes["gnb"] = None
    running_processes["ues"].clear()
    ue_states.clear()
    
    add_log("[SYS]", "All network namespaces deleted. Ready for new scenario.")

# ── Signal handler (Dùng khi bấm Ctrl+C ở Terminal) ──────────────────
def signal_handler(sig, frame):
    print("\n[INFO] Nhận tín hiệu tắt (Ctrl+C). Đang ép tắt hệ thống...")
    
    # Ép hệ điều hành dọn dẹp ngay lập tức (ẩn log rác bằng > /dev/null)
    os.system(f"sudo pkill -9 -f {GNB_BIN} > /dev/null 2>&1")
    os.system(f"sudo pkill -9 -f {SRSUE_BIN} > /dev/null 2>&1")
    os.system(f"sudo pkill -9 -f {SCENARIO_FILE} > /dev/null 2>&1")
    os.system("sudo ip -all netns delete > /dev/null 2>&1")
    
    print("[INFO] Đã dọn dẹp xong. Tạm biệt!")
    
    # Tử hình chính tiến trình Python này để thoát ngay lập tức khỏi Flask
    os.kill(os.getpid(), signal.SIGKILL)

# ── Routes ────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/save-config", methods=["POST"])
def save_config():
    data = request.json or {}
    k    = data.get("k",    "").strip()
    opc  = data.get("opc",  "").strip()
    imei = data.get("imei", "").strip()
    apn  = data.get("apn",  "").strip()
    sd   = data.get("sd",   "").strip()
    sst  = data.get("sst",  "").strip()
    imsi = data.get("imsi", "").strip()
    num_ues = int(data.get("num_ues", 1))

    if not all([k, opc, imei, apn, sd, sst, imsi]):
        return jsonify({"success": False, "error": "All fields are required"}), 400

    saved, errors = [], []
    for i in range(1, num_ues + 1):
        conf_path = UE_CONF_TEMPLATE.format(i)
        if not os.path.exists(conf_path):
            errors.append(f"ue{i}.conf not found")
            continue
        try:
            with open(conf_path, "r") as f: content = f.read()
            imsi_i = str(int(imsi) + (i - 1))
            content = update_conf("k",    k,      content)
            content = update_conf("opc",  opc,    content)
            content = update_conf("imei", imei,   content)
            content = update_conf("imsi", imsi_i, content)
            
            apn_pattern = r'(?im)^\s*apn\s*=.*'
            if re.search(apn_pattern, content):
                content = re.sub(apn_pattern, f"apn  = {apn}", content)
            else:
                content = re.sub(r'(?im)(\[nas\])', rf'\1\napn  = {apn}', content)
                
            content = update_conf("nssai-sst", sst, content)
            content = update_conf("nssai-sd",  sd,  content)

            with open(conf_path, "w") as f: f.write(content)
            saved.append(f"ue{i}.conf")
        except Exception as e:
            errors.append(f"ue{i}.conf: {str(e)}")

    if errors: return jsonify({"success": False, "error": "; ".join(errors)}), 500
    return jsonify({"success": True, "message": f"Config saved to: {', '.join(saved)}"})


@app.route("/attach", methods=["POST"])
def attach():
    global is_starting
    data    = request.json or {}
    num_ues = int(data.get("num_ues", 3)) # Mặc định chạy 3 UEs

    with start_lock:
        gnb_running = (running_processes["gnb"] is not None and running_processes["gnb"].poll() is None)
        if is_starting or gnb_running:
            return jsonify({"success": False, "error": "Scenario đang chạy"}), 409
        is_starting = True

    def start_sequence():
        global is_starting
        try:
            _stop_all()
            all_logs.clear()
            clear_log_queue()

            # 1. Tạo netns
            for i in range(1, num_ues + 1):
                netns = f"{NETNS_PREFIX}{i}"
                subprocess.run(["sudo", "ip", "netns", "add", netns], capture_output=True)
                add_log("[SYS]", f"netns {netns} ready")

            # 2. Khởi động gNB
            add_log("[gNB]", f"Starting: sudo {GNB_BIN} -c {GNB_CONF}")
            gnb_proc = subprocess.Popen(
                ["sudo", GNB_BIN, "-c", GNB_CONF],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=GNB_DIR
            )
            running_processes["gnb"] = gnb_proc
            threading.Thread(target=run_gnb, daemon=True).start()
            
            add_log("[SYS]", "Waiting 3s for gNB to connect to AMF...")
            time.sleep(3)

            # 3. Khởi động các srsUE
            for i in range(1, num_ues + 1):
                conf_name = f"ue{i}.conf"
                add_log(f"[UE-{i}]", f"Starting: sudo {SRSUE_BIN} {conf_name}")
                proc = subprocess.Popen(
                    ["sudo", SRSUE_BIN, conf_name],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=SRSUE_DIR
                )
                running_processes["ues"][i] = proc
                ue_states[i] = {"state": "attaching", "ip": None}
                threading.Thread(target=run_ue, args=(i,), daemon=True).start()
                time.sleep(0.5)

            # 4. Chạy Scenario
            scenario_path = os.path.join(SCENARIO_DIR, SCENARIO_FILE)
            if os.path.exists(scenario_path):
                add_log("[SCENARIO]", f"Starting: sudo  {SCENARIO_FILE}")
                scenario_proc = subprocess.Popen(
                    ["sudo",SCENARIO_BIN, SCENARIO_FILE],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=SCENARIO_DIR
                )
                running_processes["scenario"] = scenario_proc
                threading.Thread(target=run_scenario, daemon=True).start()
            
            add_log("[SYS]", f"Scenario started: {num_ues} UE(s) running")
        finally:
            with start_lock: is_starting = False

    threading.Thread(target=start_sequence, daemon=True).start()
    return jsonify({"success": True})


@app.route("/detach/<int:ue_id>", methods=["POST"])
def detach_ue(ue_id):
    netns = f"{NETNS_PREFIX}{ue_id}"
    tun_name = f"tun_srsue{ue_id}" # Đã sửa thành biến động để không bị lỗi
    
    result = subprocess.run(
        ["sudo", "ip", "netns", "exec", netns, "ip", "link", "set", tun_name, "down"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        if ue_id in ue_states:
            ue_states[ue_id]["state"] = "disconnected"
            ue_states[ue_id]["ip"]    = None
        add_log(f"[UE-{ue_id}]", "Interface down — soft detached")
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": result.stderr.strip()}), 500


@app.route("/stop-scenario", methods=["POST"])
def stop_scenario():
    _stop_all()
    return jsonify({"success": True, "message": "All stopped and cleaned up"})


@app.route("/status")
def status():
    return jsonify({
        "ues": ue_states,
        "gnb": "running" if (running_processes["gnb"] and running_processes["gnb"].poll() is None) else "stopped",
        "scenario": "running" if (running_processes["scenario"] and running_processes["scenario"].poll() is None) else "stopped",
    })

@app.route("/logs/stream")
def logs_stream():
    def generate():
        for line in list(all_logs): yield f"data: {line}\n\n"
        while True:
            try:
                line = log_queue.get(timeout=30)
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield "data: [PING]\n\n"
    return Response(generate(), mimetype="text/event-stream")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    print("5G Multi-UE Manager running at http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
