#!/usr/bin/env bash
set -euo pipefail

ROUNDS="${1:-5}"
ATTACH_WAIT="${ATTACH_WAIT:-25}"
DRAIN_WAIT="${DRAIN_WAIT:-15}"
UE_START_GAP="${UE_START_GAP:-4}"
UE_LIST="${UE_LIST:-1 2 3}"
RESET_IP_STATE="${RESET_IP_STATE:-0}"
SHARED_AMF_NAMESPACE="${SHARED_AMF_NAMESPACE:-share}"
read -r -a UES <<<"${UE_LIST}"
UE_COUNT="${#UES[@]}"
GNB_DIR="${GNB_DIR:-/home/ubuntu/ran/srsRAN_project/build/apps/gnb}"
UE_DIR="${UE_DIR:-/home/ubuntu/ran/srsRAN_4G/build/srsue/src}"
RELAY_DIR="${RELAY_DIR:-/home/ubuntu/ran}"
RELAY_SCRIPT="${RELAY_SCRIPT:-multi_ue_scenario.py}"
RESULT_ROOT="${RESULT_ROOT:-/home/ubuntu/ran/multiue-test-results}"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${RESULT_ROOT}/${RUN_ID}"

mkdir -p "${RUN_DIR}"
mkfifo "${RUN_DIR}/relay.stdin" "${RUN_DIR}/gnb.stdin" "${RUN_DIR}/ue1.stdin" "${RUN_DIR}/ue2.stdin" "${RUN_DIR}/ue3.stdin"
exec 5<>"${RUN_DIR}/relay.stdin"
exec 9<>"${RUN_DIR}/gnb.stdin"
exec 8<>"${RUN_DIR}/ue1.stdin"
exec 7<>"${RUN_DIR}/ue2.stdin"
exec 6<>"${RUN_DIR}/ue3.stdin"

cleanup_radio() {
  # Give srsUE/gNB the same graceful shutdown signal as Ctrl+C so NAS/PFCP
  # contexts have a chance to be released before the next round.
  sudo -n pkill -INT -x srsue 2>/dev/null || true
  sudo -n pkill -INT -x gnb 2>/dev/null || true
  pkill -INT -f "^python3 ./${RELAY_SCRIPT}$" 2>/dev/null || true
  sleep 5
  sudo -n pkill -TERM -x srsue 2>/dev/null || true
  sudo -n pkill -TERM -x gnb 2>/dev/null || true
  pkill -TERM -f "^python3 ./${RELAY_SCRIPT}$" 2>/dev/null || true
  sleep 2
  sudo -n pkill -KILL -x srsue 2>/dev/null || true
  sudo -n pkill -KILL -x gnb 2>/dev/null || true
  pkill -KILL -f "^python3 ./${RELAY_SCRIPT}$" 2>/dev/null || true
}

discover_target_namespaces() {
  local ue sst sd namespace config found
  TARGET_NAMESPACES=()

  for ue in "${UES[@]}"; do
    sst="$(awk -F= '/^[[:space:]]*nssai-sst[[:space:]]*=/{gsub(/[[:space:]]/, "", $2); print $2; exit}' "${UE_DIR}/ue${ue}.conf")"
    sd="$(awk -F= '/^[[:space:]]*nssai-sd[[:space:]]*=/{gsub(/[[:space:]]/, "", $2); print toupper($2); exit}' "${UE_DIR}/ue${ue}.conf")"
    found=""

    while read -r namespace; do
      [[ -n "${namespace}" ]] || continue
      config="$(kubectl get configmap open5gs-smf -n "${namespace}" -o jsonpath='{.data.open5gs-smf}' 2>/dev/null || true)"
      if grep -Eq "sst:[[:space:]]*${sst}([[:space:]]|$)" <<<"${config}" \
        && grep -Eq "sd:[[:space:]]*[\"']?${sd}[\"']?([[:space:]]|$)" <<<"${config}"; then
        found="${namespace}"
        break
      fi
    done < <(
      kubectl get namespaces -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
        | grep -E '^slice-[0-9]+$' \
        | sort -V
    )

    if [[ -z "${found}" ]]; then
      echo "ERROR: cannot map UE${ue} SST=${sst} SD=${sd} to a slice namespace." >&2
      return 1
    fi
    if [[ ! " ${TARGET_NAMESPACES[*]} " =~ " ${found} " ]]; then
      TARGET_NAMESPACES+=("${found}")
    fi
  done
}

