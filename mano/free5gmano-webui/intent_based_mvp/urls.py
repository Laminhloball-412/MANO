from django.urls import path

from . import views


urlpatterns = [
    path("", views.page, name="slice_provisioning"),
    path("api/catalog/", views.api_catalog, name="slice_provisioning_catalog"),
    path("api/intents/", views.api_submit, name="slice_provisioning_submit"),
    path(
        "api/intents/<str:intent_id>/",
        views.api_job,
        name="slice_provisioning_job",
    ),
    path(
        "api/intents/<str:intent_id>/configuration/",
        views.api_configuration,
        name="slice_provisioning_configuration",
    ),
    path(
        "api/instances/",
        views.api_instances,
        name="slice_provisioning_instances",
    ),
]
