#!/usr/bin/env bash
set -euo pipefail

mano_root=${1:-/home/ubuntu/mano}
webui_root=${WEBUI_ROOT:-$mano_root/free5gmano-webui}
package_root=$mano_root/session_orchestrator
backup_root=$mano_root/runtime/backups/$(date -u +%Y%m%dT%H%M%SZ)-multipdu-dashboard
mkdir -p "$backup_root"

kubectl get configmap grafana-auto-slice-dashboard -n monitoring -o yaml \
  >"$backup_root/grafana-auto-slice-dashboard.yaml"
cp "$webui_root/ui_route/views.py" "$backup_root/views.py"
cp "$webui_root/ui_templates/dashboard.html" "$backup_root/dashboard.html"

install -m 0644 "$mano_root/intent_flow_v2/webui_overlay/ui_route/views.py" \
  "$webui_root/ui_route/views.py"
install -m 0644 "$mano_root/intent_flow_v2/webui_overlay/ui_templates/dashboard.html" \
  "$webui_root/ui_templates/dashboard.html"

dashboard=$package_root/dashboards/ansa-multipdu-dashboard.json
python3 -m json.tool "$dashboard" >/dev/null
kubectl create configmap grafana-auto-slice-dashboard -n monitoring \
  --from-file=auto-slice-operations.json="$dashboard" \
  --dry-run=client -o yaml | kubectl apply -f -

(cd "$webui_root" && .venv/bin/python manage.py check)
sudo -n systemctl restart free5gmano-webui.service
echo "backup=$backup_root"
