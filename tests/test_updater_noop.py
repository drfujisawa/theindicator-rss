import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import theindicator_rss as updater


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_FEED = REPO_ROOT / "theindicator_feed.xml"
class UpdaterNoOpTests(unittest.TestCase):
    def test_no_new_guids_leaves_existing_feed_byte_for_byte_unchanged(self):
        official_xml = PRODUCTION_FEED.read_bytes()
        existing_tree = ET.ElementTree(ET.fromstring(official_xml))
        before = ET.tostring(existing_tree.getroot())

        with (
            patch.object(updater, "download_official_feed", return_value=official_xml),
            patch.object(updater, "load_existing_feed", return_value=existing_tree),
            patch.object(ET.ElementTree, "write") as write,
        ):
            updater.build_feed()

        write.assert_not_called()
        self.assertEqual(ET.tostring(existing_tree.getroot()), before)


if __name__ == "__main__":
    unittest.main()
