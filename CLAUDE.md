# 訂位雷達 (daoyuu-monitor)

Watches hard-to-book restaurants and notifies subscribers when a table opens.

## Layout

- `app.py` — FastAPI app (Railway, `Procfile`). Serves `templates/*.html` as raw HTML, `static/` at `/static`, JSON under `/api/*`. SQLite `subscriptions.db` (gitignored). A background thread polls EZTABLE every 30 min.
- `daoyuu_monitor.py` — CLI monitor for EZTABLE (`--once`, loop, `--heartbeat`).
- `inline_playwright.py` — Playwright scraper for inline.app / Catch Table / Google Reserve.
- `config.json` — restaurant list and notification settings. `monitor_state.json` — seen slots, committed back by CI.
- `.github/workflows/` — scheduled runs that commit state.

## Frontend

Three plain HTML pages with vanilla JS, no build step. All styling lives in `static/style.css`; do not re-add inline `<style>` blocks or per-page CSS variables.

Design rules (see `.agents/skills/frontend-design` and `.agents/skills/impeccable`):
- Dark, warm palette. Amber (`--lit`) is the only accent and means exactly one thing: a restaurant has a table.
- Noto Serif TC for headlines and restaurant names, Noto Sans TC for UI. No Inter.
- The only authored motion is a lit row turning on. No pulsing dots, glows, gradient text, per-card fade-ins.
- Copy is plain zh-TW. Buttons name the action. Errors say what went wrong and what to do.

## Run locally

```
pip install -r requirements.txt
uvicorn app:app --reload
```

`test_eztable.py` is a manual script, not a pytest suite.
