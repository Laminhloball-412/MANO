"""
slice_exporter.py v4 - AMF chung, filter cm_state=connected + pdu_state=active
Build: docker build -f Dockerfile.exporter -t nguyen0105/open5gs-slice-exporter:v4 .
"""
import os, json, time, threading, logging
from flask import Flask, jsonify
import requests
from prometheus_client import Gauge, CollectorRegistry, make_wsgi_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REG = CollectorRegistry()

ue_connected = Gauge(
    "open5gs_ue_connected",
    "UE connected to slice: 1=connected 0=idle/gone",
    ["slice_id", "sst", "sd", "imsi", "gnb_id"],
    registry=REG)

pdu_active = Gauge(
    "open5gs_pdu_session_active",
    "PDU session: 1=active 0=inactive",
    ["slice_id", "sst", "sd", "imsi", "dnn", "ipv4"],
    registry=REG)

ue_count = Gauge(
    "open5gs_ue_count", "Connected UE count per slice",
    ["slice_id"], registry=REG)

pdu_count = Gauge(
    "open5gs_pdu_count", "Active PDU session count per slice",
    ["slice_id"], registry=REG)

scrape_ok = Gauge(
    "open5gs_scrape_success", "Scrape status",
    ["slice_id"], registry=REG)

_cache = {}
_lock  = threading.Lock()
_known_ues:  dict[str, set] = {}
_known_pdus: dict[str, set] = {}


def fetch(addr, path):
    try:
        r = requests.get(f"http://{addr}{path}?page=-1", timeout=3)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as e:
        log.warning(f"{addr}{path}: {e}")
        return None


def build_slice_map(slices: list) -> dict:
    m = {}
    for s in slices:
        key = (str(s["sst"]), str(s["sd"]).upper())
        m[key] = s["slice_id"]
    return m


def collect_all(slices: list):
    amf_addr  = slices[0]["amf"]
    slice_map = build_slice_map(slices)

    # ── Lấy UE từ AMF (1 lần duy nhất) ───────────────────
    all_ues = fetch(amf_addr, "/ue-info")
    if all_ues is None:
        for s in slices:
            scrape_ok.labels(slice_id=s["slice_id"]).set(0)
        return

    ue_by_slice: dict[str, list] = {s["slice_id"]: [] for s in slices}

    for ue in all_ues:
        imsi   = ue.get("supi", "unknown")
        gnb_id = str(ue.get("gnb", {}).get("gnb_id", ""))
        state  = ue.get("cm_state", "idle")

        # Chỉ xử lý UE đang connected — bỏ qua idle hoàn toàn
        # idle = UE vẫn registered nhưng không có radio link
        # connected = UE đang active
        is_connected = (state == "connected")

        allowed = ue.get("allowed_slices", [])
        matched = []
        for nssai in allowed:
            key = (str(nssai.get("sst", "")), str(nssai.get("sd", "")).upper())
            if key in slice_map:
                matched.append(slice_map[key])

        if not matched:
            log.warning(f"UE {imsi} không match slice: allowed={allowed}")
            continue

        for sid in matched:
            s = next((x for x in slices if x["slice_id"] == sid), None)
            if not s:
                continue
            sst = str(s["sst"])
            sd  = s["sd"]

            # Set 1 nếu connected, 0 nếu idle
            ue_connected.labels(
                slice_id=sid, sst=sst, sd=sd,
                imsi=imsi, gnb_id=gnb_id
            ).set(1 if is_connected else 0)

            # Chỉ đưa vào list nếu connected
            if is_connected:
                ue_by_slice[sid].append({
                    "imsi": imsi, "gnb_id": gnb_id,
                    "cm_state": state, "slice_id": sid,
                    "sst": s["sst"], "sd": sd
                })

    # Remove UE không còn trong AMF (deregistered hoàn toàn)
    for s in slices:
        sid = s["slice_id"]
        sst = str(s["sst"])
        sd  = s["sd"]

        # UE hiện tại trong AMF (cả connected lẫn idle)
        current_imsis = set()
        for ue in all_ues:
            for nssai in ue.get("allowed_slices", []):
                key = (str(nssai.get("sst", "")), str(nssai.get("sd", "")).upper())
                if key in slice_map and slice_map[key] == sid:
                    imsi   = ue.get("supi", "unknown")
                    gnb_id = str(ue.get("gnb", {}).get("gnb_id", ""))
                    current_imsis.add((imsi, gnb_id))

        prev = _known_ues.get(sid, set())
        for (imsi, gnb_id) in prev - current_imsis:
            try:
                ue_connected.remove(sid, sst, sd, imsi, gnb_id)
                log.info(f"[{sid}] UE deregistered: {imsi}")
            except Exception:
                pass
        _known_ues[sid] = current_imsis

        # Count chỉ UE connected
        ue_count.labels(slice_id=sid).set(len(ue_by_slice[sid]))

    # ── Lấy PDU session từng slice ────────────────────────
    for s in slices:
        sid = s["slice_id"]
        sst = str(s["sst"])
        sd  = s["sd"]

        pdus = fetch(s["smf"], "/pdu-info")
        if pdus is None:
            scrape_ok.labels(slice_id=sid).set(0)
            continue

        current_pdus = set()
        pdu_list = []

        for item in pdus:
            imsi = item.get("supi", "unknown")
            # Chỉ xử lý UE không idle
            ue_activity = item.get("ue_activity", "idle")

            for pdu in item.get("pdu", []):
                dnn       = pdu.get("dnn", "")
                ipv4      = pdu.get("ipv4", "")
                pdu_state = pdu.get("pdu_state", "inactive")

                # Chỉ count PDU đang active
                is_active = (pdu_state == "active")

                pdu_active.labels(
                    slice_id=sid, sst=sst, sd=sd,
                    imsi=imsi, dnn=dnn, ipv4=ipv4
                ).set(1 if is_active else 0)

                current_pdus.add((imsi, dnn, ipv4))

                if is_active:
                    pdu_list.append({
                        "imsi": imsi, "dnn": dnn,
                        "ipv4": ipv4, "slice_id": sid,
                        "pdu_state": pdu_state
                    })

        # Remove PDU không còn trong SMF
        prev_pdus = _known_pdus.get(sid, set())
        for (imsi, dnn, ipv4) in prev_pdus - current_pdus:
            try:
                pdu_active.remove(sid, sst, sd, imsi, dnn, ipv4)
                log.info(f"[{sid}] PDU removed: {imsi} {ipv4}")
            except Exception:
                pass
        _known_pdus[sid] = current_pdus

        # Count chỉ PDU active
        pdu_count.labels(slice_id=sid).set(len(pdu_list))
        scrape_ok.labels(slice_id=sid).set(1)

        with _lock:
            _cache[sid] = {
                "ues":  ue_by_slice[sid],
                "pdus": pdu_list,
            }

        log.info(f"[{sid}] UEs={len(ue_by_slice[sid])} PDUs={len(pdu_list)}")


