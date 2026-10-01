from django.urls import path

from . import views


urlpatterns = [
    path(
        "api/business-intents/translate/",
        views.api_business_translate,
        name="business_intent_translate",
    ),
    path(
        "api/business-intents/",
        views.api_business_intents,
        name="business_intent_submit",
    ),
    path("intent-attach/", views.intent_attach, name="intent_attach"),
    path("api/intent-attach/ues/", views.api_ues, name="intent_attach_ues"),
    path("api/intent-attach/jobs/", views.api_submit, name="intent_attach_submit"),
    path(
        "api/intent-attach/jobs/<str:job_id>/",
        views.api_job,
        name="intent_attach_job",
    ),
    path(
        "api/slice-intents/catalog/",
        views.api_slice_catalog,
        name="slice_intent_catalog",
    ),
    path(
        "api/slice-intents/",
        views.api_slice_intents,
        name="slice_intent_submit",
    ),
    path(
        "api/slice-intents/<str:intent_id>/",
        views.api_slice_intent,
        name="slice_intent_detail",
    ),
    path(
        "api/slice-intents/<str:intent_id>/configuration/",
        views.api_slice_configuration,
        name="slice_intent_configuration",
    ),
    path(
        "api/slice-instances/",
        views.api_slice_instances,
        name="slice_instances",
    ),
]

