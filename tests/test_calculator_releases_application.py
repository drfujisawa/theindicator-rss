import unittest

from scripts.maintenance import apply_calculator_releases as application


class CalculatorReleaseApplicationTests(unittest.TestCase):
    def test_reviewed_staging_validates(self):
        counts = application.validate(
            application.DEFAULT_STAGE_DIR,
            {"history": 1718, "enclosure_map": 1718, "feed": 2019},
            {"592961699", "642675050"},
        )
        self.assertEqual(counts, {"history": 1718, "enclosure_map": 1718, "feed": 2019})

    def test_cli_requires_explicit_apply(self):
        source = application.Path(application.__file__).read_text(encoding="utf-8")
        self.assertIn('parser.error("Refusing to write production without --apply")', source)


if __name__ == "__main__":
    unittest.main()
