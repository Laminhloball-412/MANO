#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 HOST:PORT" >&2
  exit 2
fi

ran_exporter_target=$1
mano_root=${MANO_ROOT:-/home/ubuntu/mano}
if [[ "$mano_root" != /home/ubuntu/mano ]]; then
  echo "MANO_ROOT must be /home/ubuntu/mano because the systemd unit uses fixed paths" >&2
  exit 2
fi
package_root=$mano_root/session_orchestrator
runtime_root=$mano_root/runtime
venv=$runtime_root/ran-exporter-venv
config=$package_root/ran-exporter.json
namespace=${MONITORING_NAMESPACE:-monitoring}
prometheus_configmap=${PROMETHEUS_CONFIGMAP:-prometheus-config}
grafana_configmap=${GRAFANA_CONFIGMAP:-grafana-auto-slice-dashboard}
prometheus_container=${PROMETHEUS_CONTAINER:-prometheus}
prometheus_url=${PROMETHEUS_URL:-http://127.0.0.1:30090}

printf 'scrape_configs:\n' | python3 "$package_root/deploy/patch_prometheus_config.py" \
  add --target "$ran_exporter_target" >/dev/null

[[ -f "$config" ]] || install -m 0644 "$package_root/ran-exporter.example.json" "$config"

mkdir -p "$runtime_root"
python3 -m venv "$venv"
"$venv/bin/python3" -m pip install -r "$package_root/ran-exporter-requirements.txt"

sudo -n install -m 0644 \
  "$package_root/deploy/ansa-ocudu-ran-exporter.service" \
  /etc/systemd/system/ansa-ocudu-ran-exporter.service
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now ansa-ocudu-ran-exporter.service

backup_parent=$runtime_root/backups
mkdir -p "$backup_parent"
backup_root=$(mktemp -d "$backup_parent/$(date -u +%Y%m%dT%H%M%SZ)-ran-observability.XXXXXX")
prometheus_configmap_backup=$backup_root/$prometheus_configmap.json
grafana_configmap_backup=$backup_root/$grafana_configmap.json
kubectl get configmap "$prometheus_configmap" -n "$namespace" -o json \
  >"$prometheus_configmap_backup"
kubectl get configmap "$grafana_configmap" -n "$namespace" -o json \
  >"$grafana_configmap_backup"

prometheus_source=$backup_root/prometheus.yml
prometheus_candidate=$backup_root/prometheus.candidate.yml
prometheus_configmap_candidate=$backup_root/$prometheus_configmap.candidate.json
"$venv/bin/python3" -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    configmap = json.load(source)
sys.stdout.write(configmap["data"]["prometheus.yml"])
' "$prometheus_configmap_backup" >"$prometheus_source"
"$venv/bin/python3" "$package_root/deploy/patch_prometheus_config.py" \
  add --target "$ran_exporter_target" \
  <"$prometheus_source" >"$prometheus_candidate"

prometheus_pod=${PROMETHEUS_POD:-}
if [[ -z "$prometheus_pod" ]]; then
  prometheus_pod=$(kubectl get pods -n "$namespace" -l app=prometheus \
    -o jsonpath='{.items[0].metadata.name}')
fi
if [[ -z "$prometheus_pod" ]]; then
  echo "no Prometheus pod found; set PROMETHEUS_POD explicitly" >&2
  exit 1
fi

kubectl exec -i -n "$namespace" "$prometheus_pod" -c "$prometheus_container" -- \
  sh -ceu 'candidate=$(mktemp); trap '\''rm -f "$candidate"'\'' EXIT; cat >"$candidate"; promtool check config "$candidate"' \
  <"$prometheus_candidate"

"$venv/bin/python3" "$package_root/deploy/patch_prometheus_config.py" \
  replace-configmap-data --key prometheus.yml --value-file "$prometheus_candidate" \
  <"$prometheus_configmap_backup" \
  >"$prometheus_configmap_candidate"
kubectl apply -f "$prometheus_configmap_candidate"
prometheus_candidate_sha=$(sha256sum "$prometheus_candidate" | awk '{print $1}')
prometheus_active_sha=
for _ in $(seq 1 60); do
  prometheus_active_sha=$(kubectl exec -n "$namespace" "$prometheus_pod" \
    -c "$prometheus_container" -- sha256sum /etc/prometheus/prometheus.yml \
    | awk '{print $1}')
  [[ "$prometheus_active_sha" == "$prometheus_candidate_sha" ]] && break
  sleep 2
done
if [[ "$prometheus_active_sha" != "$prometheus_candidate_sha" ]]; then
  echo "Prometheus pod did not project the candidate ConfigMap within 120 seconds" >&2
  exit 1
fi
curl --fail --silent --show-error -X POST "$prometheus_url/-/reload"

dashboard=$package_root/dashboards/ansa-multipdu-dashboard.json
grafana_configmap_candidate=$backup_root/$grafana_configmap.candidate.json
"$venv/bin/python3" "$package_root/dashboards/build_dashboard.py"
"$venv/bin/python3" -m json.tool "$dashboard" >/dev/null
"$venv/bin/python3" "$package_root/dashboards/validate_dashboard.py" \
  --prometheus "$prometheus_url" --dashboard "$dashboard"
"$venv/bin/python3" "$package_root/deploy/patch_prometheus_config.py" \
  replace-configmap-data --key auto-slice-operations.json --value-file "$dashboard" \
  <"$grafana_configmap_backup" \
  >"$grafana_configmap_candidate"
kubectl apply -f "$grafana_configmap_candidate"

echo "backup=$backup_root"
echo "OCUDU launch overlay: -c EXISTING_CONFIG -c $package_root/deploy/ocudu-ran-metrics.overlay.yml"
