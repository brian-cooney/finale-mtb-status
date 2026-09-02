# finale-mtb-status

A tiny "API" for the [Finale Outdoor Region](https://www.finaleoutdoor.com/en/live/bike)
mountain-bike trail status.

A GitHub Actions cron job scrapes the official "MTB live info" page every 30
minutes, normalizes the dated closure bulletins into JSON, and commits the
result to [`data/status.json`](data/status.json). GitHub Pages serves that file
so any number of clients (e.g. the Omarchy bar widget) can poll one small,
cache-friendly URL instead of scraping the CMS page themselves.

## Published URL

```
https://<user>.github.io/finale-mtb-status/data/status.json
```

Enable **Settings → Pages → Deploy from a branch → `main` / `/ (root)`**.

## JSON shape

```json
{
  "generated_at": "2026-08-28T15:40:00Z",
  "checked_at": "2026-08-29T09:10:00Z",
  "source": "https://www.finaleoutdoor.com/en/live/bike",
  "as_of_date": "2026-08-28",
  "summary": { "state": "partial", "closed_count": 11 },
  "closed_trails": [
    { "area": "NATO BASE AREA", "trail": "101 Ingegnere", "note": "until 2pm" }
  ],
  "notices": [
    { "date": "2026-08-28", "title": "TRAIL STATUS - 28th AUGUST 2026", "text": "..." }
  ]
}
```

| field          | meaning                                                                  |
|----------------|-------------------------------------------------------------------------|
| `generated_at` | when the closure list last **changed** — unchanged re-scrapes keep it   |
| `checked_at`   | when the source was last read successfully — refreshed at least every 6 h so a consumer can tell the scraper is alive |
| `as_of_date`   | date on the bulletin the status was derived from                        |

`summary.state` is one of:

| state     | meaning                                                        |
|-----------|---------------------------------------------------------------|
| `open`    | notice says the whole network is open / no closures           |
| `partial` | some trails closed (see `closed_trails`) or an unstructured closure notice |
| `closed`  | notice indicates a full-network / weather closure             |
| `unknown` | newest notice is >14 days old or could not be parsed          |

The scraper never overwrites `status.json` when the page can't be parsed
(missing `.news-item` cards, a "TRAIL STATUS" bulletin with no parseable
trails) — it exits non-zero and keeps the last good file.

## Local development

```bash
python -m venv .venv && .venv/bin/pip install -r scraper/requirements.txt
.venv/bin/python scraper/test_scrape.py                 # fixture tests
.venv/bin/python scraper/scrape.py --dry-run            # print JSON from the live page
.venv/bin/python scraper/scrape.py --dry-run --from-file scraper/fixtures/all-open.html
```

## Consumers

- [`omarchy-finale-plugin`](https://github.com/brian-cooney/omarchy-finale-plugin) — Omarchy bar widget + panel.
