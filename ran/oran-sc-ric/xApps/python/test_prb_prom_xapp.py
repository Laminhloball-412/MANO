import unittest

from prb_prom_xapp import PrbMetricStore


class PrbMetricStoreTests(unittest.TestCase):
    def test_exports_latest_cell_prb_values(self):
        store = PrbMetricStore("gnbd_460_099_00019b_0", stale_after_seconds=5)
        store.update_cell(
            {
                "measData": {
                    "RRU.PrbUsedDl": [11, 12],
                    "RRU.PrbAvailDl": [41, 40],
                    "RRU.PrbTotDl": [21, 23],
                    "RRU.PrbUsedUl": [5, 6],
                    "RRU.PrbAvailUl": [47, 46],
                    "RRU.PrbTotUl": [10, 12],
                }
            },
            received_at=100,
        )

        body = store.render(now=102)

        self.assertIn('srsran_prb_used{direction="dl",e2_node_id="gnbd_460_099_00019b_0",scope="cell"} 12', body)
        self.assertIn('srsran_prb_available{direction="ul",e2_node_id="gnbd_460_099_00019b_0",scope="cell"} 46', body)
        self.assertIn('srsran_prb_utilization_percent{direction="dl",e2_node_id="gnbd_460_099_00019b_0",scope="cell"} 23', body)
        self.assertIn("srsran_kpm_exporter_up 1", body)

    def test_exports_prb_by_ran_ue_id(self):
        store = PrbMetricStore("node-a", stale_after_seconds=5)
        store.update_ues(
            {
                "ueMeasData": {
                    0: {"measData": {"RRU.PrbUsedDl": [7], "RRU.PrbUsedUl": [2]}},
                    3: {"measData": {"RRU.PrbUsedDl": [9], "RRU.PrbUsedUl": [4]}},
                }
            },
            received_at=200,
        )

        body = store.render(now=201)

        self.assertIn('srsran_prb_used{direction="dl",e2_node_id="node-a",ran_ue_id="0",scope="ue"} 7', body)
        self.assertIn('srsran_prb_used{direction="ul",e2_node_id="node-a",ran_ue_id="3",scope="ue"} 4', body)

    def test_ignores_no_value_and_non_numeric_samples(self):
        store = PrbMetricStore("node-a")
        store.update_cell(
            {"measData": {"RRU.PrbUsedDl": ["noValue"], "RRU.PrbUsedUl": [None]}},
            received_at=300,
        )

        body = store.render(now=301)

        self.assertNotIn('srsran_prb_used{direction="dl"', body)
        self.assertNotIn('srsran_prb_used{direction="ul"', body)

    def test_marks_exporter_down_when_samples_are_stale(self):
        store = PrbMetricStore("node-a", stale_after_seconds=5)
        store.update_cell({"measData": {"RRU.PrbUsedDl": [1]}}, received_at=400)

        body = store.render(now=406)

        self.assertIn("srsran_kpm_exporter_up 0", body)
        self.assertIn("srsran_kpm_sample_age_seconds 6", body)


if __name__ == "__main__":
    unittest.main()
