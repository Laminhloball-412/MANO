from __future__ import annotations

from .models import ServiceIntent, SliceCandidate, SliceProfile, SliceRuntime, TrafficMetrics


SERVICE_TO_PROFILE = {
    "video": "embb",
    "streaming": "embb",
    "control": "urllc",
    "robot": "urllc",
    "sensor": "mmtc",
    "telemetry": "mmtc",
}

SERVICE_REQUIREMENTS = {
    "embb": {"throughput": 20.0, "rtt": 150.0, "jitter": 50.0, "loss": 2.0, "message_rate": 100.0},
    "urllc": {"throughput": 1.0, "rtt": 75.0, "jitter": 30.0, "loss": 1.0, "message_rate": 100.0},
    "mmtc": {"throughput": 0.1, "rtt": 500.0, "jitter": 200.0, "loss": 5.0, "message_rate": 10000.0},
}


def service_class_for(service: str, observed: TrafficMetrics | None = None) -> str:
    """Return the explicit class, or a visible best-effort traffic inference."""
    text = service.lower()
    for token, name in SERVICE_TO_PROFILE.items():
        if token in text:
            return name
    observed = observed or TrafficMetrics(0, 0, 0, 0, 0, 0, 0, "", "")
    signatures = {
        "embb": min(30.0, observed.throughput_mbps * 3.0),
        "urllc": min(30.0, max(
            observed.rtt_ms / 50.0 * 12.0,
            observed.jitter_ms / 15.0 * 12.0,
            observed.loss_percent * 12.0,
        )),
        "mmtc": min(30.0, observed.message_rate_per_second / 100.0 * 20.0)
        + (10.0 if observed.packet_size_bytes <= 256 else 0.0),
    }
    return max(signatures, key=signatures.get)


def service_requirements(service_class: str) -> dict[str, float]:
    return dict(SERVICE_REQUIREMENTS[service_class])


def assess_service_sla(
    metrics: TrafficMetrics,
    service_class: str,
    dimensions: tuple[str, ...] = ("throughput", "rtt", "jitter", "loss", "message_rate"),
) -> dict:
    """Assess only dimensions actually measured by the current probe.

    A connectivity ping must not pretend it measured application throughput or
    message rate.  Callers therefore pass the dimensions supplied by their
    measurement source.
    """
    required = SERVICE_REQUIREMENTS[service_class]
    measured = {
        "throughput": metrics.throughput_mbps,
        "rtt": metrics.rtt_ms,
        "jitter": metrics.jitter_ms,
        "loss": metrics.loss_percent,
        "message_rate": metrics.message_rate_per_second,
    }
    checks = {}
    for name in dimensions:
        value = measured[name]
        limit = required[name]
        checks[name] = value >= limit if name in {"throughput", "message_rate"} else value <= limit
    return {
        "service_class": service_class,
        "status": "MET" if checks and all(checks.values()) else "DEGRADED",
        "source": "active_probe" if set(dimensions) <= {"rtt", "jitter", "loss"} else "application",
        "dimensions": list(dimensions),
        "requirements": {name: required[name] for name in dimensions},
        "measured": {name: measured[name] for name in dimensions},
        "checks": checks,
    }


def _ratio_at_least(actual: float, required: float) -> float:
    return min(1.0, actual / max(required, 1e-9))


def _ratio_at_most(actual: float, maximum: float) -> float:
    return min(1.0, maximum / max(actual, 1e-9))


def _effective_capacity(
    profile: SliceProfile, status: SliceRuntime
) -> tuple[float, float, str]:
    configured = min(
        value for value in (
            profile.capacity_mbps,
            profile.session_ambr_mbps,
            profile.downlink_limit_mbps,
        ) if value > 0
    )
    benchmark = profile.benchmark_downlink_mbps
    enforced = status.enforced_downlink_mbps
    values = [configured]
    if enforced is not None and enforced > 0:
        values.append(enforced)
    if benchmark > 0:
        values.append(benchmark)
    effective = min(values)
    if status.downlink_enforcement_up is False:
        return effective, 0.25, "NOT_ENFORCED"
    if status.downlink_enforcement_up is True and benchmark > 0:
        return effective, 1.0, "ENFORCED_AND_BENCHMARKED"
    if status.downlink_enforcement_up is True:
        return effective, 0.7, "ENFORCED_NOT_BENCHMARKED"
    if benchmark > 0:
        return effective, 0.7, "BENCHMARKED_ENFORCEMENT_UNKNOWN"
    return effective, 0.4, "DECLARED_ONLY"


def _requirement_fit(profile: SliceProfile, service_class: str, capacity: float) -> float:
    required = SERVICE_REQUIREMENTS[service_class]
    values = (
        _ratio_at_least(capacity, required["throughput"]),
        _ratio_at_most(profile.baseline_rtt_ms, required["rtt"]),
        _ratio_at_most(profile.baseline_jitter_ms, required["jitter"]),
        _ratio_at_most(profile.baseline_loss_percent, required["loss"]),
        _ratio_at_least(profile.max_message_rate_per_second, required["message_rate"]),
    )
    return sum(values) / len(values)


