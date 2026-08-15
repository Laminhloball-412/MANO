#!/usr/bin/env bash
set -euo pipefail

GNB_DIR=/home/ubuntu/ran/srsRAN_project/build/apps/gnb
UE_DIR=/home/ubuntu/ran/srsRAN_4G/build/srsue/src
RESULT_ROOT=/home/ubuntu/ran/codex-baseline-after-reset
RUN_DIR="${RESULT_ROOT}/$(date +%Y%m%d-%H%M%S)"
mkdir -p "${RUN_DIR}"

gnb_pid=""
ue_pid=""
cleanup() {
  [[ -z "${ue_pid}" ]] || sudo -n kill -INT "${ue_pid}" 2>/dev/null || true
  [[ -z "${gnb_pid}" ]] || sudo -n kill -INT "${gnb_pid}" 2>/dev/null || true
  sleep 3
  [[ -z "${ue_pid}" ]] || sudo -n kill -TERM "${ue_pid}" 2>/dev/null || true
  [[ -z "${gnb_pid}" ]] || sudo -n kill -TERM "${gnb_pid}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if pgrep -x gnb >/dev/null || pgrep -x srsue >/dev/null || pgrep -x nr-uesoftmodem >/dev/null; then
  echo "Refusing to disturb an existing RAN/UE process." >&2
  exit 2
fi

if ! sudo -n ip netns list | grep -Eq '^ue1([[:space:]]|$)'; then
  sudo -n ip netns add ue1
  echo created >"${RUN_DIR}/netns-created"
fi

mkfifo "${RUN_DIR}/gnb.stdin" "${RUN_DIR}/ue.stdin"
exec 9<>"${RUN_DIR}/gnb.stdin"
exec 8<>"${RUN_DIR}/ue.stdin"

start_time="$(date -Is)"
(
  cd "${GNB_DIR}"
  exec sudo -n timeout --signal=TERM 80s ./gnb -c gnb1.yaml
) <&9 >"${RUN_DIR}/gnb.log" 2>&1 &
gnb_pid=$!
sleep 8

(
  cd "${UE_DIR}"
  exec sudo -n timeout --signal=TERM 62s ./srsue uetest.conf \
    --log.filename="${RUN_DIR}/ue-internal.log"
) <&8 >"${RUN_DIR}/ue-console.log" 2>&1 &
ue_pid=$!

sleep 38
sudo -n ip netns exec ue1 ip -details addr >"${RUN_DIR}/ue1-addresses.txt" 2>&1 || true
sudo -n ip netns exec ue1 ip route >"${RUN_DIR}/ue1-routes.txt" 2>&1 || true
sudo -n ip netns exec ue1 ping -c 4 -W 2 10.45.1.1 >"${RUN_DIR}/ping-gateway.txt" 2>&1 || true
sudo -n ip netns exec ue1 ping -c 4 -W 2 8.8.8.8 >"${RUN_DIR}/ping-internet.txt" 2>&1 || true

grep -nE 'N2:|AMF|PDU Session|RRC Connected|IP:|Reject|failed|Error|ERROR' \
  "${RUN_DIR}/gnb.log" "${RUN_DIR}/ue-console.log" >"${RUN_DIR}/evidence.txt" || true

pdu_count="$(grep -c 'PDU Session Establishment successful' "${RUN_DIR}/ue-console.log" || true)"
ue_ip="$(grep -oE 'IP: [0-9]+(\.[0-9]+){3}' "${RUN_DIR}/ue-console.log" | tail -1 | awk '{print $2}' || true)"
gateway_rx="$(grep -c 'bytes from 10.45.1.1' "${RUN_DIR}/ping-gateway.txt" || true)"
internet_rx="$(grep -c 'bytes from 8.8.8.8' "${RUN_DIR}/ping-internet.txt" || true)"
n2_ok="$(grep -Ec 'N2: Connection to AMF.*completed' "${RUN_DIR}/gnb.log" || true)"

printf 'start=%s\nn2_ok=%s\npdu_count=%s\nue_ip=%s\ngateway_ping_replies=%s\ninternet_ping_replies=%s\nresult_dir=%s\n' \
  "${start_time}" "${n2_ok}" "${pdu_count}" "${ue_ip}" "${gateway_rx}" "${internet_rx}" "${RUN_DIR}" \
  | tee "${RUN_DIR}/summary.txt"

[[ "${n2_ok}" -ge 1 && "${pdu_count}" -ge 1 && -n "${ue_ip}" && "${gateway_rx}" -ge 1 ]]
