# Business Intent and Shared NSSF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a customer-facing Vietnamese Business Intent page to Free5GMANO and make Auto Slice reconcile each operational S-NSSAI into a shared Open5GS NSSF and AMF before reporting the slice ready for UE use.

**Architecture:** Keep NSSF as one shared Open5GS network function in namespace `share`. Extend the existing deterministic Slice Intent API with a rule-based natural-language adapter, then reuse the current policy, candidate selection, Free5GMANO allocation, kube5gnfvo, and inspection path. A focused core registry reconciler owns managed S-NSSAI entries in AMF and NSSF and supplies verifiable lifecycle evidence.

**Tech Stack:** Python 3.12, Django, JavaScript, HTML/CSS, Open5GS v2.7.7, Kubernetes, PyYAML, Free5GMANO, kube5gnfvo, `unittest`.

**Spec:** `mano/docs/superpowers/specs/2026-09-04-business-intent-nssf-design.md`

## Global Constraints

- Work in the existing Git repository rooted at `/home/ubuntu` on `192.168.180.35` and preserve all unrelated dirty-worktree changes.
- Keep exactly one NSSF in namespace `share`; never add NSSF to a `slice-N` descriptor.
- Use image `nguyen0105/open5gs:v2.7.7` and SBI TCP port `7777`.
- Keep `/Intent_Attach/` and all existing structured Slice Intent API contracts working.
- Default every customer request to `dry_run`; infrastructure mutation requires explicit `execute` confirmation.
- Do not use an external LLM or require Internet access at runtime.
- Do not infer UE traffic and migrate a UE from a default slice.
- Do not claim PRB control or measured radio SLA.
- A slice is not `READY_FOR_UE` until PCF/SMF/UPF, PFCP, AMF S-NSSAI, NSSF endpoint, NSSF-to-NRF registration, and NSSF mapping all verify.
- Never delete a healthy allocated slice automatically when AMF/NSSF registration fails.
- All live ConfigMap changes require a timestamped backup and a verified rollback path.

---

## File Structure

### Shared core and reconciliation

- Create `mano/intent_flow_v2/shared_core/open5gs-nrf.yaml`: reproducible shared NRF configuration with sufficient peer capacity.
- Create `mano/intent_flow_v2/shared_core/open5gs-nssf.yaml`: ConfigMap, Service, and Deployment for the single shared NSSF.
- Create `mano/intent_flow_v2/core_slice_registry.py`: pure S-NSSAI rendering plus Kubernetes reconciliation and verification.
- Create `mano/intent_flow_v2/tests/test_core_slice_registry.py`: unit tests for normalization, preservation, idempotence, rollback inputs, and verification parsing.
- Modify `mano/intent_flow_v2/config.json`: shared NRF/NSSF resource names, baseline entries, timeouts, and backup directory.
- Modify `mano/intent_flow_v2/intent_flow.py`: lifecycle integration, CLI commands, state, delete reconciliation, doctor, and status evidence.
- Modify `mano/intent_flow_v2/tests/test_flow.py`: lifecycle tests for registration success and failure.

### Business intent backend

- Create `mano/ue-service-portal/portal/intent_based/business_intent.py`: Vietnamese deterministic extraction and mapping.
- Modify `mano/ue-service-portal/portal/intent_based/service_catalog.json`: versioned phrases and safe defaults for each service family.
- Modify `mano/ue-service-portal/portal/intent_based/provisioning.py`: business context persistence and `READY_FOR_UE` lifecycle result.
- Modify `mano/ue-service-portal/portal/intent_based/views.py`: translation and submission endpoints.
- Modify `mano/ue-service-portal/portal/intent_based/urls.py`: API routes.
- Modify `mano/ue-service-portal/portal/intent_based/tests.py`: translator, endpoint, context, and lifecycle tests.

### Free5GMANO WebUI

- Create `mano/free5gmano-webui/intent_based_mvp/business_urls.py`: routes under `/Business_Intent/`.
- Create `mano/free5gmano-webui/intent_based_mvp/templates/intent_based_mvp/business_intent.html`: customer page.
- Create `mano/free5gmano-webui/intent_based_mvp/static/intent_based_mvp/business_intent.js`: translate, confirm, submit, poll, and render.
- Create `mano/free5gmano-webui/intent_based_mvp/static/intent_based_mvp/business_intent.css`: responsive styling using the existing WebUI visual language.
- Modify `mano/free5gmano-webui/intent_based_mvp/views.py`: business API proxy and page view.
- Modify `mano/free5gmano-webui/intent_based_mvp/tests.py`: proxy and page tests.
- Modify `mano/free5gmano-webui/ui_route/urls.py`: mount `/Business_Intent/`.
- Modify `mano/free5gmano-webui/ui_templates/bar.html`: add the Business Intent navigation entry.
- Modify `mano/intent_flow_v2/webui_overlay/`: mirror every WebUI source change used by the existing deployment overlay workflow.

### Operations and evidence

- Modify `mano/docs/INTENT-BASED-SLICE-PROVISIONING-TEST-VI.md`: updated six-step test and rollback commands.
- Create `mano/docs/BUSINESS-INTENT-NSSF-OPERATIONS-VI.md`: deployment, verification, recovery, and evidence collection runbook.

