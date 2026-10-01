from django.urls import path

from . import views


urlpatterns = [
    path("", views.business_page, name="business_intent_page"),
    path("api/catalog/", views.api_catalog, name="business_catalog"),
    path("api/translate/", views.api_business_translate, name="business_translate"),
    path("api/intents/", views.api_business_submit, name="business_submit"),
    path(
        "api/intents/<str:intent_id>/",
        views.api_business_job,
        name="business_job",
    ),
    path("api/instances/", views.api_business_instances, name="business_instances"),
]
