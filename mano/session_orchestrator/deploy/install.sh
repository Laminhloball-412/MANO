#!/usr/bin/env bash
set -euo pipefail

root=${1:-/home/ubuntu/mano}
config="$root/session_orchestrator/config.json"
[[ -f "$config" ]] || cp "$root/session_orchestrator/config.example.json" "$config"
sudo -n install -m 0644 \
  "$root/session_orchestrator/deploy/ansa-session-metrics.service" \
  /etc/systemd/system/ansa-session-metrics.service
sudo -n install -m 0644 \
  "$root/session_orchestrator/deploy/ansa-intent-engine.service" \
  /etc/systemd/system/ansa-intent-engine.service
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now ansa-session-metrics.service
sudo -n systemctl enable --now ansa-intent-engine.service
