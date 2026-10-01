from __future__ import annotations

import json
import math
from pathlib import Path


CATALOG_PATH = Path(__file__).with_name("service_catalog.json")
STANDARDIZED_5QI_VALUES = {
    1, 2, 3, 4, 5, 6, 7, 8, 9,
    65, 66, 67, 69, 70, 71, 72, 73, 74, 75, 76, 79, 80,
    82, 83, 84, 85, 86, 87, 88, 89,
}
SERVICE_TYPE_ALIASES = {"massive_sensor": "sensor_telemetry"}
ALLOWED_FIELDS = {
    "schema_version",
    "service_type",
    "quality",
    "scale",
    "priority",
    "duration_minutes",
    "execution_mode",
}


def load_catalog(path=None):
    source = Path(path) if path else CATALOG_PATH
    with source.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if value.get("catalog_version") != "2.0":
        raise ValueError("Unsupported service catalog version")
    return value


def _integer(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{} must be an integer".format(field))
    if not math.isfinite(float(value)) or int(value) != value:
        raise ValueError("{} must be a finite integer".format(field))
    return int(value)


def normalize_qos_profile(value, field="qos"):
    if not isinstance(value, dict):
        raise ValueError("{} must be an object".format(field))
    five_qi = _integer(value.get("five_qi"), "{}.five_qi".format(field))
    if five_qi not in STANDARDIZED_5QI_VALUES:
        raise ValueError("{}.five_qi is not a supported standardized 5QI".format(field))
    arp = _integer(value.get("arp_priority"), "{}.arp_priority".format(field))
    if not 1 <= arp <= 15:
        raise ValueError("{}.arp_priority must be in the range 1..15".format(field))
    resource_type = str(value.get("resource_type", "")).strip().upper()
    if resource_type not in {"GBR", "NON_GBR", "DELAY_CRITICAL_GBR"}:
        raise ValueError(
            "{}.resource_type must be GBR, NON_GBR, or "
            "DELAY_CRITICAL_GBR".format(field)
        )

    normalized = {
        "five_qi": five_qi,
        "arp_priority": arp,
        "resource_type": resource_type,
    }
    if value.get("five_qi_source"):
        normalized["five_qi_source"] = str(value["five_qi_source"])
    if value.get("arp_source"):
        normalized["arp_source"] = str(value["arp_source"])
    if value.get("example_service"):
        normalized["example_service"] = str(value["example_service"])
    if value.get("packet_error_rate"):
        normalized["packet_error_rate"] = str(value["packet_error_rate"])
    if value.get("five_qi_priority_level") is not None:
        normalized["five_qi_priority_level"] = _integer(
            value["five_qi_priority_level"],
            "{}.five_qi_priority_level".format(field),
        )
    if value.get("packet_delay_budget_ms") is not None:
        packet_delay_budget = _integer(
            value["packet_delay_budget_ms"],
            "{}.packet_delay_budget_ms".format(field),
        )
        if packet_delay_budget <= 0:
            raise ValueError(
                "{}.packet_delay_budget_ms must be positive".format(field)
            )
        normalized["packet_delay_budget_ms"] = packet_delay_budget
    gfbr = value.get("gfbr_mbps")
    mfbr = value.get("mfbr_mbps")
    if resource_type == "NON_GBR":
        if gfbr is not None or mfbr is not None:
            raise ValueError("{} must not define GFBR or MFBR".format(field))
        return normalized

    if isinstance(gfbr, bool) or not isinstance(gfbr, (int, float)) or not math.isfinite(float(gfbr)) or gfbr <= 0:
        raise ValueError("{}.GFBR must be a positive number".format(field))
    if isinstance(mfbr, bool) or not isinstance(mfbr, (int, float)) or not math.isfinite(float(mfbr)) or mfbr <= 0:
        raise ValueError("{}.MFBR must be a positive number".format(field))
    if mfbr < gfbr:
        raise ValueError("{}.MFBR must be greater than or equal to GFBR".format(field))
    normalized["gfbr_mbps"] = _rounded(gfbr)
    normalized["mfbr_mbps"] = _rounded(mfbr)
    return normalized


def normalize_service_intent(value, catalog=None):
    catalog = catalog or load_catalog()
    if not isinstance(value, dict):
        raise ValueError("Service Intent must be an object")
    unknown = set(value) - ALLOWED_FIELDS
    if unknown:
        raise ValueError("Unsupported Service Intent fields: {}".format(sorted(unknown)))
    if value.get("schema_version", "2.0") != "2.0":
        raise ValueError("schema_version must be 2.0")

    service_type = str(value.get("service_type", "")).strip().lower()
    service_type = SERVICE_TYPE_ALIASES.get(service_type, service_type)
    if service_type not in catalog["services"]:
        raise ValueError("service_type must be one of {}".format(sorted(catalog["services"])))
    service = catalog["services"][service_type]

    quality = str(value.get("quality", "")).strip().lower()
    if quality not in service["qualities"]:
        raise ValueError("quality must be one of {}".format(sorted(service["qualities"])))

    priority = str(value.get("priority", "normal")).strip().lower()
    if priority not in catalog["priorities"]:
        raise ValueError("priority must be one of {}".format(sorted(catalog["priorities"])))

    scale = _integer(value.get("scale"), "scale")
    scale_bounds = service["scale"]
    if not scale_bounds["minimum"] <= scale <= scale_bounds["maximum"]:
        raise ValueError(
            "scale must be in the range {}..{}".format(
                scale_bounds["minimum"], scale_bounds["maximum"]
            )
        )

    duration = _integer(value.get("duration_minutes"), "duration_minutes")
    duration_bounds = catalog["duration_minutes"]
    if not duration_bounds["minimum"] <= duration <= duration_bounds["maximum"]:
        raise ValueError(
            "duration_minutes must be in the range {}..{}".format(
                duration_bounds["minimum"], duration_bounds["maximum"]
            )
        )

    execution_mode = str(value.get("execution_mode", "dry_run")).strip().lower()
    if execution_mode not in {"dry_run", "execute"}:
        raise ValueError("execution_mode must be dry_run or execute")

    return {
        "schema_version": "2.0",
        "service_type": service_type,
        "quality": quality,
        "scale": scale,
        "priority": priority,
        "duration_minutes": duration,
        "execution_mode": execution_mode,
    }


def _rounded(value):
    return round(float(value), 3)


def translate_service_intent(value, catalog=None):
    catalog = catalog or load_catalog()
    intent = normalize_service_intent(value, catalog)
    service = catalog["services"][intent["service_type"]]
    rule = service["qualities"][intent["quality"]]
    priority = catalog["priorities"][intent["priority"]]
    template = catalog["templates"][rule["preferred_template"]]
    default_qos = normalize_qos_profile(template["default_qos"], "default_qos")
    service_qos = normalize_qos_profile(service["qos"], "service_qos")

    target = rule["throughput_per_unit_mbps"] * intent["scale"]
    minimum = target * rule["guaranteed_ratio"]
    peak = target * rule["burst_ratio"]
    technical_sla = {
        "min_throughput_mbps": _rounded(minimum),
        "target_throughput_mbps": _rounded(target),
        "peak_throughput_mbps": _rounded(peak),
        "max_latency_ms": _rounded(rule["max_latency_ms"]),
        "max_jitter_ms": _rounded(rule["max_jitter_ms"]),
        "max_packet_loss_percent": _rounded(rule["max_packet_loss_percent"]),
        "min_reliability_percent": _rounded(rule["min_reliability_percent"]),
        "expected_sessions": intent["scale"],
        "message_rate_per_second": _rounded(
            rule["message_rate_per_unit"] * intent["scale"]
        ),
        "preferred_template": rule["preferred_template"],
    }
    deployment_policy = {
        "template_family": rule["preferred_template"],
        "dnn": template["dnn"],
        "default_qos": default_qos,
        "service_qos": dict(service_qos, profile=intent["service_type"]),
        "five_qi": service_qos["five_qi"],
        "arp_priority": service_qos["arp_priority"],
        "allocation_preference": priority["allocation_preference"],
        "session_ambr_mbps": _rounded(
            min(peak, template["session_ambr_max_mbps"])
        ),
        "downlink_limit_mbps": _rounded(
            min(peak, template["max_throughput_mbps"])
        ),
        "evidence_status": template["evidence_status"],
    }
    return {
        "intent": intent,
        "technical_sla": technical_sla,
        "deployment_policy": deployment_policy,
        "translation_trace": {
            "catalog_version": catalog["catalog_version"],
            "service_label": service["label"],
            "scale_label": service["scale_label"],
            "formula": {
                "throughput_per_unit_mbps": rule["throughput_per_unit_mbps"],
                "scale": intent["scale"],
                "guaranteed_ratio": rule["guaranteed_ratio"],
                "burst_ratio": rule["burst_ratio"],
            },
            "output": technical_sla,
        },
    }
