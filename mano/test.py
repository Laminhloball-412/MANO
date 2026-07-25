#!/usr/bin/env python3
"""
Slice Registry — quản lý vòng đời slice với namespace per slice
================================================================
Tích hợp với free5gmano API thực tế từ intent_test.py

API endpoints:
  GET  /ObjectManagement/SliceTemplate/        → danh sách template
  POST /ObjectManagement/NSS/SliceProfiles/    → tạo NSSI
  GET  /ObjectManagement/NSS/topology/         → xem topology

Usage:
  python3 slice_registry.py list-templates     # xem templates có sẵn
  python3 slice_registry.py create <template_id>  # tạo slice mới
  python3 slice_registry.py delete <slice-N>   # xóa slice
  python3 slice_registry.py list               # xem tất cả slice
  python3 slice_registry.py status <slice-N>   # xem trạng thái 1 slice
"""

import json
import os
import subprocess
import sys
import time
import logging
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("❌ Thiếu thư viện requests. Chạy: pip install requests")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─── Config — chỉnh ở đây ─────────────────────────────────────────────────────

FREE5G_URL    = "http://192.168.180.35:30088"   # free5gmano URL
K8S_HOST      = "192.168.180.35"                # IP máy host
K8S_NS        = "default"                       # namespace hiện tại (để tìm webui)
MAX_SLOTS     = 10                              # tối đa 10 slice đồng thời
REGISTRY_FILE = Path("slice_registry.json")     # file lưu trạng thái

# ─── Registry helpers ─────────────────────────────────────────────────────────
#
# Cấu trúc slice_registry.json:
# {
#   "slots": {
#     "1": null,                    ← slot trống
#     "2": {                        ← slot đang chạy
#       "nssi_id": "abc-123",
#       "slice_name": "slice-2",
#       "namespace": "slice-2",
#       "template_id": "tmpl-xyz",
#       "webui_port": 31456,
#       "webui_url": "http://192.168.180.54:31456",
#       "created_at": "2026-05-25T10:00:00"
#     }
#   }
# }

def load_registry() -> dict:
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text())
    # Khởi tạo lần đầu
    return {"slots": {str(i): None for i in range(1, MAX_SLOTS + 1)}}

def save_registry(registry: dict):
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2))

def next_free_slot(registry: dict) -> Optional[str]:
    for slot, info in registry["slots"].items():
        if info is None:
            return slot
    return None

def find_slot_by_name(registry: dict, slice_name: str) -> Optional[str]:
    for slot, info in registry["slots"].items():
        if info and info.get("slice_name") == slice_name:
            return slot
    return None

# ─── Kubernetes helpers ────────────────────────────────────────────────────────

def namespace_exists(ns: str) -> bool:
    result = subprocess.run(
        ["kubectl", "get", "namespace", ns],
        capture_output=True, text=True
    )
    return result.returncode == 0

