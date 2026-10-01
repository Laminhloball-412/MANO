#!/usr/bin/env python3
"""Generate the provisioned Grafana dashboard for multi-UE/multi-PDU flows."""

from __future__ import annotations

import json
from pathlib import Path


DS = {"type": "prometheus", "uid": "PBFA97CFB590B2093"}


def target(ref: str, expr: str, legend: str = "", instant: bool = False, fmt: str = "time_series") -> dict:
    return {
        "refId": ref, "datasource": DS, "editorMode": "code", "expr": expr,
        "legendFormat": legend, "instant": instant, "range": not instant, "format": fmt,
    }


def panel(panel_id: int, kind: str, title: str, x: int, y: int, w: int, h: int,
          targets: list[dict], description: str = "", unit: str = "short",
          text_mode: str = "auto") -> dict:
    result = {
        "id": panel_id, "type": kind, "title": title, "description": description,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit, "color": {"mode": "palette-classic"},
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 12,
                           "showPoints": "never", "spanNulls": True},
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "table", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }
    if kind == "stat":
        result["options"] = {
            "colorMode": "value", "graphMode": "area", "justifyMode": "auto",
            "orientation": "horizontal", "textMode": text_mode,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        }
    elif kind == "bargauge":
        result["options"] = {
            "displayMode": "gradient", "orientation": "horizontal", "showUnfilled": True,
            "valueMode": "color", "reduceOptions": {"calcs": ["lastNotNull"], "values": False},
        }
    elif kind == "table":
        result["options"] = {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}
    return result


def variable(name: str, label: str, query: str, include_all: bool = True) -> dict:
    return {
        "name": name, "label": label, "type": "query", "datasource": DS,
        "definition": query, "query": {"query": query, "refId": "StandardVariableQuery"},
        "includeAll": include_all, "allValue": ".*", "multi": True,
        "refresh": 2, "sort": 1, "options": [],
        "current": {"selected": include_all, "text": ["All"] if include_all else "", "value": ["$__all"] if include_all else ""},
    }