---

### Task 1: Stabilize NRF capacity and establish a clean baseline

**Files:**
- Create: `mano/intent_flow_v2/shared_core/open5gs-nrf.yaml`
- Create: `mano/docs/BUSINESS-INTENT-NSSF-OPERATIONS-VI.md`

**Interfaces:**
- Consumes: live ConfigMap `share/open5gs-nrf` and Deployment `share/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu`.
- Produces: a reproducible NRF configuration with `global.max.peer: 1024` and a verified NF discovery baseline used by Task 2.

- [ ] **Step 1: Capture the live NRF baseline without mutation**

Run:

```bash
mkdir -p /home/ubuntu/mano/runtime/core-config-backups
kubectl get configmap open5gs-nrf -n share -o yaml > /home/ubuntu/mano/runtime/core-config-backups/open5gs-nrf-before-nssf.yaml
kubectl get pods,svc,endpoints -n share -o wide
kubectl logs -n share deployment/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu --since=5m > /home/ubuntu/mano/runtime/core-config-backups/nrf-before-nssf.log
```

Expected: the backup files are non-empty; the NRF pod, Service, and endpoint exist.

- [ ] **Step 2: Write the reproducible NRF manifest**

Create `shared_core/open5gs-nrf.yaml` from the live ConfigMap and add the peer limit under `global.max`:

```yaml
global:
  max:
    ue: 1024
    peer: 1024
```

Keep `db_uri`, PLMN `460/99`, SBI bind `0.0.0.0:7777`, and advertised name `nrf-svc.share.svc.cluster.local` unchanged.

- [ ] **Step 3: Validate the manifest before applying it**

Run:

```bash
kubectl apply --dry-run=server -f /home/ubuntu/mano/intent_flow_v2/shared_core/open5gs-nrf.yaml
python3 -c "import yaml; list(yaml.safe_load_all(open('/home/ubuntu/mano/intent_flow_v2/shared_core/open5gs-nrf.yaml')))"
```

Expected: both commands exit `0` and the dry-run reports `configmap/open5gs-nrf configured`.

- [ ] **Step 4: Apply and restart NRF**

Run:

```bash
kubectl apply -f /home/ubuntu/mano/intent_flow_v2/shared_core/open5gs-nrf.yaml
kubectl rollout restart deployment/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu -n share
kubectl rollout status deployment/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu -n share --timeout=120s
```

Expected: rollout completes and NRF returns to `1/1 Ready`.

- [ ] **Step 5: Verify NF discovery and the error window**

Run from the NRF pod with HTTP/2 prior knowledge:

```bash
kubectl exec -n share deployment/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu -- curl --http2-prior-knowledge -s 'http://nrf-svc.share.svc.cluster.local:7777/nnrf-disc/v1/nf-instances?target-nf-type=PCF&requester-nf-type=AMF'
kubectl logs -n share deployment/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu --since=2m
```

Expected: discovery returns registered PCFs with usable `ipEndPoints`; the new log window contains neither `No memory in client_pool` nor `NFProfile has no usable endpoint`. If either exact error remains, restore the saved ConfigMap and stop before Task 2.

- [ ] **Step 6: Document the observed baseline and recovery command**

Add the exact backup path, rollout result, discovery result summary, and this rollback command to the operations document:

```bash
kubectl apply -f /home/ubuntu/mano/runtime/core-config-backups/open5gs-nrf-before-nssf.yaml
kubectl rollout restart deployment/6ef97a96-d17e-4f21-86d3-af2532ea1nrf-gtecu -n share
```

- [ ] **Step 7: Commit the NRF baseline change**

```bash
git add mano/intent_flow_v2/shared_core/open5gs-nrf.yaml mano/docs/BUSINESS-INTENT-NSSF-OPERATIONS-VI.md
git commit -m "fix: stabilize shared NRF discovery capacity"
```

---

### Task 2: Deploy and verify the shared Open5GS NSSF

**Files:**
- Create: `mano/intent_flow_v2/shared_core/open5gs-nssf.yaml`
- Create: `mano/intent_flow_v2/tests/test_shared_core_manifest.py`
- Modify: `mano/intent_flow_v2/config.json`

**Interfaces:**
- Consumes: healthy `nrf-svc.share.svc.cluster.local:7777` from Task 1 and the existing `nguyen0105/open5gs:v2.7.7` image.
- Produces: ConfigMap `open5gs-nssf`, Service `nssf-svc`, Deployment `open5gs-nssf`, and a registered `NSSF` NF profile.

- [ ] **Step 1: Write the failing manifest contract test**

Create `tests/test_shared_core_manifest.py`:

```python
from pathlib import Path
import unittest
import yaml


ROOT = Path(__file__).resolve().parents[1]


class SharedNssfManifestTests(unittest.TestCase):
    def test_manifest_has_one_shared_nssf_and_nrf_client(self):
        docs = list(yaml.safe_load_all(
            (ROOT / "shared_core/open5gs-nssf.yaml").read_text()
        ))
        by_kind = {item["kind"]: item for item in docs}
        self.assertEqual("share", by_kind["Deployment"]["metadata"]["namespace"])
        self.assertEqual("open5gs-nssf", by_kind["Deployment"]["metadata"]["name"])
        container = by_kind["Deployment"]["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual("nguyen0105/open5gs:v2.7.7", container["image"])
        self.assertIn("open5gs-nssfd", " ".join(container["command"]))
        config = by_kind["ConfigMap"]["data"]["open5gs-nssf"]
        self.assertIn("nrf-svc.share.svc.cluster.local:7777", config)
        self.assertIn("nssf-svc.share.svc.cluster.local", config)
```

