#!/usr/bin/env python3
"""Fixture-driven tests for scrape.build_status. Run: python scraper/test_scrape.py"""

import datetime as dt
import sys
from pathlib import Path

import scrape

FIX = Path(__file__).resolve().parent / "fixtures"
NOW = dt.datetime(2026, 8, 28, 18, 0, tzinfo=dt.timezone.utc)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "ok  " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load(fixture: str) -> dict:
    html = (FIX / fixture).read_text(encoding="utf-8")
    return scrape.build_status(html, now=NOW)


# --- real page capture -------------------------------------------------------
s = load("live-bike-2026-08-27.html")
check("real: state partial", s["summary"]["state"] == "partial")
check("real: 11 closed", s["summary"]["closed_count"] == 11, str(s["summary"]))
check("real: as_of 2026-08-28", s["as_of_date"] == "2026-08-28")
trails = {(t["area"], t["trail"], t["note"]) for t in s["closed_trails"]}
check(
    "real: Ingegnere carries 'until 2pm' note",
    ("NATO BASE AREA", "101 Ingegnere", "until 2pm") in trails,
)
check(
    "real: Ca du Puncin parsed without note",
    ("MANIE AREA", "3 Ca du Puncin", "") in trails,
)
check("real: notices capped at 3", len(s["notices"]) <= 3)
check("real: newest notice first", s["notices"][0]["date"] == "2026-08-28")
check("real: generated_at stamped from now", s["generated_at"] == "2026-08-28T18:00:00Z")
check("real: checked_at stamped from now", s["checked_at"] == "2026-08-28T18:00:00Z")

# --- all trails open -------------------------------------------------------
s = load("all-open.html")
check("all-open: state open", s["summary"]["state"] == "open", str(s["summary"]))
check("all-open: nothing closed", s["closed_trails"] == [])

# --- full network closure (weather) -------------------------------------------
s = load("weather-closure.html")
check("weather: state closed", s["summary"]["state"] == "closed", str(s["summary"]))

# --- prose-only closure, no structured list ----------------------------------
s = load("prose-only.html")
check(
    "prose: state partial (closure we can't structure != open)",
    s["summary"]["state"] == "partial",
    str(s["summary"]),
)
check("prose: no phantom trails", s["closed_trails"] == [])
check("prose: keeps notice text", "Ca du Puncin" in s["notices"][0]["text"])

# --- stale content becomes unknown -----------------------------------------
old = scrape.build_status(
    (FIX / "live-bike-2026-08-27.html").read_text(encoding="utf-8"),
    now=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc),
)
check("stale: >14d old -> state unknown", old["summary"]["state"] == "unknown")

# --- structural guard --------------------------------------------------------
try:
    scrape.build_status("<html><body><p>nothing here</p></body></html>", now=NOW)
    check("guard: raises on missing .news-item", False)
except scrape.ScrapeError:
    check("guard: raises on missing .news-item", True)

# --- main(): timestamp handling on an unchanged source ----------------------
import json as _json  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as _td:
    out = Path(_td) / "status.json"
    live = str(FIX / "live-bike-2026-08-27.html")

    scrape.main(["--from-file", live, "--out", str(out)])
    first = _json.loads(out.read_text())

    # Re-run against the same page: generated_at is pinned to the first run,
    # checked_at moves forward (last write is now older than MAX_CHECK_AGE
    # only in wall-clock terms, so this run rewrites and bumps it).
    first_written = _json.loads(out.read_text())
    aged = dict(first_written)
    aged["checked_at"] = "2000-01-01T00:00:00Z"
    out.write_text(_json.dumps(aged))
    scrape.main(["--from-file", live, "--out", str(out)])
    second = _json.loads(out.read_text())
    check("main: generated_at carried forward on unchanged source",
          second["generated_at"] == first["generated_at"])
    check("main: checked_at refreshed when the last write is stale",
          second["checked_at"] != "2000-01-01T00:00:00Z")

    # A fresh checked_at means the re-run is a no-op (no rewrite).
    before = out.read_text()
    scrape.main(["--from-file", live, "--out", str(out)])
    check("main: no rewrite while checked_at is fresh", out.read_text() == before)

print()
if failures:
    print(f"{len(failures)} failing: {', '.join(failures)}")
    sys.exit(1)
print("all green")
