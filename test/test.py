import requests
from flask import Flask, jsonify
from pymongo import MongoClient

app = Flask(__name__)

def get_amf_active_sessions(amf_url):
    """Lấy danh sách IMSI đang connected từ AMF metrics"""
    try:
        resp = requests.get(f"{amf_url}/metrics", timeout=2)
        active = []
        for line in resp.text.splitlines():
            if "imsi" in line and "amf_session" in line:
                # parse IMSI từ label
                active.append(line)
        return active
    except:
        return []

@app.route('/imsi-by-slice')
def imsi_by_slice():
    # Lấy subscribers từ MongoDB
    client = MongoClient("mongodb://...")
    subscribers = client.open5gs.subscribers.find()
    
    result = []
    for sub in subscribers:
        imsi = sub.get("imsi")
        # Check AMF xem có session active không
        # Nếu có → "connected", không → "registered"
        status = "registered"  # default
        
        result.append({
            "imsi": imsi,
            "status": status,
            ...
        })
    
    return jsonify(result)