- [ ] **Step 2: Run the test and confirm the missing manifest failure**

Run:

```bash
cd /home/ubuntu/mano/intent_flow_v2
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_shared_core_manifest -v
```

Expected: FAIL with `FileNotFoundError` for `shared_core/open5gs-nssf.yaml`.

- [ ] **Step 3: Create the NSSF manifest**

The ConfigMap data must contain this effective structure, seeded with the three currently active lab pairs:

```yaml
logger:
  file:
    path: /open5gs/install/var/log/open5gs/nssf.log
global:
  max:
    ue: 1024
    peer: 1024
nssf:
  sbi:
    server:
      - address: 0.0.0.0
        port: 7777
        advertise: nssf-svc.share.svc.cluster.local
    client:
      nrf:
        - uri: http://nrf-svc.share.svc.cluster.local:7777
      nsi:
        - uri: http://nrf-svc.share.svc.cluster.local:7777
          s_nssai: {sst: 1, sd: "000001"}
        - uri: http://nrf-svc.share.svc.cluster.local:7777
          s_nssai: {sst: 2, sd: "000001"}
        - uri: http://nrf-svc.share.svc.cluster.local:7777
          s_nssai: {sst: 3, sd: "000001"}
```

The Deployment mounts the key as `/open5gs/install/etc/open5gs/nssf.yaml`, exposes container port `7777`, and runs:

```yaml
command: [sh, -c, "sleep 5 && /open5gs-entrypoint.sh && ./install/bin/open5gs-nssfd"]
```

- [ ] **Step 4: Add shared NSSF configuration keys**

Add these exact keys to `config.json`:

```json
"shared_nssf_enabled": true,
"shared_nssf_namespace": "share",
"shared_nssf_manifest": "shared_core/open5gs-nssf.yaml",
"shared_nssf_configmap": "open5gs-nssf",
"shared_nssf_config_key": "open5gs-nssf",
"shared_nssf_deployment": "open5gs-nssf",
"shared_nssf_service": "nssf-svc",
"shared_nrf_service_uri": "http://nrf-svc.share.svc.cluster.local:7777",
"core_registry_backup_dir": "runtime/core-config-backups",
"core_rollout_timeout_seconds": 120
```

- [ ] **Step 5: Run manifest tests and server dry-run**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_shared_core_manifest -v
kubectl apply --dry-run=server -f shared_core/open5gs-nssf.yaml
```

Expected: tests PASS and the server validates all three resources.

- [ ] **Step 6: Deploy NSSF and verify Kubernetes health**

```bash
kubectl apply -f shared_core/open5gs-nssf.yaml
kubectl rollout status deployment/open5gs-nssf -n share --timeout=120s
kubectl get pod,svc,endpoints -n share -l app=nssf-svc -o wide
```

Expected: one NSSF pod is Ready, `nssf-svc` exists, and its endpoint has one address on port `7777`.

- [ ] **Step 7: Verify NSSF registration through NRF discovery**

```bash
kubectl exec -n share deployment/open5gs-nssf -- curl --http2-prior-knowledge -s 'http://nrf-svc.share.svc.cluster.local:7777/nnrf-disc/v1/nf-instances?target-nf-type=NSSF&requester-nf-type=AMF'
kubectl logs -n share deployment/open5gs-nssf --since=5m
```

Expected: NRF returns one `nfType: NSSF`, status `REGISTERED`, and a usable endpoint for `nssf-svc`; NSSF logs contain no fatal/error loop.

- [ ] **Step 8: Commit the shared NSSF deployment**

```bash
git add mano/intent_flow_v2/shared_core/open5gs-nssf.yaml mano/intent_flow_v2/tests/test_shared_core_manifest.py mano/intent_flow_v2/config.json
git commit -m "feat: deploy shared Open5GS NSSF"
```

---

### Task 3: Implement the tested AMF/NSSF S-NSSAI reconciler

**Files:**
- Create: `mano/intent_flow_v2/core_slice_registry.py`
- Create: `mano/intent_flow_v2/tests/test_core_slice_registry.py`
- Modify: `mano/intent_flow_v2/requirements.txt`

**Interfaces:**
- Consumes: `dict` state records, baseline `list[dict]`, AMF/NSSF YAML strings, and a callable command runner.
- Produces: `normalize_snssai()`, `desired_snssai()`, `render_amf_config()`, `render_nssf_config()`, and `CoreSliceRegistry.reconcile()` returning registration evidence.

- [ ] **Step 1: Write failing pure rendering tests**

Create `tests/test_core_slice_registry.py` with these cases:

```python
import unittest
import yaml

from core_slice_registry import (
    desired_snssai,
    normalize_snssai,
    render_amf_config,
    render_nssf_config,
)


