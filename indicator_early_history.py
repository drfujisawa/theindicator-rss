import json
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup


# Proof-of-concept:
# Try to recover 10 early episodes of The Indicator from NPR.
#
# This script is intentionally separate from indicator_history.json.
# It will only write to indicator_early_history.json.

TARGET_COUNT = 10

# Start near the beginning of The Indicator's history.
current_date = datetime(2017, 11, 5)

episodes = []
seen_urls = set()


def extract_episode(url):
    print(f"Checking: {url}")

    try:
        response = requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    title_tag = soup.find("meta", property="og:title")
    description_tag = soup.find("meta", property="og:description")

    if not title_tag:
        return None

    title = title_tag.get("content", "").strip()

    # Make sure this actually appears to be an Indicator page.
    page_text = soup.get_text(" ", strip=True).lower()

    if "indicator" not in page_text:
        return None

    description = ""
    if description_tag:
        description = description_tag.get("content", "").strip()

    return {
        "title": title,
        "npr_url": url,
        "description": description,
    }


print("Starting early-history proof of concept...")
print(f"Target: {TARGET_COUNT} episodes")


# NPR story IDs around this period are not predictable enough to
# brute-force directly, so use NPR search pages to discover candidates.

search_url = (
    "https://www.npr.org/search"
    "?query=%22The%20Indicator%20from%20Planet%20Money%22"
)

try:
    response = requests.get(
        search_url,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    links = soup.find_all("a", href=True)

    candidates = []

    for link in links:
        href = link["href"]

        if "npr.org/2017/" not in href:
            continue

        if href in seen_urls:
            continue

        seen_urls.add(href)
        candidates.append(href)

    print(f"Found {len(candidates)} candidate NPR links.")

    for url in candidates:
        episode = extract_episode(url)

        if episode:
            episodes.append(episode)
            print(f"FOUND: {episode['title']}")

        if len(episodes) >= TARGET_COUNT:
            break

        time.sleep(1)

except requests.RequestException as exc:
    print(f"Search request failed: {exc}")


output = {
    "proof_of_concept": True,
    "target_count": TARGET_COUNT,
    "episode_count": len(episodes),
    "episodes": episodes,
}


with open("indicator_early_history.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)


print()
print("--------------------------------")
print("Early-history test complete")
print(f"Episodes recovered: {len(episodes)}")
print("Saved to indicator_early_history.json")
print("--------------------------------")
