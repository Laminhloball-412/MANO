#!/usr/bin/env python3
import json
import subprocess


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


configmap = json.loads(run("kubectl", "get", "configmap", "prometheus-config", "-n", "monitoring", "-o", "json"))
config = configmap["data"]["prometheus.yml"].rstrip() + "\n"
job_name = "ue-service-portal"

if "job_name: {}".format(job_name) not in config:
    config += """

  - job_name: ue-service-portal
    scrape_interval: 2s
    scrape_timeout: 1s
    metrics_path: /metrics/
    static_configs:
      - targets:
          - 192.168.180.35:8890
        labels:
          component: ue-service-portal
          scope: ue-workload
"""
    patch = json.dumps({"data": {"prometheus.yml": config}})
    run("kubectl", "patch", "configmap", "prometheus-config", "-n", "monitoring", "--type", "merge", "-p", patch)
    print("Added Prometheus job {}".format(job_name))
else:
    print("Prometheus job {} already exists".format(job_name))

run("kubectl", "rollout", "restart", "deployment/prometheus", "-n", "monitoring")
run("kubectl", "rollout", "status", "deployment/prometheus", "-n", "monitoring", "--timeout=180s")
print("Prometheus reloaded")
