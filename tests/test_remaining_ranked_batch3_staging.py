import unittest

from scripts.analysis import stage_remaining_ranked_batch3 as staging


class RemainingRankedBatch3StagingTests(unittest.TestCase):
    def test_staging_invariants(self):
        report = staging.stage()
        self.assertTrue(report["all_checks_passed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counts"]["promoted_candidates"], 4)
        self.assertEqual(
            set(report["candidate_story_ids"]),
            {"692150886", "696426869", "698360321", "705944777"},
        )


if __name__ == "__main__":
    unittest.main()
