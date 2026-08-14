import unittest

from scripts.maintenance import apply_remaining_ranked_batch1 as application


class RemainingRankedBatch1ApplicationTests(unittest.TestCase):
    def test_reviewed_staging_validates_and_hashes_match(self):
        counts = application.validate(
            application.DEFAULT_STAGE_DIR,
            {"history": 1722, "enclosure_map": 1722, "feed": 2023},
            {"605033696", "615292695", "623637446", "703928648"},
        )
        self.assertEqual(counts, {"history": 1722, "enclosure_map": 1722, "feed": 2023})
        report = application.json.loads(application.STAGING_REPORT.read_text(encoding="utf-8"))
        self.assertEqual(application.artifact_hashes(application.DEFAULT_STAGE_DIR), report["staged_sha256"])

    def test_cli_requires_explicit_apply(self):
        source = application.Path(application.__file__).read_text(encoding="utf-8")
        self.assertIn('parser.error("Refusing to write production without --apply")', source)


if __name__ == "__main__":
    unittest.main()
