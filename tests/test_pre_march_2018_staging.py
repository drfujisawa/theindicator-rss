import unittest
import json
import xml.etree.ElementTree as ET

from scripts.analysis import stage_pre_march_2018_catalog as staging


class PreMarch2018StagingTests(unittest.TestCase):
    def test_reviewed_stage_is_present_in_production(self):
        report = json.loads(staging.REPORT.read_text(encoding="utf-8"))
        self.assertTrue(report["all_checks_passed"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counts"]["promoted_candidates"], 59)
        self.assertEqual(report["staged_feed_summary"]["items"], 2195)
        self.assertEqual(report["staged_feed_summary"]["unknown_enclosure_lengths"], 0)
        self.assertTrue(report["trailer_review"]["included_in_stage"])
        self.assertEqual(report["trailer_review"]["staged_itunes_episode_type"], "trailer")
        self.assertEqual(report["trailer_review"]["production_decision"], "approved_for_inclusion")
        self.assertTrue(all(not values for values in report["collision_review"].values()))
        self.assertFalse(report["preservation_review"]["existing_history_records_changed"])
        self.assertFalse(report["preservation_review"]["existing_enclosure_map_records_changed"])
        self.assertFalse(report["preservation_review"]["existing_feed_items_semantically_changed"])

        required_ids = set(report["candidate_story_ids"])
        history = json.loads((staging.REPO_ROOT / "indicator_history.json").read_text(encoding="utf-8"))
        enclosure = json.loads((staging.REPO_ROOT / "indicator_enclosure_map.json").read_text(encoding="utf-8"))
        items = ET.parse(staging.REPO_ROOT / "theindicator_feed.xml").getroot().findall("./channel/item")
        history_ids = {str(item.get("story_id")) for item in history["episodes"]}
        feed_ids = {(item.findtext("guid") or "").strip() for item in items}
        self.assertTrue(required_ids <= history_ids)
        self.assertTrue(required_ids <= set(enclosure["episodes"]))
        self.assertTrue(required_ids <= feed_ids)


if __name__ == "__main__":
    unittest.main()
