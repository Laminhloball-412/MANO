#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Bước 1: Tạo namespace monitoring ==="
kubectl apply -f $SCRIPT_DIR/00-namespace.yaml

echo "=== Bước 2: RBAC ==="
kubectl apply -f $SCRIPT_DIR/01-prometheus-rbac.yaml

echo "=== Bước 3: Prometheus config ==="
kubectl apply -f $SCRIPT_DIR/02-prometheus-config.yaml

echo "=== Bước 4: Deploy Prometheus ==="
kubectl apply -f $SCRIPT_DIR/03-prometheus-deploy.yaml

echo "=== Bước 5: Deploy Grafana ==="
kubectl apply -f $SCRIPT_DIR/04-grafana-deploy.yaml

echo "=== Bước 6: Deploy Slice Exporter ==="
kubectl apply -f $SCRIPT_DIR/05-slice-exporter.yaml

echo ""
echo "=== Chờ pods sẵn sàng ==="
kubectl rollout status deployment/prometheus -n monitoring
kubectl rollout status deployment/grafana -n monitoring

NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
echo ""
echo "✅ Done!"
echo "   Prometheus : http://$NODE_IP:30090"
echo "   Grafana    : http://$NODE_IP:30030  (admin/admin123)"
echo "   Exporter   : kubectl port-forward svc/slice-exporter 8080:8080 -n monitoring"
