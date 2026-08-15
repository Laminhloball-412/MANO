from django.test import SimpleTestCase

from .policy import choose_slice, choose_slice_from_observed


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
