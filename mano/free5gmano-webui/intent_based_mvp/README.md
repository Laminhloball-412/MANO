# Intent-based UE Attach MVP

This Django app is the minimal Free5GMANO integration. It owns only the UI,
input normalization, and proxy endpoints. Slice selection, slice lifecycle,
runtime UE configuration, gNB restart, UE attach, and PDU verification remain
in the single UE Intent API on port 8890.

## MVP scope

- Select one registered UE.
- Submit a simulated, aggregated Prometheus KPI observation.
- Show the selected fixed eMBB/URLLC/mMTC slice.
- Reuse or create that slice through the existing backend.
- Show PDU status and assigned UE IP.

Internet verification and Prometheus closed-loop reassignment are deliberately
outside this MVP. The current classifier compares the observed KPI vector with
fixed eMBB, URLLC, and mMTC reference vectors using normalized weighted
distance. It is a deterministic baseline for testing, not a trained AI model.

## Prometheus upgrade boundary

Do not send the raw Prometheus HTTP response to the decision policy. A future
adapter should query a stable window, aggregate time-series samples per UE,
flow, PDU and S-NSSAI, and produce the normalized document shown in
`examples/prometheus-normalized-future.json`. `qos_requirements` remains the
service objective; `observed_kpis` contains measured runtime evidence.
