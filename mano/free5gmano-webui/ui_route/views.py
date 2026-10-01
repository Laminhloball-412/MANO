import requests
from free5gmano_webui import settings
from .intent_payload import build_runtime_intent
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

# Create your views here.
from datetime import datetime
from urllib.parse import urlencode
import requests
from free5gmano_webui import settings


AUTO_SLICE_ROOT = Path(
    os.environ.get("AUTO_SLICE_ROOT", "/home/ubuntu/mano/intent_flow_v2")
)
AUTO_SLICE_PYTHON = os.environ.get("AUTO_SLICE_PYTHON", "/usr/bin/python3")
AUTO_SLICE_SCRIPT = AUTO_SLICE_ROOT / "intent_flow.py"


def _run_auto_slice(arguments, timeout=360):
    command = [
        AUTO_SLICE_PYTHON,
        str(AUTO_SLICE_SCRIPT),
        *arguments,
    ]
    completed = subprocess.run(
        command,
        cwd=str(AUTO_SLICE_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or "Auto Slice command failed")
    return json.loads(completed.stdout)


def get_grafana_datasource_uid():
    try:
        r = requests.get(
            f'{settings.GRAFANA_BASE_URL}/api/datasources/name/{settings.GRAFANA_DATASOURCE_NAME}',
            auth=(settings.GRAFANA_API_USER, settings.GRAFANA_API_PASSWORD),
            timeout=2,
        )
        if r.ok:
            return r.json().get('uid')
    except requests.RequestException:
        pass
    return None


def build_grafana_url():
    params = {
        'orgId': '1',
        'from': 'now-1h',
        'to': 'now',
        'timezone': 'browser',
        'var-ue': '$__all',
        'var-pdu': '$__all',
        'var-service': '$__all',
        'var-flow': '$__all',
        'var-profile': '$__all',
        'var-slice': '$__all',
        'refresh': '5s',
        'theme': 'light',
    }

    return (
        f'{settings.GRAFANA_PUBLIC_URL.rstrip("/")}/d/'
        f'{settings.GRAFANA_DASHBOARD_UID}/'
        f'{settings.GRAFANA_DASHBOARD_SLUG}?'
        f'{urlencode(params)}&kiosk'
    )

def dashboard(request):
    return render(request, 'dashboard.html', {
        'dashboard_active': str('active'),
        'generic_template_link_status': str('nav-link collapsed'),
        'generic_template_expanded': str('false'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('false'),
        'NM_URL': settings.NM_URL,
        'GRAFANA_URL': build_grafana_url(),
    })


def auto_slice(request):
    return render(request, 'auto_slice.html', {
        'auto_slice_active': str('active'),
        'generic_template_link_status': str('nav-link collapsed'),
        'generic_template_expanded': str('false'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('false'),
    })


@require_http_methods(["GET"])
def auto_slice_status(request):
    try:
        return JsonResponse({'ok': True, 'data': _run_auto_slice(
            ['web-status'], timeout=90
        )})
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        return JsonResponse({'ok': False, 'error': str(error)}, status=503)


@csrf_exempt
@require_http_methods(["POST"])
def auto_slice_create(request):
    intent_path = None
    try:
        payload = json.loads(request.body or b'{}')
        slice_type = str(payload.get('slice_type', '')).lower()
        if slice_type not in {'embb', 'urllc', 'mmtc'}:
            raise ValueError('slice_type must be embb, urllc, or mmtc')
        expected_sst = {'embb': 1, 'urllc': 2, 'mmtc': 3}[slice_type]
        requested_sst = payload.get('sst')
        requested_sd = payload.get('sd')
        if requested_sst is not None and int(requested_sst) != expected_sst:
            raise ValueError(
                'sst {} does not match the fixed {} profile (SST {})'.format(
                    requested_sst, slice_type, expected_sst
                )
            )
        if requested_sd is not None:
            requested_sd = str(requested_sd).strip().upper()
            if len(requested_sd) != 6:
                raise ValueError('sd must contain exactly 6 hexadecimal characters')
            try:
                int(requested_sd, 16)
            except ValueError:
                raise ValueError('sd must contain exactly 6 hexadecimal characters')
        intent = build_runtime_intent(
            payload,
            intent_id=f'web-{slice_type}-{int(time.time())}-{uuid.uuid4().hex[:8]}',
        )
        if requested_sst is not None:
            intent['sst'] = expected_sst
        if requested_sd is not None:
            intent['sd'] = requested_sd
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.json',
            prefix='auto-slice-',
            delete=False,
            encoding='utf-8',
        ) as handle:
            json.dump(intent, handle)
            intent_path = handle.name
        result = _run_auto_slice(['create', intent_path, '--execute'])
        summary = {
            key: result.get(key)
            for key in (
                'intent_id', 'slice_type', 'namespace', 'sst', 'sd',
                'status', 'nssi_id', 'pods', 'deployment_configuration',
                'core_registration', 'core_registration_error',
            )
        }
        return JsonResponse({'ok': True, 'data': summary})
    except (
        RuntimeError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError
    ) as error:
        return JsonResponse({'ok': False, 'error': str(error)}, status=500)
    finally:
        if intent_path:
            Path(intent_path).unlink(missing_ok=True)


@csrf_exempt
@require_http_methods(["POST"])
def auto_slice_delete(request):
    try:
        payload = json.loads(request.body or b'{}')
        target = str(payload.get('target', '')).strip()
        if not target:
            raise ValueError('Missing slice target')
        result = _run_auto_slice([
            'delete', target, '--execute', '--force-fallback',
            '--purge-namespace', '--cleanup-templates',
        ])
        return JsonResponse({'ok': True, 'data': result})
    except (
        RuntimeError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError
    ) as error:
        return JsonResponse({'ok': False, 'error': str(error)}, status=500)


def nssi_topology(request):
    return render(request, 'nssi_topology.html', {
        'id': str(request.GET['id']),
        'status': str(request.GET['status']),
        'graph_view_active': str('active'),
        'generic_template_link_status': str('nav-link collapsed'),
        'generic_template_expanded': str('false'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('true'),
        'nssi_view_show': str('show'),
        'NM_URL': settings.NM_URL,
    })


def NSS_Instance(request):
    return render(request, 'nss_instance.html', {
        'list_view_active': str('active'),
        'generic_template_link_status': str('nav-link collapsed'),
        'generic_template_expanded': str('false'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('true'),
        'nssi_view_show': str('show'),
        'NM_URL': settings.NM_URL,
    })


def nfv_mano_plugin(request):
    return render(request, 'service_mapping_plugin.html', {
        'plugin_active': str('active'),
        'generic_template_link_status': str('nav-link collapsed'),
        'generic_template_expanded': str('false'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('false'),
        'NM_URL': settings.NM_URL,
    })


def VNF_Template(request):
    return render(request, 'vnf_template.html', {
        'vnf_template_active': str('active'),
        'generic_template_link_status': str('nav-link'),
        'generic_template_expanded': str('true'),
        'generic_template_show': str('show'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('false'),
        'NM_URL': settings.NM_URL,
    })


def NSD_Template(request):
    return render(request, 'nsd_template.html', {
        'nsd_template_active': str('active'),
        'generic_template_link_status': str('nav-link'),
        'generic_template_expanded': str('true'),
        'generic_template_show': str('show'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('false'),
        'NM_URL': settings.NM_URL,
    })


def NRM_Template(request):
    return render(request, 'nrm_template.html', {
        'nrm_template_active': str('active'),
        'generic_template_link_status': str('nav-link'),
        'generic_template_expanded': str('true'),
        'generic_template_show': str('show'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('false'),
        'NM_URL': settings.NM_URL,
    })


def NSS_Template(request):
    return render(request, 'nss_template.html', {
        'nss_template_active': str('active'),
        'generic_template_link_status': str('nav-link collapsed'),
        'generic_template_expanded': str('false'),
        'nssi_view_link_status': str('nav-link collapsed'),
        'nssi_view_expanded': str('false'),
        'NM_URL': settings.NM_URL,
    })
