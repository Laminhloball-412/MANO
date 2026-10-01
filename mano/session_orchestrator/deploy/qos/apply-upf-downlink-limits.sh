#!/usr/bin/env bash
set -euo pipefail

# This shapes only downlink packets sent from each UPF through ogstun to its UE.
# It intentionally leaves CPU, memory and the original Kubernetes templates alone.
for entry in "slice-1:100mbit:256kb" "slice-2:5mbit:64kb" "slice-3:1mbit:16kb"; do
  IFS=: read -r namespace rate burst <<<"$entry"
  pod=$(kubectl get pod -n "$namespace" -o name | grep upf | head -1)
  [[ -n "$pod" ]]
  kubectl exec -n "$namespace" "$pod" -- \
    tc qdisc replace dev ogstun root handle 1: tbf rate "$rate" burst "$burst" latency 100ms
  printf '%s %s ' "$namespace" "$rate"
  kubectl exec -n "$namespace" "$pod" -- tc qdisc show dev ogstun
done
