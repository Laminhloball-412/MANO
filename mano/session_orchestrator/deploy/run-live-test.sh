#!/usr/bin/env bash
set -euo pipefail

lab_root=${LAB_ROOT:-/home/ubuntu/ran}
mano_root=${MANO_ROOT:-/home/ubuntu/mano}
ue_config=${UE_CONFIG:-$lab_root/oai-ue-lab/private-one.conf}
service=${SERVICE:-control}
duration=${DURATION:-3}
packet_size=${PACKET_SIZE:-120}
message_rate=${MESSAGE_RATE:-10}
benchmark=${BENCHMARK:-0}
iperf_seconds=${IPERF_SECONDS:-3}
iperf_rate=${IPERF_RATE:-50M}
run_dir="$lab_root/oai-ue-lab/runs/orchestrator-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$run_dir"

if pgrep -x gnb >/dev/null || pgrep -x nr-uesoftmodem >/dev/null; then
  echo "radio process already running; refusing to disturb it" >&2
  exit 1
fi

gnb_pid=
ue_pid=
iperf_pid=
cleanup() {
  set +e
  [[ -z "$iperf_pid" ]] || sudo -n kill "$iperf_pid" 2>/dev/null
  [[ -z "$ue_pid" ]] || kill -INT "$ue_pid" 2>/dev/null
  [[ -z "$gnb_pid" ]] || sudo -n kill -INT "$gnb_pid" 2>/dev/null
  sleep 2
  [[ -z "$ue_pid" ]] || kill -TERM "$ue_pid" 2>/dev/null
  [[ -z "$gnb_pid" ]] || sudo -n kill -TERM "$gnb_pid" 2>/dev/null
  exec 9>&- 2>/dev/null
}
trap cleanup EXIT INT TERM

mkfifo "$run_dir/gnb.stdin"
exec 9<>"$run_dir/gnb.stdin"
(
  cd "$lab_root/ocudu-oai/build-oai/apps/gnb_split_8"
  exec sudo -n timeout --signal=INT 120s ./gnb \
    -c "$lab_root/oai-ue-lab/templates/gnb-oai-tdd.yaml"
) <&9 >"$run_dir/gnb.log" 2>&1 &
gnb_pid=$!
sleep 8
kill -0 "$gnb_pid"

(
  cd "$lab_root/openairinterface5g-duranta/cmake_targets/ran_build/build"
  exec timeout --signal=INT 100s ./nr-uesoftmodem \
    -O "$ue_config" -r 51 --numerology 1 --band 78 -C 3489420000 \
    --ue-scan-carrier -E \
    --uecap_file "$lab_root/openairinterface5g-duranta/targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports1.xml" \
    --log_config.global_log_options level,nocolor,time \
    --device.name oai_zmqdevif \
    --zmq.[0].tx_channels tcp://0.0.0.0:2001 \
    --zmq.[0].rx_channels tcp://127.0.0.1:2000 \
    --telnetsrv --telnetsrv.shrmod ciUE \
    --telnetsrv.listenaddr 127.0.0.1 --telnetsrv.listenport 9090
) </dev/null >"$run_dir/oai-ue.log" 2>&1 &
ue_pid=$!

for _ in $(seq 1 60); do
  ip -o -4 addr show oaitun_ue1 2>/dev/null | grep -q '10\.45\.1\.' && break
  kill -0 "$ue_pid"
  sleep 1
done
ip -o -4 addr show oaitun_ue1 | grep -q '10\.45\.1\.'

cd "$mano_root"
python3 -m session_orchestrator.cli \
  --config session_orchestrator/config.lab.json run \
  --service "$service" --duration "$duration" \
  --packet-size "$packet_size" --message-rate "$message_rate" \
  | tee "$run_dir/controller-result.json"

if [[ "$benchmark" == 1 ]]; then
  case "$service" in
    video|streaming) namespace=slice-1; gateway=10.45.1.1; interface=oaitun_ue1 ;;
    control|robot) namespace=slice-2; gateway=10.45.2.1; interface=oaitun_ue1p2 ;;
    sensor|telemetry) namespace=slice-3; gateway=10.45.3.1; interface=oaitun_ue1p3 ;;
    *) echo "no benchmark mapping for service $service" >&2; exit 2 ;;
  esac
  ue_ip=$(ip -o -4 addr show "$interface" | awk '{sub(/\/.*/, "", $4); print $4}')
  pod=$(kubectl get pod -n "$namespace" -o name | grep upf | head -1)
  container_id=$(kubectl get -n "$namespace" "$pod" \
    -o jsonpath='{.status.containerStatuses[0].containerID}')
  container_id=${container_id#containerd://}
  container_pid=$(sudo -n crictl inspect "$container_id" | jq -r .info.pid)
  sudo -n nsenter -t "$container_pid" -n \
    iperf3 -s -1 -B "$gateway" -p 5201 >"$run_dir/iperf-server.log" 2>&1 &
  iperf_pid=$!
  sleep 1
  set +e
  timeout --signal=TERM 12s iperf3 -c "$gateway" -p 5201 -B "$ue_ip" \
    -R -u -b "$iperf_rate" -t "$iperf_seconds" -J \
    >"$run_dir/iperf-downlink.json"
  benchmark_status=$?
  sudo -n kill "$iperf_pid" 2>/dev/null
  wait "$iperf_pid" 2>/dev/null
  iperf_pid=
  set -e
  if jq -e '.end.sum_received.bits_per_second' "$run_dir/iperf-downlink.json" >/dev/null 2>&1; then
    jq --argjson status "$benchmark_status" \
      '{status:$status,receiver_mbps:(.end.sum_received.bits_per_second/1000000),sender_mbps:(.end.sum_sent.bits_per_second/1000000),lost_percent:.end.sum_received.lost_percent}' \
      "$run_dir/iperf-downlink.json" | tee "$run_dir/iperf-summary.json"
  else
    printf '{"status":%s,"error":"iperf result incomplete"}\n' "$benchmark_status" \
      | tee "$run_dir/iperf-summary.json"
  fi
fi

ip -br addr | awk '$1 ~ /^oaitun_ue/' | tee "$run_dir/oai-tunnels-after.txt"
curl -fsS http://127.0.0.1:9105/metrics >"$run_dir/exported-metrics.txt"
echo "run_dir=$run_dir"
