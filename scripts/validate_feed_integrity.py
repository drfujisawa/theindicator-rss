#!/usr/bin/env python3
"""Validate invariants of the generated Indicator RSS feed."""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = REPO_ROOT / "theindicator_feed.xml"
DEFAULT_ARCHIVE = REPO_ROOT / "theindicator_overcast_archive.xml"
ARCHIVE_TITLE = "The Indicator from Planet Money — Overcast Archive"
OVERCAST_ITEM_LIMIT = 2000
ARCHIVE_OVERLAP = 250


class IntegrityError(RuntimeError):
    """Raised when a feed invariant is violated."""


@dataclass(frozen=True)
class FeedSummary:
    items: int
    unique_guids: int
    unknown_enclosure_lengths: int
    newest_date: str
    oldest_date: str


def parse_feed_bytes(payload: bytes, source: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise IntegrityError(f"{source}: invalid XML: {exc}") from exc
    items = root.findall("./channel/item")
    if not items:
        raise IntegrityError(f"{source}: feed contains no items")
    return items


def element_signature(element: ET.Element) -> tuple:
    """Return a semantic XML signature, ignoring indentation-only tail text."""
    return (
        element.tag,
        element.text or "",
        tuple(sorted(element.attrib.items())),
        tuple(element_signature(child) for child in element),
    )


def validate_archive_roots(
    main_root: ET.Element,
    archive_root: ET.Element,
    item_limit: int = OVERCAST_ITEM_LIMIT,
    overlap: int = ARCHIVE_OVERLAP,
) -> FeedSummary:
    main_channel = main_root.find("channel")
    archive_channel = archive_root.find("channel")
    if main_channel is None or archive_channel is None:
        raise IntegrityError("Main feed and archive must both contain a channel")

    main_items = main_channel.findall("item")
    archive_items = archive_channel.findall("item")
    summary = validate_items(archive_items, "Overcast archive")

    if archive_channel.findtext("title") != ARCHIVE_TITLE:
        raise IntegrityError("Overcast archive has the wrong channel title")

    main_metadata = [
        element_signature(element)
        for element in main_channel
        if element.tag not in {"title", "item"}
    ]
    archive_metadata = [
        element_signature(element)
        for element in archive_channel
        if element.tag not in {"title", "item"}
    ]
    if archive_metadata != main_metadata:
        raise IntegrityError("Overcast archive channel metadata differs from the main feed")

    archive_start = max(0, item_limit - overlap)
    expected_items = main_items[archive_start:]
    if len(archive_items) != len(expected_items):
        raise IntegrityError(
            "Overcast archive item count does not match the main-feed slice: "
            f"expected {len(expected_items)}, found {len(archive_items)}"
        )
    for index, (actual, expected) in enumerate(
        zip(archive_items, expected_items), start=archive_start + 1
    ):
        if element_signature(actual) != element_signature(expected):
            raise IntegrityError(
                f"Overcast archive item differs from main-feed item {index}"
            )
    return summary


def validate_items(items: list[ET.Element], source: str = "feed") -> FeedSummary:
    guids: list[str] = []
    dates = []
    failures: list[str] = []
    unknown_enclosure_lengths = 0

    for index, item in enumerate(items, start=1):
        label = f"{source}: item {index}"
        guid = (item.findtext("guid") or "").strip()
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        enclosure = item.find("enclosure")

        if not guid:
            failures.append(f"{label} has no GUID")
        else:
            guids.append(guid)
        if not title:
            failures.append(f"{label} ({guid or 'unknown GUID'}) has no title")
        try:
            dates.append(parsedate_to_datetime(pub_date))
        except (TypeError, ValueError):
            failures.append(f"{label} ({guid or 'unknown GUID'}) has invalid pubDate")

        if enclosure is None:
            failures.append(f"{label} ({guid or 'unknown GUID'}) has no enclosure")
            continue
        url = enclosure.get("url", "").strip()
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            failures.append(f"{label} ({guid or 'unknown GUID'}) has invalid enclosure URL")
        if enclosure.get("type") != "audio/mpeg":
            failures.append(f"{label} ({guid or 'unknown GUID'}) has non-MP3 enclosure type")
        try:
            enclosure_length = int(enclosure.get("length", ""))
            if enclosure_length < 0:
                raise ValueError
        except ValueError:
            failures.append(f"{label} ({guid or 'unknown GUID'}) has invalid enclosure length")
        else:
            if enclosure_length == 0:
                unknown_enclosure_lengths += 1

    duplicates = sorted(guid for guid, count in Counter(guids).items() if count > 1)
    if duplicates:
        failures.append(f"{source}: duplicate GUIDs: {', '.join(duplicates[:10])}")
    if len(dates) == len(items) and dates != sorted(dates, reverse=True):
        failures.append(f"{source}: items are not sorted newest first")
    if failures:
        raise IntegrityError("\n".join(failures))

    return FeedSummary(
        items=len(items),
        unique_guids=len(set(guids)),
        unknown_enclosure_lengths=unknown_enclosure_lengths,
        newest_date=dates[0].isoformat(),
        oldest_date=dates[-1].isoformat(),
    )


def feed_from_git(ref: str, path: Path) -> bytes:
    try:
        relative = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError as exc:
        raise IntegrityError("Baseline comparison requires a feed inside the repository") from exc
    result = subprocess.run(
        ["git", "show", f"{ref}:{relative}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise IntegrityError(f"Could not read baseline {ref}:{relative}: {detail}")
    return result.stdout


def validate_regression(current: FeedSummary, baseline: FeedSummary) -> None:
    if current.items < baseline.items:
        raise IntegrityError(
            f"Episode count decreased from {baseline.items} to {current.items}"
        )
    if current.unknown_enclosure_lengths > baseline.unknown_enclosure_lengths:
        raise IntegrityError(
            "Unknown enclosure lengths increased from "
            f"{baseline.unknown_enclosure_lengths} to "
            f"{current.unknown_enclosure_lengths}"
        )


def validate(feed_path: Path = DEFAULT_FEED, baseline_ref: str | None = None) -> FeedSummary:
    current_items = parse_feed_bytes(feed_path.read_bytes(), str(feed_path))
    summary = validate_items(current_items, str(feed_path))
    if baseline_ref:
        baseline_payload = feed_from_git(baseline_ref, feed_path)
        baseline_items = parse_feed_bytes(baseline_payload, f"{baseline_ref}:{feed_path.name}")
        baseline_summary = validate_items(baseline_items, f"{baseline_ref}:{feed_path.name}")
        validate_regression(summary, baseline_summary)
    return summary


def validate_archive(
    main_feed_path: Path = DEFAULT_FEED,
    archive_path: Path = DEFAULT_ARCHIVE,
    baseline_ref: str | None = None,
) -> FeedSummary:
    try:
        main_root = ET.fromstring(main_feed_path.read_bytes())
        archive_root = ET.fromstring(archive_path.read_bytes())
    except ET.ParseError as exc:
        raise IntegrityError(f"Archive relationship XML is invalid: {exc}") from exc
    summary = validate_archive_roots(main_root, archive_root)
    if baseline_ref:
        baseline_payload = feed_from_git(baseline_ref, archive_path)
        baseline_items = parse_feed_bytes(
            baseline_payload, f"{baseline_ref}:{archive_path.name}"
        )
        baseline_summary = validate_items(
            baseline_items, f"{baseline_ref}:{archive_path.name}"
        )
        validate_regression(summary, baseline_summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument(
        "--baseline-ref",
        help="Git ref whose feed count must not exceed the working feed count",
    )
    args = parser.parse_args()
    try:
        summary = validate(args.feed, args.baseline_ref)
        archive_summary = validate_archive(args.feed, args.archive, args.baseline_ref)
    except (IntegrityError, OSError) as exc:
        print(f"Feed integrity check failed:\n{exc}", file=sys.stderr)
        return 1
    print(
        "Feed integrity check passed: "
        f"{summary.items} items, {summary.unique_guids} unique GUIDs, "
        f"{summary.unknown_enclosure_lengths} legacy unknown enclosure lengths, "
        f"{summary.oldest_date} through {summary.newest_date}."
    )
    print(
        "Overcast archive integrity check passed: "
        f"{archive_summary.items} items, {archive_summary.unique_guids} unique GUIDs, "
        f"{archive_summary.oldest_date} through {archive_summary.newest_date}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
