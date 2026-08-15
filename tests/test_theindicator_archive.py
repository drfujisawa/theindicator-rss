import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from theindicator_archive import ARCHIVE_TITLE, build_overcast_archive


class OvercastArchiveTests(unittest.TestCase):
    def test_archive_preserves_metadata_and_items_beyond_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            main_path = Path(tmp) / "main.xml"
            archive_path = Path(tmp) / "archive.xml"
            root = ET.Element("rss", {"version": "2.0"})
            channel = ET.SubElement(root, "channel")
            ET.SubElement(channel, "title").text = "Main title"
            ET.SubElement(channel, "description").text = "Same metadata"
            for number in range(1, 6):
                item = ET.SubElement(channel, "item")
                ET.SubElement(item, "title").text = f"Episode {number}"
                ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"guid-{number}"
                ET.SubElement(item, "pubDate").text = f"0{6-number} Jan 2020 00:00:00 +0000"
                ET.SubElement(item, "enclosure", {"url": f"https://example.com/{number}.mp3"})
            ET.ElementTree(root).write(main_path, encoding="utf-8", xml_declaration=True)

            count = build_overcast_archive(main_path, archive_path, item_limit=3, overlap=1)

            self.assertEqual(count, 3)
            main_tree = ET.parse(main_path)
            archive_tree = ET.parse(archive_path)
            self.assertEqual(len(main_tree.findall("./channel/item")), 5)
            archive_channel = archive_tree.getroot().find("channel")
            self.assertEqual(archive_channel.findtext("title"), ARCHIVE_TITLE)
            self.assertEqual(archive_channel.findtext("description"), "Same metadata")
            self.assertEqual(
                [item.findtext("guid") for item in archive_channel.findall("item")],
                ["guid-3", "guid-4", "guid-5"],
            )
            self.assertEqual(
                archive_channel.findall("item")[0].find("guid").attrib,
                {"isPermaLink": "false"},
            )
            self.assertEqual(
                archive_channel.findall("item")[0].find("enclosure").attrib,
                {"url": "https://example.com/3.mp3"},
            )


if __name__ == "__main__":
    unittest.main()
