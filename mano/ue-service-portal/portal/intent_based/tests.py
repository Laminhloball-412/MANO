import json

from django.test import SimpleTestCase
from django.test import RequestFactory
from unittest.mock import patch
from requests import Timeout
from tempfile import TemporaryDirectory

from .policy import (
    NoFeasibleTemplate,
    choose_slice,
    choose_slice_from_observed,
    decide_slice,
)
from .service_intent import normalize_service_intent, translate_service_intent
from .slice_lifecycle import ensure_slice, inspect_slice, plan_slice
from .provisioning import JobStore as ProvisioningJobStore, process_intent


class IntentTranslationTests(SimpleTestCase):
    def test_high_video_translates_business_scale_to_sla(self):
        result = translate_service_intent(
            {
                "schema_version": "2.0",
                "service_type": "live_video",
                "quality": "high",
                "scale": 20,
                "priority": "high",
                "duration_minutes": 120,
                "execution_mode": "dry_run",
            }
        )

        self.assertEqual(20.0, result["technical_sla"]["min_throughput_mbps"])
        self.assertEqual(80.0, result["technical_sla"]["target_throughput_mbps"])
        self.assertEqual(80.0, result["technical_sla"]["peak_throughput_mbps"])
        self.assertEqual(100.0, result["technical_sla"]["max_latency_ms"])
        self.assertEqual("embb", result["technical_sla"]["preferred_template"])
        self.assertEqual("2.0", result["translation_trace"]["catalog_version"])

    def test_unknown_service_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "service_type"):
            normalize_service_intent(
                {
                    "schema_version": "2.0",
                    "service_type": "unknown",
                    "quality": "high",
                    "scale": 1,
                    "priority": "normal",
                    "duration_minutes": 10,
                    "execution_mode": "dry_run",
                }
            )

    def test_execution_mode_defaults_to_dry_run(self):
        normalized = normalize_service_intent(
            {
                "schema_version": "2.0",
                "service_type": "massive_sensor",
                "quality": "standard",
                "scale": 100,
                "priority": "normal",
                "duration_minutes": 60,
            }
        )

        self.assertEqual("dry_run", normalized["execution_mode"])

    def test_boolean_scale_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "scale"):
            normalize_service_intent(
                {
                    "schema_version": "2.0",
                    "service_type": "massive_sensor",
                    "quality": "standard",
                    "scale": True,
                    "priority": "normal",
                    "duration_minutes": 60,
                }
            )


