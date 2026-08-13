import unittest

from scripts.analysis import stage_early_near_ready_candidates as staging


class EarlyNearReadyStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stage_dir = staging.REPO_ROOT / "work/early-near-ready-staging-test"
        cls.report = staging.json.loads(staging.REPORT.read_text(encoding="utf-8"))

    def test_all_invariants_pass_without_production_writes(self):
        self.assertTrue(self.report["all_checks_passed"])
        self.assertFalse(self.report["production_files_modified"])
        self.assertEqual(self.report["production_sha256_before"], self.report["production_sha256_after"])

    def test_exactly_eight_records_are_staged_in_each_artifact(self):
        counts = self.report["counts"]
        self.assertEqual(counts["history_after"] - counts["history_before"], 8)
        self.assertEqual(counts["enclosure_map_after"] - counts["enclosure_map_before"], 8)
        self.assertEqual(counts["feed_after"] - counts["feed_before"], 8)

    def test_staged_artifacts_exist(self):
        for name in staging.PRODUCTION_FILES:
            self.assertTrue((staging.DEFAULT_STAGE_DIR / name).is_file())

    def test_staging_refuses_to_duplicate_applied_cohort(self):
        with self.assertRaisesRegex(RuntimeError, "already in production"):
            staging.stage(stage_dir=self.stage_dir)


if __name__ == "__main__":
    unittest.main()
