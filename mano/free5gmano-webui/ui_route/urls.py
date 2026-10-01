from django.urls import path
from django.conf.urls import url, include
from ui_route.views import *
from ui_route.pdu_session_flow import (
    pdu_session_flow,
    pdu_session_flow_status,
)
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

urlpatterns = [
    url(r'^Business_Intent/', include('intent_based_mvp.business_urls')),
    url(r'^Intent_Attach/', include('intent_based_mvp.urls')),
    url(r'^dashboard/$', dashboard),
    url(r'^Auto_Slice/$', auto_slice),
    url(r'^api/auto-slice/status/$', auto_slice_status),
    url(r'^api/auto-slice/create/$', auto_slice_create),
    url(r'^api/auto-slice/delete/$', auto_slice_delete),
    url(r'^PDU_Session_Flow/$', pdu_session_flow),
    url(r'^api/pdu-session-flow/status/$', pdu_session_flow_status),
    url(r'^nssi_topology/$', nssi_topology),
    url(r'^NSS_Instance/$', NSS_Instance),
    url(r'^nfv_mano_plugin/$', nfv_mano_plugin),
    url(r'^VNF_Template/$', VNF_Template),
    url(r'^NSD_Template/$', NSD_Template),
    url(r'^NRM_Template/$', NRM_Template),
    url(r'^NSS_Template/$', NSS_Template),
]

urlpatterns += staticfiles_urlpatterns()