reset_one_slice_ip_state() {
  local namespace="$1" component deployment
  for component in upf-svc smf-svc; do
    deployment="$(
      kubectl get deployment -n "${namespace}" -o json \
        | jq -r --arg app "${component}" \
          '.items[] | select(.spec.selector.matchLabels.app == $app) | .metadata.name'
    )"
    if [[ -z "${deployment}" ]] || [[ "$(wc -l <<<"${deployment}")" -ne 1 ]]; then
      echo "ERROR: ${namespace} expected one deployment for app=${component}, got: ${deployment}" >&2
      return 1
    fi
    kubectl rollout restart "deployment/${deployment}" -n "${namespace}" >/dev/null
    kubectl rollout status "deployment/${deployment}" -n "${namespace}" --timeout=180s
  done
}

reset_ip_state() {
  local namespace pid amf_deployment
  local -a pids=()
  [[ "${RESET_IP_STATE}" == "1" ]] || return 0
  echo "Resetting SMF/UPF IP allocator state for: ${TARGET_NAMESPACES[*]}"
  for namespace in "${TARGET_NAMESPACES[@]}"; do
    reset_one_slice_ip_state "${namespace}" &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "${pid}"
  done

  # Clear cached UE/PDU contexts after the SMFs have new NF instance IDs.
  amf_deployment="$(
    kubectl get deployment -n "${SHARED_AMF_NAMESPACE}" -o json \
      | jq -r '.items[] | select(.spec.selector.matchLabels.app == "amf-svc") | .metadata.name'
  )"
  if [[ -z "${amf_deployment}" ]] || [[ "$(wc -l <<<"${amf_deployment}")" -ne 1 ]]; then
    echo "ERROR: expected one shared AMF deployment, got: ${amf_deployment}" >&2
    return 1
  fi
  kubectl rollout restart "deployment/${amf_deployment}" -n "${SHARED_AMF_NAMESPACE}" >/dev/null
  kubectl rollout status "deployment/${amf_deployment}" -n "${SHARED_AMF_NAMESPACE}" --timeout=180s

  # Let the AMF/SMFs finish NRF registration and PFCP association.
  sleep 5
}

