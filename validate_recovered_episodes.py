import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

RECOVERED_FILE = Path("indicator_recovered_episodes.json")
HISTORY_FILE = Path("indicator_history.json")
OUTPUT_FILE = Path("indicator_recovery_validation.json")


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_title(title):
    if not title:
        return ""
    return " ".join(
        "".join(c.lower() if c.isalnum() else " " for c in title).split()
    )


def looks_like_audio(url):
    if not url:
        return False

    u = url.lower()

    bad_parts = [
        "/assets/img/",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
    ]

    if any(part in u for part in bad_parts):
        return False

    good_parts = [
        ".mp3",
        ".m4a",
        ".aac",
        "playerservices",
        "stream",
        "audio",
    ]

    return any(part in u for part in good_parts)


recovered_data = load_json(RECOVERED_FILE)
history_data = load_json(HISTORY_FILE)

recovered = recovered_data.get("recovered", [])
failed = recovered_data.get("failed", [])
history = history_data.get("episodes", [])

history_urls = {
    ep.get("npr_url")
    for ep in history
    if ep.get("npr_url")
}

history_audio = {
    ep.get("audio_url") or ep.get("player_url")
    for ep in history
    if ep.get("audio_url") or ep.get("player_url")
}

source_counts = Counter(
    ep.get("source_url")
    for ep in recovered
    if ep.get("source_url")
)

audio_counts = Counter()

for ep in recovered:
    for url in ep.get("all_audio_urls", []):
        if looks_like_audio(url):
            audio_counts[url] += 1


issues = []
clean_candidates = []

for ep in recovered:
    ep_issues = []

    reference_title = ep.get("reference_title", "")
    source_title = ep.get("source_title", "")

    ref_norm = normalize_title(reference_title)
    source_norm = normalize_title(source_title)

    title_score = ep.get("title_score")

    source_url = ep.get("source_url")

    all_media_urls = ep.get("all_audio_urls", [])
    valid_audio_urls = [
        url for url in all_media_urls
        if looks_like_audio(url)
    ]

    suspicious_media_urls = [
        url for url in all_media_urls
        if not looks_like_audio(url)
    ]

    if title_score is not None and title_score < 0.85:
        ep_issues.append(
            f"weak_title_score:{title_score}"
        )

    if ref_norm and source_norm and ref_norm != source_norm:
        if title_score is None or title_score < 0.95:
            ep_issues.append("title_mismatch")

    if not valid_audio_urls:
        ep_issues.append("no_valid_audio_url")

    if suspicious_media_urls:
        ep_issues.append("non_audio_media_detected")

    if source_url and source_counts[source_url] > 1:
        ep_issues.append("duplicate_source_url")

    duplicate_audio = [
        url for url in valid_audio_urls
        if audio_counts[url] > 1
    ]

    if duplicate_audio:
        ep_issues.append("duplicate_audio_url")

    if source_url in history_urls:
        ep_issues.append("source_already_in_history")

    existing_audio = [
        url for url in valid_audio_urls
        if url in history_audio
    ]

    if existing_audio:
        ep_issues.append("audio_already_in_history")

    result = {
        "reference_date": ep.get("reference_date"),
        "reference_title": reference_title,
        "reference_year": ep.get("reference_year"),
        "reference_episode": ep.get("reference_episode"),
        "source_url": source_url,
        "source_domain": ep.get("source_domain"),
        "source_title": source_title,
        "title_score": title_score,
        "valid_audio_urls": valid_audio_urls,
        "suspicious_media_urls": suspicious_media_urls,
        "issues": ep_issues,
    }

    if ep_issues:
        issues.append(result)
    else:
        clean_candidates.append(result)


failure_statuses = Counter()

for ep in failed:
    status = (
        ep.get("status")
        or ep.get("reason")
        or "unknown"
    )
    failure_statuses[str(status)] += 1


issue_type_counts = Counter()

for ep in issues:
    for issue in ep["issues"]:
        issue_type_counts[issue.split(":")[0]] += 1


duplicate_sources = {
    url: count
    for url, count in source_counts.items()
    if count > 1
}

duplicate_audio_urls = {
    url: count
    for url, count in audio_counts.items()
    if count > 1
}


report = {
    "validation_version": 1,
    "source_file": str(RECOVERED_FILE),
    "history_file": str(HISTORY_FILE),

    "summary": {
        "recovered_input_count": len(recovered),
        "failed_input_count": len(failed),
        "history_episode_count": len(history),
        "clean_candidate_count": len(clean_candidates),
        "needs_review_count": len(issues),
        "duplicate_source_url_count": len(duplicate_sources),
        "duplicate_audio_url_count": len(duplicate_audio_urls),
    },

    "issue_type_counts": dict(
        sorted(issue_type_counts.items())
    ),

    "failure_status_counts": dict(
        sorted(failure_statuses.items())
    ),

    "duplicate_source_urls": duplicate_sources,
    "duplicate_audio_urls": duplicate_audio_urls,

    "clean_candidates": clean_candidates,
    "needs_review": issues,

    "failed_recoveries": failed,
}


with OUTPUT_FILE.open("w", encoding="utf-8") as f:
    json.dump(
        report,
        f,
        indent=2,
        ensure_ascii=False,
    )


print("Validation complete")
print("-------------------")
print(f"Recovered input: {len(recovered)}")
print(f"Clean candidates: {len(clean_candidates)}")
print(f"Needs review: {len(issues)}")
print(f"Failed recoveries: {len(failed)}")
print(f"Report: {OUTPUT_FILE}")
