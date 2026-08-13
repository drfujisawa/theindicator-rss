import unittest

from scripts.analysis import audit_early_metadata_enrichment as enrichment


class EarlyMetadataEnrichmentTests(unittest.TestCase):
    def test_enrichment_accounts_for_all_strong_candidates(self):
        audit = enrichment.build_enrichment_audit()
        summary = audit["summary"]
        self.assertEqual(summary["strong_candidates_reviewed"], 226)
        self.assertEqual(
            summary["ready_with_limited_metadata"] + summary["manual_review"],
            226,
        )
        self.assertFalse(audit["production_files_modified"])

    def test_verified_metadata_is_not_inferred(self):
        audit = enrichment.build_enrichment_audit()
        self.assertEqual(audit["summary"]["unambiguous_player_identity"], 225)
        self.assertEqual(audit["summary"]["selected_player_identity"], 226)
        self.assertEqual(audit["summary"]["duration_available"], 226)
        ambiguous = [
            record
            for record in audit["episodes"]
            if "ambiguous_npr_player_pair" in record["review_flags"]
        ]
        self.assertEqual(len(ambiguous), 0)
        selected = next(
            record for record in audit["episodes"]
            if record["reference_date"] == "2019-04-23"
        )
        self.assertEqual(
            selected["verified_metadata"]["player_identity"]["player_story_id"],
            "716413259",
        )

    def test_filename_reruns_are_preserved_and_classified(self):
        audit = enrichment.build_enrichment_audit()
        reruns = [
            record
            for record in audit["episodes"]
            if "confirmed_rebroadcast_release" in record["review_flags"]
        ]
        self.assertEqual(len(reruns), 2)
        self.assertTrue(
            all(record["promotion_tier"] == "ready_with_limited_metadata" for record in reruns)
        )
        self.assertTrue(
            all(record["release_classification"] == "confirmed_indicator_rebroadcast" for record in reruns)
        )


if __name__ == "__main__":
    unittest.main()
