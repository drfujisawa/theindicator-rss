import json
import unittest

from scripts.analysis import test_updater_compatibility as compatibility


class UpdaterCompatibilityTests(unittest.TestCase):
    def test_reviewed_isolated_run_preserved_complete_feed(self):
        report = json.loads(compatibility.REPORT.read_text(encoding="utf-8"))
        self.assertTrue(report["all_checks_passed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertFalse(report["production_files_modified"])
        self.assertEqual(report["counts"]["before"], 2195)
        self.assertEqual(report["counts"]["after"], 2195)
        self.assertEqual(report["counts"]["launch_items_preserved"], 59)
        self.assertEqual(report["counts"]["removed"], 0)
        self.assertEqual(report["counts"]["semantically_changed_existing_items"], 0)
        self.assertEqual(report["counts"]["unknown_or_zero_lengths_after"], 0)
        self.assertEqual(
            compatibility.sha256(compatibility.PRODUCTION_FEED),
            report["production_feed_sha256_after"],
        )


if __name__ == "__main__":
    unittest.main()
