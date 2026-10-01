from __future__ import annotations

import copy


def build_runtime_intent(payload, intent_id):
    intent = {
        "intent_id": str(intent_id),
        "slice_type": str(payload["slice_type"]).lower(),
    }
    for field in (
        "sst",
        "sd",
        "slot",
        "service_intent",
        "technical_sla",
        "deployment_policy",
    ):
        if payload.get(field) is not None:
            intent[field] = copy.deepcopy(payload[field])
    return intent
