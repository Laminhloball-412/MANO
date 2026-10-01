from __future__ import annotations

import re
import unicodedata

from .service_intent import load_catalog, translate_service_intent


ALLOWED_FIELDS = {
    "schema_version",
    "business_requirement",
    "scale",
    "duration_minutes",
    "execution_mode",
}
QUALITY_ORDER = {"standard": 0, "high": 1, "critical": 2}


class AmbiguousBusinessIntent(ValueError):
    """Raised when deterministic catalog rules cannot choose one service."""


def _normalize_text(value):
    decomposed = unicodedata.normalize("NFD", str(value).strip().lower())
    without_marks = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"\s+", " ", without_marks.replace("đ", "d"))


def _matched_phrases(text, phrases):
    return [phrase for phrase in phrases if _normalize_text(phrase) in text]


def _extract_duration(text):
    match = re.search(
        r"\b(\d{1,5})\s*(phut|phút|minute|minutes|gio|giờ|hour|hours)\b",
        text,
    )
    if not match:
        return None, None
    value = int(match.group(1))
    unit = _normalize_text(match.group(2))
    return value * 60 if unit in {"gio", "hour", "hours"} else value, match.span()


def _extract_sla_hints(text):
    hints = {}
    excluded_spans = []
    latency = re.search(r"\b(\d+(?:\.\d+)?)\s*(ms|millisecond|milliseconds)\b", text)
    if latency:
        hints["max_latency_ms"] = float(latency.group(1))
        excluded_spans.append(latency.span())
    bandwidth = re.search(r"\b(\d+(?:\.\d+)?)\s*(mbps|gbps)\b", text)
    if bandwidth:
        value = float(bandwidth.group(1))
        hints["bandwidth_mbps"] = value * 1000 if bandwidth.group(2) == "gbps" else value
        excluded_spans.append(bandwidth.span())
    scale = re.search(
        r"\b(\d{1,7})\s*(users?|viewers?|participants?|calls?|players?|"
        r"devices?|sensors?|meters?|vehicles?|headsets?|responders?|endpoints?)\b",
        text,
    )
    if scale:
        hints["scale"] = int(scale.group(1))
        hints["scale_unit"] = scale.group(2)
        if scale.group(2).startswith(("device", "sensor", "meter")):
            hints["device_count"] = int(scale.group(1))
    return hints, excluded_spans


def _extract_scale(text, excluded_spans):
    for match in re.finditer(r"\b(\d{1,7})\b", text):
        if any(start <= match.start() < end for start, end in excluded_spans):
            continue
        return int(match.group(1))
    return None


def _service_scores(text, catalog):
    scores = {}
    matches = {}
    for service_type, service in catalog["services"].items():
        phrases = _matched_phrases(text, service.get("business_phrases", []))
        # Prefer the most specific phrase. Summing overlapping phrases lets a
        # generic profile win only because it contains several nested aliases
        # (for example "push to talk" inside a longer service name).
        scores[service_type] = max(
            (len(_normalize_text(phrase)) for phrase in phrases),
            default=0,
        )
        matches[service_type] = phrases
    return scores, matches


def _infer_service_from_sla(hints):
    candidates = []
    latency = hints.get("max_latency_ms")
    if latency is not None and latency <= 10:
        candidates.append(("interactive_control", "urllc", "max_latency_ms <= 10"))
    if hints.get("device_count", 0) >= 1000:
        candidates.append(("sensor_telemetry", "mmtc", "device_count >= 1000"))
    if hints.get("bandwidth_mbps") is not None:
        candidates.append(("broadband", "embb", "bandwidth requirement supplied"))
    families = {family for _, family, _ in candidates}
    if len(families) > 1:
        raise AmbiguousBusinessIntent(
            "The SLA cues imply conflicting slice families: {}. Clarify the primary "
            "service or submit separate intents.".format(", ".join(sorted(families)))
        )
    if not candidates:
        return None, None
    service_type, _, reason = candidates[0]
    return service_type, reason


def _apply_explicit_sla_hints(translated, hints, catalog):
    technical_sla = translated["technical_sla"]
    policy = translated["deployment_policy"]
    if hints.get("max_latency_ms") is not None:
        technical_sla["max_latency_ms"] = hints["max_latency_ms"]
    bandwidth = hints.get("bandwidth_mbps")
    if bandwidth is None:
        return
    formula = translated["translation_trace"]["formula"]
    technical_sla["min_throughput_mbps"] = round(
        bandwidth * formula["guaranteed_ratio"], 3
    )
    technical_sla["target_throughput_mbps"] = round(bandwidth, 3)
    technical_sla["peak_throughput_mbps"] = round(
        bandwidth * formula["burst_ratio"], 3
    )
    template = catalog["templates"][policy["template_family"]]
    policy["session_ambr_mbps"] = round(
        min(technical_sla["peak_throughput_mbps"], template["session_ambr_max_mbps"]),
        3,
    )
    policy["downlink_limit_mbps"] = round(
        min(technical_sla["peak_throughput_mbps"], template["max_throughput_mbps"]),
        3,
    )


