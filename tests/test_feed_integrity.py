import unittest
import xml.etree.ElementTree as ET

from scripts.validate_feed_integrity import (
    ARCHIVE_TITLE,
    IntegrityError,
    validate_archive_roots,
    validate_items,
    validate_regression,
)


def feed_items(*items: str):
    return ET.fromstring(f"<rss><channel>{''.join(items)}</channel></rss>").findall("./channel/item")


def item(guid="1", date="Fri, 14 Aug 2026 12:00:00 +0000", length="100"):
    return f"""
    <item>
      <title>Episode {guid}</title>
      <pubDate>{date}</pubDate>
      <guid>{guid}</guid>
      <enclosure url="https://ondemand.npr.org/{guid}.mp3" length="{length}" type="audio/mpeg" />
    </item>
    """


class FeedIntegrityTests(unittest.TestCase):
    def test_valid_feed(self):
        summary = validate_items(feed_items(item("2"), item("1", "Thu, 13 Aug 2026 12:00:00 +0000")))
        self.assertEqual(summary.items, 2)
        self.assertEqual(summary.unique_guids, 2)

    def test_duplicate_guid_fails(self):
        with self.assertRaisesRegex(IntegrityError, "duplicate GUIDs"):
            validate_items(feed_items(item("1"), item("1")))

    def test_oldest_first_fails(self):
        with self.assertRaisesRegex(IntegrityError, "not sorted newest first"):
            validate_items(feed_items(item("1", "Thu, 13 Aug 2026 12:00:00 +0000"), item("2")))

    def test_invalid_enclosure_fails(self):
        with self.assertRaisesRegex(IntegrityError, "invalid enclosure length"):
            validate_items(feed_items(item("1", length="invalid")))

    def test_zero_length_is_counted_as_legacy_unknown(self):
        summary = validate_items(feed_items(item("1", length="0")))
        self.assertEqual(summary.unknown_enclosure_lengths, 1)

    def test_episode_count_regression_fails(self):
        baseline = validate_items(feed_items(item("2"), item("1", "Thu, 13 Aug 2026 12:00:00 +0000")))
        current = validate_items(feed_items(item("2")))
        with self.assertRaisesRegex(IntegrityError, "Episode count decreased"):
            validate_regression(current, baseline)

    def test_unknown_length_regression_fails(self):
        baseline = validate_items(feed_items(item("1")))
        current = validate_items(feed_items(item("1", length="0")))
        with self.assertRaisesRegex(IntegrityError, "Unknown enclosure lengths increased"):
            validate_regression(current, baseline)

    def test_archive_must_equal_expected_main_feed_slice(self):
        main = ET.fromstring(
            "<rss><channel><title>Main</title><description>Metadata</description>"
            + "".join(item(str(number)) for number in range(5, 0, -1))
            + "</channel></rss>"
        )
        archive = ET.fromstring(
            f"<rss><channel><title>{ARCHIVE_TITLE}</title>"
            "<description>Metadata</description>"
            + item("3") + item("2") + item("1")
            + "</channel></rss>"
        )
        summary = validate_archive_roots(main, archive, item_limit=3, overlap=1)
        self.assertEqual(summary.items, 3)

    def test_archive_rejects_a_gap(self):
        main = ET.fromstring(
            "<rss><channel><title>Main</title><description>Metadata</description>"
            + item("3") + item("2") + item("1")
            + "</channel></rss>"
        )
        archive = ET.fromstring(
            f"<rss><channel><title>{ARCHIVE_TITLE}</title>"
            "<description>Metadata</description>"
            + item("1")
            + "</channel></rss>"
        )
        with self.assertRaisesRegex(IntegrityError, "item count"):
            validate_archive_roots(main, archive, item_limit=2, overlap=1)


if __name__ == "__main__":
    unittest.main()
