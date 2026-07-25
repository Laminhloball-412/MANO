#!/bin/bash

# ── AMF nằm ở namespace share ──────────────────────────────
NS_SHARE="share"

AMF_POD=$(kubectl get pod -n $NS_SHARE 2>/dev/null | grep amf | awk '{print $1}' | head -1)

if [ -z "$AMF_POD" ]; then
  echo "[share] Không tìm thấy AMF, bỏ qua"
else
  echo "[share] AMF=$AMF_POD"
  kubectl apply -f - << YAML
apiVersion: v1
kind: Service
metadata:
  name: amf-metrics
  namespace: $NS_SHARE
  labels:
    open5gs.io/nf-type: amf
    open5gs.io/slice-id: share
spec:
  selector:
    app: amf-svc
  ports:
    - name: metrics
      port: 9090
      targetPort: 9090
  type: ClusterIP
YAML
  echo "[share] amf-metrics service created"
fi

# ── SMF nằm ở từng slice ───────────────────────────────────
for i in $(seq 1 10); do
  NS="slice-$i"
  kubectl get namespace $NS &>/dev/null || continue

  SMF_POD=$(kubectl get pod -n $NS 2>/dev/null | grep smf | awk '{print $1}' | head -1)

  if [ -z "$SMF_POD" ]; then
    echo "[$NS] Không tìm thấy SMF, bỏ qua"
    continue
  fi

  echo "[$NS] SMF=$SMF_POD"

  kubectl apply -f - << YAML
apiVersion: v1
kind: Service
metadata:
  name: smf-metrics
  namespace: $NS
  labels:
    open5gs.io/nf-type: smf
    open5gs.io/slice-id: "$NS"
spec:
  selector:
    app: smf-svc
  ports:
    - name: metrics
      port: 9090
      targetPort: 9090
  type: ClusterIP
YAML
  echo "[$NS] smf-metrics service created"
done
