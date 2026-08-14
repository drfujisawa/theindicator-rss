import unittest

from scripts.analysis import stage_remaining_ranked_batch5 as staging


EXPECTED_IDS = {
    "624806713", "629586025", "634082530", "634384070", "635463414",
    "638293909", "640596144", "641623813", "645291326", "656269912",
    "663640858",
}


class RemainingRankedBatch5StagingTests(unittest.TestCase):
    def test_staging_invariants(self):
        report = staging.stage()
        self.assertTrue(report["all_checks_passed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counts"]["promoted_candidates"], 11)
        self.assertEqual(set(report["candidate_story_ids"]), EXPECTED_IDS)


if __name__ == "__main__":
    unittest.main()
