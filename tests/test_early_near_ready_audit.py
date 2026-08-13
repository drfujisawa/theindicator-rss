import unittest

from scripts.analysis import audit_early_near_ready_candidates as audit


class EarlyNearReadyAuditTests(unittest.TestCase):
    def setUp(self):
        self.report = audit.build_audit()

    def test_all_eight_candidates_are_accounted_for_without_mutation(self):
        self.assertEqual(self.report["summary"]["candidates_reviewed"], 8)
        self.assertEqual(self.report["summary"]["outcomes"], {"already_in_production": 8})
        self.assertEqual(self.report["mode"], "read_only")
        self.assertFalse(self.report["production_files_modified"])

    def test_rebroadcasts_are_retained_as_distinct_dated_releases(self):
        rebroadcasts = [
            item for item in self.report["episodes"]
            if item["release_classification"] == "confirmed_indicator_rebroadcast"
        ]
        self.assertEqual(
            {item["npr_story_id"] for item in rebroadcasts},
            {"643056045", "643423980"},
        )

    def test_every_candidate_has_unique_player_audio_and_no_production_collision(self):
        for item in self.report["episodes"]:
            evidence = item["evidence"]
            self.assertTrue(evidence["single_observed_player_pair"])
            self.assertTrue(evidence["audio_identity_supported"])
            self.assertFalse(evidence["story_absent_from_production"])
            self.assertFalse(evidence["audio_path_unique_in_production"])

    def test_special_recoveries_retain_their_independent_source(self):
        special = {
            item["npr_story_id"]: item for item in self.report["episodes"]
            if item["prior_category"] == "special_recovery_review"
        }
        self.assertEqual(set(special), {"662708285", "716132270"})
        self.assertTrue(all(item["evidence"]["special_recovery_source"] for item in special.values()))


if __name__ == "__main__":
    unittest.main()
