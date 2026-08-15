import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .coordinator import get_job, submit_job, ue_inventory
from .policy import SLICE_PROFILES


def intent_attach(request):
    return render(
        request,
        "intent_based/intent_attach.html",
        {"active": "intent", "slice_profiles": SLICE_PROFILES},
    )


@require_http_methods(["GET"])
def api_ues(request):
    try:
        return JsonResponse({"ok": True, "ues": ue_inventory()})
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=503)


@csrf_exempt
@require_http_methods(["POST"])
def api_submit(request):
    try:
        payload = json.loads(request.body or b"{}")
        job = submit_job(payload)
        return JsonResponse({"ok": True, "job": job}, status=202)
    except (ValueError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    except Exception as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=500)


@require_http_methods(["GET"])
def api_job(request, job_id):
    job = get_job(job_id)
    if not job:
        return JsonResponse({"ok": False, "error": "Job not found"}, status=404)
    return JsonResponse({"ok": True, "job": job})

