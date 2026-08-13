import unittest

from scripts.analysis import stage_two_recovered_episodes as staging


class TwoEpisodeRecoveryStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage_dir = staging.REPO_ROOT / "work/two-episode-recovery-staging-test"
        cls.report = staging.json.loads(staging.REPORT.read_text(encoding="utf-8"))

    def test_every_staging_invariant_passes(self):
        self.assertTrue(self.report["all_checks_passed"])
        self.assertTrue(all(self.report["checks"].values()))

    def test_exact_two_record_deltas(self):
        counts = self.report["counts"]
        self.assertEqual(counts["history_after"] - counts["history_before"], 2)
        self.assertEqual(counts["enclosure_map_after"] - counts["enclosure_map_before"], 2)
        self.assertEqual(counts["feed_after"] - counts["feed_before"], 2)

    def test_production_hashes_remain_unchanged(self):
        self.assertFalse(self.report["production_files_modified"])
        self.assertEqual(self.report["production_sha256_before"], self.report["production_sha256_after"])

    def test_staging_refuses_to_duplicate_applied_cohort(self):
        with self.assertRaisesRegex(RuntimeError, "already in production"):
            staging.stage(stage_dir=self.stage_dir)


if __name__ == "__main__":
    unittest.main()
