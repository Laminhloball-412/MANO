from django.urls import path

from . import views


urlpatterns = [
    path("", views.page, name="intent_attach_mvp"),
    path("api/ues/", views.api_ues, name="intent_attach_mvp_ues"),
    path("api/jobs/", views.api_submit, name="intent_attach_mvp_submit"),
    path("api/jobs/<str:job_id>/", views.api_job, name="intent_attach_mvp_job"),
    path("api/radio/status/", views.api_radio_status, name="intent_attach_mvp_radio_status"),
    path("api/radio/disconnect/", views.api_radio_disconnect, name="intent_attach_mvp_radio_disconnect"),
]
