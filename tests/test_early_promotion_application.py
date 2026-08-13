import unittest

from scripts.maintenance import apply_early_history_promotion as application


class EarlyPromotionApplicationTests(unittest.TestCase):
    def test_reviewed_staging_artifacts_have_expected_counts(self):
        counts = application.validate_artifacts(application.DEFAULT_STAGE_DIR)
        self.assertEqual(
            counts,
            {"history": 1706, "enclosure_map": 1706, "feed": 2007},
        )

    def test_cli_requires_explicit_apply_flag(self):
        source = application.Path(application.__file__).read_text(encoding="utf-8")
        self.assertIn('parser.error("Refusing to write production without --apply")', source)


if __name__ == "__main__":
    unittest.main()
