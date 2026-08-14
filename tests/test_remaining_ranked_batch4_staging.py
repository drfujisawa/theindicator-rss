import unittest

from scripts.analysis import stage_remaining_ranked_batch4 as staging


class RemainingRankedBatch4StagingTests(unittest.TestCase):
    def test_staging_invariants(self):
        report = staging.stage()
        self.assertTrue(report["all_checks_passed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counts"]["promoted_candidates"], 6)
        self.assertEqual(
            set(report["candidate_story_ids"]),
            {"669768474", "674297137", "678327898", "683323818", "690508923", "691782578"},
        )


if __name__ == "__main__":
    unittest.main()