start_relay() {
  local log_file="$1"
  local attempt ue ports_ok
  (
    cd "${RELAY_DIR}"
    exec env PYTHONNOUSERSITE=1 python3 "./${RELAY_SCRIPT}"
  ) <&5 >"${log_file}" 2>&1 &

  for attempt in $(seq 1 20); do
    ports_ok=1
    ss -lntp 2>/dev/null | grep -q '127.0.0.1:2001' || ports_ok=0
    for ue in "${UES[@]}"; do
      ss -lntp 2>/dev/null | grep -q "127.0.0.1:2${ue}00" || ports_ok=0
    done
    if [[ "${ports_ok}" -eq 1 ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_gnb() {
  local attempt
  # srsRAN buffers console output when redirected, so liveness is checked first.
  # The N2 result is verified from the completed round log afterwards.
  for attempt in $(seq 1 12); do
    sleep 1
    if pgrep -x gnb >/dev/null; then
      if [[ "${attempt}" -ge 5 ]]; then
        return 0
      fi
    elif [[ "${attempt}" -ge 3 ]]; then
      return 1
    fi
  done
  return 1
}

preflight() {
  local ns cluster_ip
  command -v kubectl >/dev/null
  command -v jq >/dev/null
  command -v timeout >/dev/null
  command -v ss >/dev/null
  sudo -n true

  for ns in slice-1 slice-2 slice-3; do
    cluster_ip="$(kubectl get service upf-svc -n "${ns}" -o jsonpath='{.spec.clusterIP}')"
    if [[ "${cluster_ip}" != "None" ]]; then
      echo "ERROR: ${ns}/upf-svc is ${cluster_ip}, expected a headless service (clusterIP None)." >&2
      return 1
    fi
    kubectl get endpoints upf-svc -n "${ns}" -o jsonpath='{.subsets[0].addresses[0].ip}' | grep -Eq '^[0-9]+(\.[0-9]+){3}$'
  done

  for ue in "${UES[@]}"; do
    [[ "${ue}" =~ ^[123]$ ]]
    [[ -f "${UE_DIR}/ue${ue}.conf" ]]
    sudo -n ip netns list | grep -Eq "^ue${ue}([[:space:]]|$)"
  done
  [[ -x "${GNB_DIR}/gnb" ]]
  [[ -x "${UE_DIR}/srsue" ]]
  [[ -f "${RELAY_DIR}/${RELAY_SCRIPT}" ]]
}

trap cleanup_radio EXIT INT TERM
preflight
cleanup_radio
discover_target_namespaces
reset_ip_state

printf 'run_id=%s\nrounds=%s\nues=%s\nreset_ip_state=%s\nreset_namespaces=%s\nattach_wait=%s\ndrain_wait=%s\nue_start_gap=%s\n' \
  "${RUN_ID}" "${ROUNDS}" "${UE_LIST}" "${RESET_IP_STATE}" "${TARGET_NAMESPACES[*]}" "${ATTACH_WAIT}" "${DRAIN_WAIT}" "${UE_START_GAP}" \
  | tee "${RUN_DIR}/summary.txt"

passed_rounds=0

for round in $(seq 1 "${ROUNDS}"); do
  round_dir="${RUN_DIR}/round-${round}"
  mkdir -p "${round_dir}"
  echo "=== ROUND ${round}/${ROUNDS} ===" | tee -a "${RUN_DIR}/summary.txt"

  cleanup_radio
  echo "Waiting ${DRAIN_WAIT}s for AMF/SMF/PFCP context cleanup..."
  sleep "${DRAIN_WAIT}"

  if ! start_relay "${round_dir}/relay.log"; then
    echo "ROUND ${round}: FAIL (multi-UE ZMQ relay did not bind all ports)" | tee -a "${RUN_DIR}/summary.txt"
    continue
  fi

  (
    cd "${GNB_DIR}"
    exec sudo -n timeout --signal=TERM 90s ./gnb -c gnb1.yaml
  ) <&9 >"${round_dir}/gnb.log" 2>&1 &

  if ! wait_for_gnb; then
    echo "ROUND ${round}: FAIL (gNB did not connect to AMF)" | tee -a "${RUN_DIR}/summary.txt"
    continue
  fi

  ue_index=0
  for ue in "${UES[@]}"; do
    (
      cd "${UE_DIR}"
      exec sudo -n timeout --signal=TERM 70s ./srsue "ue${ue}.conf"
    ) <&$((9 - ue)) >"${round_dir}/ue${ue}.log" 2>&1 &
    ue_index=$((ue_index + 1))
    if [[ "${ue_index}" -lt "${UE_COUNT}" ]]; then
      sleep "${UE_START_GAP}"
    fi
  done

  sleep "${ATTACH_WAIT}"

  round_ok=1
  for ue in "${UES[@]}"; do
    if grep -q "PDU Session Establishment successful" "${round_dir}/ue${ue}.log"; then
      ip="$(grep -oE 'IP: [0-9]+(\.[0-9]+){3}' "${round_dir}/ue${ue}.log" | tail -1 | awk '{print $2}')"
      echo "ROUND ${round}: UE${ue} PASS IP=${ip:-unknown}" | tee -a "${RUN_DIR}/summary.txt"
    else
      last_status="$(grep -E 'RRC Connected|PDU Session|Reject|failed|Error|ERROR' "${round_dir}/ue${ue}.log" | tail -1 || true)"
      echo "ROUND ${round}: UE${ue} FAIL ${last_status:-no attach status}" | tee -a "${RUN_DIR}/summary.txt"
      round_ok=0
    fi
  done

  if [[ "${round_ok}" -eq 1 ]]; then
    passed_rounds=$((passed_rounds + 1))
    echo "ROUND ${round}: PASS (${UE_COUNT}/${UE_COUNT} UEs)" | tee -a "${RUN_DIR}/summary.txt"
  else
    echo "ROUND ${round}: FAIL" | tee -a "${RUN_DIR}/summary.txt"
  fi

  cleanup_radio
  reset_ip_state
done

cleanup_radio
echo "FINAL: ${passed_rounds}/${ROUNDS} rounds passed with ${UE_COUNT}/${UE_COUNT} UEs" | tee -a "${RUN_DIR}/summary.txt"
echo "RESULT_DIR=${RUN_DIR}" | tee -a "${RUN_DIR}/summary.txt"

[[ "${passed_rounds}" -eq "${ROUNDS}" ]]
