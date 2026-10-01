from __future__ import annotations

from copy import deepcopy

from .service_intent import load_catalog


class NoFeasibleTemplate(ValueError):
    def __init__(self, candidates):
        super().__init__("NO_FEASIBLE_TEMPLATE: no slice candidate satisfies all SLA constraints")
        self.candidates = candidates


SLICE_PROFILES = {
    "embb": {
        "label": "eMBB",
        "sst": 1,
        "sd": "000001",
        "namespace": "slice-1",
        "selection_cost": 2,
        "capabilities": {
            "max_throughput_mbps": 1000.0,
            "latency_ms": 30.0,
            "jitter_ms": 15.0,
            "packet_loss_percent": 1.0,
            "reliability_percent": 99.0,
        },
    },
    "urllc": {
        "label": "URLLC",
        "sst": 2,
        "sd": "000001",
        "namespace": "slice-2",
        "selection_cost": 3,
        "capabilities": {
            "max_throughput_mbps": 100.0,
            "latency_ms": 5.0,
            "jitter_ms": 2.0,
            "packet_loss_percent": 0.01,
            "reliability_percent": 99.999,
        },
    },
    "mmtc": {
        "label": "mMTC",
        "sst": 3,
        "sd": "000001",
        # The existing lab allocation for logical slice 3 is deployed in the
        # Kubernetes namespace slice-4 (slice-3 is already used elsewhere).
        "namespace": "slice-4",
        "selection_cost": 1,
        "capabilities": {
            "max_throughput_mbps": 2.0,
            "latency_ms": 200.0,
            "jitter_ms": 100.0,
            "packet_loss_percent": 5.0,
            "reliability_percent": 95.0,
        },
    },
}


OBSERVED_REFERENCES = {
    "embb": {
        "throughput_mbps_avg": 50.0,
        "latency_ms_p95": 80.0,
        "jitter_ms_p95": 15.0,
        "packet_loss_percent_avg": 0.5,
        "reliability_percent_window": 99.5,
        "message_rate_per_second_avg": 30.0,
    },
    "urllc": {
        "throughput_mbps_avg": 5.0,
        "latency_ms_p95": 8.0,
        "jitter_ms_p95": 2.0,
        "packet_loss_percent_avg": 0.01,
        "reliability_percent_window": 99.999,
        "message_rate_per_second_avg": 100.0,
    },
    "mmtc": {
        "throughput_mbps_avg": 0.2,
        "latency_ms_p95": 180.0,
        "jitter_ms_p95": 80.0,
        "packet_loss_percent_avg": 3.0,
        "reliability_percent_window": 97.0,
        "message_rate_per_second_avg": 1.0,
    },
}

OBSERVED_SCALES = {
    "throughput_mbps_avg": 100.0,
    "latency_ms_p95": 200.0,
    "jitter_ms_p95": 100.0,
    "packet_loss_percent_avg": 5.0,
    "reliability_percent_window": 5.0,
    "message_rate_per_second_avg": 100.0,
}

OBSERVED_WEIGHTS = {
    "throughput_mbps_avg": 0.35,
    "latency_ms_p95": 0.20,
    "jitter_ms_p95": 0.15,
    "packet_loss_percent_avg": 0.10,
    "reliability_percent_window": 0.10,
    "message_rate_per_second_avg": 0.10,
}


FIELDS = {
    "min_throughput_mbps": (0.0, 100000.0),
    "max_latency_ms": (0.01, 60000.0),
    "max_jitter_ms": (0.0, 60000.0),
    "max_packet_loss_percent": (0.0, 100.0),
    "min_reliability_percent": (0.0, 100.0),
    "message_rate_per_second": (0.0, 10000000.0),
}


def normalize_requirements(value):
    if not isinstance(value, dict):
        raise ValueError("qos_requirements must be an object")
    normalized = {}
    for field, (minimum, maximum) in FIELDS.items():
        if field not in value:
            raise ValueError("Missing qos_requirements.{}".format(field))
        try:
            number = float(value[field])
        except (TypeError, ValueError):
            raise ValueError("{} must be numeric".format(field))
        if number < minimum or number > maximum:
            raise ValueError(
                "{} must be in the range {}..{}".format(field, minimum, maximum)
            )
        normalized[field] = number
    return normalized


def _evaluate(name, profile, requirements):
    capability = profile["capabilities"]
    checks = {
        "throughput": capability["max_throughput_mbps"]
        >= requirements["min_throughput_mbps"],
        "latency": capability["latency_ms"] <= requirements["max_latency_ms"],
        "jitter": capability["jitter_ms"] <= requirements["max_jitter_ms"],
        "packet_loss": capability["packet_loss_percent"]
        <= requirements["max_packet_loss_percent"],
        "reliability": capability["reliability_percent"]
        >= requirements["min_reliability_percent"],
    }
    return {
        "slice_type": name,
        "eligible": all(checks.values()),
        "checks": checks,
        "selection_cost": profile["selection_cost"],
    }