def build() -> dict:
    panels = [
        panel(1, "stat", "Healthy slices", 0, 0, 4, 4,
              [target("A", 'sum(ansa_slice_health{slice_id=~"$slice"} == 1)', instant=True)],
              "All PCF/SMF/UPF pods Ready in each selected slice"),
        panel(2, "stat", "Active UEs", 4, 0, 4, 4,
              [target("A", 'count(count by (ue_id) (ansa_ue_pdu_up{ue_id=~"$ue"} == 1)) or vector(0)', instant=True)],
              "Unique UEs with at least one active PDU"),
        panel(3, "stat", "Active PDU sessions", 8, 0, 4, 4,
              [target("A", 'sum(ansa_ue_pdu_up{ue_id=~"$ue",pdu_session_id=~"$pdu"} == 1) or vector(0)', instant=True)],
              "Counts sessions, not UEs; one UE can have several PDUs"),
        panel(4, "stat", "Selected slice", 12, 0, 6, 4,
              [target("A", 'ansa_application_intent_info{ue_id=~"$ue",service=~"$service",flow_id=~"$flow",intent_id=~"$intent",slice_id!=""}',
                      'UE {{ue_id}} · {{service}} · {{slice_id}} · PDU {{pdu_session_id}}', True)],
              "Latest explainable policy decision", text_mode="name"),
        panel(5, "stat", "Intent flow state", 18, 0, 6, 4,
              [target("A", 'ansa_application_intent_info{ue_id=~"$ue",service=~"$service",flow_id=~"$flow",intent_id=~"$intent"}', 'UE {{ue_id}} · {{state}} · {{mode}} · {{intent_id}}', True)],
              "Make-before-break state machine", text_mode="name"),
        panel(6, "bargauge", "Slice suitability by UE and candidate slice", 0, 4, 12, 7,
              [target("A", 'ansa_intent_candidate_score{ue_id=~"$ue",service=~"$service",intent_id=~"$intent",profile=~"$profile",slice_id=~"$slice"}',
                      'UE {{ue_id}} · {{profile}} · {{slice_id}} · {{reason}}', True)],
              "Current per-intent ranking. Historical CLI snapshots are excluded.", "percent"),
        panel(7, "table", "Current UE → PDU → slice mapping", 12, 4, 12, 7,
              [target("A", 'ansa_ue_pdu_up{ue_id=~"$ue",pdu_session_id=~"$pdu",slice_id=~"$slice"} == 1',
                      "", True, "table")],
              "Live OAI TUNs only; this table is independent from old intent snapshots."),
        panel(8, "timeseries", "Passive throughput", 0, 11, 8, 7, [
              target("A", 'ansa_application_throughput_mbps{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"}', 'UE {{ue_id}} · {{flow_id}} · application'),
              target("B", 'ansa_ue_rx_mbps{ue_id=~"$ue",pdu_session_id=~"$pdu"}', 'UE {{ue_id}} · PDU {{pdu_session_id}} · RX'),
              target("C", 'ansa_ue_tx_mbps{ue_id=~"$ue",pdu_session_id=~"$pdu"}', 'UE {{ue_id}} · PDU {{pdu_session_id}} · TX')],
              "Instrumented flow telemetry plus passive TUN counter deltas.", unit="Mbits/sec"),
        panel(9, "timeseries", "RTT", 8, 11, 8, 7,
              [target("A", 'ansa_application_rtt_ms{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"} or ansa_ue_active_probe_rtt_ms{ue_id=~"$ue",pdu_session_id=~"$pdu"}',
                      'UE {{ue_id}} · {{source}}')], "No data until application instrumentation or an explicitly enabled active probe is present.", unit="ms"),
        panel(10, "timeseries", "Jitter", 16, 11, 8, 7,
              [target("A", 'ansa_application_jitter_ms{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"} or ansa_ue_active_probe_jitter_ms{ue_id=~"$ue",pdu_session_id=~"$pdu"}',
                      'UE {{ue_id}} · {{source}}')], "No synthetic value is inferred from throughput.", unit="ms"),
        panel(11, "timeseries", "Packet loss", 0, 18, 8, 7,
              [target("A", 'ansa_application_packet_loss_percent{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"} or ansa_ue_active_probe_packet_loss_percent{ue_id=~"$ue",pdu_session_id=~"$pdu"}',
                      'UE {{ue_id}} · {{source}}')], "No data without an instrumented application or active probe.", unit="percent"),
        panel(12, "timeseries", "Packet size", 8, 18, 8, 7,
              [target("A", 'ansa_application_packet_size_bytes{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"}', 'UE {{ue_id}} · {{flow_id}} · application'),
               target("B", 'ansa_ue_average_rx_packet_bytes{ue_id=~"$ue",pdu_session_id=~"$pdu"}', 'UE {{ue_id}} · PDU {{pdu_session_id}} · RX'),
               target("C", 'ansa_ue_average_tx_packet_bytes{ue_id=~"$ue",pdu_session_id=~"$pdu"}', 'UE {{ue_id}} · PDU {{pdu_session_id}} · TX')], unit="bytes"),
        panel(13, "timeseries", "Message rate", 16, 18, 8, 7,
              [target("A", 'ansa_application_message_rate_per_second{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"}', 'UE {{ue_id}} · {{flow_id}} · application'),
               target("B", 'ansa_ue_rx_packets_per_second{ue_id=~"$ue",pdu_session_id=~"$pdu"}', 'UE {{ue_id}} · PDU {{pdu_session_id}} · RX'),
               target("C", 'ansa_ue_tx_packets_per_second{ue_id=~"$ue",pdu_session_id=~"$pdu"}', 'UE {{ue_id}} · PDU {{pdu_session_id}} · TX')], unit="pps"),
        panel(14, "table", "Open5GS active PDU contexts", 0, 25, 12, 8,
              [target("A", 'open5gs_pdu_session_active{slice_id=~"$slice"} == 1', "", True, "table")],
              "5GC-side session evidence from AMF/SMF/UPF exporter"),
        panel(15, "table", "Connected UE inventory", 12, 25, 12, 8,
              [target("A", 'open5gs_ue_connected{slice_id=~"$slice"} == 1', "", True, "table")],
              "Network registration view; IMSI and UE address are exporter labels"),
        panel(16, "timeseries", "UE and PDU counts per slice", 0, 33, 24, 8, [
            target("A", 'open5gs_ue_count{slice_id=~"$slice"}', 'UE · {{slice_id}}'),
            target("B", 'open5gs_pdu_count{slice_id=~"$slice"}', 'PDU · {{slice_id}}'),
        ], "UE count and PDU count are separate because multi-PDU makes them unequal"),
        panel(17, "bargauge", "Score components by UE and slice", 0, 41, 12, 8,
              [target("A", 'ansa_intent_candidate_score_component{ue_id=~"$ue",service=~"$service",intent_id=~"$intent",profile=~"$profile",slice_id=~"$slice"}',
                      'UE {{ue_id}} · {{slice_id}} · {{component}}', True)],
              "Auditable inputs: requirement fit, QoS headroom, live health/load and intent affinity.", "percent"),
        panel(18, "timeseries", "Slice CPU and memory load", 12, 41, 12, 8, [
            target("A", 'ansa_slice_cpu_load_ratio{slice_id=~"$slice"}', '{{slice_id}} · CPU'),
            target("B", 'ansa_slice_memory_load_ratio{slice_id=~"$slice"}', '{{slice_id}} · memory'),
        ], "Live Kubernetes usage divided by configured resource limits.", "percentunit"),
        panel(19, "bargauge", "Configured vs enforced vs measured throughput", 0, 49, 12, 8, [
              target("A", 'ansa_slice_capacity_mbps{slice_id=~"$slice"}',
                     '{{slice_id}} · configured', True),
              target("B", 'ansa_slice_enforced_downlink_mbps{slice_id=~"$slice"}',
                     '{{slice_id}} · enforced', True),
              target("C", 'ansa_slice_benchmark_downlink_mbps{slice_id=~"$slice"} > 0',
                     '{{slice_id}} · benchmark', True)],
              "Configured policy is not a guarantee. Enforced is the live UPF qdisc; benchmark is measured evidence.", "Mbits/sec"),
        panel(20, "bargauge", "PDU session capacity usage", 12, 49, 6, 8,
              [target("A", 'open5gs_pdu_count{slice_id=~"$slice"} / on(slice_id) group_left(profile) ansa_slice_max_pdu_sessions{slice_id=~"$slice"}',
                      '{{profile}} · {{slice_id}}', True)],
              "Active 5GC PDU contexts divided by the per-slice policy limit.", "percentunit"),
        panel(21, "table", "Slice capability profile", 18, 49, 6, 8, [
            target("A", 'ansa_slice_baseline_rtt_ms{slice_id=~"$slice"}', "", True, "table"),
            target("B", 'ansa_slice_baseline_jitter_ms{slice_id=~"$slice"}', "", True, "table"),
            target("C", 'ansa_slice_baseline_loss_percent{slice_id=~"$slice"}', "", True, "table"),
            target("D", 'ansa_slice_max_message_rate_per_second{slice_id=~"$slice"}', "", True, "table"),
            target("E", 'ansa_slice_five_qi{slice_id=~"$slice"}', "", True, "table"),
            target("F", 'ansa_slice_session_ambr_mbps{slice_id=~"$slice"}', "", True, "table"),
            target("G", 'ansa_slice_arp_priority{slice_id=~"$slice"}', "", True, "table"),
            target("H", 'ansa_slice_benchmark_downlink_mbps{slice_id=~"$slice"}', "", True, "table"),
        ], "Declared QoS/AMBR/ARP plus measured baseline. A zero benchmark means the saturation test is not yet valid."),
        panel(22, "stat", "Current service SLA assessment", 0, 57, 8, 5,
              [target("A", 'ansa_intent_sla_met{ue_id=~"$ue",service=~"$service",intent_id=~"$intent"}',
                      'UE {{ue_id}} · {{service}} · {{status}}', True)],
              "Live validation result. DEGRADED remains usable but is not presented as an SLA guarantee.", text_mode="name"),
        panel(23, "bargauge", "SLA dimensions", 8, 57, 8, 5,
              [target("A", 'ansa_intent_sla_dimension_met{ue_id=~"$ue",service=~"$service",intent_id=~"$intent"}',
                      'UE {{ue_id}} · {{dimension}}', True)],
              "1 means the measured dimension meets the intent threshold; 0 means it does not.", "percentunit"),
        panel(24, "bargauge", "UPF QoS enforcement", 16, 57, 8, 5,
              [target("A", 'ansa_slice_downlink_enforcement_up{slice_id=~"$slice"}',
                      '{{slice_id}} · TBF', True)],
              "Live verification of the expected downlink TBF after UPF restarts.", "percentunit"),
        panel(25, "table", "Effective capacity used by Intent Engine", 0, 62, 12, 7,
              [target("A", 'ansa_intent_candidate_effective_capacity_mbps{ue_id=~"$ue",service=~"$service",intent_id=~"$intent",slice_id=~"$slice"}',
                      '', True, "table")],
              "Minimum credible configured/enforced/benchmark value, labelled with evidence status."),
        panel(26, "table", "Active intent and reference inventory", 12, 62, 12, 7,
              [target("A", 'ansa_pdu_reference_count{ue_id=~"$ue",pdu_session_id=~"$pdu",slice_id=~"$slice"}',
                      '', True, "table")],
              "Reference count prevents one UE/service from releasing a PDU still used by another intent."),
        panel(27, "table", "Active application flows", 0, 69, 16, 7,
              [target("A", 'ansa_application_flow_info{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"}',
                      '', True, "table")],
              "Observe-first lifecycle: application flow exists before its intent and target PDU."),
        panel(28, "stat", "Application telemetry freshness", 16, 69, 8, 7,
              [target("A", 'ansa_application_flow_sample_age_seconds{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"}',
                      'UE {{ue_id}} · {{flow_id}}', True)],
              "Intent decisions wait for a complete, fresh observation window.", "s"),
        panel(29, "row", "RAN Resource View", 0, 76, 24, 1, [],
              "Live O-CU/DU scheduler telemetry grouped by discovered cell and RNTI."),
        panel(30, "stat", "Connected RAN UEs", 0, 77, 4, 5,
              [target("A", 'sum(ocudu_ran_ue_connected{cell_id=~"$cell",rnti=~"$rnti"} == 1) or vector(0)', instant=True),
               target("B", 'ocudu_ran_metrics_sample_age_seconds', "Global sample age", True)],
              "Currently connected radio UEs matching the selected cell and RNTI. Global sample age is a separate exporter status field and is never joined to a UE row."),
        panel(31, "timeseries", "Cell DL/UL PRB utilization", 4, 77, 20, 5, [
            target("A", 'sum by (cell_id) (ocudu_ran_ue_dl_prb_share_ratio{cell_id=~"$cell",rnti=~"$rnti"})', 'Cell {{cell_id}} · DL'),
            target("B", 'sum by (cell_id) (ocudu_ran_ue_ul_prb_share_ratio{cell_id=~"$cell",rnti=~"$rnti"})', 'Cell {{cell_id}} · UL'),
        ], "Scheduled PRB-symbol share used by selected UEs, separated by direction.", "percentunit"),
        panel(32, "table", "Per-UE radio resources", 0, 82, 24, 10, [
            target("A", 'ocudu_ran_ue_connected{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("B", 'ocudu_ran_ue_dl_prbs_used{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("C", 'ocudu_ran_ue_ul_prbs_used{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("D", 'ocudu_ran_ue_dl_prb_share_ratio{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("E", 'ocudu_ran_ue_ul_prb_share_ratio{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("F", 'ocudu_ran_ue_cqi{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("G", 'ocudu_ran_ue_dl_mcs{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("H", 'ocudu_ran_ue_ul_mcs{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("I", 'ocudu_ran_ue_dl_bler_ratio{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("J", 'ocudu_ran_ue_ul_bler_ratio{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("K", 'ocudu_ran_ue_dl_bitrate_mbps{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
            target("L", 'ocudu_ran_ue_ul_bitrate_mbps{cell_id=~"$cell",rnti=~"$rnti"}', "", True),
        ], "One current row per RNTI, connection ID, and DU UE index with PRB use/share, CQI, MCS, BLER, and bitrate. Global sample age is shown separately above."),
        panel(33, "timeseries", "PRB allocation by UE", 0, 92, 12, 7, [
            target("A", 'ocudu_ran_ue_dl_prbs_used{cell_id=~"$cell",rnti=~"$rnti"}', 'RNTI {{rnti}} · DL'),
            target("B", 'ocudu_ran_ue_ul_prbs_used{cell_id=~"$cell",rnti=~"$rnti"}', 'RNTI {{rnti}} · UL'),
        ], "Allocated PRBs per discovered UE, kept distinct for downlink and uplink."),
        panel(34, "timeseries", "Stacked PRB share", 12, 92, 12, 7, [
            target("A", 'ocudu_ran_ue_dl_prb_share_ratio{cell_id=~"$cell",rnti=~"$rnti"}', 'RNTI {{rnti}} · DL'),
            target("B", 'ocudu_ran_ue_ul_prb_share_ratio{cell_id=~"$cell",rnti=~"$rnti"}', 'RNTI {{rnti}} · UL'),
        ], "Stacked scheduled PRB-symbol share by UE; legends retain the DL/UL direction.", "percentunit"),
        panel(35, "timeseries", "Throughput per PRB-symbol", 0, 99, 24, 7,
              [target("A", 'ocudu_ran_ue_dl_bitrate_mbps{cell_id=~"$cell",rnti=~"$rnti"} / clamp_min(ocudu_ran_ue_dl_prb_symbols_used{cell_id=~"$cell",rnti=~"$rnti"}, 1)',
                      'RNTI {{rnti}} · DL')],
              "Engineering comparison of downlink throughput per scheduled PRB-symbol; not 3GPP spectral efficiency.", "Mbits/sec"),
    ]
    panels[6]["transformations"] = [{
        "id": "organize",
        "options": {
            "excludeByName": {"Time": True, "__name__": True, "instance": True, "job": True, "Value": True},
            "indexByName": {"ue_id": 0, "pdu_session_id": 1, "role": 2, "state": 3,
                            "slice_id": 4, "sst": 5, "sd": 6, "dnn": 7,
                            "interface": 8},
            "renameByName": {"ue_id": "UE", "pdu_session_id": "PDU", "role": "Role",
                             "state": "State", "slice_id": "Slice", "sst": "SST", "sd": "SD",
                             "dnn": "DNN", "interface": "TUN"},
        },
    }]
    panels[29]["fieldConfig"]["overrides"] = [{
        "matcher": {"id": "byFrameRefID", "options": "B"},
        "properties": [
            {"id": "displayName", "value": "Global sample age"},
            {"id": "unit", "value": "s"},
        ],
    }]
    panels[31]["transformations"] = [
        {"id": "labelsToFields", "options": {"mode": "columns", "valueLabel": "__name__"}},
        {
            "id": "organize",
            "options": {
                "excludeByName": {"Time": True, "__name__": True, "instance": True, "job": True},
                "indexByName": {
                    "rnti": 0, "connection_id": 1, "du_ue_index": 2, "cell_id": 3, "pci": 4,
                    "ocudu_ran_ue_connected": 5,
                    "ocudu_ran_ue_dl_prbs_used": 6, "ocudu_ran_ue_ul_prbs_used": 7,
                    "ocudu_ran_ue_dl_prb_share_ratio": 8, "ocudu_ran_ue_ul_prb_share_ratio": 9,
                    "ocudu_ran_ue_cqi": 10, "ocudu_ran_ue_dl_mcs": 11, "ocudu_ran_ue_ul_mcs": 12,
                    "ocudu_ran_ue_dl_bler_ratio": 13, "ocudu_ran_ue_ul_bler_ratio": 14,
                    "ocudu_ran_ue_dl_bitrate_mbps": 15, "ocudu_ran_ue_ul_bitrate_mbps": 16,
                },
                "renameByName": {
                    "rnti": "RNTI", "connection_id": "Connection ID", "du_ue_index": "DU UE index",
                    "cell_id": "Cell", "pci": "PCI", "ocudu_ran_ue_connected": "Connected",
                    "ocudu_ran_ue_dl_prbs_used": "DL PRB", "ocudu_ran_ue_ul_prbs_used": "UL PRB",
                    "ocudu_ran_ue_dl_prb_share_ratio": "DL share", "ocudu_ran_ue_ul_prb_share_ratio": "UL share",
                    "ocudu_ran_ue_cqi": "CQI", "ocudu_ran_ue_dl_mcs": "DL MCS", "ocudu_ran_ue_ul_mcs": "UL MCS",
                    "ocudu_ran_ue_dl_bler_ratio": "DL BLER", "ocudu_ran_ue_ul_bler_ratio": "UL BLER",
                    "ocudu_ran_ue_dl_bitrate_mbps": "DL bitrate (Mbit/s)",
                    "ocudu_ran_ue_ul_bitrate_mbps": "UL bitrate (Mbit/s)",
                },
            },
        },
    ]
    panels[33]["fieldConfig"]["defaults"]["custom"]["stacking"] = {"mode": "normal", "group": "A"}
    return {
        "annotations": {"list": []},
        "description": "Explainable multi-UE, multi-PDU intent and slice operations dashboard.",
        "editable": True, "graphTooltip": 1, "id": None, "links": [], "liveNow": True,
        "panels": panels, "refresh": "5s", "schemaVersion": 39,
        "tags": ["5G", "Free5GMANO", "Multi-PDU", "Intent", "Open5GS"],
        "templating": {"list": [
            variable("ue", "UE", "label_values(ansa_ue_pdu_up, ue_id)"),
            variable("pdu", "PDU Session", 'label_values(ansa_ue_pdu_up{ue_id=~"$ue"}, pdu_session_id)'),
            variable("service", "Service", 'label_values(ansa_application_flow_info{ue_id=~"$ue"}, service)'),
            variable("flow", "Application flow", 'label_values(ansa_application_flow_info{ue_id=~"$ue",service=~"$service"}, flow_id)'),
            variable("intent", "Intent", 'label_values(ansa_application_intent_info{ue_id=~"$ue",service=~"$service",flow_id=~"$flow"}, intent_id)'),
            variable("profile", "Service class", "label_values(ansa_intent_candidate_score, profile)"),
            variable("slice", "Slice", "label_values(ansa_slice_health, slice_id)"),
            variable("cell", "RAN cell", "label_values(ocudu_ran_ue_connected, cell_id)"),
            variable("rnti", "RAN RNTI", 'label_values(ocudu_ran_ue_connected{cell_id=~"$cell"}, rnti)'),
        ]},
        "time": {"from": "now-1h", "to": "now"},
        "timepicker": {"refresh_intervals": ["5s", "10s", "15s", "30s", "1m", "5m"]},
        "timezone": "browser", "title": "ANSA – UE Multi-PDU Intent & Slice Operations",
        "uid": "auto-slice-operations", "version": 10, "weekStart": "monday",
    }


if __name__ == "__main__":
    output = Path(__file__).with_name("ansa-multipdu-dashboard.json")
    output.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(output)
