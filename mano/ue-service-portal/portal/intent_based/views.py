import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .business_intent import AmbiguousBusinessIntent, translate_business_requirement
from .policy import SLICE_PROFILES
from .provisioning import (
    catalog_document,
    get_job as get_slice_job,
    slice_inventory,
    submit_business_job,
    submit_job as submit_slice_job,
)


def intent_attach(request):
    return render(
        request,
        "intent_based/intent_attach.html",
        {"active": "intent", "slice_profiles": SLICE_PROFILES},
    )


@require_http_methods(["GET"])
def api_ues(request):
    try:
        from .coordinator import ue_inventory

        return JsonResponse({"ok": True, "ues": ue_inventory()})
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=503)


@csrf_exempt
@require_http_methods(["POST"])
def api_submit(request):
    try:
        from .coordinator import submit_job

        payload = json.loads(request.body or b"{}")
        job = submit_job(payload)
        return JsonResponse({"ok": True, "job": job}, status=202)
    except (ValueError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=500)


@require_http_methods(["GET"])
def api_job(request, job_id):
    from .coordinator import get_job

    job = get_job(job_id)
    if not job:
        return JsonResponse({"ok": False, "error": "Job not found"}, status=404)
    return JsonResponse({"ok": True, "job": job})


@require_http_methods(["GET"])
def api_slice_catalog(request):
    return JsonResponse({"ok": True, "data": catalog_document()})


@csrf_exempt
@require_http_methods(["POST"])
def api_slice_intents(request):
    try:
        payload = json.loads(request.body or b"{}")
        job = submit_slice_job(payload)
        return JsonResponse({"ok": True, "job": job}, status=202)
    except (ValueError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=500)


@require_http_methods(["GET"])
def api_slice_intent(request, intent_id):
    job = get_slice_job(intent_id)
    if not job:
        return JsonResponse({"ok": False, "error": "Intent not found"}, status=404)
    return JsonResponse({"ok": True, "job": job})


@require_http_methods(["GET"])
def api_slice_configuration(request, intent_id):
    job = get_slice_job(intent_id)
    if not job:
        return JsonResponse({"ok": False, "error": "Intent not found"}, status=404)
    return JsonResponse(
        {
            "ok": True,
            "intent_id": intent_id,
            "stage": job.get("stage"),
            "configuration": job.get("configuration"),
            "plan": job.get("plan"),
        }
    )


@require_http_methods(["GET"])
def api_slice_instances(request):
    try:
        return JsonResponse({"ok": True, "instances": slice_inventory()})
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=503)


@csrf_exempt
@require_http_methods(["POST"])
def api_business_translate(request):
    try:
        payload = json.loads(request.body or b"{}")
        translated = translate_business_requirement(payload)
        return JsonResponse({"ok": True, "data": translated})
    except (AmbiguousBusinessIntent, ValueError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_business_intents(request):
    try:
        payload = json.loads(request.body or b"{}")
        job = submit_business_job(payload)
        return JsonResponse({"ok": True, "job": job}, status=202)
    except (AmbiguousBusinessIntent, ValueError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=500)

