import queue
import threading
import time

from flask import Flask, Response, jsonify, render_template, request


app = Flask(__name__)

status_lock = threading.Lock()
log_queue = queue.Queue()

preview_status = {
    "state": "disconnected",
    "ip": None,
}

simulation_id = 0


def add_log(message):
    log_queue.put(f"[PREVIEW] {message}")


def set_status(state, ip=None):
    with status_lock:
        preview_status["state"] = state
        preview_status["ip"] = ip


def simulation_is_current(run_id):
    with status_lock:
        return run_id == simulation_id


def simulate_connection(run_id):
    steps = [
        (
            1.2,
            "random_access",
            "[INFO] Random Access Complete",
        ),
        (
            1.5,
            "authenticating",
            "[OK] RRC Connected - authenticating subscriber",
        ),
        (
            1.7,
            "connected",
            "[OK] PDU Session Establishment successful",
        ),
    ]

    for delay, state, message in steps:
        time.sleep(delay)

        if not simulation_is_current(run_id):
            return

        ip = "10.45.0.2" if state == "connected" else None
        set_status(state, ip)
        add_log(message)

    add_log("[INFO] Preview UE is ready for data traffic")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/status")
def get_status():
    with status_lock:
        return jsonify(dict(preview_status))


@app.post("/save-config")
def save_config():
    data = request.get_json(silent=True) or {}

    required_fields = [
        "imsi",
        "k",
        "opc",
        "imei",
        "apn",
        "sst",
        "sd",
    ]

    missing_fields = [
        field
        for field in required_fields
        if not str(data.get(field, "")).strip()
    ]

    if missing_fields:
        return jsonify(
            {
                "success": False,
                "error": "Missing fields: " + ", ".join(missing_fields),
            }
        ), 400

    # Preview mode deliberately does not save or print submitted values.
    add_log("[OK] Configuration form validated in preview mode")

    return jsonify(
        {
            "success": True,
            "preview": True,
        }
    )


@app.post("/attach")
def attach():
    global simulation_id

    with status_lock:
        current_state = preview_status["state"]

        if current_state in {
            "attaching",
            "random_access",
            "authenticating",
            "connected",
        }:
            return jsonify(
                {
                    "success": False,
                    "error": "Preview UE is already active.",
                }
            ), 409

        simulation_id += 1
        run_id = simulation_id
        preview_status["state"] = "attaching"
        preview_status["ip"] = None

    add_log("[INFO] Starting safe preview simulation")
    add_log("[INFO] Attaching UE")

    thread = threading.Thread(
        target=simulate_connection,
        args=(run_id,),
        daemon=True,
    )
    thread.start()

    return jsonify(
        {
            "success": True,
            "preview": True,
        }
    )


@app.post("/detach")
def detach():
    global simulation_id

    with status_lock:
        simulation_id += 1
        preview_status["state"] = "disconnected"
        preview_status["ip"] = None

    add_log("[INFO] Preview UE detached")

    return jsonify(
        {
            "success": True,
            "preview": True,
        }
    )


@app.get("/logs/stream")
def stream_logs():
    def generate():
        while True:
            try:
                message = log_queue.get(timeout=15)
                yield f"data: {message}\n\n"
            except queue.Empty:
                yield "data: [PING]\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    print("Safe preview: http://0.0.0.0:5050")

    app.run(
        host="0.0.0.0",
        port=5050,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
