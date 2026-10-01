#!/usr/bin/env bash
set -euo pipefail

for namespace in slice-1 slice-2 slice-3; do
  pod=$(kubectl get pod -n "$namespace" -o name | grep upf | head -1)
  [[ -n "$pod" ]]
  kubectl exec -n "$namespace" "$pod" -- tc qdisc replace dev ogstun root fq_codel
  printf '%s ' "$namespace"
  kubectl exec -n "$namespace" "$pod" -- tc qdisc show dev ogstun
done
