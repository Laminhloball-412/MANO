# Business Intent and Shared NSSF Integration

## Overview

Free5GMANO will expose a business-facing intent page and complete the slice
provisioning lifecycle through shared Open5GS NSSF registration. A customer can
describe a service in ordinary Vietnamese, review the deterministic technical
translation, and request execution. A slice becomes `READY_FOR_UE` only after
Free5GMANO has instantiated and verified its per-slice functions and reconciled
the resulting S-NSSAI into the shared AMF and NSSF.

The design keeps the existing structured `/Intent_Attach/` page as the
engineering and audit view. It adds `/Business_Intent/` as the customer entry
point and reuses the existing Slice Intent API, policy catalog, candidate
selection, Auto Slice allocator, and Kubernetes deployment pipeline.

## Goals

- Accept a Vietnamese business requirement without requiring the customer to
  know SST, SD, 5QI, ARP, DNN, AMF, SMF, UPF, or NSSF.
- Produce an auditable and deterministic translation into a structured service
  intent, Technical SLA, deployment policy, and slice template family.
- Reuse an operational compatible slice or allocate a new slice through
  Free5GMANO and kube5gnfvo.
- Run one Open5GS NSSF in the `share` namespace for all managed slices.
- Reconcile active S-NSSAIs into both AMF and NSSF after successful allocation.
- Expose enough lifecycle evidence to demonstrate the six-step flow requested
  by the supervisor.
- Preserve existing slice workloads and unrelated uncommitted project changes.

## Non-goals

- The intent engine will not use an external LLM. Lab results must remain
  reproducible without Internet access or model credentials.
- Provisioning will not infer a UE's application by monitoring traffic on a
  default slice and then migrate the UE.
- NSSF will not allocate PRBs. Radio resources remain the responsibility of
  the gNB scheduler and RRM configuration.
- Provisioning will not claim an end-to-end radio SLA from declared descriptor
  values. UE and gNB traffic tests remain separate evidence.
- The system will not create one NSSF per `slice-N` namespace.

## Current System

The current lab at `192.168.180.35` has these relevant components:

- Free5GMANO WebUI and Auto Slice orchestration.
- A Slice Intent API on port `8890`.
- `intent_flow_v2`, which renders VNF, NSD, and NRM packages and allocates
  PCF, SMF, and UPF workloads into `slice-N` namespaces.
- Shared Open5GS AMF, AUSF, BSF, NRF, UDM, UDR, MongoDB, and WebUI workloads in
  the `share` namespace.
- No NSSF Deployment, Service, or ConfigMap in `share`.
- An Open5GS v2.7.7 image that already contains `open5gs-nssfd`.

The current AMF ConfigMap contains SST 1, 2, and 3 with SD `000001`. Although
`intent_flow_v2/config.json` contains `shared_amf_sync_*` settings, the current
orchestrator does not invoke an AMF reconciliation implementation. The new
design makes the ownership and update path explicit.

## Customer Input

### Page and route

Add a Free5GMANO WebUI menu item named **Business Intent** with this route:

```text
http://192.168.180.35:8888/Business_Intent/
```

The page contains:

- a required `business_requirement` textarea;
- optional `scale` and `duration_minutes` fields used when the sentence does
  not provide them;
- an `execution_mode` selector with `dry_run` as the default;
- a translation preview;
- a confirmation action before any execute request;
- lifecycle, deployed configuration, NSSF evidence, and UE-ready configuration
  sections.

Example input:

```text
Tôi cần mạng cho 20 robot trong nhà máy, phản hồi thời gian thực,
độ trễ rất thấp và độ tin cậy cao trong 2 giờ.
```

### API contract

Add a business-intent translation endpoint while preserving the current
structured endpoint:

```http
POST /api/business-intents/translate/
Content-Type: application/json
```

```json
{
  "schema_version": "1.0",
  "business_requirement": "Tôi cần mạng cho 20 robot trong nhà máy, phản hồi thời gian thực, độ trễ rất thấp và độ tin cậy cao trong 2 giờ.",
  "execution_mode": "dry_run"
}
```