class CoreSliceRegistryTests(unittest.TestCase):
    def test_normalize_snssai_canonicalizes_sd(self):
        self.assertEqual((2, "00000A"), normalize_snssai({"sst": 2, "sd": "a"}))

    def test_desired_set_keeps_only_active_and_explicit_pending_pair(self):
        state = {"slices": {
            "slice-1": {"status": "READY", "sst": 1, "sd": "000001"},
            "slice-2": {"status": "DELETED", "sst": 2, "sd": "000001"},
        }}
        result = desired_snssai(state, [{"sst": 3, "sd": "000001"}], {(2, "000002")})
        self.assertEqual([(1, "000001"), (2, "000002"), (3, "000001")], result)

    def test_render_amf_preserves_unrelated_configuration(self):
        source = "amf:\n  amf_name: lab\n  plmn_support:\n  - plmn_id: {mcc: 460, mnc: 99}\n    s_nssai: []\n"
        rendered = yaml.safe_load(render_amf_config(source, [(2, "000001")]))
        self.assertEqual("lab", rendered["amf"]["amf_name"])
        self.assertEqual([{"sst": 2, "sd": "000001"}], rendered["amf"]["plmn_support"][0]["s_nssai"])

    def test_render_nssf_maps_every_pair_to_shared_nrf(self):
        source = "nssf:\n  sbi:\n    server: [{address: 0.0.0.0, port: 7777}]\n    client: {nrf: [{uri: http://nrf:7777}], nsi: []}\n"
        rendered = yaml.safe_load(render_nssf_config(source, [(1, "000001"), (2, "000002")], "http://nrf:7777"))
        entries = rendered["nssf"]["sbi"]["client"]["nsi"]
        self.assertEqual([1, 2], [item["s_nssai"]["sst"] for item in entries])
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

```bash
cd /home/ubuntu/mano/intent_flow_v2
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_core_slice_registry -v
```

Expected: FAIL with `ModuleNotFoundError: core_slice_registry`.

- [ ] **Step 3: Implement the pure functions**

Implement four pure functions with these exact callable contracts:

- `normalize_snssai(value: dict) -> tuple[int, str]`
- `desired_snssai(state: dict, baseline: list[dict], include: set[tuple[int, str]] | None = None) -> list[tuple[int, str]]`
- `render_amf_config(source: str, pairs: list[tuple[int, str]]) -> str`
- `render_nssf_config(source: str, pairs: list[tuple[int, str]], nrf_uri: str) -> str`

`normalize_snssai()` accepts SST `0..255`, accepts SD as one to six hexadecimal characters, uppercases it, and left-pads it to six characters. `desired_snssai()` includes statuses `READY`, `READY_FOR_UE`, `VERIFYING`, and `REGISTERING_NSSF`, excludes `DELETED` and `ROLLED_BACK`, de-duplicates, and sorts by numeric SST then SD.

- [ ] **Step 4: Add failing reconciliation tests with an injected runner**

Add a `FakeRunner` that records command arrays and supplies ConfigMap JSON, Deployment status, endpoint JSON, and NRF discovery JSON. Assert that:

```python
result = CoreSliceRegistry(config, runner=fake).reconcile([(2, "000002")])
self.assertTrue(result["verified"])
self.assertEqual([{"sst": 2, "sd": "000002"}], result["snssai"])
self.assertIn("NSSF", result["nrf_registration"]["nf_types"])
```

Add a second test where rendered content equals live content and assert that no `rollout restart` command is recorded.

- [ ] **Step 5: Implement `CoreSliceRegistry`**

Expose the public class `CoreSliceRegistry` with constructor
`CoreSliceRegistry(config: dict, runner=run)` and methods `ensure_nssf() -> dict`,
`snapshot() -> dict`, `reconcile(pairs: list[tuple[int, str]]) -> dict`, and
`verify(pairs: list[tuple[int, str]]) -> dict`.

`reconcile()` must snapshot both ConfigMaps into `core_registry_backup_dir`, render both documents before writing either, compare content hashes, apply changed ConfigMaps, roll out NSSF before AMF, and restore both snapshots if either rollout or verification fails. Return:

```python
{
    "verified": True,
    "snssai": [{"sst": 2, "sd": "000002"}],
    "changed": {"amf": True, "nssf": True},
    "deployments": {"amf": "Available", "nssf": "Available"},
    "nssf_endpoint": ["10.244.0.200:7777"],
    "nrf_registration": {"nf_types": ["NSSF"], "status": "REGISTERED"},
    "backup_dir": "runtime/core-config-backups/20260904T070000Z"
}
```

The real endpoint value is discovered at runtime; the example above defines the return shape only.

