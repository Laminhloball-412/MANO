from django.test import SimpleTestCase

from .views import _normalize_simulation


class SimulationInputTests(SimpleTestCase):
    def test_normalizes_minimal_mvp_document(self):
        result = _normalize_simulation(
            {
                "input_source": "prometheus_simulation",
                "ue": {"ue_id": 2},
                "service": {"name": "robot_control", "category": "control"},
                "observed_kpis": {"throughput_mbps_avg": 2},
            }
        )
        self.assertEqual("1.0", result["schema_version"])
        self.assertEqual(2, result["ue"]["ue_id"])
        self.assertEqual("prometheus_simulation", result["input_source"])

    def test_prometheus_input_is_reserved_for_future_adapter(self):
        with self.assertRaisesRegex(ValueError, "prometheus_simulation only"):
            _normalize_simulation(
                {
                    "input_source": "simulation",
                    "ue": {"ue_id": 1},
                    "service": {"name": "video"},
                }
            )
