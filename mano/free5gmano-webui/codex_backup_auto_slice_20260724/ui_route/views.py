import requests
from free5gmano_webui import settings
from django.shortcuts import render

# Create your views here.
from datetime import datetime
from urllib.parse import urlencode
import requests
from free5gmano_webui import settings


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
        'var-slice_filter': '$__all',
        'refresh': '15s',
        'theme': 'light',
    }

    datasource_uid = get_grafana_datasource_uid()
    if datasource_uid:
        params['var-datasource'] = datasource_uid

    return (
        f'{settings.GRAFANA_BASE_URL}/d/'
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