import unittest
import json

from scripts.maintenance import stage_early_history_promotion as staging


class EarlyPromotionStagingTests(unittest.TestCase):
    def test_staging_refuses_after_production_application(self):
        output = staging.REPO_ROOT / "work" / "early-promotion-staging-test"
        with self.assertRaisesRegex(RuntimeError, "already present in production"):
            staging.stage_promotion(stage_dir=output)

    def test_historical_staging_report_had_no_replace_phase(self):
        report = json.loads(staging.REPORT.read_text(encoding="utf-8"))
        replacement = next(
            phase for phase in report["proposed_transaction"] if phase["phase"] == 3
        )
        self.assertFalse(replacement["implemented_in_simulation"])


if __name__ == "__main__":
    unittest.main()
