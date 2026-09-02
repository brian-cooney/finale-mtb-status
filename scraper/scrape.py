#!/usr/bin/env python3
"""Scrape the Finale Outdoor Region "MTB live info" page into a normalized JSON.

Source: https://www.finaleoutdoor.com/en/live/bike

The page is a Kumbe CMS page whose "Live info" block is server-rendered as a
list of ``.news-item`` cards (a slick carousel clones them client-side, but the
raw HTML already contains one node per notice). Each card looks like::

    <div class="news-item">
      <span class="news-date">28 August 2026</span>
      <h3><b> TRAIL STATUS - 28th AUGUST 2026</b></h3>
      <p>Please note that the following trails will be CLOSED ...

    MANIE AREA: 3 Ca du Puncin***
    NATO BASE AREA: 96 Crestino pt.2  - 101 Ingegnere (until 2pm)***
    ...
    ****************************************   (boilerplate separator)
    For updates ... Telegram channel (...) ...
    </p>
    </div>

We treat the most recent card that reads like a daily "TRAIL STATUS" bulletin as
authoritative for the closed-trail list; the few most recent cards are also kept
verbatim as ``notices`` for the plugin's detail panel.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.finaleoutdoor.com/en/live/bike"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "(+omarchy-finale-mtb status scraper)"
)
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "status.json"

# When the closure list is unchanged we still rewrite status.json (bumping
# checked_at) if the last write is older than this, so consumers can tell the
# scraper is alive without a commit every run.
MAX_CHECK_AGE = dt.timedelta(hours=6)

# A run of 6+ asterisks marks the end of the trail list and the start of
# boilerplate ("For updates ... follow our Telegram channel ...").
BOILERPLATE_SEP = re.compile(r"\*{6,}")
# Segment separator between areas inside the trail list ("AREA A: x*** AREA B: y").
AREA_SEP = re.compile(r"\*{2,}")
NOTE_RE = re.compile(r"\(([^)]*)\)\s*$")
FULL_CLOSURE_RE = re.compile(
    r"\b(all|entire|whole)\b.{0,40}\b(trails?|network|region)\b.{0,40}\bclos",
    re.IGNORECASE,
)
ALL_OPEN_RE = re.compile(
    r"\b(all|entire|whole)\b.{0,40}\b(trails?|network)\b.{0,40}\b(open|reopened)\b",
    re.IGNORECASE,
)

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}


class ScrapeError(RuntimeError):
    """Raised when the page cannot be parsed into a trustworthy result."""


def fetch(url: str = SOURCE_URL, timeout: int = 30) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def parse_news_date(raw: str) -> dt.date | None:
    """'28 August 2026' -> date(2026, 8, 28)."""
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*$", raw or "")
    if not m:
        return None
    day, month_name, year = m.groups()
    month = MONTHS.get(month_name.lower())
    if not month:
        return None
    try:
        return dt.date(int(year), month, int(day))
    except ValueError:
        return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_trail_list(trail_block: str) -> list[dict]:
    """Parse 'AREA A: t1 - t2 (note)*** AREA B: t3' into closed-trail records."""
    closed: list[dict] = []
    for segment in AREA_SEP.split(trail_block):
        segment = segment.strip().strip("-–").strip()
        if not segment or ":" not in segment:
            continue
        area, _, trails_raw = segment.partition(":")
        area = _clean(area).upper()
        # Guard against prose sentences that happen to contain a colon.
        if len(area.split()) > 5 or not area:
            continue
        for trail_raw in re.split(r"\s+-\s+|\s+–\s+", trails_raw):
            trail = _clean(trail_raw)
            if not trail:
                continue
            note = ""
            note_m = NOTE_RE.search(trail)
            if note_m:
                note = _clean(note_m.group(1))
                trail = _clean(trail[: note_m.start()])
            if trail:
                closed.append({"area": area, "trail": trail, "note": note})
    return closed


def card_to_notice(card) -> dict:
    date_el = card.select_one(".news-date")
    title_el = card.select_one("h3")
    body_el = card.select_one("p")
    date = parse_news_date(date_el.get_text() if date_el else "")
    body = body_el.get_text("\n") if body_el else ""
    body = body.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    return {
        "date": date.isoformat() if date else None,
        "title": _clean(title_el.get_text(" ")) if title_el else "",
        "text": re.sub(r"\n{3,}", "\n\n", body).strip(),
    }


def looks_like_daily_status(notice: dict) -> bool:
    hay = f"{notice['title']} {notice['text']}".lower()
    return "trail status" in hay or bool(
        re.search(r"following trails.{0,30}clos", hay)
    )


def derive_state(closed_trails: list[dict], notice: dict | None) -> str:
    text = _clean((notice or {}).get("text", ""))
    lower = text.lower()
    if closed_trails:
        # A parsed per-trail list is the strongest signal.
        return "partial"
    if notice is None:
        return "unknown"
    if ALL_OPEN_RE.search(text) or "no closures" in lower or "no closure " in lower:
        return "open"
    if FULL_CLOSURE_RE.search(text):
        return "closed"
    if "clos" in lower:
        # A prose closure notice we could not structure -> don't claim "open".
        return "partial"
    return "open"


def build_status(html: str, *, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".news-slider .news-item") or soup.select(".news-item")
    if not cards:
        raise ScrapeError("no .news-item cards found - page structure changed")

    notices: list[dict] = []
    seen: set[tuple] = set()
    for card in cards:
        notice = card_to_notice(card)
        key = (notice["date"], notice["title"], notice["text"][:80])
        if key in seen:
            continue
        seen.add(key)
        notices.append(notice)

    notices.sort(key=lambda n: n["date"] or "", reverse=True)

    daily = next((n for n in notices if looks_like_daily_status(n)), None)
    source_notice = daily or (notices[0] if notices else None)

    closed_trails: list[dict] = []
    if daily:
        head = BOILERPLATE_SEP.split(daily["text"], maxsplit=1)[0]
        looks_like_list = "***" in head or re.search(
            r"following trails\b.{0,40}\bclos", head, re.IGNORECASE
        )
        if looks_like_list:
            # Drop the "Please note that ... will be CLOSED ...:" preamble.
            preamble = re.search(r":\s*\n", head)
            list_text = head[preamble.end():] if preamble else head
            closed_trails = parse_trail_list(list_text)
            if not closed_trails:
                raise ScrapeError(
                    "daily status notice found but no trails parsed from it"
                )

    as_of = None
    if source_notice and source_notice["date"]:
        as_of = source_notice["date"]

    state = derive_state(closed_trails, source_notice)

    # Stale-ish content guard: if the newest notice is very old, flag unknown.
    if as_of:
        age_days = (now.date() - dt.date.fromisoformat(as_of)).days
        if age_days > 14:
            state = "unknown"

    stamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        # generated_at: when the closure list last changed (main() carries the
        # old value forward when a re-scrape finds the same content).
        # checked_at: when the source was last successfully read (every run).
        "generated_at": stamp,
        "checked_at": stamp,
        "source": SOURCE_URL,
        "as_of_date": as_of,
        "summary": {"state": state, "closed_count": len(closed_trails)},
        "closed_trails": closed_trails,
        "notices": notices[:3],
    }


def dumps(status: dict) -> str:
    return json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _content(status: dict) -> str:
    """Canonical form of everything except the timestamps."""
    return json.dumps(
        {k: v for k, v in status.items() if k not in ("generated_at", "checked_at")},
        sort_keys=True,
    )


def _parse_stamp(raw: object) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print JSON, don't write")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path")
    ap.add_argument("--from-file", type=Path, help="parse a local HTML file instead of fetching")
    args = ap.parse_args(argv)

    try:
        html = args.from_file.read_text(encoding="utf-8") if args.from_file else fetch()
        status = build_status(html)
    except (requests.RequestException, ScrapeError) as exc:
        print(f"scrape failed: {exc}", file=sys.stderr)
        if args.out.exists():
            print("keeping existing status.json", file=sys.stderr)
        return 1

    if args.dry_run:
        sys.stdout.write(dumps(status))
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    old = _load(args.out)

    if old is not None and _content(old) == _content(status):
        # Same closures as last time. Keep the original generated_at and only
        # rewrite (to bump checked_at) once the last write goes stale, so an
        # unchanged source does not mean a commit every run.
        status["generated_at"] = old.get("generated_at", status["generated_at"])
        last_check = _parse_stamp(old.get("checked_at"))
        now = _parse_stamp(status["checked_at"])
        if last_check and now and now - last_check < MAX_CHECK_AGE:
            print("status unchanged; checked_at still fresh")
            return 0
        print("status unchanged; refreshing checked_at")
    else:
        print(f"status changed (state={status['summary']['state']}, "
              f"closed={status['summary']['closed_count']})")

    args.out.write_text(dumps(status), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
