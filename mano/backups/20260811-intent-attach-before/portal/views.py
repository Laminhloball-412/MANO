import configparser
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .lab_controller import LabControllerError, connect_radio, disconnect_radio, radio_status
from .workloads import prometheus_text, read_telemetry, start_workload, stop_workload


SLICE_CATALOG = {
    "embb": {"key": "embb", "label": "eMBB", "sst": 1, "sd": "000001", "tone": "blue"},
    "urllc": {"key": "urllc", "label": "URLLC", "sst": 2, "sd": "000001", "tone": "violet"},
    "mmtc": {"key": "mmtc", "label": "mMTC", "sst": 3, "sd": "000001", "tone": "mint"},
}

SAMPLE_MEASUREMENTS = {
    "video": {
        "title": "Video streaming",
        "throughput": "24.8 Mbps",
        "latency": "31.4 ms",
        "jitter": "4.8 ms",
        "loss": "0.12%",
        "packet_size": "1,286 B",
        "message_rate": "2,410 pkt/s",
        "expected_slice": "embb",
    },
    "control": {
        "title": "Control loop",
        "throughput": "0.18 Mbps",
        "latency": "8.7 ms",
        "jitter": "1.3 ms",
        "loss": "0.04%",
        "packet_size": "128 B",
        "message_rate": "100 msg/s",
        "expected_slice": "urllc",
    },
    "sensor": {
        "title": "Sensor publisher",
        "throughput": "0.006 Mbps",
        "latency": "46.2 ms",
        "jitter": "7.1 ms",
        "loss": "0.00%",
        "packet_size": "74 B",
        "message_rate": "0.2 msg/s",
        "expected_slice": "mmtc",
    },
}

def _slice_key(sst):
    return {1: "embb", 2: "urllc", 3: "mmtc"}.get(int(sst or 0), "embb")


def _read_config(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(str(path), encoding="utf-8")
    try:
        imsi = parser.get("usim", "imsi").strip()
    except (configparser.Error, KeyError):
        return None
    match = re.search(r"(\d+)$", path.stem)
    if not match:
        return None
    ue_id = int(match.group(1))
    try:
        sst = parser.getint("slicing", "nssai-sst", fallback=1)
        sd = parser.get("slicing", "nssai-sd", fallback="000001").strip().zfill(6)
        dnn = parser.get("nas", "apn", fallback="srsapn").strip()
    except (configparser.Error, ValueError):
        sst, sd, dnn = 1, "000001", "srsapn"
    requested = _slice_key(sst)
    return {
        "id": ue_id,
        "name": "ue-{}".format(ue_id),
        "imsi": imsi,
        "dnn": dnn,
        "requested_slice": requested,
        "requested_sst": sst,
        "requested_sd": sd,
        "allowed_slices": [requested],
        "status": "CONFIGURED",
        "source": "LAB CONFIG",
        "config_path": str(path),
    }


def _fallback_profiles():
    return [
        {
            "id": index,
            "name": "ue-{}".format(index),
            "imsi": "460990000000{:03d}".format(index),
            "dnn": "srsapn",
            "requested_slice": key,
            "requested_sst": SLICE_CATALOG[key]["sst"],
            "requested_sd": SLICE_CATALOG[key]["sd"],
            "allowed_slices": [key],
            "status": "CONFIGURED",
            "source": "UI FALLBACK",
            "config_path": "Not available on this host",
        }
        for index, key in enumerate(("embb", "urllc", "mmtc"), start=1)
    ]


def _load_drafts():
    path = settings.UE_DRAFT_REGISTRY
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _save_drafts(items):
    path = settings.UE_DRAFT_REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(items, indent=2), encoding="utf-8")
    temporary.replace(path)


def _all_profiles():
    profiles = []
    if settings.UE_CONFIG_DIR.exists():
        for path in sorted(settings.UE_CONFIG_DIR.glob("ue[0-9]*.conf")):
            profile = _read_config(path)
            if profile:
                profiles.append(profile)
    if not profiles:
        profiles = _fallback_profiles()
    existing_names = {item["name"] for item in profiles}
    profiles.extend(item for item in _load_drafts() if item.get("name") not in existing_names)
    return sorted(profiles, key=lambda item: int(item["id"]))