def _select_service(scores, inferred_service=None):
    highest = max(scores.values(), default=0)
    winners = sorted(name for name, score in scores.items() if score == highest)
    if highest == 0:
        if inferred_service:
            return inferred_service
        raise AmbiguousBusinessIntent(
            "The service type could not be identified. Describe voice/video calls, "
            "streaming, enterprise data, AR/VR, gaming, industrial control, V2X, "
            "IoT telemetry, or mission-critical communications."
        )
    if len(winners) != 1:
        raise AmbiguousBusinessIntent(
            "The requirement matches multiple service types equally: {}. Separate "
            "the services or clarify the primary service.".format(", ".join(winners))
        )
    return winners[0]


def _select_quality(text, service):
    scores = {}
    matches = {}
    for quality, phrases in service.get("quality_phrases", {}).items():
        found = _matched_phrases(text, phrases)
        scores[quality] = len(found)
        matches[quality] = found
    highest = max(scores.values(), default=0)
    if not highest:
        return None, [], scores
    winners = [name for name, score in scores.items() if score == highest]
    selected = max(winners, key=lambda name: QUALITY_ORDER.get(name, -1))
    return selected, matches[selected], scores


def _select_priority(text, catalog):
    matches = {
        priority: _matched_phrases(text, phrases)
        for priority, phrases in catalog.get("priority_phrases", {}).items()
    }
    scored = {priority: len(found) for priority, found in matches.items()}
    highest = max(scored.values(), default=0)
    if not highest:
        return "normal", [], True
    winners = [name for name, score in scored.items() if score == highest]
    selected = min(
        winners,
        key=lambda name: catalog["priorities"][name]["allocation_preference"],
    )
    return selected, matches[selected], False


def translate_business_requirement(payload, catalog=None):
    catalog = catalog or load_catalog()
    if not isinstance(payload, dict):
        raise ValueError("Business Intent must be an object")
    unknown = set(payload) - ALLOWED_FIELDS
    if unknown:
        raise ValueError("Unsupported Business Intent fields: {}".format(sorted(unknown)))
    if str(payload.get("schema_version", "2.0")).strip() != "2.0":
        raise ValueError("schema_version must be 2.0")
    requirement = str(payload.get("business_requirement", "")).strip()
    if not requirement:
        raise ValueError("business_requirement is required")

    normalized = _normalize_text(requirement)
    scores, service_matches = _service_scores(normalized, catalog)
    sla_hints, sla_spans = _extract_sla_hints(normalized)
    inferred_service, inference_reason = (None, None)
    if max(scores.values(), default=0) == 0:
        inferred_service, inference_reason = _infer_service_from_sla(sla_hints)
    service_type = _select_service(scores, inferred_service)
    service = catalog["services"][service_type]
    quality, quality_matches, quality_scores = _select_quality(normalized, service)

    duration_extracted, duration_span = _extract_duration(normalized)
    excluded_spans = list(sla_spans)
    if duration_span:
        excluded_spans.append(duration_span)
    scale_extracted = sla_hints.get("scale")
    if scale_extracted is None:
        scale_extracted = _extract_scale(normalized, excluded_spans)
    scale = payload.get("scale", scale_extracted)
    duration = payload.get("duration_minutes", duration_extracted)
    defaults_applied = []
    warnings = []

    if quality is None:
        quality = service["default_quality"]
        defaults_applied.append("quality")
    if scale is None:
        scale = service["default_scale"]
        defaults_applied.append("scale")
    if duration is None:
        duration = service["default_duration_minutes"]
        defaults_applied.append("duration_minutes")

    priority, priority_matches, priority_defaulted = _select_priority(
        normalized, catalog
    )
    if priority_defaulted:
        defaults_applied.append("priority")

    explicit_service = bool(service_matches[service_type])
    explicit_scale = payload.get("scale") is not None or scale_extracted is not None
    explicit_duration = (
        payload.get("duration_minutes") is not None or duration_extracted is not None
    )
    bounded_defaults = sum(
        name in defaults_applied for name in ("quality", "scale", "duration_minutes")
    )
    if explicit_service and explicit_scale and explicit_duration:
        confidence = "high"
    elif bounded_defaults <= 1:
        confidence = "medium"
    else:
        confidence = "low"
    if defaults_applied:
        warnings.append(
            "Default values were applied for: {}.".format(", ".join(defaults_applied))
        )

    execution_mode = str(payload.get("execution_mode", "dry_run")).strip().lower()
    if execution_mode == "execute" and confidence == "low":
        raise ValueError(
            "Business Intent confidence is low. Confirm the scale and duration "
            "before execution."
        )
    structured = {
        "schema_version": "2.0",
        "service_type": service_type,
        "quality": quality,
        "scale": scale,
        "priority": priority,
        "duration_minutes": duration,
        "execution_mode": execution_mode,
    }
    translated = translate_service_intent(structured, catalog)
    _apply_explicit_sla_hints(translated, sla_hints, catalog)
    matched = service_matches[service_type] + quality_matches + priority_matches
    return {
        "business_requirement": requirement,
        "structured_intent": translated["intent"],
        "technical_sla": translated["technical_sla"],
        "deployment_policy": translated["deployment_policy"],
        "trace": {
            "catalog_version": catalog["catalog_version"],
            "matched_phrases": list(dict.fromkeys(matched)),
            "service_scores": scores,
            "quality_scores": quality_scores,
            "extracted": {
                "scale": scale_extracted,
                "duration_minutes": duration_extracted,
                "sla_hints": sla_hints,
            },
            "inference": inference_reason,
            "defaults_applied": defaults_applied,
            "confidence": confidence,
        },
        "warnings": warnings,
    }
