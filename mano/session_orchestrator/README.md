# ANSA session orchestrator

This package adds asynchronous application intent and multi-PDU orchestration
beside the existing UE Portal. It does not replace or import the portal code and
it never edits the original subscriber, gNB or UE templates.

The primary path is observe-first: an independent application starts on the
protected default PDU, publishes flow-scoped telemetry, then its application
descriptor is submitted as an intent. The engine queries the Prometheus copy
of those measurements before PDU execution. The scenario matrix and safety
rules are documented in `RUNTIME-DESIGN.md`.

```bash
# Services
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json serve
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json intentd

# Terminal 1: application starts first on the default PDU and registers flow ID
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json \
  simulate --service sensor --ue-id 1 --flow-id ue1-sensor --duration 120 \
  --interface oaitun_ue1 --gateway 10.45.1.1

# Terminal 2: attach an explicit application intent to the observed flow
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json \
  submit --service sensor --ue-id 1 --flow-id ue1-sensor \
  --intent-source application --mode concurrent --lease-seconds 120

# A replacement/migration. Source release waits for the new flow to bind and ACK.
python3 -m session_orchestrator.cli --config session_orchestrator/config.lab.json \
  submit --service control --ue-id 1 --flow-id ue1-control-new \
  --mode migrate --replaces-intent-id intent-old
```

The asynchronous Intent API exports application flow metrics on port 9106;
Prometheus scrapes them before the worker makes a decision. `serve` exports
live TUN counters without generating traffic. The first scrape
only establishes a baseline; rates appear from the second scrape. RTT, jitter
and loss remain absent unless an application exports them or an active probe is
explicitly enabled. They are never inferred from a service name.

`run` below is retained as a legacy all-in-one lab regression command. It
generates ping traffic itself and must not be used as evidence that the
asynchronous architecture observed an independent application.

The legacy executable flow is:

1. keep the default PDU active;
2. generate real ICMP application traffic bound to its OAI TUN;
3. measure throughput, RTT, jitter, loss, packet size and message rate;
4. expose the values to Prometheus on port 9105;
5. rank S-NSSAI/DNN candidates from service requirements, declared capability,
   live Kubernetes health/load and current PDU occupancy;
6. reuse a healthy slice or call `intent_flow_v2` to allocate one;
7. establish a second OAI PDU without restarting the UE;
8. validate its TUN, gateway and SLA for a stability window;
9. bind new application flows to the new TUN, drain old flows, then release
   the old PDU.

The OAI runtime commands deliberately have no restart fallback. If validation
fails, the old PDU remains active and only the uncommitted target PDU is
released. The shared/default namespaces are protected, and deallocation
defaults to dry-run.

```bash
python3 -m unittest discover -s session_orchestrator/tests -v
python3 -m session_orchestrator.cli --config session_orchestrator/config.json serve
python3 -m session_orchestrator.cli --config session_orchestrator/config.json run \
  --service control --duration 20
```

The runtime establish/release commands require the separately tracked OAI
patch `patches/0001-oai-runtime-multipdu-mbb.patch`, based on Duranta OAI
commit `31eb466`. It adds runtime establish/release commands, fixes the ciUE
module so it uses the running NAS context, and stabilizes concurrent OpenSSL 3
key derivation during DRB reconfiguration. It also preserves the default TUN
name when PDU1 is released and re-established. This therefore includes necessary
changes beyond the unmodified OCUDU tutorial.

```bash
cd /home/ubuntu/ran/openairinterface5g-duranta
git switch -c codex/multipdu-runtime 31eb466
git am /home/ubuntu/mano/session_orchestrator/patches/0001-oai-runtime-multipdu-mbb.patch
cd cmake_targets
./build_oai -I --nrUE --build-lib telnetsrv
sudo setcap cap_net_admin,cap_net_raw+ep ran_build/build/nr-uesoftmodem
```

Install/restart only the new metrics service with `deploy/install.sh`. Apply
`deploy/prometheus-configmap-patch.json` to the existing Prometheus ConfigMap
after taking a backup, validate it with `promtool`, then reload Prometheus. The
lab-only end-to-end check is `deploy/run-live-test.sh`; it refuses to disturb an
already running radio process and cleans up only the processes it launches.

The simulator polls its `flow_id`, opens a socket on the published target TUN,
and ACKs only after bind succeeds. It does not transparently migrate an
already-open TCP socket; that later step needs an application proxy,
QUIC/MPTCP, or policy/eBPF steering.

The explainable multi-UE/multi-PDU Prometheus and Grafana model is documented in
`OBSERVABILITY.md`. The provisioned Grafana template keeps the existing
`auto-slice-operations` UID, so existing Free5GMANO links continue to work.
The exporter exposes per-slice capability, pod health, CPU/memory load and score
components. Capability values are policy baselines, not enforced QoS guarantees;
the target-PDU gateway and SLA stability probe remains the final acceptance gate.
