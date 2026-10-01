#!/usr/bin/env bash
set -euo pipefail

mode=${1:-one}
private_conf=${2:-}
gnb_mode=${GNB_MODE:-host}
gnb_profile=${GNB_PROFILE:-band3}
case "$mode" in
  one) expected_sessions=1 ;;
  two) expected_sessions=2 ;;
  *) echo "mode must be 'one' or 'two'" >&2; exit 2 ;;
esac
[[ -n "$private_conf" && -f "$private_conf" ]] || {
  echo "private UE config not found" >&2
  exit 2
}

gnb_dir=/home/ubuntu/ran/srsRAN_project/build/apps/gnb
oai_flavor=${OAI_FLAVOR:-duranta}
case "$oai_flavor" in
  duranta)
    oai_root=/home/ubuntu/ran/openairinterface5g-duranta
    ;;
  upstream)
    oai_root=/home/ubuntu/ran/openairinterface5g-oaiue
    ;;
  *) echo "OAI_FLAVOR must be 'duranta' or 'upstream'" >&2; exit 2 ;;
esac
oai_build="$oai_root/cmake_targets/ran_build/build"
run_root=/home/ubuntu/ran/oai-ue-lab/runs
run_dir="$run_root/$(date -u +%Y%m%dT%H%M%SZ)-$mode"
mkdir -p "$run_dir"

case "$gnb_profile" in
  band3)
    gnb_config=/home/ubuntu/ran/oai-ue-lab/templates/gnb-oai.yaml
    ue_radio_args=(-r 52 --numerology 0 --band 3 -C 1842500000 --CO -95000000 --ssb 162)
    ;;
  band78-ci)
    gnb_config=/home/ubuntu/ran/oai-ue-lab/templates/gnb-oai-tdd.yaml
    ue_radio_args=(-r 51 --numerology 1 --band 78 -C 3489420000 --ue-scan-carrier -E)
    ;;
  *) echo "GNB_PROFILE must be 'band3' or 'band78-ci'" >&2; exit 2 ;;
esac

if pgrep -x gnb >/dev/null || pgrep -x gnb_split_8 >/dev/null || pgrep -x srsue >/dev/null || pgrep -x nr-uesoftmodem >/dev/null; then
  echo "radio process already running; refusing to disturb the existing flow" >&2
  exit 1
fi

gnb_pid=
ue_pid=
cleanup() {
  set +e
  [[ -z "$ue_pid" ]] || sudo -n kill -INT "$ue_pid" 2>/dev/null
  if [[ "$gnb_mode" == ocudu-container ]]; then
    docker stop --time 2 codex-ocudu-oai-test >/dev/null 2>&1 || true
  else
    [[ -z "$gnb_pid" ]] || sudo -n kill -INT "$gnb_pid" 2>/dev/null
  fi
  sleep 2
  [[ -z "$ue_pid" ]] || sudo -n kill -TERM "$ue_pid" 2>/dev/null
  [[ "$gnb_mode" == ocudu-container || -z "$gnb_pid" ]] || sudo -n kill -TERM "$gnb_pid" 2>/dev/null
  exec 9>&- 2>/dev/null
}
trap cleanup EXIT INT TERM

mkfifo "$run_dir/gnb.stdin"
exec 9<>"$run_dir/gnb.stdin"
if [[ "$gnb_mode" == ocudu-container ]]; then
  (
    exec docker run --rm --name codex-ocudu-oai-test --network host \
      --cap-add SYS_NICE \
      -v "$gnb_config":/ocudu.yml:ro \
      ghcr.io/bpodrygajlo/ocudu/ocudu-avx2:7cecca5 \
      ./usr/local/bin/gnb_split_8 -c /ocudu.yml
  ) <&9 >"$run_dir/gnb.log" 2>&1 &
elif [[ "$gnb_mode" == ocudu-source ]]; then
  (
    cd /home/ubuntu/ran/ocudu-oai/build-oai/apps/gnb_split_8
    exec sudo -n timeout --signal=INT 125s ./gnb \
      -c "$gnb_config"
  ) <&9 >"$run_dir/gnb.log" 2>&1 &
