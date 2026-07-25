"""Read-only PDU session observer kept separate from the MANO sample views."""

import configparser
import json
import re
import subprocess
from pathlib import Path

import requests
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods


UE_CONFIG_GLOB = "/home/ubuntu/ran/srsRAN_4G/build/srsue/src/ue*.conf"
SAFE_SUPI = re.compile(r"^imsi-[0-9]{5,20}$")


def pdu_session_flow(request):
    return render(request, "pdu_session_flow.html", {
        "pdu_flow_active": "active",
        "generic_template_link_status": "nav-link collapsed",
        "generic_template_expanded": "false",
        "nssi_view_link_status": "nav-link collapsed",
        "nssi_view_expanded": "false",
    })


def _metrics_services():
    completed = subprocess.run(
        [
            "kubectl", "get", "svc", "-A",
            "-l", "app.kubernetes.io/name=open5gs-smf-metrics",
            "-o", "json",
        ],
        capture_output=True,
        text=True,
        timeout=8,
        check=True,
    )
    services = []
    for item in json.loads(completed.stdout).get("items", []):
        namespace = item["metadata"]["namespace"]
        cluster_ip = item["spec"].get("clusterIP")
        port = next(
            (
                port["port"]
                for port in item["spec"].get("ports", [])
                if port.get("port") == 9090
            ),
            None,
        )
        if cluster_ip and port:
            services.append((namespace, "http://{}:{}".format(
                cluster_ip, port
            )))
    return services


def _ue_aliases():
    aliases = {}
    root = Path(UE_CONFIG_GLOB).parent
    pattern = Path(UE_CONFIG_GLOB).name
    for config_path in sorted(root.glob(pattern)):
        parser = configparser.ConfigParser(strict=False)
        try:
            parser.read(str(config_path))
            imsi = parser.get("usim", "imsi").strip()
        except (configparser.Error, KeyError, OSError):
            continue
        # Prefer the first numbered UE profile when test profiles reuse an IMSI.
        aliases.setdefault("imsi-{}".format(imsi), config_path.stem)
    return aliases


def _normalise_session(namespace, supi, pdu, ue_activity):
    snssai = pdu.get("snssai") or {}
    n3 = pdu.get("n3") or {}
    gnb = n3.get("gnb") or {}
    upf = n3.get("upf") or {}
    qos_flows = pdu.get("qos_flows") or []
    state = str(pdu.get("pdu_state") or "unknown").lower()
    activity = str(ue_activity or "unknown").lower()
    return {
        "namespace": namespace,
        "supi": supi,
        "psi": pdu.get("psi"),
        "dnn": pdu.get("dnn") or "-",
        "ipv4": pdu.get("ipv4") or "-",
        "sst": snssai.get("sst"),
        "sd": snssai.get("sd") or "-",
        "qos_flows": qos_flows,
        "state": state,
        "ue_activity": activity,
        "gnb_teid": gnb.get("teid"),
        "gnb_addr": gnb.get("addr") or "-",
        "upf_teid": upf.get("teid"),
        "upf_addr": upf.get("addr") or "-",
        "pdr_id": upf.get("pdr_id"),
        "online": state == "active" and activity == "connected",
    }


def _timeline(session):
    has_context = bool(session)
    has_tunnel = has_context and (
        session.get("gnb_teid") is not None
        and session.get("upf_teid") is not None
    )
    active = has_context and session.get("online")
    return [
        {
            "plane": "N1 / N2",
            "source": "UE",
            "target": "gNB / AMF",
            "message": "PDU Session Establishment Request",
            "status": "capture-required",
            "detail": "Enable NGAP/NAS capture to observe the original request.",
        },
        {
            "plane": "SBI",
            "source": "AMF",
            "target": "SMF",
            "message": "Create SM Context",
            "status": "context-observed" if has_context else "not-observed",
            "detail": (
                "SMF session context is present in Open5GS."
                if has_context else "No SMF session context was returned."
            ),
        },
        {
            "plane": "N4",
            "source": "SMF",
            "target": "UPF",
            "message": "PFCP Session Establishment",
            "status": "context-observed" if has_tunnel else "not-observed",
            "detail": (
                "N3 TEIDs and PDR are present; packet-level PFCP capture is "
                "not enabled yet."
                if has_tunnel else "No programmed N3 tunnel was returned."
            ),
        },
        {
            "plane": "N2 / N1",
            "source": "AMF / gNB",
            "target": "UE",
            "message": "Resource Setup and Establishment Accept",
            "status": "context-observed" if has_tunnel else "not-observed",
            "detail": (
                "A PDU session and N3 tunnel exist in the core."
                if has_tunnel else "Waiting for the session resource context."
            ),
        },
        {
            "plane": "N3",
            "source": "gNB",
            "target": "UPF",
            "message": "GTP-U User Data",
            "status": "live" if active else "inactive",
            "detail": (
                "UE and PDU session are active."
                if active else
                "UE is idle/offline. Start ue1 to observe live packets."
            ),
        },
    ]


@require_http_methods(["GET"])
def pdu_session_flow_status(request):
    requested_supi = request.GET.get("supi", "").strip()
    if requested_supi and not SAFE_SUPI.match(requested_supi):
        return JsonResponse(
            {"ok": False, "error": "Invalid SUPI format."},
            status=400,
        )

    aliases = _ue_aliases()
    sessions = []
    sources = []
    errors = []
    try:
        services = _metrics_services()
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        OSError,
    ) as error:
        services = []
        errors.append("Kubernetes discovery failed: {}".format(error))

    for namespace, base_url in services:
        try:
            response = requests.get(
                "{}/pdu-info?page=-1".format(base_url),
                timeout=3,
            )
            response.raise_for_status()
            payload = response.json()
            sources.append({"namespace": namespace, "status": "up"})
            for item in payload.get("items", []):
                supi = item.get("supi") or "unknown"
                for pdu in item.get("pdu", []):
                    session = _normalise_session(
                        namespace, supi, pdu, item.get("ue_activity")
                    )
                    session["ue_name"] = aliases.get(supi, supi)
                    sessions.append(session)
        except (requests.RequestException, ValueError) as error:
            sources.append({"namespace": namespace, "status": "down"})
            errors.append("{}: {}".format(namespace, error))

    sessions.sort(key=lambda item: (item["ue_name"], item["psi"] or 0))
    selected = next(
        (
            item for item in sessions
            if not requested_supi or item["supi"] == requested_supi
        ),
        sessions[0] if sessions else None,
    )
    return JsonResponse({
        "ok": True,
        "sessions": sessions,
        "selected": selected,
        "timeline": _timeline(selected),
        "sources": sources,
        "errors": errors,
        "refresh_seconds": 5,
        "capture": {
            "ngap": False,
            "pfcp": False,
            "gtpu": False,
            "message": (
                "Live session context is enabled. Packet capture is not "
                "enabled, so message rows marked Capture required are the "
                "protocol path, not decoded packets."
            ),
        },
    })
