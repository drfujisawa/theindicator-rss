import unittest

from scripts.maintenance import apply_remaining_ranked_batch3 as application


class RemainingRankedBatch3ApplicationTests(unittest.TestCase):
    def test_reviewed_staging_validates_and_hashes_match(self):
        counts = application.validate(
            application.DEFAULT_STAGE_DIR,
            {"history": 1729, "enclosure_map": 1729, "feed": 2030},
            application.REQUIRED_IDS,
        )
        self.assertEqual(counts, {"history": 1729, "enclosure_map": 1729, "feed": 2030})
        report = application.json.loads(application.STAGING_REPORT.read_text(encoding="utf-8"))
        self.assertEqual(application.artifact_hashes(application.DEFAULT_STAGE_DIR), report["staged_sha256"])

    def test_cli_requires_explicit_apply(self):
        source = application.Path(application.__file__).read_text(encoding="utf-8")
        self.assertIn('parser.error("Refusing to write production without --apply")', source)


if __name__ == "__main__":
    unittest.main()