def create_namespace(ns: str):
    """Tạo namespace idempotent — không lỗi nếu đã tồn tại."""
    result = subprocess.run(
        f"kubectl create namespace {ns} --dry-run=client -o yaml | kubectl apply -f -",
        shell=True, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Không tạo được namespace {ns}: {result.stderr}")
    log.info(f"    → Namespace '{ns}' ready")

    # Copy ovs-net sang namespace mới để Multus không bị lỗi
    ovs_result = subprocess.run(
        f"kubectl get network-attachment-definition ovs-net -n default -o yaml "
        f"| sed 's/namespace: default/namespace: {ns}/' "
        f"| kubectl apply -f -",
        shell=True, capture_output=True, text=True
    )
    if ovs_result.returncode == 0:
        log.info(f"    → ovs-net copied to '{ns}'")
    else:
        log.warning(f"    ⚠ Không copy được ovs-net: {ovs_result.stderr.strip()}")
def delete_namespace(ns: str):
    """Xóa namespace — force delete, không hang."""
    check = subprocess.run(
        ["kubectl", "get", "namespace", ns],
        capture_output=True, text=True
    )
    if check.returncode != 0:
        log.info(f"    → Namespace '{ns}' không tồn tại, bỏ qua")
        return

    log.info(f"    → Xóa namespace '{ns}' (force)...")
    result = subprocess.run(
        ["kubectl", "delete", "namespace", ns,
         "--ignore-not-found", "--force", "--grace-period=0"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        log.warning(f"    ⚠ Lỗi xóa namespace: {result.stderr.strip()}")
    else:
        log.info(f"    → Namespace '{ns}' deleted")
def get_webui_nodeports(namespace: str = None) -> set:
    """Lấy tất cả NodePort của webui service."""
    ns_flag = ["-n", namespace] if namespace else ["--all-namespaces"]
    result = subprocess.run(
        ["kubectl", "get", "svc"] + ns_flag + ["-o", "json"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        log.error(f"kubectl error: {result.stderr.strip()}")
        return set()

    data = json.loads(result.stdout)
    ports = set()
    for svc in data.get("items", []):
        name = svc["metadata"]["name"]
        if "webui" not in name.lower():
            continue
        for port_spec in svc.get("spec", {}).get("ports", []):
            node_port = port_spec.get("nodePort")
            if node_port:
                ports.add(node_port)
    return ports

def wait_for_new_webui(ports_before: set, timeout: int = 180, interval: int = 5) -> Optional[int]:
    """Poll K8s chờ webui NodePort mới xuất hiện."""
    log.info(f"    → Đang chờ webui mới (timeout={timeout}s)...")
    elapsed = 0
    while elapsed < timeout:
        time.sleep(interval)
        elapsed += interval
        after = get_webui_nodeports()
        new_ports = after - ports_before
        if new_ports:
            port = next(iter(new_ports))
            log.info(f"    → Phát hiện webui NodePort mới: {port} (sau {elapsed}s)")
            return port
        log.info(f"    → Đang chờ... ({elapsed}s/{timeout}s)")

    log.error(f"    → Timeout sau {timeout}s")
    return None

# ─── free5gmano API helpers ───────────────────────────────────────────────────

def get_slice_templates() -> list:
    resp = requests.get(
        f"{FREE5G_URL}/ObjectManagement/SliceTemplate/",
        headers={"Accept": "application/json"},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()

def allocate_nssi(template_id: str) -> dict:
    body = {
        "attributeListIn": {
            "nsstid":        template_id,
            "using_existed": False,
        }
    }

    log.info(f"    → Gọi API allocate NSSI với templateId={template_id}")

    resp = requests.post(
        f"{FREE5G_URL}/ObjectManagement/NSS/SliceProfiles/",
        json=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        timeout=60
    )

    if resp.status_code not in (200, 201):
        log.error(f"    → API lỗi: HTTP {resp.status_code} — {resp.text}")
        resp.raise_for_status()

    return resp.json()

def terminate_nssi(nssi_id: str):
    """Xóa NSSI trên free5gmano."""
    # Thử các endpoint có thể có
    endpoints = [
        f"{FREE5G_URL}/ObjectManagement/NSS/SliceProfiles/{nssi_id}/",
        f"{FREE5G_URL}/ObjectManagement/NSS/{nssi_id}/",
    ]
    for url in endpoints:
        resp = requests.delete(url, timeout=30)
        if resp.status_code in (200, 204, 404):
            log.info(f"    → NSSI {nssi_id} terminated (HTTP {resp.status_code})")
            return
    log.warning(f"    ⚠ Không terminate được NSSI {nssi_id}, xóa namespace thủ công")

# ─── Core operations ──────────────────────────────────────────────────────────

def cmd_create(template_id: str):
    """Tạo slice mới với namespace riêng."""
    print("\n" + "="*60)

    registry = load_registry()
    slot = next_free_slot(registry)
    if not slot:
        print(f"❌ Hết slot (tối đa {MAX_SLOTS} slice)")
        sys.exit(1)

    slice_name = f"slice-{slot}"
    print(f"[1/4] Slot: {slot} → Tên slice: {slice_name}")

    # Bước 1: Tạo namespace
    print(f"[2/4] Tạo namespace '{slice_name}'...")
    create_namespace(slice_name)

    # Bước 2: Snapshot webui ports trước khi deploy
    ports_before = get_webui_nodeports()
    log.info(f"      Webui ports hiện có: {ports_before}")

    # Bước 3: Allocate NSSI qua free5gmano API
    print(f"[3/4] Allocate NSSI (templateId={template_id})...")
    result = allocate_nssi(template_id)

    # Lấy nssi_id từ response (thử nhiều key)
    nssi_id = (
        result.get("instanceId")
        or result.get("nssiId")
        or result.get("id")
        or template_id
    )
    if isinstance(nssi_id, list):
        nssi_id = nssi_id[-1]
    log.info(f"      nssi_id = {nssi_id}")

    # Bước 4: Chờ webui NodePort mới
    print(f"[4/4] Chờ webui NodePort xuất hiện...")
    webui_port = wait_for_new_webui(ports_before)
    webui_url  = f"http://{K8S_HOST}:{webui_port}" if webui_port else None

    # Lưu vào registry
    registry["slots"][slot] = {
        "nssi_id":    nssi_id,
        "slice_name": slice_name,
        "namespace":  slice_name,
        "template_id": template_id,
        "webui_port": webui_port,
        "webui_url":  webui_url,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_registry(registry)

    print("\n✅ Slice created!")
    print(f"   Slot        : {slot}")
    print(f"   Tên slice   : {slice_name}")
    print(f"   Namespace   : {slice_name}")
    print(f"   NSSI ID     : {nssi_id}")
    print(f"   WebUI URL   : {webui_url or '⚠ Không detect được'}")
    print()


def cmd_delete(slice_name: str):
    """Xóa slice và giải phóng slot."""
    print("\n" + "="*60)
    print(f"Xóa slice: {slice_name}")
    print("="*60)

    registry = load_registry()
    slot = find_slot_by_name(registry, slice_name)

    if not slot:
        print(f"❌ Không tìm thấy slice '{slice_name}' trong registry")
        print("   Chạy 'python3 slice_registry.py list' để xem danh sách")
        sys.exit(1)

    info = registry["slots"][slot]
    nssi_id = info["nssi_id"]

    # Bước 1: Terminate NSSI trên free5gmano
    print(f"[1/3] Terminate NSSI {nssi_id}...")
    terminate_nssi(nssi_id)

    # Bước 2: Xóa Kubernetes namespace
    print(f"[2/3] Xóa namespace '{slice_name}'...")
    delete_namespace(slice_name)

    # Bước 3: Giải phóng slot
    print(f"[3/3] Giải phóng slot {slot}...")
    registry["slots"][slot] = None
    save_registry(registry)

    print(f"\n✅ Slice '{slice_name}' đã xóa, slot {slot} trống")
    print()


def cmd_list():
    """Liệt kê tất cả slice."""
    registry = load_registry()

    print(f"\n{'='*70}")
    print(f"Slice Registry (tối đa {MAX_SLOTS} slot)")
    print(f"{'='*70}")
    print(f"{'Slot':<6} {'Tên slice':<12} {'Namespace':<12} {'WebUI URL':<35} {'Status'}")
    print(f"{'-'*70}")

    for slot in sorted(registry["slots"], key=int):
        info = registry["slots"][slot]
        if info:
            print(
                f"{slot:<6} "
                f"{info['slice_name']:<12} "
                f"{info['namespace']:<12} "
                f"{(info.get('webui_url') or '-'):<35} "
                f"🟢 running"
            )
        else:
            print(f"{slot:<6} {'-':<12} {'-':<12} {'-':<35} ⚪ free")

    running = sum(1 for v in registry["slots"].values() if v)
    print(f"\nTổng: {running}/{MAX_SLOTS} slot đang dùng")
    print()


def cmd_status(slice_name: str):
    """Xem chi tiết 1 slice."""
    registry = load_registry()
    slot = find_slot_by_name(registry, slice_name)

    if not slot:
        print(f"❌ Không tìm thấy slice '{slice_name}'")
        sys.exit(1)

    info = registry["slots"][slot]
    ns   = info["namespace"]

    print(f"\n{'='*50}")
    print(f"Slice: {slice_name} (slot {slot})")
    print(f"{'='*50}")
    print(f"  NSSI ID    : {info['nssi_id']}")
    print(f"  Namespace  : {ns}")
    print(f"  Template   : {info['template_id']}")
    print(f"  WebUI URL  : {info.get('webui_url') or '-'}")
    print(f"  Created at : {info['created_at']}")

    # Kiểm tra K8s thực tế
    print(f"\n  --- Pods trong namespace {ns} ---")
    subprocess.run(["kubectl", "get", "pods", "-n", ns])
    print(f"\n  --- Services trong namespace {ns} ---")
    subprocess.run(["kubectl", "get", "svc", "-n", ns])
    print()


def cmd_list_templates():
    """Xem danh sách template có sẵn trên free5gmano."""
    print("\nĐang lấy danh sách SliceTemplate từ free5gmano...")
    try:
        templates = get_slice_templates()
    except Exception as e:
        print(f"❌ Không kết nối được free5gmano: {e}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"SliceTemplates ({len(templates)} template):")
    print(f"{'='*60}")
    for t in templates:
        print(f"  templateId  : {t.get('templateId')}")
        print(f"  description : {t.get('description')}")
        print(f"  nfvoType    : {t.get('nfvoType')}")
        instances = t.get('instanceId') or []
        print(f"  instances   : {len(instances)} đang chạy")
        print()

# ─── Main ─────────────────────────────────────────────────────────────────────
def cmd_purge(slice_name: str):
    """Xóa namespace kể cả khi không có trong registry."""
    print(f"\n[purge] Xóa namespace '{slice_name}'...")
    delete_namespace(slice_name)

    # Nếu có trong registry thì giải phóng slot luôn
    registry = load_registry()
    slot = find_slot_by_name(registry, slice_name)
    if slot:
        registry["slots"][slot] = None
        save_registry(registry)
        print(f"[purge] Slot {slot} đã giải phóng")
    print(f"[purge] ✅ Done")

USAGE = """
Slice Registry — quản lý vòng đời 5G network slice

Usage:
  python3 slice_registry.py list-templates
      Xem danh sách template trên free5gmano

  python3 slice_registry.py create <template_id>
      Tạo slice mới từ template
      Ví dụ: python3 slice_registry.py create tmpl-embb-001

  python3 slice_registry.py delete <slice-N>
      Xóa slice (xóa NSSI + xóa namespace)
      Ví dụ: python3 slice_registry.py delete slice-1

  python3 slice_registry.py list
      Xem tất cả slot (đang chạy và trống)

  python3 slice_registry.py status <slice-N>
      Xem chi tiết 1 slice kèm pod/service trong K8s
      Ví dụ: python3 slice_registry.py status slice-2
"""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "list-templates":
        cmd_list_templates()

    elif cmd == "create":
        if len(sys.argv) < 3:
            print("❌ Thiếu template_id")
            print("   Chạy 'python3 slice_registry.py list-templates' để xem danh sách")
            sys.exit(1)
        cmd_create(sys.argv[2])
    elif cmd == "purge":
        if len(sys.argv) < 3:
            print("❌ Thiếu tên slice. Ví dụ: python3 slice_registry.py purge slice-1")
            sys.exit(1)
        cmd_purge(sys.argv[2])
    elif cmd == "delete":
        if len(sys.argv) < 3:
            print("❌ Thiếu tên slice. Ví dụ: python3 slice_registry.py delete slice-1")
            sys.exit(1)
        cmd_delete(sys.argv[2])

    elif cmd == "list":
        cmd_list()

    elif cmd == "status":
        if len(sys.argv) < 3:
            print("❌ Thiếu tên slice. Ví dụ: python3 slice_registry.py status slice-1")
            sys.exit(1)
        cmd_status(sys.argv[2])

    else:
        print(f"❌ Lệnh không hợp lệ: '{cmd}'")
        print(USAGE)
        sys.exit(1)
