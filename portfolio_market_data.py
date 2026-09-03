from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


API_ROOT = "https://api.massive.com"
OUTPUT_PATH = Path(__file__).with_name("portfolio-market-data.json")
SCHEMA_VERSION = 1
LOOKBACK_DAYS = 14
MARKET_TIME_ZONE = ZoneInfo("America/New_York")
DAILY_BAR_SETTLE_TIME = time(16, 15)

PORTFOLIO_ASSETS: tuple[dict[str, Any], ...] = (
    {"ticker": "QQQM", "name": "Invesco NASDAQ 100 ETF", "asset_type": "ETF", "target_pct": 30, "monthly_target": 1500},
    {"ticker": "XMMO", "name": "Invesco S&P MidCap Momentum ETF", "asset_type": "ETF", "target_pct": 18, "monthly_target": 900},
    {"ticker": "SCHA", "name": "Schwab U.S. Small-Cap ETF", "asset_type": "ETF", "target_pct": 12, "monthly_target": 600},
    {"ticker": "GOOGL", "name": "Alphabet Class A", "asset_type": "Stock", "target_pct": 12, "monthly_target": 600},
    {"ticker": "AMZN", "name": "Amazon", "asset_type": "Stock", "target_pct": 12, "monthly_target": 600},
)

JsonFetcher = Callable[[str, dict[str, Any], int], dict[str, Any]]


def completed_market_date(moment: datetime | None = None) -> date:
    """Return the latest date whose regular US trading session is complete."""
    observed = moment or datetime.now(tz=UTC)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    market_now = observed.astimezone(MARKET_TIME_ZONE)
    if market_now.time().replace(tzinfo=None) < DAILY_BAR_SETTLE_TIME:
        return market_now.date() - timedelta(days=1)
    return market_now.date()


def _load_local_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line or line.startswith("export "):
            continue
        key, value = line.split("=", 1)
        clean_key = key.strip()
        if clean_key and clean_key not in os.environ:
            os.environ[clean_key] = value.strip().strip('"').strip("'")


def get_api_key() -> str:
    _load_local_env(Path(__file__).with_name(".env"))
    _load_local_env(Path(__file__).parent.parent / ".env")
    return os.getenv("MASSIVE_API_KEY", "").strip()


def _default_fetcher(api_key: str) -> JsonFetcher:
    def fetch(path: str, params: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        query = dict(params)
        query["apiKey"] = api_key
        request = urllib.request.Request(
            f"{API_ROOT}{path}?{urllib.parse.urlencode(query)}",
            headers={"Accept": "application/json", "User-Agent": "Swing-Bot-Portfolio/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Massive returned HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Massive request failed for {path}: {exc.reason}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Massive returned an invalid payload for {path}")
        return payload

    return fetch


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _market_date(timestamp_ms: Any) -> str | None:
    value = _number(timestamp_ms)
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).date().isoformat()


def _quote_for_asset(asset: dict[str, Any], as_of: date, fetcher: JsonFetcher) -> dict[str, Any]:
    ticker = str(asset["ticker"])
    start = as_of - timedelta(days=LOOKBACK_DAYS)
    payload = fetcher(
        f"/v2/aggs/ticker/{urllib.parse.quote(ticker, safe='')}/range/1/day/{start.isoformat()}/{as_of.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 30},
        30,
    )
    rows = payload.get("results")
    if not isinstance(rows, list):
        rows = []
    valid = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close = _number(row.get("c"))
        market_date = _market_date(row.get("t"))
        if close is None or close <= 0 or market_date is None:
            continue
        valid.append({"price": close, "market_date": market_date})
    if not valid:
        raise RuntimeError(f"No usable daily price was returned for {ticker}")

    latest = valid[-1]
    previous = valid[-2] if len(valid) > 1 else None
    change_pct = None
    if previous and previous["price"]:
        change_pct = (latest["price"] / previous["price"] - 1.0) * 100.0
    return {
        **asset,
        "currency": "USD",
        "price": round(latest["price"], 4),
        "previous_close": round(previous["price"], 4) if previous else None,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "market_date": latest["market_date"],
    }


def build_portfolio_market_feed(
    *,
    as_of: date | None = None,
    fetcher: JsonFetcher | None = None,
) -> dict[str, Any]:
    requested_date = as_of or completed_market_date()
    if fetcher is None:
        api_key = get_api_key()
        if not api_key:
            raise RuntimeError("MASSIVE_API_KEY is required to build the portfolio market feed")
        fetcher = _default_fetcher(api_key)

    quotes = [_quote_for_asset(asset, requested_date, fetcher) for asset in PORTFOLIO_ASSETS]
    market_dates = {quote["market_date"] for quote in quotes}
    if len(market_dates) != 1:
        details = ", ".join(f"{quote['ticker']}={quote['market_date']}" for quote in quotes)
        raise RuntimeError(f"Portfolio prices are not aligned to one completed market date: {details}")
    market_date = market_dates.pop()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "requested_through": requested_date.isoformat(),
        "market_date": market_date,
        "source": "Massive adjusted daily aggregates",
        "price_type": "Latest available daily close; scheduled and delayed, not a broker fill or live quote",
        "quotes": {quote["ticker"]: quote for quote in quotes},
    }


def write_portfolio_market_feed(payload: dict[str, Any], output_path: Path = OUTPUT_PATH) -> None:
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    try:
        payload = build_portfolio_market_feed()
    except RuntimeError as exc:
        print(str(exc))
        return 1
    write_portfolio_market_feed(payload)
    print(f"Built {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