else
  (
    cd "$gnb_dir"
    exec sudo -n timeout --signal=INT 125s ./gnb -c "$gnb_config"
  ) <&9 >"$run_dir/gnb.log" 2>&1 &
fi
gnb_pid=$!

# srsRAN buffers redirected console output, so the N2 line may only be flushed
# when the process exits. Give it a fixed startup window and verify it stayed up.
sleep 8
kill -0 "$gnb_pid" 2>/dev/null || {
  echo "gNB exited during startup; see $run_dir/gnb.log" >&2
  exit 1
}

(
  cd "$oai_build"
  # Run without SYS_NICE. On a small VM OAI's realtime-priority workers can
  # starve both gNB and the wrapper; the binary only needs NET_ADMIN/NET_RAW
  # file capabilities to create and configure its TUN interfaces.
  exec timeout --signal=INT 100s ./nr-uesoftmodem \
    -O "$private_conf" \
    "${ue_radio_args[@]}" \
    --uecap_file "$oai_root/targets/PROJECTS/GENERIC-NR-5GC/CONF/uecap_ports1.xml" \
    --log_config.global_log_options level,nocolor,time \
    --device.name oai_zmqdevif \
    --zmq.[0].tx_channels tcp://0.0.0.0:2001 \
    --zmq.[0].rx_channels tcp://127.0.0.1:2000
) </dev/null >"$run_dir/oai-ue.log" 2>&1 &
ue_pid=$!

for _ in $(seq 1 90); do
  # OAI creates a TUN early during startup. Count only TUNs that received an
  # IPv4 address from an accepted PDU session.
  tun_count=$(ip -o -4 addr show | awk '$2 ~ /^oaitun_ue/ {count++} END {print count+0}')
  [[ "$tun_count" -ge "$expected_sessions" ]] && sleep 5 && break
  kill -0 "$ue_pid" 2>/dev/null || break
  sleep 1
done

ip -details addr >"$run_dir/ip-addresses.txt"
ip route show table all >"$run_dir/ip-routes.txt"
ip -br addr | awk '$1 ~ /^oaitun_ue/' >"$run_dir/oai-tunnels.txt"
ss -lntp >"$run_dir/listeners.txt" 2>&1 || true
grep -Ei 'registration|PDU Session|oaitun|RRC|sync|error|fail|reject|assert' \
  "$run_dir/gnb.log" "$run_dir/oai-ue.log" >"$run_dir/evidence.txt" || true

# Verify user-plane reachability while the UE and gNB are still running. The
# lab UPF gateway is .1 in each assigned /24 PDU network.
ping_fail=0
: >"$run_dir/gateway-ping.txt"
while read -r tun cidr; do
  ue_ip=${cidr%/*}
  gateway=${ue_ip%.*}.1
  echo "interface=$tun ue_ip=$ue_ip gateway=$gateway" >>"$run_dir/gateway-ping.txt"
  ping -I "$tun" -c 3 -W 2 "$gateway" >>"$run_dir/gateway-ping.txt" 2>&1 || ping_fail=1
done < <(ip -o -4 addr show | awk '$2 ~ /^oaitun_ue/ {print $2, $4}')

tun_count=$(wc -l <"$run_dir/oai-tunnels.txt")
echo "mode=$mode"
echo "gnb_mode=$gnb_mode"
echo "gnb_profile=$gnb_profile"
echo "oai_flavor=$oai_flavor"
echo "expected_sessions=$expected_sessions"
echo "tun_count=$tun_count"
echo "gateway_ping_fail=$ping_fail"
echo "run_dir=$run_dir"
cat "$run_dir/oai-tunnels.txt"

if ! kill -0 "$ue_pid" 2>/dev/null; then
  wait "$ue_pid" || ue_status=$?
  echo "ue_exit_status=${ue_status:-0}"
fi

[[ "$tun_count" -ge "$expected_sessions" && "$ping_fail" -eq 0 ]]
