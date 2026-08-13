import unittest

from scripts.maintenance import apply_early_near_ready_promotion as application


class EarlyNearReadyApplicationTests(unittest.TestCase):
    def test_reviewed_staging_has_expected_counts(self):
        counts = application.validate_artifacts(
            application.DEFAULT_STAGE_DIR,
            {"history": 1714, "enclosure_map": 1714, "feed": 2015},
        )
        self.assertEqual(counts, {"history": 1714, "enclosure_map": 1714, "feed": 2015})

    def test_cli_requires_explicit_apply_flag(self):
        source = application.Path(application.__file__).read_text(encoding="utf-8")
        self.assertIn('parser.error("Refusing to write production without --apply")', source)


if __name__ == "__main__":
    unittest.main()