def choose_slice(qos_requirements, allowed_slice_types=None):
    requirements = normalize_requirements(qos_requirements)
    allowed = set(allowed_slice_types or SLICE_PROFILES)
    unknown = allowed - set(SLICE_PROFILES)
    if unknown:
        raise ValueError("Unknown allowed slice types: {}".format(sorted(unknown)))

    evaluations = []
    candidates = []
    for name, profile in SLICE_PROFILES.items():
        evaluation = _evaluate(name, profile, requirements)
        if name not in allowed:
            evaluation["eligible"] = False
            evaluation["excluded_by_constraint"] = True
        evaluations.append(evaluation)
        if evaluation["eligible"]:
            candidates.append((profile["selection_cost"], name, profile))

    if not candidates:
        raise ValueError("No slice profile satisfies all submitted QoS requirements")

    _, selected_name, selected_profile = min(candidates, key=lambda item: item[:2])
    selected = deepcopy(selected_profile)
    selected["slice_type"] = selected_name
    reasons = []
    capability = selected["capabilities"]
    reasons.extend(
        [
            "{} supports {:.3f} Mbps required throughput".format(
                selected["label"], requirements["min_throughput_mbps"]
            ),
            "{} achievable latency {:.3f} ms satisfies maximum {:.3f} ms".format(
                selected["label"], capability["latency_ms"], requirements["max_latency_ms"]
            ),
            "Lowest policy cost among eligible profiles: {}".format(
                selected["selection_cost"]
            ),
        ]
    )
    return {
        "requirements": requirements,
        "selected_slice": selected,
        "evaluations": evaluations,
        "reasons": reasons,
    }


def normalize_observed_kpis(value):
    if not isinstance(value, dict):
        raise ValueError("observed_kpis must be an object")
    normalized = {}
    for field in OBSERVED_SCALES:
        if field not in value:
            raise ValueError("Missing observed_kpis.{}".format(field))
        try:
            number = float(value[field])
        except (TypeError, ValueError):
            raise ValueError("observed_kpis.{} must be numeric".format(field))
        if number < 0 or ("percent" in field and number > 100):
            raise ValueError("observed_kpis.{} is outside its valid range".format(field))
        normalized[field] = number
    return normalized


def choose_slice_from_observed(observed_kpis, allowed_slice_types=None):
    observed = normalize_observed_kpis(observed_kpis)
    allowed = set(allowed_slice_types or SLICE_PROFILES)
    unknown = allowed - set(SLICE_PROFILES)
    if unknown:
        raise ValueError("Unknown allowed slice types: {}".format(sorted(unknown)))
    if not allowed:
        raise ValueError("Subscriber has no allowed fixed slice profile")

    evaluations = []
    candidates = []
    for name, reference in OBSERVED_REFERENCES.items():
        contributions = {}
        distance = 0.0
        for field, target in reference.items():
            contribution = (
                abs(observed[field] - target)
                / OBSERVED_SCALES[field]
                * OBSERVED_WEIGHTS[field]
            )
            contributions[field] = round(contribution, 6)
            distance += contribution
        permitted = name in allowed
        evaluation = {
            "slice_type": name,
            "permitted_by_subscriber": permitted,
            "distance": round(distance, 6),
            "similarity_score": round(max(0.0, 100.0 * (1.0 - distance)), 3),
            "contributions": contributions,
            "reference": reference,
        }
        evaluations.append(evaluation)
        if permitted:
            candidates.append((distance, SLICE_PROFILES[name]["selection_cost"], name))

    _, _, selected_name = min(candidates)
    selected = deepcopy(SLICE_PROFILES[selected_name])
    selected["slice_type"] = selected_name
    selected_evaluation = next(
        item for item in evaluations if item["slice_type"] == selected_name
    )
    return {
        "decision_mode": "prometheus_observation_similarity",
        "observed_kpis": observed,
        "selected_slice": selected,
        "evaluations": evaluations,
        "reasons": [
            "{} has the smallest normalized KPI distance ({:.4f})".format(
                selected["label"], selected_evaluation["distance"]
            ),
            "Only slice profiles permitted by this subscriber were considered",
            "Input represents a 30-second aggregated Prometheus observation",
        ],
    }


def _at_least(actual, required):
    return {
        "passed": float(actual) >= float(required),
        "required": float(required),
        "capability": float(actual),
        "operator": ">=",
    }


def _at_most(actual, maximum):
    return {
        "passed": float(actual) <= float(maximum),
        "required": float(maximum),
        "capability": float(actual),
        "operator": "<=",
    }