- [ ] **Step 6: Run the reconciler tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_core_slice_registry -v
```

Expected: all pure rendering, changed, unchanged, verification, and restoration-input tests PASS.

- [ ] **Step 7: Commit the reconciler**

```bash
git add mano/intent_flow_v2/core_slice_registry.py mano/intent_flow_v2/tests/test_core_slice_registry.py mano/intent_flow_v2/requirements.txt
git commit -m "feat: reconcile S-NSSAI across AMF and NSSF"
```

---

### Task 4: Integrate NSSF registration into Auto Slice lifecycle

**Files:**
- Modify: `mano/intent_flow_v2/intent_flow.py`
- Modify: `mano/intent_flow_v2/tests/test_flow.py`
- Modify: `mano/intent_flow_v2/README.md`

**Interfaces:**
- Consumes: `CoreSliceRegistry.reconcile()` from Task 3 and current slice state.
- Produces: `REGISTERING_NSSF`, `VERIFYING_NSSF`, `READY_FOR_UE`, `REGISTRATION_FAILED`, `core-status`, and `core-reconcile` behavior.

- [ ] **Step 1: Write failing lifecycle tests**

Inject a fake core registry into `Flow`. Add tests asserting:

```python
registry.reconcile.return_value = {"verified": True, "snssai": [{"sst": 2, "sd": "000002"}]}
record = flow.register_core_slice(store, record)
self.assertEqual("READY_FOR_UE", record["status"])
self.assertEqual("nssf_registration_verified", record["events"][-1]["event"])
```

Add a failure case where `reconcile()` raises `RuntimeError("NSSF not registered")`; assert status `REGISTRATION_FAILED`, the NSSI remains in `owned_resources`, and rollback does not delete the allocated namespace.

- [ ] **Step 2: Run targeted tests and confirm failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_flow -v
```

Expected: new tests fail because `register_core_slice` and the new statuses do not exist.

- [ ] **Step 3: Add lifecycle integration**

After `wait_ready()`, PFCP strategy, and `runtime_checks()` succeed, update the record in this order:

```python
record["status"] = "REGISTERING_NSSF"
record["events"].append({"at": now(), "event": "core_registration_started"})
store.save()
evidence = self.core_registry.reconcile(
    desired_snssai(store.data, self.config.get("shared_amf_baseline_snssai", []), {(plan.sst, plan.sd)})
)
record["status"] = "VERIFYING_NSSF"
record["core_registration"] = evidence
record["events"].append({"at": now(), "event": "nssf_registration_verified", "snssai": evidence["snssai"]})
record["status"] = "READY_FOR_UE"
```

On registration failure, keep the allocated resources, store the error and backup path, and return `REGISTRATION_FAILED` instead of entering the allocation rollback branch.

- [ ] **Step 4: Add delete reconciliation and CLI commands**

After a successful managed delete, compute the remaining desired pairs and reconcile. Add:

```text
python3 intent_flow.py core-status
python3 intent_flow.py core-reconcile
```

`core-status` is read-only. `core-reconcile` applies the desired state and prints the full evidence JSON.

- [ ] **Step 5: Extend `doctor` and `web-status`**

`doctor` must report NSSF manifest validity, current Deployment/Service/endpoint state, NRF discovery state, and configured S-NSSAI pairs. `web-status` must expose `core_registration` for each managed slice and a top-level `shared_core` summary.

- [ ] **Step 6: Run all Auto Slice tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 intent_flow.py doctor
python3 intent_flow.py core-status
```

Expected: unit tests PASS; both CLI reads return valid JSON and identify the three current S-NSSAIs.

- [ ] **Step 7: Run an idempotent live reconcile**

```bash
python3 intent_flow.py core-reconcile
python3 intent_flow.py core-reconcile
```

Expected: first run verifies AMF/NSSF/NRF. The second run reports `changed.amf=false` and `changed.nssf=false` and performs no rollout.

- [ ] **Step 8: Commit lifecycle integration**

```bash
git add mano/intent_flow_v2/intent_flow.py mano/intent_flow_v2/tests/test_flow.py mano/intent_flow_v2/README.md
git commit -m "feat: gate slice readiness on NSSF registration"
```

---

### Task 5: Add deterministic Vietnamese Business Intent translation

**Files:**
- Create: `mano/ue-service-portal/portal/intent_based/business_intent.py`
- Modify: `mano/ue-service-portal/portal/intent_based/service_catalog.json`
- Modify: `mano/ue-service-portal/portal/intent_based/tests.py`

**Interfaces:**
- Consumes: payload `{business_requirement, scale?, duration_minutes?, execution_mode?}` and the current catalog.
- Produces: `translate_business_requirement(payload, catalog=None) -> dict` with normalized structured intent and an auditable extraction trace.

- [ ] **Step 1: Add failing translator tests**

Add:

```python
from .business_intent import AmbiguousBusinessIntent, translate_business_requirement


class BusinessIntentTranslationTests(SimpleTestCase):
    def test_robot_factory_sentence_maps_to_critical_urllc(self):
        result = translate_business_requirement({
            "business_requirement": "Tôi cần mạng cho 20 robot trong nhà máy, phản hồi thời gian thực, độ trễ rất thấp và độ tin cậy cao trong 2 giờ.",
            "execution_mode": "dry_run",
        })
        self.assertEqual("interactive_control", result["structured_intent"]["service_type"])
        self.assertEqual("critical", result["structured_intent"]["quality"])
        self.assertEqual(20, result["structured_intent"]["scale"])
        self.assertEqual(120, result["structured_intent"]["duration_minutes"])
        self.assertEqual("urllc", result["technical_sla"]["preferred_template"])
        self.assertIn("robot", result["trace"]["matched_phrases"])

    def test_video_sentence_maps_to_embb(self):
        result = translate_business_requirement({
            "business_requirement": "Phát video trực tiếp chất lượng cao cho 100 người xem trong 30 phút"
        })
        self.assertEqual("live_video", result["structured_intent"]["service_type"])
        self.assertEqual("embb", result["technical_sla"]["preferred_template"])

    def test_ambiguous_request_requires_customer_clarification(self):
        with self.assertRaises(AmbiguousBusinessIntent):
            translate_business_requirement({
                "business_requirement": "Video robot và hàng nghìn cảm biến"
            })