The response includes the normalized structured intent, extracted facts,
matched rules, warnings, confidence, Technical SLA, deployment policy, and the
selected template family. Execution uses the normalized structured intent and
the existing asynchronous provisioning job API rather than implementing a
second allocator.

## Intent Translation

The translator is deterministic and catalog-driven.

1. Normalize Vietnamese text without discarding the original sentence.
2. Extract explicit counts and durations with bounded parsing rules.
3. Match domain and service phrases against versioned catalog entries.
4. Map the request to one of the current service types:
   - video, streaming, camera, or viewers -> `live_video`;
   - robot, industrial control, real-time control, or very low latency ->
     `interactive_control`;
   - sensors, meters, telemetry, or massive device count -> `massive_sensor`.
5. Infer `quality` and `priority` only from catalog rules. Safety-critical,
   real-time, or very-high-reliability terms map to `critical`; explicit high
   quality maps to `high`; otherwise the catalog default applies.
6. Reject ambiguous requests that match competing service families with the
   same score. Return a warning instead of silently guessing missing scale or
   duration values.
7. Pass the normalized structured intent to the existing
   `translate_service_intent()` function so Technical SLA calculations remain
   centralized.

Every result records the catalog version, matched phrases, defaults applied,
formula trace, and final mapping. This trace explains why the system selected
eMBB, URLLC, or mMTC.

## Shared NSSF Architecture

### Deployment

Add an idempotent manifest owned by the project for:

- ConfigMap `open5gs-nssf`;
- Service `nssf-svc` on SBI TCP port `7777`;
- Deployment `open5gs-nssf` in namespace `share`;
- image `nguyen0105/open5gs:v2.7.7`;
- command `open5gs-nssfd`;
- the existing `ovs-net` network attachment, matching the shared control-plane
  deployment pattern.

NSSF registers with `nrf-svc.share.svc.cluster.local:7777`. Its Network Slice
Instance entries point to the NRF used to discover the functions for each
S-NSSAI. In this lab all per-slice SMFs and PCFs register with the shared NRF,
so each managed S-NSSAI maps to that NRF URI.

### S-NSSAI reconciliation

Introduce a single reconciler as the owner of the managed S-NSSAI catalog. It
computes the desired set from:

- baseline S-NSSAIs declared in configuration;
- active managed slices whose allocation exists and whose per-slice runtime
  checks passed.

The reconciler performs these operations:

1. Read and validate the current AMF and NSSF ConfigMaps.
2. Render a sorted, de-duplicated list of `(sst, sd)` pairs.
3. Preserve unrelated Open5GS configuration and operator-managed baseline
   entries.
4. Update AMF `plmn_support[].s_nssai` and NSSF `sbi.client.nsi` from the same
   desired set.
5. Store the previous documents before mutation so a partial failure can be
   restored.
6. Roll out NSSF and AMF only when their rendered configuration changes.
7. Wait for Deployment availability and Service endpoints.
8. Verify that NSSF registers with NRF and that the effective NSSF ConfigMap
   contains the new S-NSSAI.

Deletion invokes the same reconciler after state removal. A pair remains when
another active slice or the baseline configuration still uses it.

## Provisioning Lifecycle

The execution lifecycle becomes:

```text
RECEIVED
  -> VALIDATED
  -> TRANSLATED
  -> DECIDING
  -> PLANNED
  -> EXECUTING
  -> ALLOCATING or REUSED
  -> VERIFYING_SLICE
  -> REGISTERING_NSSF
  -> VERIFYING_NSSF
  -> READY_FOR_UE
```

`READY_FOR_UE` requires all of these conditions:

- the selected or allocated namespace exists;
- PCF, SMF, and UPF pods and endpoints are ready;
- the SMF has a current PFCP association with its UPF;
- AMF advertises the S-NSSAI;
- NSSF is available and exposes an SBI endpoint;
- NSSF is registered with NRF;
- NSSF configuration maps the exact SST and SD to the shared NRF.

If allocation succeeds but registration fails, the slice remains deployed and
the job ends in `REGISTRATION_FAILED`. The system does not report it as ready
and does not delete a healthy allocation automatically. A later reconcile can
complete registration safely.

## Six-Step Demonstration

The WebUI presents the supervisor's requested flow as six auditable steps:

1. **Business need:** show the original customer sentence.
2. **Intent translation:** show extracted facts, Technical SLA, and mapping
   reasons.
3. **NFVO request:** show the chosen template, SST/SD, and allocation request.
4. **Instantiation:** show the NSSI ID and PCF/SMF/UPF readiness evidence.
5. **NSSF registration:** show AMF and NSSF synchronization plus NRF
   registration evidence.
6. **UE readiness:** show S-NSSAI, DNN, PDU Session type, and the UE/gNB values
   needed for the later radio test.

Step 6 proves that the core is ready to receive a UE request. When gNB and UE
are available, a separate controlled test supplies radio and PDU Session
evidence without changing the provisioning semantics.

## NRF Stabilization Gate

The current NRF log reports repeated `No memory in client_pool` errors and NF
profiles without usable endpoints. Before adding NSSF, implementation must:

- enumerate current NRF registrations;
- identify stale profiles and invalid advertised endpoints;
- verify whether the fault is stale state, endpoint generation, or a pool
  limit;
- fix the root cause with the smallest reversible change;
- verify that existing AMF, SMF, PCF, AUSF, UDM, UDR, and BSF registrations are
  healthy before NSSF deployment.

The implementation must not hide the error by marking NSSF healthy solely
because its pod is Running.

## Error Handling and Rollback

- Translation errors return field-level messages and make no infrastructure
  changes.
- Dry-run produces the complete translation and plan without changing
  Free5GMANO, Kubernetes, AMF, or NSSF.
- ConfigMap parsing or validation failure stops reconciliation before writing.
- A failed AMF/NSSF rollout restores the saved ConfigMap and reports the exact
  failed verification.
- Reconciliation is idempotent; repeating it with the same desired set causes
  no rollout.
- Existing operator-managed S-NSSAIs and unrelated ConfigMap keys are never
  removed.
- Existing user changes in the dirty Git worktree are preserved and are not
  included in implementation commits unless they belong to this feature.

## User Interface

The new page follows the current Free5GMANO navigation and visual conventions.
It does not duplicate the full engineering dashboard. Its primary sequence is:

1. customer sentence;
2. interpreted meaning;
3. confirmation;
4. lifecycle;
5. NSSF evidence;
6. UE-ready configuration.

The existing `/Intent_Attach/` page remains available for structured inputs,
candidate comparison, inventory, and detailed audit information. Both pages
use the same jobs and status records.

## Testing

### Unit tests

- Vietnamese normalization and keyword matching.
- Counts, durations, quality, and priority extraction.
- ambiguous and incomplete request handling.
- deterministic mapping into the existing structured intent schema.
- sorted and de-duplicated S-NSSAI rendering.
- preservation of baseline and operator-managed AMF/NSSF configuration.
- idempotent reconciliation and deletion semantics.
- lifecycle transitions and failure status.

### Integration tests

- WebUI proxy and validation for business-intent endpoints.
- Dry-run creates no namespace and does not update either ConfigMap.
- Shared NSSF Deployment, Service, endpoint, and NRF registration.
- Execute an intent that reuses an existing slice.
- Execute an intent that allocates a new slice and adds its SST/SD to AMF and
  NSSF.
- Repeat reconciliation and confirm no rollout occurs.
- Delete a managed slice and confirm safe pruning.

### End-to-end acceptance

Use an interactive-control sentence that produces URLLC. Record:

- original sentence and translation trace;
- Technical SLA and selected URLLC template;
- NSSI ID and namespace;
- healthy PCF, SMF, UPF, endpoints, and PFCP;
- AMF and NSSF effective SST/SD;
- NSSF registration visible at NRF;
- final `READY_FOR_UE` configuration;
- UE registration and PDU Session evidence when radio equipment is available.

## Delivery Order

1. Stabilize and verify NRF registrations.
2. Add and verify the shared NSSF deployment.
3. Implement tested AMF/NSSF S-NSSAI reconciliation.
4. Insert registration and verification into Auto Slice lifecycle.
5. Implement the deterministic business-intent translator and API.
6. Add the `/Business_Intent/` page and navigation.
7. Run unit, integration, dry-run, live allocation, and optional UE tests.
8. Update operator documentation with commands, expected evidence, and
   rollback procedures.
