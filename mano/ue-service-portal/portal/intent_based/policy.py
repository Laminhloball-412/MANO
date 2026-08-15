from __future__ import annotations

from copy import deepcopy


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
