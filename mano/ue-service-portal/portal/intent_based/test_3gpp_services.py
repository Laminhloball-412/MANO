from django.test import SimpleTestCase

from .business_intent import translate_business_requirement


class Standardized3gppBusinessServiceTests(SimpleTestCase):
    EXISTING_STANDARDIZED_5QI = (
        ("broadband", 9, 90),
        ("voice_call", 1, 20),
        ("video_call", 2, 40),
        ("video_streaming", 8, 80),
        ("live_video", 7, 70),
        ("immersive_media", 80, 68),
        ("interactive_control", 3, 30),
        ("v2x", 3, 30),
        ("low_latency_gaming", 3, 30),
        ("sensor_telemetry", 9, 90),
        ("mission_critical_voice", 65, 7),
        ("mission_critical_video", 67, 15),
        ("priority_alarm", 8, 80),
    )
    SERVICE_CASES = (
        ("ims_signalling", "IMS signalling for 20 sessions for 60 minutes", "embb", 5, "NON_GBR", 10),
        ("ai_ml_model_download", "AI ML model download for 10 devices for 60 minutes", "embb", 6, "NON_GBR", 60),
        ("non_critical_push_to_talk", "Non mission critical push to talk voice for 20 users for 60 minutes", "embb", 66, "GBR", 20),
        ("live_uplink_streaming", "Live uplink streaming for 20 cameras for 60 minutes", "embb", 71, "GBR", 56),
        ("mission_critical_signalling", "Mission critical delay sensitive signalling for 20 responders for 60 minutes", "urllc", 69, "NON_GBR", 5),
        ("mission_critical_data", "Mission critical data for 20 responders for 60 minutes", "urllc", 70, "NON_GBR", 55),
        ("electricity_distribution_medium_voltage", "Medium voltage electricity distribution for 20 sites for 60 minutes", "urllc", 3, "GBR", 30),
        ("process_automation_monitoring", "Process automation monitoring for 20 machines for 60 minutes", "urllc", 3, "GBR", 30),
        ("discrete_automation", "Discrete automation for 20 controllers for 60 minutes", "urllc", 82, "DELAY_CRITICAL_GBR", 19),
        ("intelligent_transport", "Intelligent transport systems for 20 vehicles for 60 minutes", "urllc", 84, "DELAY_CRITICAL_GBR", 24),
        ("electricity_distribution_high_voltage", "High voltage electricity distribution for 20 sites for 60 minutes", "urllc", 85, "DELAY_CRITICAL_GBR", 21),
        ("remote_driving", "Remote driving for 20 vehicles for 60 minutes", "urllc", 85, "DELAY_CRITICAL_GBR", 21),
        ("advanced_driving", "Advanced driving collision avoidance for 20 vehicles for 60 minutes", "urllc", 86, "DELAY_CRITICAL_GBR", 18),
        ("motion_tracking", "Interactive motion tracking for 20 users for 60 minutes", "urllc", 87, "DELAY_CRITICAL_GBR", 25),
        ("split_ai_ml_inference", "Split AI ML inference uplink for 20 devices for 60 minutes", "urllc", 88, "DELAY_CRITICAL_GBR", 25),
        ("edge_visual_rendering", "Visual content for edge rendering for 20 users for 60 minutes", "urllc", 89, "DELAY_CRITICAL_GBR", 25),
    )

    def test_english_business_phrases_map_to_standardized_5qi_profiles(self):
        for service_type, sentence, family, five_qi, resource_type, priority_level in self.SERVICE_CASES:
            with self.subTest(service_type=service_type):
                result = translate_business_requirement(
                    {"business_requirement": sentence, "execution_mode": "dry_run"}
                )
                self.assertEqual(service_type, result["structured_intent"]["service_type"])
                self.assertEqual(family, result["technical_sla"]["preferred_template"])
                service_qos = result["deployment_policy"]["service_qos"]
                self.assertEqual(five_qi, service_qos["five_qi"])
                self.assertEqual(resource_type, service_qos["resource_type"])
                self.assertEqual(priority_level, service_qos["five_qi_priority_level"])
                self.assertEqual("3GPP_TS_23_501_TABLE_5_7_4_1", service_qos["five_qi_source"])
                self.assertEqual("PROJECT_OPERATOR_POLICY", service_qos["arp_source"])

    def test_arp_is_identified_as_operator_policy_not_a_3gpp_service_mapping(self):
        result = translate_business_requirement(
            {
                "business_requirement": "Remote driving for 10 vehicles for 30 minutes",
                "execution_mode": "dry_run",
            }
        )

        service_qos = result["deployment_policy"]["service_qos"]
        self.assertEqual(85, service_qos["five_qi"])
        self.assertEqual(2, service_qos["arp_priority"])
        self.assertEqual("PROJECT_OPERATOR_POLICY", service_qos["arp_source"])

    def test_existing_profiles_also_expose_3gpp_characteristics_provenance(self):
        from .service_intent import load_catalog, normalize_qos_profile

        catalog = load_catalog()
        for service_type, five_qi, priority_level in self.EXISTING_STANDARDIZED_5QI:
            with self.subTest(service_type=service_type):
                qos = normalize_qos_profile(
                    catalog["services"][service_type]["qos"], "service_qos"
                )
                self.assertEqual(five_qi, qos["five_qi"])
                self.assertEqual(priority_level, qos["five_qi_priority_level"])
                self.assertEqual(
                    "3GPP_TS_23_501_TABLE_5_7_4_1", qos["five_qi_source"]
                )
                self.assertEqual("PROJECT_OPERATOR_POLICY", qos["arp_source"])
