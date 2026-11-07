from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals


REPORTS = ("stocks.html", "stocks-edge.html", "etfs.html")
EASTERN = ZoneInfo("America/New_York")


def latest_completed_session(moment: datetime | None = None) -> date:
    observed = moment or datetime.now(UTC)
    if observed.tzinfo is None:
        raise ValueError("Freshness checks require a timezone-aware timestamp")
    day = observed.astimezone(EASTERN).date()
    calendar = xcals.get_calendar("XNYS")
    session = calendar.date_to_session(day.isoformat(), direction="previous")
    # Allow delayed daily bars to settle after both regular and early closes.
    if observed < calendar.session_close(session).to_pydatetime() + timedelta(minutes=15):
        session = calendar.previous_session(session)
    return session.date()


def report_market_date() -> date:
    frozen = os.getenv("SWINGBOT_MARKET_DATE")
    return date.fromisoformat(frozen) if frozen else latest_completed_session()


def fetch_market_json(url: str) -> dict:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Market data response is not an object")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"Market data request failed: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError("Market data request timed out or was unavailable") from None
        time.sleep(2 ** attempt)
    raise RuntimeError("Market data request failed")


def history_is_current(history: list[dict], expected: str) -> bool:
    return bool(history) and datetime.fromtimestamp(history[-1]["t"] / 1000, UTC).date().isoformat() == expected


def require_current_history(ticker: str, history: list[dict], expected: str) -> None:
    if not history_is_current(history, expected):
        raise RuntimeError(f"{ticker} daily bars are missing the completed session {expected}")


def extract_rows(html: str) -> list[dict]:
    match = re.search(r"const rows = (\[.*?\]);", html, re.DOTALL)
    if not match:
        raise RuntimeError("Report rows were not found")
    rows = json.loads(match.group(1))
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Report has no rows")
    return rows


def validate_report(html: str, expected: str) -> list[dict]:
    rows = extract_rows(html)
    dates = {row.get("date") for row in rows}
    if dates != {expected}:
        raise RuntimeError(f"Report dates {dates} do not match completed session {expected}")
    return rows


def write_publication_status(directory: Path, expected: date) -> dict:
    market_date = expected.isoformat()
    calendar = xcals.get_calendar("XNYS")
    next_session = calendar.next_session(market_date)
    due_at = calendar.session_close(next_session).to_pydatetime() + timedelta(hours=4)
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    status = {
        "schema_version": 1,
        "market_date": market_date,
        "generated_at": generated,
        "next_refresh_due_at": due_at.isoformat(),
        "source_commit": os.getenv("GITHUB_SHA", "local"),
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "reports": {},
    }
    # Embed the expiry in each page so an old/offline page can warn without a server.
    banner = (
        f'<script defer src="report-freshness.js" data-market-date="{market_date}" '
        f'data-refresh-due="{due_at.isoformat()}" data-generated-at="{generated}"></script>'
    )
    aliases = {
        "stocks.html": "swing_trading_mag7_report.html",
        "stocks-edge.html": "swing_trading_stock_edge_report.html",
        "etfs.html": "swing_trading_etf_report.html",
    }
    for filename in REPORTS:
        path = directory / filename
        html = path.read_text(encoding="utf-8")
        rows = validate_report(html, market_date)
        html = html.replace("</body>", banner + "\n</body>")
        path.write_text(html, encoding="utf-8")
        (directory / aliases[filename]).write_text(html, encoding="utf-8")
        status["reports"][filename] = {
            "market_date": market_date,
            "rows": len(rows),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    portfolio = json.loads((directory / "portfolio-market-data.json").read_text(encoding="utf-8"))
    if portfolio.get("market_date") != market_date or any(
        quote.get("market_date") != market_date for quote in portfolio.get("quotes", {}).values()
    ) or not portfolio.get("quotes"):
        raise RuntimeError("Portfolio market feed is not current")
    (directory / "report-status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    return status
