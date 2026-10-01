from pathlib import Path

from django.test import SimpleTestCase
from django.template.loader import render_to_string


class BusinessCatalogUiTests(SimpleTestCase):
    def test_page_loads_session_store_before_business_intent_controller(self):
        html = render_to_string("intent_based_mvp/business_intent.html")
        script = (
            Path(__file__).resolve().parent
            / "static"
            / "intent_based_mvp"
            / "business_intent.js"
        ).read_text(encoding="utf-8")

        self.assertIn("business_intent_session.js", html)
        session_position = html.index("business_intent_session.js")
        controller_position = html.index("business_intent.js")
        self.assertLess(session_position, controller_position)
        self.assertIn("restoreSession", script)
        self.assertIn("sessionStore.save", script)
        self.assertIn('requestJson("api/intents/"', script)

    def test_page_uses_operator_console_copy_without_numbered_cards(self):
        html = render_to_string("intent_based_mvp/business_intent.html")

        self.assertIn("Submit a service request and review the resulting slice.", html)
        self.assertIn('class="business-workspace"', html)
        self.assertNotIn('class="step-index"', html)
        self.assertNotIn("From business need to a UE-ready slice", html)

    def test_page_loads_catalog_examples_and_explains_qos_sources(self):
        html = render_to_string("intent_based_mvp/business_intent.html")
        script = (
            Path(__file__).resolve().parent
            / "static"
            / "intent_based_mvp"
            / "business_intent.js"
        ).read_text(encoding="utf-8")

        self.assertIn('id="business-example"', html)
        self.assertIn("Service suggestions", html)
        self.assertNotIn("Try a 3GPP service example", html)
        self.assertIn('<label for="business-requirement">Request</label>', html)
        self.assertNotIn("What service does the customer need?", html)
        self.assertNotIn("Original business requirement", html)
        self.assertNotIn("I need a network slice", html)
        self.assertIn("business_intent_catalog.js?v=catalog-search-1", html)
        self.assertIn("business_intent.js?v=catalog-search-1", html)
        self.assertIn('requestJson("api/catalog/"', script)
        self.assertIn('option.textContent = service.label;', script)
        for label in (
            "3GPP 5QI Priority Level",
            "Packet Delay Budget",
            "Packet Error Rate",
            "ARP source",
        ):
            self.assertIn(label, script)

    def test_page_has_bk_mano_title_and_separate_service_search(self):
        html = render_to_string("intent_based_mvp/business_intent.html")

        self.assertIn("Business Intent - BK MANO", html)
        self.assertIn('id="business-service-search"', html)
        self.assertIn('aria-controls="business-example"', html)
