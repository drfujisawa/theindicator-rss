import unittest

from scripts.analysis import stage_remaining_ranked_batch2 as staging


class RemainingRankedBatch2StagingTests(unittest.TestCase):
    def test_staging_invariants(self):
        report = staging.stage()
        self.assertTrue(report["all_checks_passed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counts"]["promoted_candidates"], 3)
        self.assertEqual(set(report["candidate_story_ids"]), {"706611154", "711569150", "717651281"})
        production_head = (staging.REPO_ROOT / "theindicator_feed.xml").read_text(encoding="utf-8")[:1000]
        staged_head = (staging.DEFAULT_STAGE_DIR / "theindicator_feed.xml").read_text(encoding="utf-8")[:1000]
        if "xmlns:ns0=" in production_head:
            self.assertIn("xmlns:ns0=", staged_head)
            self.assertNotIn("xmlns:itunes=", staged_head)


if __name__ == "__main__":
    unittest.main()
