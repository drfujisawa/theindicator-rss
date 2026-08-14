import unittest

from scripts.maintenance import apply_pre_march_2018_catalog as application


class PreMarch2018ApplicationTests(unittest.TestCase):
    def test_reviewed_staging_validates_and_hashes_match(self):
        expected = {"history": 1893, "enclosure_map": 1893, "feed": 2195}
        counts = application.validate(
            application.DEFAULT_STAGE_DIR,
            expected,
            application.REQUIRED_IDS,
        )
        self.assertEqual(counts, expected)
        report = application.json.loads(
            application.STAGING_REPORT.read_text(encoding="utf-8")
        )
        self.assertEqual(
            application.artifact_hashes(application.DEFAULT_STAGE_DIR),
            report["staged_sha256"],
        )
        self.assertEqual(len(application.REQUIRED_IDS), 59)

    def test_cli_requires_explicit_apply(self):
        source = application.Path(application.__file__).read_text(encoding="utf-8")
        self.assertIn('parser.error("Refusing to write production without --apply")', source)


if __name__ == "__main__":
    unittest.main()