def _page_context(active):
    profiles = _all_profiles()
    used = {int(item["id"]) for item in profiles}
    next_id = 1
    while next_id in used:
        next_id += 1
    return {
        "active": active,
        "profiles": profiles,
        "slice_catalog": SLICE_CATALOG,
        "next_id": next_id,
        "next_imsi": "460990000000{:03d}".format(next_id),
        "provisioning_enabled": settings.UE_PROVISIONING_ENABLED,
        "runner_enabled": settings.WORKLOAD_RUNNER_ENABLED,
    }


def home(request):
    return redirect("ue_management")


def ue_management(request):
    return render(request, "portal/ue_management.html", _page_context("ues"))


def service_lab(request):
    return render(request, "portal/service_lab.html", _page_context("lab"))


def measurements(request):
    context = _page_context("measurements")
    ue_name = request.GET.get("ue", context["profiles"][0]["name"] if context["profiles"] else "ue-1")
    context["selected_ue"] = ue_name
    return render(request, "portal/measurements.html", context)


def architecture(request):
    return render(request, "portal/architecture.html", _page_context("architecture"))


@require_http_methods(["GET"])
def api_state(request):
    return JsonResponse({
        "ok": True,
        "profiles": _all_profiles(),
        "slice_catalog": SLICE_CATALOG,
        "provisioning_enabled": settings.UE_PROVISIONING_ENABLED,
        "workload_runner_enabled": settings.WORKLOAD_RUNNER_ENABLED,
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_create_ue_draft(request):
    try:
        payload = json.loads(request.body or b"{}")
        profiles = _all_profiles()
        used = {int(item["id"]) for item in profiles}
        next_id = 1
        while next_id in used:
            next_id += 1
        allowed = payload.get("allowed_slices") or []
        if not isinstance(allowed, list):
            raise ValueError("allowed_slices must be a list")
        allowed = list(dict.fromkeys(str(item).lower() for item in allowed))
        if not allowed or any(item not in SLICE_CATALOG for item in allowed):
            raise ValueError("Select at least one valid allowed slice")
        requested = str(payload.get("requested_slice", "")).lower()
        if requested not in allowed:
            raise ValueError("Requested slice must be included in allowed slices")
        dnn = str(payload.get("dnn", "srsapn")).strip()
        if not re.match(r"^[A-Za-z0-9.-]{1,63}$", dnn):
            raise ValueError("Invalid DNN")
        draft = {
            "id": next_id,
            "name": "ue-{}".format(next_id),
            "imsi": "460990000000{:03d}".format(next_id),
            "dnn": dnn,
            "requested_slice": requested,
            "requested_sst": SLICE_CATALOG[requested]["sst"],
            "requested_sd": SLICE_CATALOG[requested]["sd"],
            "allowed_slices": allowed,
            "status": "DRAFT",
            "source": "PORTAL DRAFT",
            "config_path": "Not generated",
        }
        drafts = _load_drafts()
        drafts.append(draft)
        _save_drafts(drafts)
        return JsonResponse({
            "ok": True,
            "profile": draft,
            "message": "Draft saved. Subscriber and UE configuration were not changed.",
        }, status=201)
    except (ValueError, json.JSONDecodeError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def api_delete_ue_draft(request, ue_id):
    drafts = _load_drafts()
    remaining = [item for item in drafts if int(item.get("id", -1)) != ue_id]
    if len(remaining) == len(drafts):
        return JsonResponse({
            "ok": False,
            "error": "Only portal-created drafts can be deleted. Lab configs require the provisioning rollback adapter.",
        }, status=409)
    _save_drafts(remaining)
    return JsonResponse({"ok": True, "message": "UE draft deleted; no subscriber or config was changed."})


@require_http_methods(["GET"])
def api_radio_status(request):
    return JsonResponse({"ok": True, "data": radio_status()})


@csrf_exempt
@require_http_methods(["POST"])
def api_radio_connect(request):
    try:
        payload = json.loads(request.body or b"{}")
        result = connect_radio(int(payload.get("count", 1)))
        return JsonResponse({"ok": True, "data": result}, status=202)
    except (ValueError, json.JSONDecodeError, LabControllerError, OSError, subprocess.SubprocessError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=409)


@csrf_exempt
@require_http_methods(["POST"])
def api_radio_disconnect(request):
    try:
        return JsonResponse({"ok": True, "data": disconnect_radio()})
    except (LabControllerError, OSError, subprocess.SubprocessError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_workload_start(request):
    try:
        payload = json.loads(request.body or b"{}")
        data = start_workload(
            str(payload.get("ue", "")),
            str(payload.get("workload", "")),
            int(payload.get("duration", 60)),
        )
        return JsonResponse({"ok": True, "data": data}, status=202)
    except (ValueError, json.JSONDecodeError, LabControllerError, OSError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=409)


@csrf_exempt
@require_http_methods(["POST"])
def api_workload_stop(request):
    try:
        payload = json.loads(request.body or b"{}")
        return JsonResponse({"ok": True, "data": stop_workload(str(payload.get("ue", "")))})
    except (ValueError, json.JSONDecodeError, OSError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


def _prometheus_value(metric, ue):
    query = '{}{{ue="{}"}}'.format(metric, ue)
    response = requests.get(
        settings.PROMETHEUS_URL.rstrip("/") + "/api/v1/query",
        params={"query": query},
        timeout=2,
    )
    response.raise_for_status()
    result = response.json().get("data", {}).get("result", [])
    return float(result[0]["value"][1]) if result else None


@require_http_methods(["GET"])
def api_telemetry(request):
    ue = request.GET.get("ue", "").strip()
    if not re.match(r"^ue-[1-9][0-9]*$", ue):
        return JsonResponse({"ok": False, "error": "Invalid UE"}, status=400)
    names = (
        "throughput_bps", "rtt_ms", "jitter_ms", "packet_loss_ratio",
        "packet_size_bytes", "message_rate", "rx_bytes", "tx_bytes",
        "connected", "workload_active",
    )
    values = {}
    errors = []
    for name in names:
        metric = "ansa_ue_{}".format(name)
        try:
            values[name] = _prometheus_value(metric, ue)
        except (requests.RequestException, ValueError, KeyError, IndexError) as error:
            values[name] = None
            errors.append(str(error))
    direct = read_telemetry().get("ues", {}).get(ue, {})
    return JsonResponse({
        "ok": True,
        "ue": ue,
        "source": "PROMETHEUS",
        "values": values,
        "workload": direct.get("workload"),
        "workload_status": direct.get("status", "IDLE"),
        "updated_at": direct.get("updated_at"),
        "prometheus_ready": any(value is not None for value in values.values()),
        "errors": errors[:1],
    })


@require_http_methods(["GET"])
def prometheus_metrics(request):
    return HttpResponse(prometheus_text(), content_type="text/plain; version=0.0.4; charset=utf-8")


@require_http_methods(["GET"])
def api_payload(request):
    try:
        megabytes = max(1, min(int(request.GET.get("megabytes", 128)), 1024))
    except ValueError:
        megabytes = 128
    block = b"ANSA-5G-EMBB-DATA\n" * 3276
    total = megabytes * 1024 * 1024

    def stream():
        remaining = total
        while remaining > 0:
            chunk = block[:min(len(block), remaining)]
            remaining -= len(chunk)
            yield chunk

    response = StreamingHttpResponse(stream(), content_type="application/octet-stream")
    response["Content-Length"] = str(total)
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_http_methods(["POST"])
def api_sensor_ingest(request):
    return HttpResponse(status=204)


def healthz(request):
    return JsonResponse({"ok": True, "service": "ue-service-portal"})
