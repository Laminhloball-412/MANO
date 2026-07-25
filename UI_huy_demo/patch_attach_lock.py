from pathlib import Path


path = Path("/home/ubuntu/UI_huy_demo/main.py")
text = path.read_text(encoding="utf-8")

old_state = '''ue_process  = None
ue_logs     = []
ue_status   = {"state": "disconnected", "ip": None}
log_queue   = queue.Queue()
'''

new_state = '''ue_process  = None
ue_starting = False
ue_process_lock = threading.Lock()
ue_logs     = []
ue_status   = {"state": "disconnected", "ip": None}
log_queue   = queue.Queue()
'''

old_global = '''def run_ue():
    global ue_process, ue_status
'''

new_global = '''def run_ue():
    global ue_process, ue_status, ue_starting
'''

old_popen_end = '''            cwd=SRSUE_DIR
        )
        for line in ue_process.stdout:
'''

new_popen_end = '''            cwd=SRSUE_DIR
        )

        with ue_process_lock:
            ue_starting = False

        for line in ue_process.stdout:
'''

old_exception = '''    except Exception as e:
        add_log(f"[ERROR] {e}")
        ue_status["state"] = "error"
'''

new_exception = '''    except Exception as e:
        with ue_process_lock:
            ue_starting = False

        add_log(f"[ERROR] {e}")
        ue_status["state"] = "error"
'''

old_attach = '''def attach():
    global ue_process, ue_logs, ue_status

    if ue_process and ue_process.poll() is None:
        return jsonify({"success": False, "error": "UE already running"})

    ue_logs = []
'''

new_attach = '''def attach():
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
'''

replacements = [
    (old_state, new_state, "state block"),
    (old_global, new_global, "run_ue global"),
    (old_popen_end, new_popen_end, "Popen block"),
    (old_exception, new_exception, "exception block"),
    (old_attach, new_attach, "attach block"),
]

for old, new, name in replacements:
    if old not in text:
        raise SystemExit(f"Unable to find {name}")

    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Attach lock patch applied")