class SliceDecisionTests(SimpleTestCase):
    def setUp(self):
        self.video_sla = {
            "min_throughput_mbps": 20.0,
            "target_throughput_mbps": 80.0,
            "peak_throughput_mbps": 80.0,
            "max_latency_ms": 100.0,
            "max_jitter_ms": 30.0,
            "max_packet_loss_percent": 1.0,
            "min_reliability_percent": 99.0,
            "expected_sessions": 20,
            "message_rate_per_second": 80.0,
            "preferred_template": "embb",
        }
        self.healthy_embb_instance = {
            "candidate_id": "slice-7",
            "namespace": "slice-7",
            "template_family": "embb",
            "sst": 1,
            "sd": "000007",
            "healthy": True,
            "residual_throughput_mbps": 900.0,
            "remaining_sessions": 500,
            "evidence_status": "ENFORCED",
        }
        self.too_small_cheap_slice = {
            "candidate_id": "small",
            "namespace": "slice-9",
            "template_family": "mmtc",
            "sst": 3,
            "sd": "000009",
            "healthy": True,
            "residual_throughput_mbps": 5.0,
            "remaining_sessions": 1000,
            "evidence_status": "DECLARED",
        }

    def test_infeasible_candidate_cannot_win_on_cost(self):
        decision = decide_slice(
            self.video_sla, instances=[self.too_small_cheap_slice]
        )

        rejected = next(
            item for item in decision["candidates"] if item["candidate_id"] == "small"
        )
        self.assertFalse(rejected["eligible"])
        self.assertIn("peak_throughput", rejected["failed_checks"])
        self.assertNotEqual("small", decision["selected_candidate"]["candidate_id"])

    def test_healthy_instance_is_reused_before_new_allocation(self):
        decision = decide_slice(
            self.video_sla, instances=[self.healthy_embb_instance]
        )

        self.assertEqual("REUSE", decision["decision_action"])
        self.assertEqual("slice-7", decision["selected_candidate"]["namespace"])

    def test_preferred_service_class_ranks_before_cross_class_reuse(self):
        reusable_urllc = {
            "candidate_id": "slice-2",
            "namespace": "slice-2",
            "template_family": "urllc",
            "sst": 2,
            "sd": "000001",
            "healthy": True,
            "residual_throughput_mbps": 100.0,
            "remaining_sessions": 1000,
            "evidence_status": "DECLARED",
        }

        decision = decide_slice(self.video_sla, instances=[reusable_urllc])

        self.assertEqual(
            "embb", decision["selected_candidate"]["template_family"]
        )

    def test_high_video_is_feasible_on_declared_embb_profile(self):
        translated = translate_service_intent(
            {
                "schema_version": "2.0",
                "service_type": "live_video",
                "quality": "high",
                "scale": 20,
                "priority": "high",
                "duration_minutes": 30,
                "execution_mode": "dry_run",
            }
        )

        decision = decide_slice(translated["technical_sla"], instances=[])

        self.assertEqual(
            "embb", decision["selected_candidate"]["template_family"]
        )

    def test_no_feasible_template_returns_explainable_error(self):
        impossible = dict(
            self.video_sla,
            peak_throughput_mbps=2000.0,
            min_throughput_mbps=1500.0,
            max_latency_ms=1.0,
            max_jitter_ms=0.5,
            max_packet_loss_percent=0.0,
            min_reliability_percent=100.0,
        )

        with self.assertRaisesRegex(
            NoFeasibleTemplate, "NO_FEASIBLE_TEMPLATE"
        ) as caught:
            decide_slice(impossible, instances=[])

        self.assertEqual(3, len(caught.exception.candidates))
        self.assertTrue(
            all(candidate["failed_checks"] for candidate in caught.exception.candidates)
        )

    def test_unhealthy_instance_is_never_reused(self):
        unhealthy = dict(self.healthy_embb_instance, healthy=False)

        decision = decide_slice(self.video_sla, instances=[unhealthy])

        rejected = next(
            item
            for item in decision["candidates"]
            if item["candidate_id"] == "slice-7"
        )
        self.assertIn("health", rejected["failed_checks"])
        self.assertEqual("ALLOCATE", decision["decision_action"])


class SliceLifecycleTests(SimpleTestCase):
    def setUp(self):
        self.sla = {
            "min_throughput_mbps": 20.0,
            "target_throughput_mbps": 80.0,
            "peak_throughput_mbps": 80.0,
            "max_latency_ms": 100.0,
            "max_jitter_ms": 30.0,
            "max_packet_loss_percent": 1.0,
            "min_reliability_percent": 99.9,
            "expected_sessions": 20,
            "message_rate_per_second": 80.0,
            "preferred_template": "embb",
        }
        self.selected = {
            "candidate_id": "template:embb",
            "action": "ALLOCATE",
            "template_family": "embb",
            "label": "eMBB",
            "namespace": None,
            "sst": 1,
            "sd": None,
            "dnn": "srsapn",
            "five_qi": 8,
            "arp_priority": 8,
            "evidence_status": "DECLARED",
        }
        self.deployment = {
            "template_family": "embb",
            "dnn": "srsapn",
            "five_qi": 8,
            "arp_priority": 4,
            "session_ambr_mbps": 80.0,
            "downlink_limit_mbps": 80.0,
            "evidence_status": "DECLARED",
        }

    def test_plan_exposes_auditable_configuration_without_allocating(self):
        plan = plan_slice(self.selected, self.sla, self.deployment)

        self.assertEqual("ALLOCATE", plan["action"])
        self.assertEqual(1, plan["deployment_configuration"]["sst"])
        self.assertEqual(8, plan["deployment_configuration"]["five_qi"])
        self.assertEqual(80.0, plan["deployment_configuration"]["peak_throughput_mbps"])
        self.assertEqual("DECLARED", plan["deployment_configuration"]["evidence_status"])

    @patch("portal.intent_based.slice_lifecycle._pod_and_endpoint_health")
    def test_inspection_keeps_health_and_configuration_evidence_separate(self, health):
        health.return_value = {
            "operational": True,
            "infrastructure_ready": True,
            "pfcp_associated": True,
            "components": {"pcf": True, "smf": True, "upf": True},
            "pods": ["pcf-a", "smf-a", "upf-a"],
            "endpoints": ["pcf-svc", "smf-svc", "upf-svc"],
        }
        plan = plan_slice(self.selected, self.sla, self.deployment)

        result = inspect_slice("slice-7", plan)

        self.assertTrue(result["health"]["operational"])
        self.assertEqual("DECLARED", result["configuration"]["evidence_status"])
        self.assertEqual("slice-7", result["namespace"])

    @patch("portal.intent_based.slice_lifecycle._pod_and_endpoint_health")
    def test_inspection_records_identity_returned_by_allocator(self, health):
        health.return_value = {
            "operational": True,
            "infrastructure_ready": True,
            "pfcp_associated": True,
            "components": {"pcf": True, "smf": True, "upf": True},
            "pods": ["pcf-a", "smf-a", "upf-a"],
            "endpoints": ["pcf-svc", "smf-svc", "upf-svc"],
        }
        plan = plan_slice(self.selected, self.sla, self.deployment)

        result = inspect_slice(
            "slice-7",
            plan,
            allocation={
                "namespace": "slice-7",
                "sst": 1,
                "sd": "000007",
                "slice_type": "embb",
                "status": "READY",
            },
        )

        self.assertEqual("000007", result["configuration"]["sd"])
        self.assertEqual(1, result["configuration"]["sst"])

    @patch("portal.intent_based.slice_lifecycle._pod_and_endpoint_health")
    @patch("portal.intent_based.slice_lifecycle.requests.post")
    @patch("portal.intent_based.slice_lifecycle._auto_slice_status")
    def test_allocate_timeout_reconciles_a_committed_slice(self, status, post, health):
        status.side_effect = [
            {"tracked_slices": {}},
            {
                "tracked_slices": {
                    "slice-7": {
                        "slice_type": "embb",
                        "sst": 1,
                        "sd": "000007",
                        "status": "READY",
                    }
                }
            },
        ]
        post.side_effect = Timeout("response timed out after commit")
        health.return_value = {
            "operational": True,
            "infrastructure_ready": True,
            "pfcp_associated": True,
            "components": {"pcf": True, "smf": True, "upf": True},
            "pods": ["pcf-a", "smf-a", "upf-a"],
            "endpoints": ["pcf-svc", "smf-svc", "upf-svc"],
        }
        plan = plan_slice(self.selected, self.sla, self.deployment)

        result = ensure_slice(plan)

        self.assertEqual("RECOVERED", result["action"])
        self.assertEqual("slice-7", result["slice"]["namespace"])