def loop(slices, interval):
    while True:
        try:
            collect_all(slices)
        except Exception as e:
            log.error(f"Collector error: {e}")
        time.sleep(interval)


app = Flask(__name__)

@app.route("/health")
def health(): return jsonify({"status": "ok"})

@app.route("/imsi-by-slice")
def all_slices():
    with _lock:
        return jsonify({
            sid: {
                "ue_count":  len(d["ues"]),
                "ues":       d["ues"],
                "pdu_count": len(d["pdus"]),
                "pdus":      d["pdus"],
            }
            for sid, d in _cache.items()
        })

@app.route("/imsi-by-slice/<sid>")
def one_slice(sid):
    with _lock:
        d = _cache.get(sid)
    if not d: return jsonify({"error": "not found"}), 404
    return jsonify({
        "slice_id":  sid,
        "ue_count":  len(d["ues"]),
        "ues":       d["ues"],
        "pdus":      d["pdus"],
    })

@app.route("/imsi/<imsi>")
def find_imsi(imsi):
    found = []
    with _lock:
        for d in _cache.values():
            for ue in d["ues"]:
                if ue["imsi"] in (imsi, f"imsi-{imsi}"):
                    found.append(ue)
    if not found: return jsonify({"status": "not_found"}), 404
    return jsonify({"imsi": imsi, "status": "connected", "slices": found})

app.wsgi_app = DispatcherMiddleware(
    app.wsgi_app, {"/metrics": make_wsgi_app(registry=REG)}
)

if __name__ == "__main__":
    cfg_file = os.getenv("CONFIG_FILE", "/config/slices.json")
    interval = int(os.getenv("SCRAPE_INTERVAL", "15"))
    port     = int(os.getenv("PORT", "8080"))

    with open(cfg_file) as f:
        slices = json.load(f)

    log.info(f"Loaded {len(slices)} slice(s): {[s['slice_id'] for s in slices]}")
    log.info(f"Slice map: { {(str(s['sst']), str(s['sd']).upper()): s['slice_id'] for s in slices} }")

    collect_all(slices)
    threading.Thread(target=loop, args=(slices, interval), daemon=True).start()

    log.info(f"Exporter running on :{port}")
    run_simple("0.0.0.0", port, app.wsgi_app)