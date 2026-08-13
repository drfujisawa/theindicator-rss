import unittest

from scripts.analysis import build_early_promotion_dry_run as dry_run


class EarlyPromotionDryRunTests(unittest.TestCase):
    def test_refuses_to_duplicate_already_promoted_cohort(self):
        output_dir = dry_run.REPO_ROOT / "work" / "early-promotion-test"
        with self.assertRaisesRegex(RuntimeError, "already present in production"):
            dry_run.build_dry_run(output_dir=output_dir)


if __name__ == "__main__":
    unittest.main()
