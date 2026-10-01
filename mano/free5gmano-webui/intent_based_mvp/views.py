import json
import os

import requests
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


SLICE_INTENT_API = os.environ.get(
    "SLICE_INTENT_API", os.environ.get("UE_INTENT_API", "http://127.0.0.1:8890")
)
SERVICE_INTENT_FIELDS = {
    "schema_version",
    "service_type",
    "quality",
    "scale",
    "priority",
    "duration_minutes",
    "execution_mode",
}
BUSINESS_INTENT_FIELDS = {
    "schema_version",
    "business_requirement",
    "scale",
    "duration_minutes",
    "execution_mode",
}


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


def business_page(request):
    return render(
        request,
        "intent_based_mvp/business_intent.html",
        {
            "business_intent_active": "active",
            "generic_template_link_status": "nav-link collapsed",
            "generic_template_expanded": "false",
            "nssi_view_link_status": "nav-link collapsed",
            "nssi_view_expanded": "false",
        },
    )


def _proxy(method, path, payload=None, timeout=30):
    response = requests.request(
        method,
        SLICE_INTENT_API.rstrip("/") + path,
        json=payload,
        timeout=timeout,
    )
    try:
        body = response.json()
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "Slice Intent API returned a non-JSON response"},
            status=502,
        )
    return JsonResponse(body, status=response.status_code, safe=isinstance(body, dict))


def _normalize_service_intent(document):
    if not isinstance(document, dict):
        raise ValueError("Request body must be a JSON object")
    unknown = set(document) - SERVICE_INTENT_FIELDS
    if unknown:
        raise ValueError("Unsupported Service Intent fields: {}".format(sorted(unknown)))
    required = {
        "service_type",
        "quality",
        "scale",
        "priority",
        "duration_minutes",
    }
    missing = required - set(document)
    if missing:
        raise ValueError("Missing Service Intent fields: {}".format(sorted(missing)))
    return {
        "schema_version": "2.0",
        "service_type": str(document["service_type"]).strip().lower(),
        "quality": str(document["quality"]).strip().lower(),
        "scale": document["scale"],
        "priority": str(document["priority"]).strip().lower(),
        "duration_minutes": document["duration_minutes"],
        "execution_mode": str(document.get("execution_mode", "dry_run"))
        .strip()
        .lower(),
    }


def _normalize_business_intent(document):
    if not isinstance(document, dict):
        raise ValueError("Request body must be a JSON object")
    unknown = set(document) - BUSINESS_INTENT_FIELDS
    if unknown:
        raise ValueError("Unsupported Business Intent fields: {}".format(sorted(unknown)))
    requirement = str(document.get("business_requirement", "")).strip()
    if not requirement:
        raise ValueError("business_requirement is required")
    schema_version = str(document.get("schema_version", "2.0")).strip()
    if schema_version != "2.0":
        raise ValueError("schema_version must be 2.0")
    normalized = {
        "schema_version": schema_version,
        "business_requirement": requirement,
        "execution_mode": str(document.get("execution_mode", "dry_run"))
        .strip()
        .lower(),
    }
    for field in ("scale", "duration_minutes"):
        if document.get(field) not in (None, ""):
            normalized[field] = document[field]
    return normalized


@require_http_methods(["GET"])
def api_catalog(request):
    return _proxy("GET", "/api/slice-intents/catalog/")


@csrf_exempt
@require_http_methods(["POST"])
def api_submit(request):
    try:
        normalized = _normalize_service_intent(json.loads(request.body or b"{}"))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    return _proxy("POST", "/api/slice-intents/", normalized)


@require_http_methods(["GET"])
def api_job(request, intent_id):
    return _proxy("GET", "/api/slice-intents/{}/".format(intent_id))


@require_http_methods(["GET"])
def api_configuration(request, intent_id):
    return _proxy(
        "GET", "/api/slice-intents/{}/configuration/".format(intent_id)
    )


@require_http_methods(["GET"])
def api_instances(request):
    return _proxy("GET", "/api/slice-instances/")


@csrf_exempt
@require_http_methods(["POST"])
def api_business_translate(request):
    try:
        normalized = _normalize_business_intent(json.loads(request.body or b"{}"))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    return _proxy("POST", "/api/business-intents/translate/", normalized)


@csrf_exempt
@require_http_methods(["POST"])
def api_business_submit(request):
    try:
        normalized = _normalize_business_intent(json.loads(request.body or b"{}"))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    return _proxy("POST", "/api/business-intents/", normalized)


@require_http_methods(["GET"])
def api_business_job(request, intent_id):
    return _proxy("GET", "/api/slice-intents/{}/".format(intent_id))


@require_http_methods(["GET"])
def api_business_instances(request):
    return _proxy("GET", "/api/slice-instances/")