```

- [ ] **Step 2: Run tests and confirm missing module failure**

```bash
cd /home/ubuntu/mano/ue-service-portal
python3 manage.py test portal.intent_based.BusinessIntentTranslationTests -v 2
```

Expected: FAIL because `business_intent.py` does not exist.

- [ ] **Step 3: Add versioned phrase rules to the catalog**

For each current service entry add `business_phrases`, `quality_phrases`, `default_quality`, `default_scale`, and `default_duration_minutes`. Include Vietnamese phrases without encoding them into Python source logic. Required mappings include:

```json
{
  "live_video": ["video trực tiếp", "phát trực tiếp", "camera", "người xem"],
  "interactive_control": ["robot", "điều khiển", "thời gian thực", "độ trễ rất thấp", "nhà máy"],
  "massive_sensor": ["cảm biến", "đồng hồ", "telemetry", "thiết bị số lượng lớn"]
}
```

- [ ] **Step 4: Implement normalization, extraction, scoring, and delegation**

Add `AmbiguousBusinessIntent` as a `ValueError` subclass and expose
`translate_business_requirement(payload, catalog=None)`. The function must execute
this deterministic sequence: reject unknown fields and empty text; normalize case
and Vietnamese combining marks; score every catalog phrase; reject a tie at the
highest non-zero score; extract integer scale and minute/hour duration with bounded
regular expressions; derive quality and priority from explicit phrases; fill only
missing bounded values from catalog defaults; call
`translate_service_intent(structured_intent, catalog)`; then return the original
text, structured intent, trace, warnings, technical SLA, and policy.

The trace contains `catalog_version`, `matched_phrases`, `service_scores`, `extracted`, `defaults_applied`, and `confidence`. Confidence is `high` when service, scale, and duration are explicit; `medium` when one bounded default is used; `low` is returned only in preview and cannot execute until the user supplies missing values.

- [ ] **Step 5: Run all portal tests**

```bash
python3 manage.py test portal.intent_based -v 2
```

Expected: existing structured translation tests and all new business sentence tests PASS.

- [ ] **Step 6: Commit the translator**

```bash
git add mano/ue-service-portal/portal/intent_based/business_intent.py mano/ue-service-portal/portal/intent_based/service_catalog.json mano/ue-service-portal/portal/intent_based/tests.py
git commit -m "feat: translate Vietnamese business intent"
```

---

### Task 6: Expose business translation and execution APIs

**Files:**
- Modify: `mano/ue-service-portal/portal/intent_based/provisioning.py`
- Modify: `mano/ue-service-portal/portal/intent_based/views.py`
- Modify: `mano/ue-service-portal/portal/intent_based/urls.py`
- Modify: `mano/ue-service-portal/portal/intent_based/tests.py`

**Interfaces:**
- Consumes: `translate_business_requirement()` and existing `submit_job()`.
- Produces: `POST /api/business-intents/translate/` and `POST /api/business-intents/`, preserving business context in the same job store.

- [ ] **Step 1: Write failing endpoint tests**

Add RequestFactory tests that POST the robot sentence to both endpoints. Assert preview returns HTTP `200` without creating a job and submit returns HTTP `202` with `intent_id`, `business_input`, `business_translation`, and normalized `input`.

Use this submit assertion:

```python
self.assertEqual("interactive_control", body["job"]["input"]["service_type"])
self.assertEqual("QUEUED", body["job"]["status"])
self.assertEqual("RECEIVED", body["job"]["stage"])
```

- [ ] **Step 2: Run targeted tests and confirm missing route/function failures**

```bash
python3 manage.py test portal.intent_based.SliceProvisioningApiTests -v 2
```

Expected: new tests fail because the business endpoints are undefined.

- [ ] **Step 3: Extend job persistence without duplicating provisioning**

Change `JobStore.create()` to accept `context=None` and merge only these server-generated keys:

```python
{"business_input": original_text, "business_translation": trace}
```

Add:

```python
def submit_business_job(payload):
    translated = translate_business_requirement(payload)
    if translated["trace"]["confidence"] == "low" and translated["structured_intent"]["execution_mode"] == "execute":
        raise ValueError("Low-confidence business intent must be clarified before execute")
    return submit_job(
        translated["structured_intent"],
        context={
            "business_input": translated["business_requirement"],
            "business_translation": translated["trace"],
        },
    )
