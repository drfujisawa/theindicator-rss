import unittest

from scripts.analysis import audit_early_promotion_candidates as audit


class EarlyPromotionAuditTests(unittest.TestCase):
    def test_current_evidence_partitions_every_reference_episode(self):
        manifest = audit.build_manifest()
        counts = manifest["summary"]["category_counts"]

        self.assertEqual(manifest["summary"]["reference_episode_count"], 287)
        self.assertEqual(sum(counts.values()), 287)
        self.assertEqual(counts["already_in_production"], 249)
        self.assertNotIn("strong_promotion_candidate", counts)
        self.assertNotIn("identity_review_candidate", counts)
        self.assertNotIn("special_recovery_review", counts)
        self.assertNotIn("probable_duplicate_rebroadcast", counts)
        self.assertNotIn("identity_found_but_audio_unresolved", counts)
        self.assertEqual(counts["unresolved_candidate"], 38)

    def test_manifest_is_explicitly_non_mutating(self):
        manifest = audit.build_manifest()
        self.assertEqual(manifest["summary"]["safe_automatic_mutations"], 0)

    def test_strong_candidates_retain_observed_audio_identifiers(self):
        manifest = audit.build_manifest()
        strong = [
            episode
            for episode in manifest["episodes"]
            if episode.get("identity_status") == "strong_npr_identity"
            and episode.get("validated_final_audio_url")
            and "indicator_npr_audio_validation.json" in episode.get("provenance", [])
        ]
        self.assertTrue(all(episode["observed_player_ids"] for episode in strong))
        self.assertTrue(all(episode["audio_url_e_parameter"] for episode in strong))


if __name__ == "__main__":
    unittest.main()
