# Intent-Based Slice Provisioning WebUI

This Django app is the Free5GMANO presentation and proxy layer for business-intent-driven core slice provisioning.

## User outcome

An application owner supplies five non-network inputs: service type, quality, scale, priority, and duration. The backend translates that request into a Technical SLA, evaluates current slice instances and the eMBB/URLLC/mMTC template catalog, and returns a dry-run plan or executes reuse/allocation through Free5GMANO.

The page does not connect a device, start radio software, change a subscriber, or create a data session. Those actions remain a separate manual validation step after the slice reports operational health.

## Page sections

1. Service Intent
2. Translated Technical SLA
3. Candidate Comparison
4. Allocation Lifecycle
5. Deployed Slice Configuration
6. Active Slice Inventory

## API proxy

The app preserves the public bookmark `/Intent_Attach/` and proxies these routes to the service on port `8890`:

- `GET /Intent_Attach/api/catalog/`
- `POST /Intent_Attach/api/intents/`
- `GET /Intent_Attach/api/intents/<intent_id>/`
- `GET /Intent_Attach/api/intents/<intent_id>/configuration/`
- `GET /Intent_Attach/api/instances/`

Set `SLICE_INTENT_API` to override the default backend URL. `UE_INTENT_API` remains a compatibility fallback for the deployed service environment.

## Safety behavior

- The UI defaults to `execution_mode=dry_run`.
- Execute mode requires an explicit browser confirmation.
- The WebUI accepts only the Service Intent v2 schema.
- It rejects device identity, observed KPI, S-NSSAI, 5QI, and other technical override fields.
- The backend retains the legacy attach API during migration, but this app no longer exposes or calls it.

## Evidence model

`DECLARED` means a capability comes from policy or a descriptor. `ENFORCED` means the deployment has an active enforcement mechanism. `BENCHMARKED` means a controlled test produced evidence. A declared throughput or latency value is not a measurement and is not an end-to-end guarantee.

See `docs/INTENT-BASED-SLICE-PROVISIONING-TEST-VI.md` for the complete manual test flow.
