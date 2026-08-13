import unittest
import json

from scripts.analysis import review_early_production_promotion as design_review


class EarlyProductionDesignReviewTests(unittest.TestCase):
    def test_all_technical_checks_pass_without_production_writes(self):
        review = json.loads(design_review.REPORT.read_text(encoding="utf-8"))
        self.assertTrue(all(review["checks"].values()))
        self.assertFalse(review["production_files_modified"])
        self.assertEqual(review["summary"]["candidate_count"], 226)
        self.assertEqual(review["summary"]["confirmed_rebroadcast_count"], 2)

    def test_review_keeps_policy_and_atomic_write_gates_open(self):
        review = json.loads(design_review.REPORT.read_text(encoding="utf-8"))
        open_ids = {
            gate["id"] for gate in review["gates"] if gate["status"] == "open"
        }
        self.assertEqual(
            open_ids,
            {"publication_time_policy", "atomic_promotion_writer", "rollback"},
        )


if __name__ == "__main__":
    unittest.main()
