import json
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

AUDIT_FILE = "indicator_early_audit.json"
OUTPUT_FILE = "indicator_recovery_test.json"

TEST_LIMIT = 10
TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IndicatorArchiveRecovery/1.0; "
        "+https://github.com/)"
    )
}


def fetch(url):
    request = Request(url, headers=HEADERS)

    with urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def clean_text(value):
    if not value:
        return ""

    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("&amp;", "&")
    value = value.replace("&#39;", "'")
    value = value.replace("&quot;", '"')
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def search_npr(title):
    query = quote(title)
    url = f"https://www.npr.org/search?query={query}"

    html = fetch(url)

    links = re.findall(
        r'href=["\'](https://www\.npr\.org/[^"\']+)["\']',
        html
    )

    results = []

    for link in links:
        link = link.replace("&amp;", "&")

        if link not in results:
            results.append(link)

    return results[:10]


def extract_title(html):
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r"<title>(.*?)</title>"
    ]

    for pattern in patterns:
        match = re.search(pattern, html, re.I | re.S)

        if match:
            return clean_text(match.group(1))

    return None


def normalize(value):
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def title_score(reference, candidate):
    a = set(normalize(reference).split())
    b = set(normalize(candidate).split())

    if not a or not b:
        return 0

    return len(a & b) / len(a | b)


def find_best_candidate(reference_title):
    links = search_npr(reference_title)

    candidates = []

    for link in links:
        try:
            html = fetch(link)
            candidate_title = extract_title(html)

            if not candidate_title:
                continue

            score = title_score(reference_title, candidate_title)

            candidates.append({
                "npr_url": link,
                "npr_title": candidate_title,
                "title_score": round(score, 3)
            })

        except Exception as exc:
            candidates.append({
                "npr_url": link,
                "error": str(exc)
            })

        time.sleep(0.5)

    scored = [
        item for item in candidates
        if "title_score" in item
    ]

    scored.sort(
        key=lambda item: item["title_score"],
        reverse=True
    )

    best = scored[0] if scored else None

    return best, candidates


with open(AUDIT_FILE, "r", encoding="utf-8") as f:
    audit = json.load(f)

missing = audit.get("possible_missing", [])[:TEST_LIMIT]

report = {
    "proof_of_concept": True,
    "test_limit": TEST_LIMIT,
    "attempted_count": len(missing),
    "found_count": 0,
    "not_found_count": 0,
    "results": []
}

for index, episode in enumerate(missing, start=1):
    reference_title = episode.get("title", "")
    reference_date = episode.get("date")

    print(
        f"[{index}/{len(missing)}] "
        f"{reference_date} - {reference_title}"
    )

    result = {
        "reference_date": reference_date,
        "reference_title": reference_title,
        "reference_year": episode.get("reference_year"),
        "reference_episode": episode.get("reference_episode")
    }

    try:
        best, candidates = find_best_candidate(reference_title)

        result["best_candidate"] = best
        result["candidates_checked"] = candidates

        if best and best.get("title_score", 0) >= 0.5:
            result["status"] = "candidate_found"
            report["found_count"] += 1

            print(
                "  Candidate:",
                best["npr_title"],
                best["npr_url"],
                best["title_score"]
            )
        else:
            result["status"] = "not_found"
            report["not_found_count"] += 1
            print("  No strong candidate found.")

    except (HTTPError, URLError, TimeoutError) as exc:
        result["status"] = "request_error"
        result["error"] = str(exc)
        report["not_found_count"] += 1
        print("  Request error:", exc)

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        report["not_found_count"] += 1
        print("  Error:", exc)

    report["results"].append(result)

    time.sleep(1)


with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print()
print("Recovery proof of concept complete.")
print("Attempted:", report["attempted_count"])
print("Candidates found:", report["found_count"])
print("Not found/errors:", report["not_found_count"])
print("Wrote:", OUTPUT_FILE)