def _qos_headroom(profile: SliceProfile, service_class: str, capacity: float) -> float:
    required = SERVICE_REQUIREMENTS[service_class]
    margins = (
        max(0.0, 1.0 - required["throughput"] / max(capacity, 1e-9)),
        max(0.0, 1.0 - profile.baseline_rtt_ms / max(required["rtt"], 1e-9)),
        max(0.0, 1.0 - profile.baseline_jitter_ms / max(required["jitter"], 1e-9)),
        max(0.0, 1.0 - profile.baseline_loss_percent / max(required["loss"], 1e-9)),
        max(0.0, 1.0 - required["message_rate"] / max(profile.max_message_rate_per_second, 1e-9)),
    )
    return sum(margins) / len(margins)


def select_profile(
    intent: ServiceIntent,
    observed: TrafficMetrics,
    profiles: dict[str, SliceProfile],
) -> SliceProfile:
    selected, _, _ = evaluate_profiles(intent, observed, profiles)
    return selected


def evaluate_profiles(
    intent: ServiceIntent,
    observed: TrafficMetrics,
    profiles: dict[str, SliceProfile],
    runtime: dict[str, SliceRuntime] | None = None,
) -> tuple[SliceProfile, list[SliceCandidate], str]:
    """Rank service classes and retain an operator-readable explanation.

    Telemetry describes the current path; it cannot identify an application by
    itself. An explicit service intent therefore wins. For unknown services,
    traffic signatures provide a best-effort classification that remains
    visible as a score rather than being presented as certainty.
    """
    explicit = any(token in intent.service.lower() for token in SERVICE_TO_PROFILE)
    requested_class = service_class_for(intent.service, observed)
    runtime = runtime or {}
    ranked = []
    seen = set()
    for profile in profiles.values():
        if profile.name == "default":
            continue
        identity = (profile.namespace, profile.pdu_session_id)
        if identity in seen:
            continue
        seen.add(identity)
        name = profile.service_class or profile.name.lower().split("-")[0]
        if name not in SERVICE_REQUIREMENTS:
            continue
        status = runtime.get(profile.namespace, SliceRuntime())
        eligible = (
            (not status.exists or status.healthy)
            and status.pdu_sessions < profile.max_pdu_sessions
        )
        effective_capacity, evidence, evidence_status = _effective_capacity(profile, status)
        requirement_fit = _requirement_fit(profile, requested_class, effective_capacity)
        headroom = _qos_headroom(profile, requested_class, effective_capacity)
        pdu_load = status.pdu_sessions / max(profile.max_pdu_sessions, 1)
        health_load = (
            1.0
            if eligible and not status.exists
            else max(0.0, 1.0 - max(
                pdu_load, status.cpu_load_ratio, status.memory_load_ratio
            )) if eligible else 0.0
        )
        affinity = 1.0 if name == requested_class else 0.0
        score = 100.0 * (
            0.35 * requirement_fit
            + 0.20 * headroom
            + 0.20 * health_load
            + 0.15 * affinity
            + 0.10 * evidence
        ) if eligible else 0.0
        reason = (
            "allocatable_absent_intent_match"
            if not status.exists and affinity
            else "allocatable_absent_cross_class"
            if not status.exists
            else "eligible_intent_match"
            if affinity
            else "eligible_cross_class"
        ) if eligible else "ineligible_health_or_capacity"
        candidate = SliceCandidate(
            name, profile.namespace, profile.sst, profile.sd, profile.dnn,
            round(min(100.0, score), 2), reason,
            round(requirement_fit * 100.0, 2), round(headroom * 100.0, 2),
            round(health_load * 100.0, 2), round(affinity * 100.0, 2),
            round(evidence * 100.0, 2), round(effective_capacity, 3),
            evidence_status, eligible,
        )
        ranked.append((candidate, profile))
    if not ranked:
        raise RuntimeError("no eMBB, URLLC or mMTC candidate slice profiles are configured")
    ranked.sort(key=lambda item: (-item[0].score, item[0].slice_id, item[1].pdu_session_id))
    candidates = [item[0] for item in ranked]
    selected = ranked[0][1]
    if not candidates[0].eligible:
        raise RuntimeError("no healthy candidate slice has remaining PDU capacity")
    reason = (
        f"explicit service intent requested {requested_class}; weighted capability/runtime score selected {selected.name}"
        if explicit else f"telemetry inferred {requested_class}; weighted capability/runtime score selected {selected.name}"
    )
    return selected, candidates, reason


def sla_ok(metrics: TrafficMetrics, profile: SliceProfile) -> bool:
    return (
        metrics.rtt_ms <= profile.max_rtt_ms
        and metrics.jitter_ms <= profile.max_jitter_ms
        and metrics.loss_percent <= profile.max_loss_percent
        and metrics.throughput_mbps >= profile.min_throughput_mbps
    )
