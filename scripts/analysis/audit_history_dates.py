from pathlib import Path
import json
from collections import Counter
from datetime import datetime
REPO_ROOT = Path(__file__).resolve().parents[2]


INPUT_FILE = str(REPO_ROOT / "indicator_history.json")
OUTPUT_FILE = str(REPO_ROOT / "data" / "audits" / "indicator_history_date_audit.json")
START_DATE = datetime.fromisoformat("2018-03-01")
END_DATE = datetime.fromisoformat("2019-04-30T23:59:59")


def parse_date(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


with open(INPUT_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

episodes = data.get("episodes", [])

in_period = []
monthly_counts = Counter()

for episode in episodes:
    date = parse_date(episode.get("date"))

    if date is None:
        continue

    if START_DATE <= date <= END_DATE:
        monthly_counts[date.strftime("%Y-%m")] += 1

        in_period.append({
            "date": episode.get("date"),
            "title": episode.get("title"),
            "npr_url": episode.get("npr_url"),
            "story_id": episode.get("story_id"),
            "audio_id": episode.get("audio_id")
        })

in_period.sort(key=lambda x: x["date"])

months = []
year = 2018
month = 3

while (year, month) <= (2019, 4):
    key = f"{year:04d}-{month:02d}"

    months.append({
        "month": key,
        "episode_count": monthly_counts.get(key, 0)
    })

    month += 1
    if month == 13:
        month = 1
        year += 1

report = {
    "audit_period": {
        "start": "2018-03-01",
        "end": "2019-04-30"
    },
    "total_archive_episodes": len(episodes),
    "episodes_in_period": len(in_period),
    "monthly_counts": months,
    "episodes": in_period
}

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

print(f"Archive episodes: {len(episodes)}")
print(f"Episodes in audit period: {len(in_period)}")

print("\nMonthly counts:")
for item in months:
    print(f"{item['month']}: {item['episode_count']}")

print(f"\nWrote {OUTPUT_FILE}")
