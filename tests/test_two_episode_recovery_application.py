import unittest

from scripts.maintenance import apply_two_recovered_episodes as application


class TwoEpisodeRecoveryApplicationTests(unittest.TestCase):
    def test_reviewed_staging_validates(self):
        counts = application.validate(
            application.DEFAULT_STAGE_DIR,
            {"history": 1716, "enclosure_map": 1716, "feed": 2017},
            {"605396270", "656634299"},
        )
        self.assertEqual(counts, {"history": 1716, "enclosure_map": 1716, "feed": 2017})

    def test_cli_requires_explicit_apply(self):
        source = application.Path(application.__file__).read_text(encoding="utf-8")
        self.assertIn('parser.error("Refusing to write production without --apply")', source)


if __name__ == "__main__":
    unittest.main()
