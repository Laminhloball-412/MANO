from django.urls import path

from . import views


urlpatterns = [
    path("intent-attach/", views.intent_attach, name="intent_attach"),
    path("api/intent-attach/ues/", views.api_ues, name="intent_attach_ues"),
    path("api/intent-attach/jobs/", views.api_submit, name="intent_attach_submit"),
    path(
        "api/intent-attach/jobs/<str:job_id>/",
        views.api_job,
        name="intent_attach_job",
    ),
]

