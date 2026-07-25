from flask import Flask, render_template, request, jsonify, Response
import subprocess
import threading
import signal
import re
import os
import queue

app = Flask(__name__)

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ── Config ────────────────────────────────────────────────────
SRSUE_DIR  = "/home/ubuntu/ran/srsRAN_4G/build/srsue/src"
SRSUE_BIN  = "./srsue"
UE_CONF    = "/home/ubuntu/ran/srsRAN_4G/build/srsue/src/uetest.conf"
NETNS_NAME = "ue1"

# ── State ─────────────────────────────────────────────────────
ue_process  = None
ue_starting = False
ue_process_lock = threading.Lock()
ue_logs     = []
ue_status   = {"state": "disconnected", "ip": None}
log_queue   = queue.Queue()

# ── Helpers ───────────────────────────────────────────────────
def add_log(line):
    ue_logs.append(line)
    log_queue.put(line)
    if len(ue_logs) > 200:
        ue_logs.pop(0)

def clear_log_queue():
    while True:
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break


def update_conf(key, value, content):
    pattern = rf'(?im)^\s*{key}\s*=.*'
    if re.search(pattern, content):
        return re.sub(pattern, f"{key}  = {value}", content)
    else:
        return re.sub(r'(?im)(\[usim\])', rf'\1\n{key}  = {value}', content)

def read_ue_ip():
    import time

    command = [
        "sudo", "ip", "netns", "exec", NETNS_NAME,
        "ip", "-4", "-o", "addr", "show",
        "dev", "tun_srsue1"
    ]

    for _ in range(10):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=3
            )

            match = re.search(
                r"\binet\s+(\d+(?:\.\d+){3})/",
                result.stdout
            )

            if result.returncode == 0 and match:
                return match.group(1)

        except (subprocess.TimeoutExpired, OSError):
            pass

        time.sleep(0.25)

    return None

def run_ue():
    global ue_process, ue_status, ue_starting
    try:
        ue_process = subprocess.Popen(
            ["sudo", SRSUE_BIN, "uetest.conf"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=SRSUE_DIR
        )

        with ue_process_lock:
            ue_starting = False

        for line in ue_process.stdout:
            line = line.strip()
            if not line:
                continue
            add_log(line)

            if "Attaching UE" in line:
                ue_status["state"] = "attaching"
            elif "Random Access Complete" in line:
                ue_status["state"] = "random_access"
            elif "RRC Connected" in line:
                ue_status["state"] = "authenticating"
            elif "PDU Session Establishment successful" in line:
                m = re.search(r"IP:\s*(\d+\.\d+\.\d+\.\d+)", line)
                ue_status["state"] = "connected"

                if m:
                    ue_status["ip"] = m.group(1)
                else:
                    ue_status["ip"] = read_ue_ip()
            elif "Received RRC Release" in line:
                if ue_status["state"] != "connected":
                    ue_status["state"] = "error"
                    add_log(
                        "[ERROR] RRC released before PDU session"
                    )

        ue_status["state"] = "disconnected"
        ue_status["ip"] = None
        add_log("[INFO] srsUE process ended")

    except Exception as e:
        with ue_process_lock:
            ue_starting = False

        add_log(f"[ERROR] {e}")
        ue_status["state"] = "error"

# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/save-config", methods=["POST"])
def save_config():
    data = request.json or {}
    k    = data.get("k", "").strip()
    opc  = data.get("opc", "").strip()
    imei = data.get("imei", "").strip()
    apn  = data.get("apn", "").strip()
    sd   = data.get("sd", "").strip()
    sst  = data.get("sst", "").strip()
    imsi = data.get("imsi", "").strip()
    if not all([k, opc, imei, apn, sd, sst, imsi]):
        return jsonify({"success": False, "error": "All fields are required"}), 400

    if not os.path.exists(UE_CONF):
        return jsonify({"success": False, "error": f"Config file not found: {UE_CONF}"}), 404

    try:
        with open(UE_CONF, "r") as f:
            content = f.read()

        content = update_conf("k",    k,   content)
        content = update_conf("opc",  opc, content)
        content = update_conf("imei", imei, content)
        content = update_conf("imsi", imsi, content)
        # APN is in [nas] section
        apn_pattern = r'(?im)^\s*apn\s*=.*'
        if re.search(apn_pattern, content):
            content = re.sub(apn_pattern, f"apn  = {apn}", content)
        else:
            content = re.sub(r'(?im)(\[nas\])', rf'\1\napn  = {apn}', content)
        content = update_conf("nssai-sst", sst, content)
        content = update_conf("nssai-sd",  sd,  content)
        with open(UE_CONF, "w") as f:
            f.write(content)

        return jsonify({"success": True, "message": "Config saved to uetest.conf"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/attach", methods=["POST"])
def attach():
    global ue_process, ue_logs, ue_status, ue_starting

    with ue_process_lock:
        process_running = (
            ue_process is not None
            and ue_process.poll() is None
        )

        if ue_starting or process_running:
            return jsonify(
                {
                    "success": False,
                    "error": "UE already running"
                }
            ), 409

        ue_starting = True

    ue_logs = []
    clear_log_queue()
    ue_status = {"state": "attaching", "ip": None}
# ── Cleanup từ lần trước ──────────────────────────
    add_log("[INFO] Cleaning up previous session...")

    # Xóa tun interface cũ bên trong namespace
    subprocess.run(
        ["sudo", "ip", "netns", "exec", NETNS_NAME,
         "ip", "link", "delete", "tun_srsue1"],
        capture_output=True, text=True
    )

    # Xóa namespace cũ
    subprocess.run(
        ["sudo", "ip", "netns", "delete", NETNS_NAME],
        capture_output=True, text=True
    )

    # Đợi một chút để ZMQ ports giải phóng
    import time
    time.sleep(1)
    # Step 1: create network namespace
    add_log(f"[INFO] Creating network namespace: {NETNS_NAME}")
    ns_result = subprocess.run(
        ["sudo", "ip", "netns", "add", NETNS_NAME],
        capture_output=True, text=True
    )
    if ns_result.returncode != 0 and "File exists" not in ns_result.stderr:
        add_log(f"[WARN] netns: {ns_result.stderr.strip()}")
    else:
        add_log(f"[OK] Network namespace '{NETNS_NAME}' ready")

    # Step 2: start srsUE in background
    add_log("[INFO] Starting srsUE...")
    threading.Thread(target=run_ue, daemon=True).start()

    return jsonify({"success": True, "message": "Attaching UE..."})


@app.route("/detach", methods=["POST"])
def detach():
    global ue_process, ue_status

    if ue_process and ue_process.poll() is None:
        ue_process.send_signal(signal.SIGTERM)
        add_log("[INFO] UE detaching...")

        # Đợi tối đa 5 giây cho process kết thúc
        try:
            ue_process.wait(timeout=5)
            add_log("[OK] UE stopped cleanly")
        except subprocess.TimeoutExpired:
            # Force kill nếu không dừng được
            ue_process.kill()
            ue_process.wait()
            add_log("[WARN] Force killed srsUE")

    ue_status["state"] = "disconnected"
    ue_status["ip"]    = None
    return jsonify({"success": True, "message": "UE detached"})


@app.route("/status")
def status():
    return jsonify(ue_status)


@app.route("/logs")
def logs():
    return jsonify({"logs": ue_logs[-50:]})


@app.route("/logs/stream")
def logs_stream():
    """Server-Sent Events for live log streaming"""
    def generate():
        while True:
            try:
                line = log_queue.get(timeout=30)
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield "data: [PING]\n\n"

    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    print("5G UE Web Manager running at http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