class SliceProvisioningTests(SimpleTestCase):
    def setUp(self):
        self.intent = {
            "schema_version": "2.0",
            "service_type": "live_video",
            "quality": "high",
            "scale": 20,
            "priority": "high",
            "duration_minutes": 120,
            "execution_mode": "dry_run",
        }

    def test_dry_run_stops_at_plan_without_calling_allocate(self):
        def forbidden_allocate(plan):
            raise AssertionError("dry-run called allocate")

        result = process_intent(
            self.intent, instances=[], ensure_provider=forbidden_allocate
        )

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("DRY_RUN", result["stage"])
        self.assertIn("deployment_configuration", result["plan"])
        self.assertNotIn("ue", result)
        self.assertNotIn("pdu", result)

    def test_execute_reaches_ready_without_ue_operations(self):
        intent = dict(self.intent, execution_mode="execute")

        def allocated(plan):
            return {
                "action": "CREATED",
                "slice": {"namespace": "slice-7", "sst": 1, "sd": "000007"},
                "inspection": {
                    "namespace": "slice-7",
                    "configuration": dict(
                        plan["deployment_configuration"], namespace="slice-7"
                    ),
                    "health": {"operational": True, "pfcp_associated": True},
                },
            }

        result = process_intent(intent, instances=[], ensure_provider=allocated)

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("READY", result["stage"])
        self.assertEqual("slice-7", result["configuration"]["namespace"])
        self.assertNotIn("attached_ue", result)

    def test_job_store_persists_configuration_for_inspection_endpoint(self):
        with TemporaryDirectory() as temporary:
            store = ProvisioningJobStore(path="{}/jobs.json".format(temporary))
            created = store.create(self.intent)
            store.update(
                created["intent_id"],
                status="COMPLETED",
                stage="DRY_RUN",
                configuration={"sst": 1, "evidence_status": "DECLARED"},
            )

            loaded = store.get(created["intent_id"])

        self.assertEqual(1, loaded["configuration"]["sst"])
        self.assertEqual("DECLARED", loaded["configuration"]["evidence_status"])


class SliceProvisioningApiTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_catalog_endpoint_exposes_business_services(self):
        from .views import api_slice_catalog

        response = api_slice_catalog(self.factory.get("/api/slice-intents/catalog/"))
        body = json.loads(response.content)

        self.assertEqual(200, response.status_code)
        self.assertTrue(body["ok"])
        self.assertIn("live_video", body["data"]["services"])

    @patch("portal.intent_based.views.submit_slice_job")
    def test_submit_endpoint_returns_intent_id_without_ue_fields(self, submit):
        from .views import api_slice_intents

        submit.return_value = {
            "intent_id": "slice-intent-abc",
            "status": "QUEUED",
            "stage": "RECEIVED",
            "input": {"execution_mode": "dry_run"},
        }
        request = self.factory.post(
            "/api/slice-intents/",
            data=json.dumps(
                {
                    "schema_version": "2.0",
                    "service_type": "live_video",
                    "quality": "high",
                    "scale": 20,
                    "priority": "high",
                    "duration_minutes": 120,
                    "execution_mode": "dry_run",
                }
            ),
            content_type="application/json",
        )

        response = api_slice_intents(request)
        body = json.loads(response.content)

        self.assertEqual(202, response.status_code)
        self.assertEqual("slice-intent-abc", body["job"]["intent_id"])
        self.assertNotIn("ue", body["job"])

class IntentPolicyTests(SimpleTestCase):
    def test_video_selects_embb(self):
        result = choose_slice(
            {
                "min_throughput_mbps": 20,
                "max_latency_ms": 120,
                "max_jitter_ms": 15,
                "max_packet_loss_percent": 1,
                "min_reliability_percent": 99,
                "message_rate_per_second": 30,
            }
        )
        self.assertEqual("embb", result["selected_slice"]["slice_type"])

    def test_prometheus_video_observation_selects_embb(self):
        result = choose_slice_from_observed({"throughput_mbps_avg":35,"latency_ms_p95":80,"jitter_ms_p95":12,"packet_loss_percent_avg":0.5,"reliability_percent_window":99.5,"message_rate_per_second_avg":30})
        self.assertEqual("embb", result["selected_slice"]["slice_type"])

    def test_prometheus_control_observation_selects_urllc(self):
        result = choose_slice_from_observed({"throughput_mbps_avg":5,"latency_ms_p95":8,"jitter_ms_p95":2,"packet_loss_percent_avg":0.01,"reliability_percent_window":99.999,"message_rate_per_second_avg":100})
        self.assertEqual("urllc", result["selected_slice"]["slice_type"])

    def test_prometheus_sensor_observation_selects_mmtc(self):
        result = choose_slice_from_observed({"throughput_mbps_avg":0.2,"latency_ms_p95":180,"jitter_ms_p95":80,"packet_loss_percent_avg":3,"reliability_percent_window":97,"message_rate_per_second_avg":1})
        self.assertEqual("mmtc", result["selected_slice"]["slice_type"])

    def test_robot_control_selects_urllc(self):
        result = choose_slice(
            {
                "min_throughput_mbps": 2,
                "max_latency_ms": 10,
                "max_jitter_ms": 3,
                "max_packet_loss_percent": 0.01,
                "min_reliability_percent": 99.999,
                "message_rate_per_second": 100,
            }
        )
        self.assertEqual("urllc", result["selected_slice"]["slice_type"])

    def test_sensor_selects_lowest_cost_mmtc(self):
        result = choose_slice(
            {
                "min_throughput_mbps": 0.2,
                "max_latency_ms": 250,
                "max_jitter_ms": 120,
                "max_packet_loss_percent": 5,
                "min_reliability_percent": 95,
                "message_rate_per_second": 1,
            }
        )
        self.assertEqual("mmtc", result["selected_slice"]["slice_type"])

    def test_impossible_requirement_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "No slice profile"):
            choose_slice(
                {
                    "min_throughput_mbps": 2000,
                    "max_latency_ms": 1,
                    "max_jitter_ms": 1,
                    "max_packet_loss_percent": 0,
                    "min_reliability_percent": 100,
                    "message_rate_per_second": 1,
                }
            )

    def test_selection_is_limited_by_subscriber_access(self):
        result = choose_slice(
            {
                "min_throughput_mbps": 0.2,
                "max_latency_ms": 250,
                "max_jitter_ms": 120,
                "max_packet_loss_percent": 5,
                "min_reliability_percent": 95,
                "message_rate_per_second": 1,
            },
            allowed_slice_types=["embb", "urllc"],
        )
        self.assertEqual("embb", result["selected_slice"]["slice_type"])
