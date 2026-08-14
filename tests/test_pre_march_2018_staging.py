import unittest

from scripts.analysis import stage_pre_march_2018_catalog as staging


class PreMarch2018StagingTests(unittest.TestCase):
    def test_staging_invariants(self):
        report = staging.stage()
        self.assertTrue(report["all_checks_passed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counts"]["promoted_candidates"], 59)
        self.assertEqual(report["staged_feed_summary"]["items"], 2195)
        self.assertEqual(report["staged_feed_summary"]["unknown_enclosure_lengths"], 0)
        self.assertTrue(report["trailer_review"]["included_in_stage"])
        self.assertEqual(report["trailer_review"]["staged_itunes_episode_type"], "trailer")
        self.assertEqual(report["trailer_review"]["production_decision"], "approved_for_inclusion")
        self.assertTrue(all(not values for values in report["collision_review"].values()))
        self.assertFalse(report["preservation_review"]["existing_history_records_changed"])
        self.assertFalse(report["preservation_review"]["existing_enclosure_map_records_changed"])
        self.assertFalse(report["preservation_review"]["existing_feed_items_semantically_changed"])


if __name__ == "__main__":
    unittest.main()
