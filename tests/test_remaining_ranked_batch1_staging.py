import unittest

from scripts.analysis import stage_remaining_ranked_batch1 as staging


class RemainingRankedBatch1StagingTests(unittest.TestCase):
    def test_staging_invariants_and_corrected_date(self):
        report = staging.stage()
        self.assertTrue(report["all_checks_passed"])
        self.assertTrue(all(report["checks"].values()))
        history = staging.json.loads(
            (staging.DEFAULT_STAGE_DIR / "indicator_history.json").read_text(encoding="utf-8")
        )
        aunt_becky = next(
            item for item in history["episodes"] if str(item.get("story_id")) == "703928648"
        )
        self.assertEqual(aunt_becky["date"], "2019-03-15")
        self.assertEqual(
            aunt_becky["catalog_correction"]["incorrect_reference_value"], "2018-03-15"
        )


if __name__ == "__main__":
    unittest.main()