```

The executor continues to call existing `process_intent()` with the normalized structured `input`.

- [ ] **Step 4: Add views and routes**

Add exact routes:

```python
path("api/business-intents/translate/", views.api_business_translate, name="business_intent_translate"),
path("api/business-intents/", views.api_business_intents, name="business_intent_submit"),
```

Return validation and ambiguity failures as HTTP `400`, infrastructure failures as HTTP `500`, and successful submissions as HTTP `202`.

- [ ] **Step 5: Rename final execution state in portal results**

When the allocator returns a slice whose status is `READY_FOR_UE`, emit lifecycle events `REGISTERING_NSSF`, `VERIFYING_NSSF`, and `READY_FOR_UE` from returned core-registration evidence. Preserve `DRY_RUN` behavior and do not emit UE attachment fields.

- [ ] **Step 6: Run the portal suite**

```bash
python3 manage.py test portal.intent_based -v 2
```

Expected: all previous and new API, persistence, dry-run, and lifecycle tests PASS.

- [ ] **Step 7: Commit API integration**

```bash
git add mano/ue-service-portal/portal/intent_based/provisioning.py mano/ue-service-portal/portal/intent_based/views.py mano/ue-service-portal/portal/intent_based/urls.py mano/ue-service-portal/portal/intent_based/tests.py
git commit -m "feat: expose business intent provisioning API"
```

---

### Task 7: Add the Business Intent page to Free5GMANO WebUI

**Files:**
- Create: `mano/free5gmano-webui/intent_based_mvp/business_urls.py`
- Create: `mano/free5gmano-webui/intent_based_mvp/templates/intent_based_mvp/business_intent.html`
- Create: `mano/free5gmano-webui/intent_based_mvp/static/intent_based_mvp/business_intent.js`
- Create: `mano/free5gmano-webui/intent_based_mvp/static/intent_based_mvp/business_intent.css`
- Modify: `mano/free5gmano-webui/intent_based_mvp/views.py`
- Modify: `mano/free5gmano-webui/intent_based_mvp/tests.py`
- Modify: `mano/free5gmano-webui/ui_route/urls.py`
- Modify: `mano/free5gmano-webui/ui_templates/bar.html`
- Mirror: `mano/intent_flow_v2/webui_overlay/`

**Interfaces:**
- Consumes: portal business APIs from Task 6 through the existing WebUI proxy model.
- Produces: `/Business_Intent/`, its proxy endpoints, a six-step presentation, and a navigation item.

- [ ] **Step 1: Write failing page and proxy tests**

Add tests asserting that the page contains these visible headings:

```python
for heading in (
    "Nhu cầu nghiệp vụ",
    "Hệ thống hiểu yêu cầu",
    "Cấu hình kỹ thuật",
    "Tiến trình tạo slice",
    "Đăng ký NSSF",
    "Sẵn sàng cho UE",
):
    self.assertIn(heading, html)
```

Add proxy tests asserting `/Business_Intent/api/translate/` maps to `/api/business-intents/translate/` and `/Business_Intent/api/intents/` maps to `/api/business-intents/`.

- [ ] **Step 2: Run WebUI tests and confirm missing page failures**

```bash
cd /home/ubuntu/mano/free5gmano-webui
source .venv/bin/activate
python manage.py test intent_based_mvp -v 2
```

Expected: new tests fail because the template, routes, and views do not exist.

- [ ] **Step 3: Implement the page and proxy routes**

Mount:

```python
url(r'^Business_Intent/', include('intent_based_mvp.business_urls')),
```

Expose local routes `api/translate/`, `api/intents/`, `api/intents/<intent_id>/`, and `api/instances/`. Proxy only the allowed fields `schema_version`, `business_requirement`, `scale`, `duration_minutes`, and `execution_mode`.

- [ ] **Step 4: Implement the customer interaction**

The page begins with a large Vietnamese textarea and an example robot-factory sentence. `Phân tích yêu cầu` calls preview only. `Xác nhận và triển khai` is disabled until a successful preview, requires `window.confirm`, then submits the normalized request and polls the existing asynchronous job.

Render these exact evidence groups:

```text
Original business requirement
Matched phrases and extracted values
Technical SLA and chosen template
Namespace, NSSI ID, SST, SD, DNN, 5QI, ARP
AMF synchronized / NSSF synchronized / NRF registered
UE-ready S-NSSAI, DNN, and PDU Session type
```

- [ ] **Step 5: Add responsive styling and preserve WebUI conventions**

Reuse the existing sidebar, typography, spacing, status colors, and focus behavior. At widths below `768px`, stack the input and preview areas, keep buttons at least 44 px tall, and avoid horizontal page scrolling. Do not add radio/PRB controls or unsupported measured-SLA claims.

- [ ] **Step 6: Mirror deployment overlay files**

Copy the new template/static files and modified URL/view/navigation sources into the corresponding `intent_flow_v2/webui_overlay/` paths used by the current deployment workflow. Run a byte comparison for every mirrored file.

- [ ] **Step 7: Run WebUI tests and Django checks**

```bash
python manage.py test intent_based_mvp -v 2
python manage.py check
```

Expected: all tests PASS and Django reports no issues.

- [ ] **Step 8: Deploy the WebUI source and verify the route**

Use the project's existing WebUI deployment/restart procedure, then verify:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8888/Business_Intent/
```

Expected: HTTP `200`.

- [ ] **Step 9: Perform browser verification**

Open `http://192.168.180.35:8888/Business_Intent/` and verify desktop and narrow layouts, keyboard focus, translation preview, dry-run, error display, lifecycle polling, NSSF evidence, and navigation back to `/Intent_Attach/`.

