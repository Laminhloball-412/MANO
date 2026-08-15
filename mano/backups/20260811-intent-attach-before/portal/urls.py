from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("ue-management/", views.ue_management, name="ue_management"),
    path("service-lab/", views.service_lab, name="service_lab"),
    path("measurements/", views.measurements, name="measurements"),
    path("architecture/", views.architecture, name="architecture"),
    path("api/state/", views.api_state, name="api_state"),
    path("api/ue-drafts/", views.api_create_ue_draft, name="api_create_ue_draft"),
    path("api/ue-drafts/<int:ue_id>/delete/", views.api_delete_ue_draft, name="api_delete_ue_draft"),
    path("api/radio/status/", views.api_radio_status, name="api_radio_status"),
    path("api/radio/connect/", views.api_radio_connect, name="api_radio_connect"),
    path("api/radio/disconnect/", views.api_radio_disconnect, name="api_radio_disconnect"),
    path("api/workloads/start/", views.api_workload_start, name="api_workload_start"),
    path("api/workloads/stop/", views.api_workload_stop, name="api_workload_stop"),
    path("api/telemetry/", views.api_telemetry, name="api_telemetry"),
    path("api/payload/", views.api_payload, name="api_payload"),
    path("api/sensor-ingest/", views.api_sensor_ingest, name="api_sensor_ingest"),
    path("metrics/", views.prometheus_metrics, name="prometheus_metrics"),
    path("healthz/", views.healthz, name="healthz"),
]