def _candidate(template_name, template, sla, instance=None):
    instance = instance or {}
    action = "REUSE" if instance else "ALLOCATE"
    residual = float(
        instance.get("residual_throughput_mbps", template["max_throughput_mbps"])
    )
    remaining_sessions = int(
        instance.get("remaining_sessions", template["max_sessions"])
    )
    healthy = bool(instance.get("healthy", True))
    checks = {
        "peak_throughput": _at_least(
            min(residual, template["max_throughput_mbps"]),
            sla["peak_throughput_mbps"],
        ),
        "min_throughput": _at_least(
            min(residual, template["guaranteed_throughput_mbps"]),
            sla["min_throughput_mbps"],
        ),
        "latency": _at_most(template["latency_ms"], sla["max_latency_ms"]),
        "jitter": _at_most(template["jitter_ms"], sla["max_jitter_ms"]),
        "packet_loss": _at_most(
            template["packet_loss_percent"], sla["max_packet_loss_percent"]
        ),
        "reliability": _at_least(
            template["reliability_percent"], sla["min_reliability_percent"]
        ),
        "sessions": _at_least(remaining_sessions, sla["expected_sessions"]),
        "message_rate": _at_least(
            template["max_message_rate_per_second"],
            sla["message_rate_per_second"],
        ),
        "health": {
            "passed": healthy,
            "required": "operational",
            "capability": "operational" if healthy else "unhealthy",
            "operator": "==",
        },
    }
    failed = [name for name, check in checks.items() if not check["passed"]]
    peak = max(float(sla["peak_throughput_mbps"]), 1e-9)
    overprovision = max(0.0, residual / peak - 1.0)
    passed_ratio = sum(check["passed"] for check in checks.values()) / len(checks)
    evidence = instance.get("evidence_status", template["evidence_status"])
    default_qos = template.get("default_qos") or {}
    five_qi = template.get("five_qi")
    if five_qi is None:
        five_qi = default_qos["five_qi"]
    arp_priority = template.get("default_arp_priority")
    if arp_priority is None:
        arp_priority = default_qos["arp_priority"]
    return {
        "candidate_id": instance.get("candidate_id")
        or instance.get("namespace")
        or "template:{}".format(template_name),
        "action": action,
        "template_family": template_name,
        "label": template["label"],
        "namespace": instance.get("namespace"),
        "sst": int(instance.get("sst", template["sst"])),
        "sd": instance.get("sd"),
        "dnn": template["dnn"],
        "five_qi": int(five_qi),
        "arp_priority": int(arp_priority),
        "selection_cost": template["selection_cost"],
        "residual_throughput_mbps": residual,
        "remaining_sessions": remaining_sessions,
        "evidence_status": evidence,
        "eligible": not failed,
        "checks": checks,
        "failed_checks": failed,
        "overprovision_ratio": round(overprovision, 6),
        "suitability_percent": round(100.0 * passed_ratio, 2),
        "suitability_role": "EXPLANATORY_ONLY",
        "capabilities": deepcopy(template),
    }


def decide_slice(technical_sla, instances=None, catalog=None):
    catalog = catalog or load_catalog()
    instances = instances or []
    candidates = []
    for instance in instances:
        family = instance.get("template_family")
        if family not in catalog["templates"]:
            continue
        candidates.append(
            _candidate(family, catalog["templates"][family], technical_sla, instance)
        )
    for name, template in catalog["templates"].items():
        candidates.append(_candidate(name, template, technical_sla))

    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    candidates.sort(
        key=lambda candidate: (
            0 if candidate["eligible"] else 1,
            0
            if candidate["template_family"]
            == technical_sla["preferred_template"]
            else 1,
            0 if candidate["action"] == "REUSE" else 1,
            candidate["selection_cost"],
            candidate["overprovision_ratio"],
            candidate["candidate_id"],
        )
    )
    if not eligible:
        raise NoFeasibleTemplate(candidates)

    selected = min(
        eligible,
        key=lambda candidate: (
            0
            if candidate["template_family"]
            == technical_sla["preferred_template"]
            else 1,
            0 if candidate["action"] == "REUSE" else 1,
            candidate["selection_cost"],
            candidate["overprovision_ratio"],
            candidate["candidate_id"],
        ),
    )
    reasons = [
        "All hard SLA constraints passed for {}".format(selected["candidate_id"]),
        "Reuse was preferred" if selected["action"] == "REUSE" else "No reusable instance ranked ahead of this template",
        "Suitability is explanatory only; feasibility gates control eligibility",
    ]
    return {
        "technical_sla": deepcopy(technical_sla),
        "decision_action": selected["action"],
        "selected_candidate": selected,
        "candidates": candidates,
        "reasons": reasons,
    }
