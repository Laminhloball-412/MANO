import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from .views import auto_slice_create, build_grafana_url


class AutoSliceCreateTests(SimpleTestCase):
    @patch("ui_route.views._run_auto_slice")
    def test_ready_for_ue_response_preserves_core_registration(self, run_auto_slice):
        evidence = {
            "verified": True,
            "snssai": [{"sst": 1, "sd": "000002"}],
        }
        run_auto_slice.return_value = {
            "intent_id": "web-embb-test",
            "slice_type": "embb",
            "namespace": "slice-2",
            "sst": 1,
            "sd": "000002",
            "status": "READY_FOR_UE",
            "nssi_id": "nssi-test",
            "pods": [],
            "deployment_configuration": {},
            "core_registration": evidence,
            "core_registration_error": None,
        }

        request = RequestFactory().post(
            "/api/auto-slice/create/",
            data=json.dumps({"slice_type": "embb"}),
            content_type="application/json",
        )
        response = auto_slice_create(request)
        document = json.loads(response.content.decode("utf-8"))

        self.assertEqual(200, response.status_code)
        self.assertEqual(evidence, document["data"]["core_registration"])
        self.assertIsNone(document["data"]["core_registration_error"])


class DashboardPublicUrlTests(SimpleTestCase):
    @override_settings(
        GRAFANA_BASE_URL="http://192.168.180.35:30030",
        GRAFANA_PUBLIC_URL="/grafana",
    )
    def test_browser_dashboard_url_uses_public_relative_base(self):
        url = build_grafana_url()

        self.assertTrue(url.startswith("/grafana/d/auto-slice-operations/"))
        self.assertNotIn("192.168.180.35", url)
