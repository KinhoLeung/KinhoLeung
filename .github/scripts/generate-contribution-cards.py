#!/usr/bin/env python3
"""Generate local streak and activity SVG cards from GitHub's contribution calendar."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""

COLORS = {
    "dark": {
        "background": "none",
        "border": "none",
        "text": "#c9d1d9",
        "muted": "#8b949e",
        "grid": "#21262d",
        "line": "#1ed760",
        "area": "#1ed760",
    },
    "light": {
        "background": "none",
        "border": "none",
        "text": "#24292f",
        "muted": "#6e7781",
        "grid": "#e4e2e3",
        "line": "#1ed760",
        "area": "#1ed760",
    },
}


def fetch_calendar(username: str, token: str, start: date, end: date) -> dict:
    start_time = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_time = datetime.combine(end, time.max, tzinfo=timezone.utc)
    payload = json.dumps(
        {
            "query": QUERY,
            "variables": {
                "login": username,
                "from": start_time.isoformat().replace("+00:00", "Z"),
                "to": end_time.isoformat().replace("+00:00", "Z"),
            },
        }
    ).encode("utf-8")
    request = Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "KinhoLeung-profile-cards",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed ({error.code}): {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach the GitHub GraphQL API: {error.reason}") from error

    if result.get("errors"):
        messages = "; ".join(item.get("message", "GraphQL error") for item in result["errors"])
        raise RuntimeError(f"GitHub GraphQL query failed: {messages}")

    user = (result.get("data") or {}).get("user")
    if user is None:
        raise RuntimeError(f"GitHub user {username!r} was not found or is not accessible")

    return user["contributionsCollection"]["contributionCalendar"]


def calendar_counts(calendar: dict, start: date, end: date) -> dict[date, int]:
    counts: dict[date, int] = {}
    for week in calendar["weeks"]:
        for day in week["contributionDays"]:
            day_date = date.fromisoformat(day["date"])
            if start <= day_date <= end:
                counts[day_date] = int(day["contributionCount"])

    current = start
    while current <= end:
        counts.setdefault(current, 0)
        current += timedelta(days=1)
    return counts


def streak_lengths(counts: dict[date, int], today: date) -> tuple[int, int]:
    ordered_days = sorted(day for day in counts if day <= today)
    longest = current_run = 0
    for day in ordered_days:
        if counts[day] > 0:
            current_run += 1
            longest = max(longest, current_run)
        else:
            current_run = 0

    current_streak = 0
    streak_end = today if counts.get(today, 0) else today - timedelta(days=1)
    day = streak_end
    while counts.get(day, 0) > 0:
        current_streak += 1
        day -= timedelta(days=1)
    return current_streak, longest


def write_streak_card(output: Path, current_streak: int, longest_streak: int, total: int) -> None:
    width, height = 400, 160
    columns = (66, 200, 334)
    metrics = (
        ("Total Contributions", str(total)),
        ("Current Streak", f"{current_streak} {'day' if current_streak == 1 else 'days'}"),
        ("Longest Streak", f"{longest_streak} {'day' if longest_streak == 1 else 'days'}"),
    )

    for theme, colors in COLORS.items():
        metric_svg = "\n".join(
            f'<text x="{x}" y="91" text-anchor="middle" class="value">{escape(value)}</text>'
            f'<text x="{x}" y="119" text-anchor="middle" class="label">{escape(label)}</text>'
            for x, (label, value) in zip(columns, metrics)
        )
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="GitHub contribution streak statistics">
<style>
  .title {{ font: 600 17px 'Segoe UI', Ubuntu, Sans-Serif; fill: {colors['text']}; }}
  .value {{ font: 700 22px 'Segoe UI', Ubuntu, Sans-Serif; fill: {colors['line']}; }}
  .label {{ font: 500 11px 'Segoe UI', Ubuntu, Sans-Serif; fill: {colors['muted']}; }}
</style>
<rect x="0.5" y="0.5" width="399" height="159" rx="6" fill="{colors['background']}" stroke="none"/>
<text x="22" y="32" class="title">GitHub Streak Stats</text>
<path d="M133 54v82 M267 54v82" stroke="{colors['grid']}"/>
{metric_svg}
</svg>
'''
        (output / f"streak-{theme}.svg").write_text(svg, encoding="utf-8")


def write_activity_graph(output: Path, counts: dict[date, int], today: date) -> None:
    days = [today - timedelta(days=offset) for offset in range(30, -1, -1)]
    values = [counts.get(day, 0) for day in days]
    width, height = 1000, 260
    left, right, top, bottom = 50, 22, 58, 38
    plot_width = width - left - right
    plot_height = height - top - bottom
    maximum = max(values, default=0)
    scale_max = max(1, maximum)
    points = [
        (left + index * plot_width / (len(days) - 1), top + plot_height * (1 - value / scale_max))
        for index, value in enumerate(values)
    ]
    line_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_points = (
        f"{left},{top + plot_height} "
        + line_points
        + f" {left + plot_width},{top + plot_height}"
    )
    ticks = sorted({0, round(scale_max / 2), scale_max})

    for theme, colors in COLORS.items():
        grid_svg = "\n".join(
            f'<line x1="{left}" y1="{top + plot_height * (1 - value / scale_max):.1f}" '
            f'x2="{left + plot_width}" y2="{top + plot_height * (1 - value / scale_max):.1f}" class="grid"/>'
            f'<text x="{left - 10}" y="{top + plot_height * (1 - value / scale_max) + 4:.1f}" text-anchor="end" class="label">{value}</text>'
            for value in ticks
        )
        label_indices = [i for i, day in enumerate(days) if i == 0 or day.weekday() == 0 or i == len(days) - 1]
        date_labels = "\n".join(
            f'<text x="{points[index][0]:.1f}" y="{height - 12}" text-anchor="middle" class="label">{days[index].strftime("%b")} {days[index].day}</text>'
            for index in label_indices
        )
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="GitHub activity over the last 31 days">
<style>
  .title {{ font: 600 17px 'Segoe UI', Ubuntu, Sans-Serif; fill: {colors['text']}; }}
  .summary {{ font: 500 12px 'Segoe UI', Ubuntu, Sans-Serif; fill: {colors['muted']}; }}
  .label {{ font: 500 11px 'Segoe UI', Ubuntu, Sans-Serif; fill: {colors['muted']}; }}
  .grid {{ stroke: {colors['grid']}; stroke-width: 1; }}
</style>
<rect x="0.5" y="0.5" width="999" height="259" rx="6" fill="{colors['background']}" stroke="none"/>
<text x="24" y="31" class="title">KinhoLeung's Activity Graph</text>
<text x="976" y="31" text-anchor="end" class="summary">{sum(values)} contributions · last 31 days</text>
{grid_svg}
<polygon points="{area_points}" fill="{colors['area']}" fill-opacity="0.12"/>
<polyline points="{line_points}" fill="none" stroke="{colors['line']}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
{date_labels}
</svg>
'''
        (output / f"activity-graph-{theme}.svg").write_text(svg, encoding="utf-8")


def main() -> int:
    token = os.environ.get("GH_TOKEN", "").strip()
    username = os.environ.get("GITHUB_USERNAME", "KinhoLeung").strip()
    if not token:
        print("GH_TOKEN is required to read GitHub contribution data", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)
    try:
        calendar = fetch_calendar(username, token, start, today)
        counts = calendar_counts(calendar, start, today)
        current_streak, longest_streak = streak_lengths(counts, today)
        output = Path(__file__).resolve().parents[2] / "profile"
        output.mkdir(parents=True, exist_ok=True)
        write_streak_card(output, current_streak, longest_streak, sum(counts.values()))
        write_activity_graph(output, counts, today)
    except (KeyError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
