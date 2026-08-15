import json
import os
from datetime import datetime, timezone

import requests
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


UE_INTENT_API = os.environ.get("UE_INTENT_API", "http://127.0.0.1:8890")


def page(request):
    return render(
        request,
        "intent_based_mvp/intent_attach.html",
        {
            "intent_attach_active": "active",
            "generic_template_link_status": "nav-link collapsed",
            "generic_template_expanded": "false",
            "nssi_view_link_status": "nav-link collapsed",
            "nssi_view_expanded": "false",
        },
    )


def _proxy(method, path, payload=None, timeout=30):
    response = requests.request(
        method,
        UE_INTENT_API.rstrip("/") + path,
        json=payload,
        timeout=timeout,
    )
    try:
        body = response.json()
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "UE Intent API returned a non-JSON response"},
            status=502,
        )
    return JsonResponse(body, status=response.status_code, safe=isinstance(body, dict))


@require_http_methods(["GET"])
def api_ues(request):
    return _proxy("GET", "/api/intent-attach/ues/")


def _normalize_simulation(document):
    if not isinstance(document, dict):
        raise ValueError("Request body must be a JSON object")
    source = document.get("input_source", "prometheus_simulation")
    if source != "prometheus_simulation":
        raise ValueError("MVP currently accepts input_source=prometheus_simulation only")
    ue = document.get("ue") or {}
    service = document.get("service") or {}
    observed = document.get("observed_kpis") or {}
    if not ue.get("ue_id"):
        raise ValueError("ue.ue_id is required")
    if not service.get("name"):
        raise ValueError("service.name is required")
    return {
        "schema_version": "1.0",
        "input_source": "prometheus_simulation",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "ue": {"ue_id": int(ue["ue_id"])},
        "service": {
            "name": str(service["name"]).strip(),
            "category": str(service.get("category", "custom")).strip(),
        },
        "measurement_window": {
            "seconds": 30,
            "samples": 30,
            "aggregation": "avg_and_p95",
        },
        "observed_kpis": observed,
    }


@csrf_exempt
@require_http_methods(["POST"])
def api_submit(request):
    try:
        normalized = _normalize_simulation(json.loads(request.body or b"{}"))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    return _proxy("POST", "/api/intent-attach/jobs/", normalized)


@require_http_methods(["GET"])
def api_job(request, job_id):
    return _proxy("GET", "/api/intent-attach/jobs/{}/".format(job_id))


@require_http_methods(["GET"])
def api_radio_status(request):
    return _proxy("GET", "/api/radio/status/")


@csrf_exempt
@require_http_methods(["POST"])
def api_radio_disconnect(request):
    return _proxy("POST", "/api/radio/disconnect/", {})
