import json
import unittest

from scripts.analysis import audit_final_catalog_completeness as audit


class FinalCatalogCompletenessAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(audit.OUTPUT.read_text(encoding="utf-8"))

    def test_defined_scope_is_complete(self):
        self.assertEqual(
            self.report["verdict"],
            "complete_for_defined_program_feed_scope",
        )
        self.assertEqual(self.report["local"]["feed_items"], 2195)
        self.assertEqual(self.report["local"]["unique_guids"], 2195)
        self.assertEqual(self.report["local"]["unknown_or_zero_enclosure_lengths"], 0)

    def test_current_first_party_catalogs_have_no_omissions(self):
        self.assertFalse(self.report["npr_current_feed"]["missing_from_local_by_date_title"])
        self.assertFalse(self.report["apple_current_catalog"]["missing_from_local_by_date_title"])
        self.assertFalse(self.report["tvdb"]["unresolved_candidate_omissions"])

    def test_remaining_tvdb_mismatches_are_classified(self):
        classifications = {
            item["classification"]
            for item in self.report["tvdb"]["classified_catalog_mismatches"]
        }
        self.assertEqual(classifications, {
            "alternate_title_for_existing_indicator_episode",
            "non_indicator_cross_feed_promotion",
        })

    def test_launch_feed_and_deliberate_exclusions_are_accounted_for(self):
        launch = self.report["original_launch_feed"]
        self.assertEqual(launch["historical_items"], 59)
        self.assertEqual(launch["exact_live_audio_responses"], 59)
        self.assertTrue(launch["all_present_in_current_feed"])
        exclusions = self.report["local"]["history_records_absent_from_feed"]
        self.assertEqual(len(exclusions), 4)
        self.assertTrue(all(
            item["classification"] == "planet_money_compilation_reusing_indicator_segments"
            for item in exclusions
        ))


if __name__ == "__main__":
    unittest.main()
