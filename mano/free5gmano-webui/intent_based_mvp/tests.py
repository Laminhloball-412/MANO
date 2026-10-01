import json
from unittest.mock import patch

import django
from django.apps import apps
from django.template import Context, Engine
from django.test import RequestFactory, SimpleTestCase
from django.template.loader import render_to_string
from pathlib import Path

if not apps.ready:
    django.setup()

from .views import _normalize_service_intent, api_catalog, api_submit


class ServiceIntentInputTests(SimpleTestCase):
    def test_normalizes_business_fields_and_defaults_to_dry_run(self):
        result = _normalize_service_intent(
            {
                "service_type": "live_video",
                "quality": "high",
                "scale": 20,
                "priority": "high",
                "duration_minutes": 120,
            }
        )

        self.assertEqual("2.0", result["schema_version"])
        self.assertEqual("live_video", result["service_type"])
        self.assertEqual("dry_run", result["execution_mode"])

    def test_rejects_ue_and_observed_metric_fields(self):
        for forbidden in ("ue", "observed_kpis", "five_qi", "sst"):
            payload = {
                "service_type": "live_video",
                "quality": "high",
                "scale": 20,
                "priority": "high",
                "duration_minutes": 120,
                forbidden: {},
            }
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    _normalize_service_intent(payload)


class SliceIntentProxyTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("intent_based_mvp.views._proxy")
    def test_catalog_maps_to_v2_backend(self, proxy):
        proxy.return_value = object()

        result = api_catalog(self.factory.get("/Intent_Attach/api/catalog/"))

        self.assertIs(proxy.return_value, result)
        self.assertEqual("GET", proxy.call_args.args[0])
        self.assertEqual("/api/slice-intents/catalog/", proxy.call_args.args[1])

    @patch("intent_based_mvp.views._proxy")
    def test_submit_forwards_only_normalized_business_intent(self, proxy):
        proxy.return_value = object()
        request = self.factory.post(
            "/Intent_Attach/api/intents/",
            data=json.dumps(
                {
                    "service_type": "massive_sensor",
                    "quality": "standard",
                    "scale": 500,
                    "priority": "normal",
                    "duration_minutes": 60,
                }
            ),
            content_type="application/json",
        )

        result = api_submit(request)

        self.assertIs(proxy.return_value, result)
        forwarded = proxy.call_args.args[2]
        self.assertEqual("dry_run", forwarded["execution_mode"])
        self.assertNotIn("ue", forwarded)
        self.assertNotIn("observed_kpis", forwarded)

    @patch("intent_based_mvp.views._proxy")
    def test_business_translate_maps_to_business_backend(self, proxy):
        from .views import api_business_translate

        proxy.return_value = object()
        request = self.factory.post(
            "/Business_Intent/api/translate/",
            data=json.dumps(
                {
                    "business_requirement": "20 robot trong nhà máy trong 2 giờ",
                    "scale": 20,
                }
            ),
            content_type="application/json",
        )

        result = api_business_translate(request)

        self.assertIs(proxy.return_value, result)
        self.assertEqual("POST", proxy.call_args.args[0])
        self.assertEqual(
            "/api/business-intents/translate/", proxy.call_args.args[1]
        )

    @patch("intent_based_mvp.views._proxy")
    def test_business_proxy_rejects_fields_outside_the_contract(self, proxy):
        from .views import api_business_translate

        request = self.factory.post(
            "/Business_Intent/api/translate/",
            data=json.dumps(
                {
                    "business_requirement": "20 robot trong nhà máy",
                    "ue": "imsi-001010000000001",
                }
            ),
            content_type="application/json",
        )

        response = api_business_translate(request)

        self.assertEqual(400, response.status_code)
        proxy.assert_not_called()

    @patch("intent_based_mvp.views._proxy")
    def test_business_submit_maps_to_business_backend(self, proxy):
        from .views import api_business_submit

        proxy.return_value = object()
        request = self.factory.post(
            "/Business_Intent/api/intents/",
            data=json.dumps(
                {
                    "business_requirement": "20 robot trong nhà máy trong 2 giờ",
                    "execution_mode": "dry_run",
                }
            ),
            content_type="application/json",
        )

        result = api_business_submit(request)

        self.assertIs(proxy.return_value, result)
        self.assertEqual("POST", proxy.call_args.args[0])
        self.assertEqual("/api/business-intents/", proxy.call_args.args[1])
        self.assertNotIn("service_type", proxy.call_args.args[2])


class SliceProvisioningPageTests(SimpleTestCase):
    def test_page_renders_six_provisioning_sections_without_radio_controls(self):
        html = render_to_string("intent_based_mvp/intent_attach.html")

        for heading in (
            "Service Intent",
            "Translated Technical SLA",
            "Candidate Comparison",
            "Allocation Lifecycle",
            "Deployed Slice Configuration",
            "Active Slice Inventory",
        ):
            self.assertIn(heading, html)
        for removed in (
            "Registered UE",
            "Simulated Prometheus output",
            "Disconnect UE",
            "Decide and attach UE",
        ):
            self.assertNotIn(removed, html)

    def test_business_page_renders_a_compact_six_part_operator_flow(self):
        html = render_to_string("intent_based_mvp/business_intent.html")

        self.assertIn('class="business-intent-page"', html)
        for heading in (
            "Request",
            "Intent analysis",
            "Slice configuration",
            "Provisioning",
            "Core registration",
            "UE access",
        ):
            self.assertIn(heading, html)
        for removed in (
            "BUSINESS INTENT / 5G CORE",
            "From business need to a UE-ready slice",
            'class="step-index"',
            "Matched phrases and extracted values",
        ):
            self.assertNotIn(removed, html)
        self.assertNotIn("PRB", html)
        self.assertNotIn("UE đã attach", html)

    def test_business_page_loads_catalog_examples_and_explains_qos_sources(self):
        html = render_to_string("intent_based_mvp/business_intent.html")
        script = (
            Path(__file__).resolve().parent
            / "static"
            / "intent_based_mvp"
            / "business_intent.js"
        ).read_text(encoding="utf-8")

        self.assertIn('id="business-example"', html)
        self.assertIn("Service suggestions", html)
        self.assertNotIn("3GPP service example", html)
        self.assertIn('requestJson("api/catalog/"', script)
        for label in (
            "3GPP 5QI Priority Level",
            "Packet Delay Budget",
            "Packet Error Rate",
            "ARP source",
        ):
            self.assertIn(label, script)

    def test_mano_dashboard_embeds_grafana_through_same_origin_path(self):
        templates = (
            Path(__file__).resolve().parents[2]
            / "intent_flow_v2"
            / "webui_overlay"
            / "ui_templates"
        )
        html = Engine(dirs=[str(templates)]).get_template("dashboard.html").render(
            Context({"GRAFANA_URL": "/grafana/d/auto-slice-operations/test"})
        )

        self.assertIn("UE Multi-PDU Intent &amp; Slice Operations", html)
        self.assertIn("<iframe", html)
        self.assertIn('src="/grafana/d/auto-slice-operations/test"', html)
        self.assertNotIn("192.168.180.35", html)
