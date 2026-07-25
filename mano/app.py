from flask import Flask, jsonify
from pymongo import MongoClient
import urllib.request
import os
import re

app = Flask(__name__)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongodb-svc.slice-1.svc.cluster.local:27017")
AMF_METRICS_URL = os.getenv("AMF_METRICS_URL", "http://amf-svc.slice-1.svc.cluster.local:9090/metrics")

def get_sessions_per_snssai():
    try:
        with urllib.request.urlopen(AMF_METRICS_URL, timeout=2) as r:
            text = r.read().decode()
        sessions = {}
        for line in text.splitlines():
            if line.startswith("fivegs_amffunction_rm_registeredsubnbr{"):
                m = re.search(r'snssai="([^"]+)"', line)
                if m:
                    snssai = m.group(1)
                    count = int(float(line.split("} ")[1].strip()))
                    sessions[snssai] = count
        return sessions
    except:
        return {}

@app.route("/imsi-by-slice")
def imsi_by_slice():
    client = MongoClient(MONGO_URI)
    db = client["open5gs"]
    sessions_per_snssai = get_sessions_per_snssai()
    snssai_to_imsis = {}

    for sub in db.subscribers.find({}, {"imsi": 1, "slice": 1, "_id": 0}):
        imsi = sub.get("imsi", "")
        for s in sub.get("slice", []):
            sst = str(s.get("sst", ""))
            sd = s.get("sd", "000000")
            snssai = f"{sst}-{sd}"
            if snssai not in snssai_to_imsis:
                snssai_to_imsis[snssai] = []
            snssai_to_imsis[snssai].append(imsi)

    result = []
    for snssai, imsis in snssai_to_imsis.items():
        sst = snssai.split("-")[0]
        slice_type = {"1":"eMBB","2":"URLLC","3":"mMTC"}.get(sst, f"SST{sst}")
        connected_count = sessions_per_snssai.get(snssai, 0)
        for i, imsi in enumerate(imsis):
            result.append({
                "imsi": imsi,
                "snssai": snssai,
                "slice_type": slice_type,
                "status": "connected" if i < connected_count else "registered"
            })

    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