- [ ] **Step 10: Commit the Business Intent UI**

```bash
git add mano/free5gmano-webui/intent_based_mvp mano/free5gmano-webui/ui_route/urls.py mano/free5gmano-webui/ui_templates/bar.html mano/intent_flow_v2/webui_overlay
git commit -m "feat: add Business Intent page to Free5GMANO"
```

---

### Task 8: Verify the complete six-step flow and publish operator evidence

**Files:**
- Modify: `mano/docs/INTENT-BASED-SLICE-PROVISIONING-TEST-VI.md`
- Modify: `mano/docs/BUSINESS-INTENT-NSSF-OPERATIONS-VI.md`
- Create: `mano/test-artifacts/business-intent-nssf-e2e.json`

**Interfaces:**
- Consumes: all components delivered by Tasks 1-7.
- Produces: repeatable evidence for business input, translation, NFVO allocation/reuse, runtime verification, NSSF registration, and UE-ready configuration.

- [ ] **Step 1: Run every automated suite from a clean process environment**

```bash
cd /home/ubuntu/mano/intent_flow_v2
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
cd /home/ubuntu/mano/ue-service-portal
python3 manage.py test portal.intent_based -v 2
cd /home/ubuntu/mano/free5gmano-webui
source .venv/bin/activate
python manage.py test intent_based_mvp -v 2
python manage.py check
```

Expected: every suite passes with zero failures and zero errors.

- [ ] **Step 2: Verify dry-run causes no infrastructure mutation**

Record namespace and ConfigMap resource versions, submit the robot-factory sentence with `execution_mode=dry_run`, then record them again. Assert that namespace inventory and both AMF/NSSF resource versions remain unchanged.

- [ ] **Step 3: Execute the robot-factory intent**

Submit:

```json
{
  "schema_version": "1.0",
  "business_requirement": "Tôi cần mạng cho 20 robot trong nhà máy, phản hồi thời gian thực, độ trễ rất thấp và độ tin cậy cao trong 2 giờ.",
  "execution_mode": "execute"
}
```

Expected: translation selects `interactive_control`, `critical`, URLLC, and SST `2`; the job ends in `READY_FOR_UE` through either a valid reuse or a new allocation.

- [ ] **Step 4: Collect six-step evidence into one JSON document**

Store these exact top-level keys in `business-intent-nssf-e2e.json`:

```json
{
  "business_need": {},
  "intent_translation": {},
  "nfvo_request": {},
  "instantiation": {},
  "nssf_registration": {},
  "ue_readiness": {},
  "verification_timestamp": "ISO-8601 UTC"
}
```

Populate values from the real API responses, NSSI record, Kubernetes state, AMF/NSSF effective ConfigMaps, and NRF discovery. Do not insert secrets or subscriber keys.

- [ ] **Step 5: Verify the exact new S-NSSAI in both shared functions**

```bash
kubectl get configmap open5gs-amf -n share -o jsonpath='{.data.open5gs-amf}'
kubectl get configmap open5gs-nssf -n share -o jsonpath='{.data.open5gs-nssf}'
python3 /home/ubuntu/mano/intent_flow_v2/intent_flow.py core-status
```

Expected: the allocated/reused SST and SD are present in AMF and NSSF, and core status reports `verified=true`.

- [ ] **Step 6: Verify UE-ready output without claiming attachment**

Confirm the final API/UI output contains S-NSSAI, DNN `srsapn`, PDU Session type, and gNB/UE configuration guidance. It must say `READY_FOR_UE`, not `UE_ATTACHED`, unless a real UE test is run and logs prove attachment.

- [ ] **Step 7: Run a real UE/PDU Session test only if radio equipment is available**

Configure the UE and gNB with the exact output S-NSSAI and DNN, then record AMF registration, SMF PDU Session, and UPF traffic evidence. If the radio path is unavailable, document `NOT_RUN_RADIO_UNAVAILABLE`; do not weaken the core acceptance criteria.

- [ ] **Step 8: Update the Vietnamese runbooks with observed results**

Document commands, expected output, actual job ID, namespace, NSSI ID, SST/SD, NSSF NF instance evidence, rollback steps, and the distinction between declared SLA and measured radio performance.

- [ ] **Step 9: Inspect the final diff and run a secret scan**

```bash
git diff --check
git status --short
git diff -- mano/intent_flow_v2 mano/ue-service-portal/portal/intent_based mano/free5gmano-webui/intent_based_mvp mano/docs
grep -RIn -E 'BEGIN (RSA|OPENSSH) PRIVATE KEY|subscriberKey|opcKey' mano/test-artifacts mano/docs mano/intent_flow_v2 mano/ue-service-portal mano/free5gmano-webui
```

Expected: no whitespace errors, no unrelated files staged, and no secret material in deliverables.

- [ ] **Step 10: Commit final evidence and documentation**

```bash
git add mano/docs/INTENT-BASED-SLICE-PROVISIONING-TEST-VI.md mano/docs/BUSINESS-INTENT-NSSF-OPERATIONS-VI.md mano/test-artifacts/business-intent-nssf-e2e.json
git commit -m "docs: verify business intent NSSF flow"
```
