#!/usr/bin/env python3
"""Generate the local Streak Stats and Activity Graph SVG cards.

The layouts follow the original github-readme-streak-stats and
github-readme-activity-graph cards, while all contribution data and rendering
are handled here for GitHub Actions.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from math import ceil, floor, log10
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape, quoteattr


QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    name
    createdAt
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
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

STREAK_COLORS = {
    "dark": {
        "background": "#00000000",
        "border": "#0000",
        "stroke": "#39D353",
        "ring": "#39D353",
        "fire": "#1ED760",
        "current_number": "#FFFFFF",
        "side_numbers": "#FFFFFF",
        "current_label": "#FFFFFF",
        "side_labels": "#FFFFFF",
        "dates": "#39D353",
    },
    "light": {
        "background": "#00000000",
        "border": "#0000",
        "stroke": "#39D353",
        "ring": "#39D353",
        "fire": "#39D353",
        "current_number": "#39D353",
        "side_numbers": "#39D353",
        "current_label": "#24292F",
        "side_labels": "#24292F",
        "dates": "#1ED760",
    },
}

ACTIVITY_COLORS = {
    "background": "#00000000",
    "border": "#0000",
    "text": "#8B949E",
    "line": "#1ED760",
    "point": "#8B949E",
    "area": "#26A641",
}


def fetch_calendar(
    username: str, token: str, start: date, end: date
) -> tuple[str, date, dict]:
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
        raise RuntimeError(
            f"GitHub GraphQL request failed ({error.code}): {detail}"
        ) from error
    except URLError as error:
        raise RuntimeError(
            f"Could not reach the GitHub GraphQL API: {error.reason}"
        ) from error

    if result.get("errors"):
        messages = "; ".join(
            item.get("message", "GraphQL error") for item in result["errors"]
        )
        raise RuntimeError(f"GitHub GraphQL query failed: {messages}")

    user = (result.get("data") or {}).get("user")
    if user is None:
        raise RuntimeError(
            f"GitHub user {username!r} was not found or is not accessible"
        )

    created_at = user.get("createdAt")
    if not created_at:
        raise RuntimeError(f"GitHub did not return the creation date for {username!r}")
    account_created = datetime.fromisoformat(
        created_at.replace("Z", "+00:00")
    ).date()
    calendar = user["contributionsCollection"]["contributionCalendar"]
    return user.get("name") or username, account_created, calendar


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


def fetch_all_contributions(
    username: str, token: str, today: date
) -> tuple[str, dict[date, int]]:
    this_year_start = date(today.year, 1, 1)
    display_name, account_created, calendar = fetch_calendar(
        username, token, this_year_start, today
    )
    first_day = max(account_created, date(2005, 1, 1))
    counts: dict[date, int] = {}

    for year in range(first_day.year, today.year + 1):
        year_start = max(first_day, date(year, 1, 1))
        year_end = min(today, date(year, 12, 31))
        if year == today.year:
            year_calendar = calendar
        else:
            _, _, year_calendar = fetch_calendar(
                username, token, year_start, year_end
            )
        counts.update(calendar_counts(year_calendar, year_start, year_end))

    return display_name, counts


def streak_summary(
    counts: dict[date, int], today: date
) -> tuple[int, date | None, int, date | None, date | None, date | None]:
    active_days = [day for day, count in counts.items() if count > 0 and day <= today]
    first_contribution = min(active_days) if active_days else None

    longest = 0
    longest_start = None
    longest_end = None
    run_length = 0
    run_start = None
    for day in sorted(day for day in counts if day <= today):
        if counts[day] > 0:
            if run_length == 0:
                run_start = day
            run_length += 1
            if run_length > longest:
                longest = run_length
                longest_start = run_start
                longest_end = day
        else:
            run_length = 0
            run_start = None

    current_end = today if counts.get(today, 0) > 0 else today - timedelta(days=1)
    current_length = 0
    current_start = None
    day = current_end
    while counts.get(day, 0) > 0:
        current_start = day
        current_length += 1
        day -= timedelta(days=1)

    return (
        current_length,
        current_start,
        longest,
        longest_start,
        longest_end,
        first_contribution,
    )


def format_date(value: date | None) -> str:
    if value is None:
        return "No contributions yet"
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def date_range(start: date | None, end: date | None, *, present: bool = False) -> str:
    if start is None:
        return "No contributions yet" if present else "No streak yet"
    if not present and start == end:
        return format_date(start)
    end_text = "Present" if present else format_date(end)
    return f"{format_date(start)} - {end_text}"


def wrap_text(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    if " - " in text:
        return text.replace(" - ", "\n- ", 1).split("\n", 1)

    words = text.split()
    lines: list[str] = []
    line = ""
    for word in words:
        while len(word) > max_chars:
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        candidate = f"{line} {word}".strip()
        if line and len(candidate) > max_chars:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines or [text]


def tspans(text: str, max_chars: int, first_line_offset: int) -> str:
    lines = wrap_text(text, max_chars)
    if len(lines) == 1:
        return escape(lines[0])
    return (
        f'<tspan x="0" dy="{first_line_offset}">{escape(lines[0])}</tspan>'
        f'<tspan x="0" dy="16">{escape(lines[1])}</tspan>'
    )


def write_streak_card(
    output: Path,
    current_streak: int,
    current_start: date | None,
    longest_streak: int,
    longest_start: date | None,
    longest_end: date | None,
    total: int,
    first_contribution: date | None,
) -> None:
    width, height = 400, 195
    columns = (width / 6, width / 2, width * 5 / 6)
    metrics = (
        (
            "Total Contributions",
            f"{total:,}",
            date_range(first_contribution, None, present=True),
            "side_numbers",
            "side_labels",
        ),
        (
            "Current Streak",
            str(current_streak),
            date_range(
                current_start,
                current_start + timedelta(days=current_streak - 1)
                if current_start is not None
                else None,
            ),
            "current_number",
            "current_label",
        ),
        (
            "Longest Streak",
            str(longest_streak),
            date_range(longest_start, longest_end),
            "side_numbers",
            "side_labels",
        ),
    )

    for theme, colors in STREAK_COLORS.items():
        metric_svg = []
        for x, (label, value, period, number_color, label_color) in zip(
            columns, metrics
        ):
            is_current = label == "Current Streak"
            label_y = 108 if is_current else 84
            period_y = 145 if is_current else 114
            period_baseline = 21 if is_current else 32
            label_text = tspans(label, int(width / 3 / 7.5), -9)
            period_text = tspans(period, int(width / 3 / 6), 0)
            metric_svg.append(
                f'<g transform="translate({x:.3f},48)">'
                f'<text x="0" y="32" text-anchor="middle" fill="{colors[number_color]}" '
                f'font-family="Segoe UI, Ubuntu, sans-serif" font-weight="700" '
                f'font-size="28px">{escape(value)}</text></g>'
                f'<g transform="translate({x:.3f},{label_y})">'
                f'<text x="0" y="32" text-anchor="middle" fill="{colors[label_color]}" '
                f'font-family="Segoe UI, Ubuntu, sans-serif" '
                f'font-weight="{700 if is_current else 400}" '
                f'font-size="14px">{label_text}</text></g>'
                f'<g transform="translate({x:.3f},{period_y})">'
                f'<text x="0" y="{period_baseline}" text-anchor="middle" fill="{colors["dates"]}" '
                f'font-family="Segoe UI, Ubuntu, sans-serif" font-weight="400" '
                f'font-size="12px">{period_text}</text></g>'
            )

        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
  style="isolation:isolate" viewBox="0 0 {width} {height}" width="{width}px" height="{height}px"
  role="img" aria-label="GitHub contribution streak statistics">
<defs>
  <clipPath id="outer_rectangle"><rect width="{width}" height="{height}" rx="4.5"/></clipPath>
  <mask id="ring_cutout">
    <rect width="{width}" height="{height}" fill="white"/>
    <ellipse cx="{columns[1]:.3f}" cy="32" rx="13" ry="18" fill="black"/>
  </mask>
</defs>
<g clip-path="url(#outer_rectangle)">
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="4.5"
    fill="{colors["background"]}" stroke="{colors["border"]}"/>
  <g fill="none" stroke="{colors["stroke"]}" stroke-width="1">
    <line x1="{width / 3:.3f}" y1="28" x2="{width / 3:.3f}" y2="170"/>
    <line x1="{width * 2 / 3:.3f}" y1="28" x2="{width * 2 / 3:.3f}" y2="170"/>
  </g>
  <circle cx="{columns[1]:.3f}" cy="71" r="40" fill="none"
    stroke="{colors["ring"]}" stroke-width="5" mask="url(#ring_cutout)"/>
  <g transform="translate({columns[1]:.3f},19.5)" fill="{colors["fire"]}">
    <path d="M1.5 0.67 C1.5 0.67 2.24 3.32 2.24 5.47 C2.24 7.53 0.89 9.2 -1.17 9.2 C-3.23 9.2 -4.79 7.53 -4.79 5.47 L-4.76 5.11 C-6.78 7.51 -8 10.62 -8 13.99 C-8 18.41 -4.42 22 0 22 C4.42 22 8 18.41 8 13.99 C8 8.6 5.41 3.79 1.5 0.67 Z M-0.29 19 C-2.07 19 -3.51 17.6 -3.51 15.86 C-3.51 14.24 -2.46 13.1 -0.7 12.74 C1.07 12.38 2.9 11.53 3.92 10.16 C4.31 11.45 4.51 12.81 4.51 14.2 C4.51 16.85 2.36 19 -0.29 19 Z"/>
  </g>
  {"".join(metric_svg)}
</g>
</svg>
'''
        (output / f"streak-{theme}.svg").write_text(svg, encoding="utf-8")


def nice_y_ticks(maximum: int) -> tuple[list[int], int]:
    target = max(1, maximum)
    raw_step = target / 5
    magnitude = 10 ** floor(log10(raw_step))
    step = next(
        candidate * magnitude
        for candidate in (1, 2, 5, 10)
        if candidate * magnitude >= raw_step
    )
    step = max(1, int(step))
    scale_max = max(step, int(ceil(target / step) * step))
    return list(range(0, scale_max + 1, step)), scale_max


def curve_commands(points: list[tuple[float, float]]) -> str:
    commands = []
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        x0, y0 = points[max(0, index - 1)]
        x3, y3 = points[min(len(points) - 1, index + 2)]
        control1 = (x1 + (x2 - x0) / 6, y1 + (y2 - y0) / 6)
        control2 = (x2 - (x3 - x1) / 6, y2 - (y3 - y1) / 6)
        low_y, high_y = sorted((y1, y2))
        control1 = (control1[0], min(high_y, max(low_y, control1[1])))
        control2 = (control2[0], min(high_y, max(low_y, control2[1])))
        commands.append(
            f"C {control1[0]:.2f} {control1[1]:.2f}, "
            f"{control2[0]:.2f} {control2[1]:.2f}, {x2:.2f} {y2:.2f}"
        )
    return " ".join(commands)


def write_activity_graph(
    output: Path,
    counts: dict[date, int],
    today: date,
    display_name: str,
) -> None:
    days = [today - timedelta(days=offset) for offset in range(30, -1, -1)]
    values = [counts.get(day, 0) for day in days]

    width, height = 1200, 420
    left, right, top = 90, 50, 80
    bottom = height - 20 - 50
    plot_width = width - left - right
    plot_height = bottom - top
    y_ticks, y_max = nice_y_ticks(max(values, default=0))
    points = [
        (
            left + index * plot_width / (len(days) - 1),
            bottom - value * plot_height / y_max,
        )
        for index, value in enumerate(values)
    ]
    line_path = f"M {points[0][0]:.2f} {points[0][1]:.2f} {curve_commands(points)}"
    area_path = (
        f"M {points[0][0]:.2f} {bottom} L {points[0][0]:.2f} {points[0][1]:.2f} "
        f"{curve_commands(points)} L {points[-1][0]:.2f} {bottom} Z"
    )
    grid_svg = []
    y_label_svg = []
    for value in y_ticks:
        y = bottom - value * plot_height / y_max
        grid_svg.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" '
            f'class="ct-grid ct-horizontal"/>'
        )
        y_label_svg.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" '
            f'class="ct-label">{value}</text>'
        )

    x_grid_svg = []
    x_label_svg = []
    for index, day in enumerate(days):
        x = points[index][0]
        x_grid_svg.append(
            f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom:.2f}" '
            f'class="ct-grid ct-vertical"/>'
        )
        x_label_svg.append(
            f'<text x="{x - 4.5:.2f}" y="{bottom + 22:.2f}" text-anchor="middle" '
            f'class="ct-label">{day.day}</text>'
        )

    point_svg = "\n".join(
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" class="ct-point"/>'
        for x, y in points
    )
    title_text = f"{display_name}'s Contribution Graph"
    title = escape(title_text)
    title_attribute = quoteattr(title_text)
    colors = ACTIVITY_COLORS

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
  viewBox="0 0 {width} {height}" fill="none" role="img"
  aria-label={title_attribute}>
<rect x="0" y="0" width="100%" height="100%" rx="0" fill="{colors["background"]}"
  stroke="{colors["border"]}" stroke-width="1"/>
<style>
  svg {{ font: 600 18px 'Segoe UI', Ubuntu, Sans-Serif; }}
  .header {{ fill: {colors["text"]}; font: 600 20px 'Segoe UI', Ubuntu, Sans-Serif; }}
  .ct-label {{ fill: {colors["text"]}; font: 400 12px 'Segoe UI', Ubuntu, Sans-Serif; }}
  .ct-grid {{ stroke: {colors["text"]}; stroke-width: 1px; stroke-opacity: .3; stroke-dasharray: 2px; }}
  .ct-line {{ fill: none; stroke: {colors["line"]}; stroke-width: 4px; stroke-linecap: round; stroke-linejoin: round; }}
  .ct-area {{ fill: {colors["area"]}; fill-opacity: .1; stroke: none; }}
  .ct-point {{ fill: {colors["point"]}; }}
  .axis-title {{ fill: {colors["text"]}; font: 400 12px 'Segoe UI', Ubuntu, Sans-Serif; }}
</style>
<text x="{width / 2}" y="40" text-anchor="middle" class="header">{title}</text>
{"".join(grid_svg)}
{"".join(x_grid_svg)}
{point_svg}
<path d="{line_path}" class="ct-line"/>
<path d="{area_path}" class="ct-area"/>
{"".join(y_label_svg)}
{"".join(x_label_svg)}
<text x="24" y="{(top + bottom) / 2}" text-anchor="middle" class="axis-title"
  transform="rotate(-90 24 {(top + bottom) / 2})">Contributions</text>
<text x="{left + plot_width / 2}" y="{height - 12}" text-anchor="middle"
  class="axis-title">Days</text>
</svg>
'''
    for theme in ("dark", "light"):
        (output / f"activity-graph-{theme}.svg").write_text(svg, encoding="utf-8")


def main() -> int:
    token = os.environ.get("GH_TOKEN", "").strip()
    username = os.environ.get("GITHUB_USERNAME", "KinhoLeung").strip()
    if not token:
        print("GH_TOKEN is required to read GitHub contribution data", file=sys.stderr)
        return 1

    today = datetime.now(timezone.utc).date()
    try:
        display_name, counts = fetch_all_contributions(username, token, today)
        (
            current_streak,
            current_start,
            longest_streak,
            longest_start,
            longest_end,
            first_contribution,
        ) = streak_summary(counts, today)
        output = Path(__file__).resolve().parents[2] / "profile"
        output.mkdir(parents=True, exist_ok=True)
        write_streak_card(
            output,
            current_streak,
            current_start,
            longest_streak,
            longest_start,
            longest_end,
            sum(counts.values()),
            first_contribution,
        )
        write_activity_graph(output, counts, today, display_name)
    except (KeyError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
