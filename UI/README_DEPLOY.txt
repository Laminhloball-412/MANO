5G SLICE & UE MANAGER — OPEN5GS AUTO-SYNC UPDATE

VERSION 2.3 OPEN5GS SUBSCRIBER AUTO-SYNC
- Reads the Open5GS subscribers collection from MongoDB every 10 seconds.
- Creates only missing local ueN.conf and ueN_multi.conf profiles.
- Maps the subscriber SST/SD list to the dashboard slice selection.
- Never writes to or deletes from Open5GS, never overwrites a local UE and never
  returns K/OP/OPc values to the browser.
- Synchronization pauses while the radio lab is running. Stop the lab to import
  a newly provisioned subscriber.
- The Subscriber page includes current sync state plus a manual Sync now button.
- The previous local form remains as a fallback.

DEFAULT DATABASE CONNECTION
OPEN5GS_MONGO_URI=mongodb://10.105.204.200:27017/open5gs
OPEN5GS_MONGO_DB=open5gs
OPEN5GS_AUTO_SYNC=1
OPEN5GS_SYNC_INTERVAL=10

These can be overridden in the environment used to launch Flask. The MongoDB
address is the current Kubernetes ClusterIP discovered on this testbed. If the
service ClusterIP changes, set OPEN5GS_MONGO_URI to the new address.

INSTALL THE NEW PYTHON DEPENDENCY
cd /home/ubuntu/UI
./venv/bin/pip install -r requirements.txt

VERSION 2.2 SUBSCRIBER SAFETY UPDATE
- Removed IMEI from the Add local UE form. Existing template IMEI is left unchanged.
- Added explicit OPc/OP selection; exactly one operator key is written.
- Added DNN/APN (currently srsapn) and PDU address type (currently IPv4).
- New profiles explicitly use soft USIM and Milenage.
- Config keys are updated only inside their intended INI section.
- Concurrent profile creation is serialized and partial files are rolled back.
- v2.2.1 supports multi-PDU templates without a [nas] section: APN and protocol
  lists are written only to [slicing] in ueN_multi.conf.

WHAT THIS VERSION FIXES
- Active PDU sessions are deduplicated by UE plus IP/interface.
- The summary card uses the authoritative backend count.
- IP-to-slice mapping uses the configured IPv4 subnet when possible.
- Multi-PDU configuration receives the exact slices selected on the dashboard.
- Slice selection uses click-to-toggle cards; Ctrl/Cmd is no longer needed.
- UE rows and live-log filters are generated from local ueN.conf profiles.
- The Subscriber page adds the next local UE profile without overwriting UE 1.
- The GNU Radio bridge accepts UE IDs 1 through UI_MAX_UES (default: 8).

SUBSCRIBER MODEL
Open5GS remains the source of truth. Provision IMSI, K, OP/OPc, slices and DNN
in Open5GS once. The sync worker reads that record and creates the matching
local simulated-USIM profile. The new files are named ueN.conf and
ueN_multi.conf and receive unique ZMQ ports and network namespace.

KNOWN RADIO/DATA-PLANE LIMITATION
The custom srsUE source can establish multiple PDU sessions and TUN interfaces,
but its current gw.cc multi-session uplink worker is incomplete. This web update
does not modify or rebuild srsUE C++ code. Validate user-plane traffic separately.

SAFE REVIEW DEPLOYMENT
1. Stop the lab from the web UI.
2. Stop the current test tmux session.
3. Extract this archive into a new review directory, not over production.
4. Reuse /home/ubuntu/UI/venv to start the review server on port 5050.

CHECKS
cd /home/ubuntu/UI_feature_review
/home/ubuntu/UI/venv/bin/python3 -m py_compile main.py dynamic_ue_scenario.py
PYTHONNOUSERSITE=1 /usr/bin/python3 dynamic_ue_scenario.py --help >/dev/null

REVIEW TMUX COMMAND
tmux new-session -d -s ui_feature_review \
  'cd /home/ubuntu/UI_feature_review && exec env PORT=5050 /home/ubuntu/UI/venv/bin/python3 main.py'

Open http://192.168.180.35:5050 in the browser.

CONFIGURATION
- UI_MAX_UES defaults to 8. Set it on the Flask process if another limit is needed.
- Each UE ID N uses ZMQ downlink port 2000 + N*100 and uplink port +1.
- Additional UEs increase CPU load. Test UE4 and UE5 before enabling more.

SHARED-SERVER SAFETY
- Do not start while another gNB, GNU Radio scenario or srsUE owns the same ports.
- Stop lab affects only process groups launched by this Flask instance.
- Namespace cleanup is limited to discovered local UE profiles.
- Do not upload or share actual IMSI, K, OP/OPc, passwords or SSH private keys.
- The Flask service has no user login and normally runs over HTTP. Keep it on a
  trusted lab network or place it behind authenticated HTTPS before wider access.
- The page never provisions or modifies Open5GS. Open5GS remains authoritative.

OUTSTANDING CUSTOM SRSUE SOURCE REVIEW
The web changes do not guess at or rebuild C++ data-plane code. The full current
versions of the modified srsUE source files are required before fixing uplink
packet forwarding, worker-thread shutdown, detached NAS scheduling, LCID fallback,
IPv6 reporting and list-bound validation.
