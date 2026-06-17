#!/usr/bin/env python3
"""
Allianz Amphitheater @ Riverfront — Subscribable Calendar Generator
Scrapes https://www.allianzamphitheater.com/shows/calendar/YYYY-MM
and outputs allianz_shows.ics for hosting as a subscribable Google Calendar.

Requires: pip install playwright icalendar python-dateutil pytz
          python -m playwright install chromium

Usage:
    python allianz_calendar.py               # scrapes current + next 11 months
    python allianz_calendar.py --months 6    # scrapes current + next 5 months
    python allianz_calendar.py --output /path/to/allianz_shows.ics
"""

import re
import sys
import time
import argparse
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
from uuid import uuid5, NAMESPACE_URL
import pytz

from playwright.sync_api import sync_playwright

BASE_URL = "https://www.allianzamphitheater.com/shows/calendar/{}"
VENUE_NAME = "Allianz Amphitheater at Riverfront"
VENUE_ADDRESS = "350 Tredegar Street, Richmond, VA 23219"
TIMEZONE = pytz.timezone("America/New_York")

TIME_PATTERN = re.compile(r"@\s*(\d{1,2}:\d{2}(?:AM|PM))", re.IGNORECASE)
DATE_IN_URL = re.compile(
    r"richmond-virginia-(\d{2})-(\d{2})-(\d{4})"
)


def parse_time(time_str: str) -> tuple[int, int]:
    dt = datetime.strptime(time_str.strip().upper(), "%I:%M%p")
    return dt.hour, dt.minute


def scrape_month(page, year: int, month: int) -> list[dict]:
    url = BASE_URL.format(f"{year}-{month:02d}")
    print(f"\nScraping {datetime(year, month, 1).strftime('%B %Y')}... ({url})")

    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(1)  # let any JS hydration settle
        html = page.content()
    except Exception as e:
        print(f"  WARNING: Could not load {url}: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    shows = []

    for link in soup.find_all("a", title=True):
        title = link.get("title", "")
        m = TIME_PATTERN.search(title)
        if not m:
            continue

        show_name = link.get_text(strip=True) or title.split("@")[0].strip()
        time_str = m.group(1)
        ticket_url = link.get("href", "")

        date_match = DATE_IN_URL.search(ticket_url)
        if not date_match:
            print(f"  WARNING: No date in URL for '{show_name}' — skipping", file=sys.stderr)
            continue

        ev_month, ev_day, ev_year = (
            int(date_match.group(1)),
            int(date_match.group(2)),
            int(date_match.group(3)),
        )

        hour, minute = parse_time(time_str)
        start_dt = TIMEZONE.localize(datetime(ev_year, ev_month, ev_day, hour, minute))
        end_dt = start_dt + timedelta(hours=3)

        shows.append({
            "name": show_name,
            "start": start_dt,
            "end": end_dt,
            "url": ticket_url,
            "uid_seed": f"{show_name}-{start_dt.isoformat()}",
        })
        print(f"  ✓ {ev_year}-{ev_month:02d}-{ev_day:02d}  {show_name}")

    return shows


def build_calendar(shows: list[dict]) -> Calendar:
    cal = Calendar()
    cal.add("prodid", f"-//{VENUE_NAME} Show Calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", f"{VENUE_NAME} Shows")
    cal.add("x-wr-caldesc", f"Concert schedule for {VENUE_NAME}, Richmond VA")
    cal.add("x-wr-timezone", "America/New_York")
    cal.add("refresh-interval;value=duration", "P1D")

    seen_uids = set()
    for show in shows:
        uid = str(uuid5(NAMESPACE_URL, show["uid_seed"]))
        if uid in seen_uids:
            continue
        seen_uids.add(uid)

        ev = Event()
        ev.add("summary", show["name"])
        ev.add("dtstart", show["start"])
        ev.add("dtend", show["end"])
        ev.add("location", f"{VENUE_NAME}, {VENUE_ADDRESS}")
        ev.add("url", show["url"])
        ev.add("description", f"Tickets: {show['url']}\n\n{VENUE_NAME}\n{VENUE_ADDRESS}")
        ev.add("uid", uid)
        ev.add("dtstamp", datetime.now(pytz.utc))
        cal.add_component(ev)

    return cal


def main():
    parser = argparse.ArgumentParser(description="Generate Allianz Amphitheater .ics")
    parser.add_argument("--months", type=int, default=12,
                        help="Months to scrape ahead (default: 12)")
    parser.add_argument("--output", default="allianz_shows.ics",
                        help="Output .ics file path (default: allianz_shows.ics)")
    args = parser.parse_args()

    today = date.today()
    all_shows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = context.new_page()

        for i in range(args.months):
            target = today + relativedelta(months=i)
            shows = scrape_month(page, target.year, target.month)
            all_shows.extend(shows)
            if i < args.months - 1:
                time.sleep(1)  # be polite

        browser.close()

    # Deduplicate and sort
    seen = set()
    unique_shows = []
    for s in all_shows:
        if s["uid_seed"] not in seen:
            seen.add(s["uid_seed"])
            unique_shows.append(s)
    unique_shows.sort(key=lambda s: s["start"])

    cal = build_calendar(unique_shows)
    with open(args.output, "wb") as f:
        f.write(cal.to_ical())

    print(f"\n✅ Wrote {len(unique_shows)} events to '{args.output}'")


if __name__ == "__main__":
    main()
