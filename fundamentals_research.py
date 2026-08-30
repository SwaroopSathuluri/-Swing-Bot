from __future__ import annotations

import json
import math
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse


API_ROOT = "https://api.massive.com"
OUTPUT_PATH = Path(__file__).with_name("fundamentals-data.json")
SCHEMA_VERSION = 1
FINANCIAL_MAX_AGE_DAYS = 190
RATIO_MAX_AGE_DAYS = 7
PRICE_MAX_AGE_DAYS = 7

RESEARCH_UNIVERSE: list[dict[str, str]] = [
    {"ticker": "NVDA", "theme": "AI compute"},
    {"ticker": "GOOGL", "theme": "Search, cloud and AI"},
    {"ticker": "AMZN", "theme": "Cloud, commerce and advertising"},
    {"ticker": "TSM", "theme": "Advanced semiconductor foundry"},
    {"ticker": "LLY", "theme": "Metabolic health and medicines"},
    {"ticker": "META", "theme": "Digital advertising and AI"},
    {"ticker": "AVGO", "theme": "AI networking and infrastructure software"},
    {"ticker": "AAPL", "theme": "Devices and services ecosystem"},
    {"ticker": "COST", "theme": "Membership retail"},
    {"ticker": "ORCL", "theme": "Database and cloud infrastructure"},
    {"ticker": "GEV", "theme": "Power generation and grid equipment"},
    {"ticker": "V", "theme": "Global payments network"},
]

RISK_LIBRARY: dict[str, list[str]] = {
    "NVDA": [
        "AI spending, customer concentration and semiconductor cycles can make growth volatile.",
        "Export controls and customers developing their own chips can pressure demand or margins.",
    ],
    "GOOGL": [
        "Antitrust remedies and AI-driven changes to search could alter the economics of the core franchise.",
        "Large infrastructure spending must translate into durable Cloud and AI cash returns.",
    ],
    "AMZN": [
        "Heavy data-center spending can suppress free cash flow before returns are proven.",
        "Retail margins, labor costs and regulatory scrutiny remain meaningful swing factors.",
    ],
    "TSM": [
        "Taiwan geopolitical risk is a severe tail risk that ordinary valuation metrics cannot capture.",
        "The U.S. ADR does not fit this SEC-quarterly model, so Swing Bot withholds an investment action.",
    ],
    "LLY": [
        "The thesis is concentrated in incretin medicines, with pricing, reimbursement and competition risk.",
        "Manufacturing execution, trial results and safety findings can change the outlook quickly.",
    ],
    "META": [
        "Advertising is economically sensitive, while regulation and platform-policy changes are persistent risks.",
        "AI and metaverse investment can raise costs faster than monetization improves.",
    ],
    "AVGO": [
        "Large customers, acquisition integration and debt create concentration and execution risk.",
        "A slowdown in custom AI accelerators or networking demand could compress a premium valuation.",
    ],
    "AAPL": [
        "Hardware concentration, China exposure and App Store regulation can pressure growth or margins.",
        "A premium valuation requires the installed base and services engine to keep compounding.",
    ],
    "COST": [
        "A premium valuation leaves little room for slower traffic, weaker renewals or margin pressure.",
        "Low merchandise margins make wage, freight and input-cost execution important.",
    ],
    "ORCL": [
        "Cloud expansion requires heavy capital spending and disciplined balance-sheet management.",
        "Execution against larger cloud competitors and converting backlog into profitable revenue are key.",
    ],
    "GEV": [
        "Backlog conversion, project execution and the path to profitability in Wind can be volatile.",
        "A large valuation rerating makes the stock sensitive to slower power-infrastructure orders.",
    ],
    "V": [
        "Payments regulation, litigation and changes in interchange economics could pressure the network model.",
        "Consumer-spending weakness and alternative payment rails can slow transaction growth.",
    ],
}

JsonFetcher = Callable[[str, dict[str, Any], int], dict[str, Any]]


def _load_local_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line or line.startswith("export "):
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def get_api_key() -> str:
    _load_local_env(Path(__file__).with_name(".env"))
    # Local Swing Bot checkouts may share the Trading workspace's server-side
    # environment file. GitHub Actions still supplies the repository secret.
    _load_local_env(Path(__file__).parent.parent / ".env")
    return os.getenv("MASSIVE_API_KEY", "").strip()


def _default_fetcher(api_key: str) -> JsonFetcher:
    def fetch(path: str, params: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        query = dict(params)
        query["apiKey"] = api_key
        url = f"{API_ROOT}{path}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "Swing-Bot-Fundamentals/1.0"},
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


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _age_days(value: Any, as_of: date) -> int | None:
    parsed = _parse_date(value)
    return (as_of - parsed).days if parsed else None


def _timestamp_iso(value: Any) -> str | None:
    number = _safe_float(value)
    if number is None:
        return None
    if number >= 1e17:
        number /= 1e9
    elif number >= 1e14:
        number /= 1e6
    elif number >= 1e11:
        number /= 1e3
    try:
        return datetime.fromtimestamp(number, tz=UTC).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _moving_average(values: list[float], length: int) -> float | None:
    return statistics.fmean(values[-length:]) if len(values) >= length else None


def _ema(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    alpha = 2.0 / (length + 1.0)
    result = statistics.fmean(values[:length])
    for value in values[length:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _rsi(values: list[float], length: int = 14) -> float | None:
    if len(values) <= length:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes[:length]]
    losses = [max(-change, 0.0) for change in changes[:length]]
    average_gain = statistics.fmean(gains)
    average_loss = statistics.fmean(losses)
    for change in changes[length:]:
        average_gain = ((average_gain * (length - 1)) + max(change, 0.0)) / length
        average_loss = ((average_loss * (length - 1)) + max(-change, 0.0)) / length
    if average_loss == 0:
        return 100.0 if average_gain else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def _atr(rows: list[dict[str, Any]], length: int = 14) -> float | None:
    true_ranges: list[float] = []
    for index, row in enumerate(rows):
        high = _safe_float(row.get("h"))
        low = _safe_float(row.get("l"))
        if high is None or low is None:
            continue
        if index == 0:
            true_ranges.append(high - low)
            continue
        prior_close = _safe_float(rows[index - 1].get("c"))
        if prior_close is None:
            continue
        true_ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    if len(true_ranges) < length:
        return None
    value = statistics.fmean(true_ranges[:length])
    for current in true_ranges[length:]:
        value = ((value * (length - 1)) + current) / length
    return value


def _interpolate_score(value: float | None, anchors: list[tuple[float, float]]) -> float | None:
    if value is None:
        return None
    ordered = sorted(anchors)
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= value <= x1:
            weight = (value - x0) / (x1 - x0)
            return y0 + weight * (y1 - y0)
    return None


def _weighted_score(parts: Iterable[tuple[float | None, float]]) -> tuple[float | None, float]:
    entries = list(parts)
    total_weight = sum(weight for _, weight in entries)
    available = [(score, weight) for score, weight in entries if score is not None]
    available_weight = sum(weight for _, weight in available)
    if not available or not total_weight:
        return None, 0.0
    score = sum(float(value) * weight for value, weight in available) / available_weight
    return max(0.0, min(100.0, score)), available_weight / total_weight


def _unique_periods(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_period: dict[str, dict[str, Any]] = {}
    for record in records:
        period = str(record.get("period_end") or "")
        if not period:
            continue
        incumbent = by_period.get(period)
        if incumbent is None or str(record.get("filing_date") or "") > str(incumbent.get("filing_date") or ""):
            by_period[period] = record
    return sorted(by_period.values(), key=lambda row: str(row.get("period_end") or ""), reverse=True)


def _year_ago(records: list[dict[str, Any]], latest: dict[str, Any]) -> dict[str, Any] | None:
    latest_date = _parse_date(latest.get("period_end"))
    if latest_date is None:
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for record in records[1:]:
        record_date = _parse_date(record.get("period_end"))
        if record_date is None:
            continue
        delta = (latest_date - record_date).days
        if 300 <= delta <= 430:
            candidates.append((abs(delta - 365), record))
    return min(candidates, key=lambda entry: entry[0])[1] if candidates else None


def _four_contiguous_quarters(records: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    quarters = _unique_periods(records)[:4]
    if len(quarters) != 4:
        return None
    dates = [_parse_date(record.get("period_end")) for record in quarters]
    if any(value is None for value in dates):
        return None
    parsed_dates = [value for value in dates if value is not None]
    gaps = [(parsed_dates[index] - parsed_dates[index + 1]).days for index in range(3)]
    span = (parsed_dates[0] - parsed_dates[-1]).days
    if not all(70 <= gap <= 120 for gap in gaps) or not 240 <= span <= 380:
        return None
    return quarters


def _group_statement_rows(rows: Iterable[dict[str, Any]], tickers: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    wanted = set(tickers)
    grouped = {ticker: [] for ticker in wanted}
    for row in rows:
        row_tickers = set(row.get("tickers") or [])
        for ticker in wanted.intersection(row_tickers):
            grouped[ticker].append(row)
    return {ticker: _unique_periods(records) for ticker, records in grouped.items()}


def _sum_if_complete(rows: list[dict[str, Any]] | None, field: str) -> float | None:
    if not rows:
        return None
    values = [_safe_float(row.get(field)) for row in rows]
    return sum(value for value in values if value is not None) if all(value is not None for value in values) else None


def _sum_present(record: dict[str, Any], fields: Iterable[str]) -> float | None:
    values = [_safe_float(record.get(field)) for field in fields]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _free_cash_flow(record: dict[str, Any]) -> float | None:
    operating_cash = _safe_float(record.get("net_cash_from_operating_activities"))
    capex = _safe_float(record.get("purchase_of_property_plant_and_equipment"))
    if operating_cash is None or capex is None:
        return None
    return operating_cash + capex if capex <= 0 else operating_cash - capex


def _technical_snapshot(rows: list[dict[str, Any]], snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    valid_rows = [row for row in rows if _safe_float(row.get("c")) is not None and row.get("t") is not None]
    closes = [float(row["c"]) for row in valid_rows]
    snapshot = snapshot or {}
    session = snapshot.get("session") or {}
    market_status = str(snapshot.get("market_status") or "").lower()
    live_statuses = {"open", "early_trading", "late_trading"}
    if market_status in live_statuses:
        snapshot_price = _safe_float(session.get("price")) or _safe_float(session.get("close"))
        price_kind = "live_or_extended"
    else:
        snapshot_price = _safe_float(session.get("close")) or _safe_float(session.get("price"))
        price_kind = "regular_close"
    price = snapshot_price if snapshot_price is not None else (closes[-1] if closes else None)
    snapshot_asof = _timestamp_iso(session.get("last_updated"))
    bar_asof = _timestamp_iso(valid_rows[-1].get("t")) if valid_rows else None
    price_asof = snapshot_asof or bar_asof
    if market_status in live_statuses:
        previous_close = _safe_float(session.get("previous_close"))
        if previous_close is None and len(closes) > 1:
            previous_close = closes[-2]
    else:
        # On closed-market/weekend snapshots Massive can roll previous_close
        # forward to the same value as session.close. The prior completed daily
        # bar is the unambiguous comparison for the last regular session.
        previous_close = closes[-2] if len(closes) > 1 else _safe_float(session.get("previous_close"))

    empty = {
        "price": _round(price),
        "change_pct": _round(_pct(price, previous_close)),
        "asof": price_asof,
        "ema20": None,
        "sma50": None,
        "sma200": None,
        "rsi14": None,
        "atr14": None,
        "high52": None,
        "low52": None,
        "preferred_band": {"low": None, "high": None},
        "deeper_band": {"low": None, "high": None},
        "status": "UNAVAILABLE",
        "price_kind": price_kind,
        "market_status": market_status or None,
        "score": None,
    }
    if price is None or len(closes) < 20:
        return empty

    ema20 = _ema(closes, 20)
    sma50 = _moving_average(closes, 50)
    sma200 = _moving_average(closes, 200)
    rsi14 = _rsi(closes, 14)
    atr14 = _atr(valid_rows, 14)
    year_rows = valid_rows[-252:]
    highs = [_safe_float(row.get("h")) for row in year_rows]
    lows = [_safe_float(row.get("l")) for row in year_rows]
    high52 = max(value for value in highs if value is not None) if any(value is not None for value in highs) else None
    low52 = min(value for value in lows if value is not None) if any(value is not None for value in lows) else None
    padding = (atr14 or price * 0.025) * 0.35
    if ema20 is not None and sma50 is not None:
        preferred_low = min(ema20, sma50) - padding
        preferred_high = max(ema20, sma50) + padding
    else:
        preferred_low = price - (atr14 or price * 0.03)
        preferred_high = price + (atr14 or price * 0.03) * 0.25
    if sma200 is not None:
        deeper_low = sma200 - (atr14 or price * 0.025) * 0.60
        deeper_high = sma200 + (atr14 or price * 0.025) * 0.60
    else:
        deeper_low = preferred_low - (atr14 or price * 0.025)
        deeper_high = preferred_low

    trend_broken = sma200 is not None and price < sma200 * 0.95
    if trend_broken:
        status = "TREND BROKEN"
    elif preferred_low <= price <= preferred_high:
        status = "INSIDE PREFERRED BAND"
    elif price > preferred_high:
        status = "ABOVE PREFERRED BAND"
    else:
        status = "BELOW PREFERRED BAND"

    trend_score = 50.0
    if sma50 is not None and sma200 is not None:
        trend_score = (45 if price >= sma200 else 8) + (30 if price >= sma50 else 10) + (25 if sma50 >= sma200 else 5)
    elif sma50 is not None:
        trend_score = 80 if price >= sma50 else 25
    if status == "INSIDE PREFERRED BAND":
        level_score = 100.0
    elif status == "ABOVE PREFERRED BAND":
        extension = _pct(price, preferred_high) or 0.0
        level_score = 70 if extension <= 3 else 48 if extension <= 7 else 20
    elif status == "BELOW PREFERRED BAND":
        level_score = 45.0
    else:
        level_score = 15.0
    if rsi14 is None:
        rsi_score = 50.0
    elif 45 <= rsi14 <= 65:
        rsi_score = 100.0
    elif 35 <= rsi14 <= 72:
        rsi_score = 72.0
    elif rsi14 > 78:
        rsi_score = 22.0
    else:
        rsi_score = 42.0
    technical_score = 0.45 * trend_score + 0.35 * level_score + 0.20 * rsi_score

    return {
        "price": _round(price),
        "change_pct": _round(_pct(price, previous_close)),
        "asof": price_asof,
        "ema20": _round(ema20),
        "sma50": _round(sma50),
        "sma200": _round(sma200),
        "rsi14": _round(rsi14, 1),
        "atr14": _round(atr14),
        "high52": _round(high52),
        "low52": _round(low52),
        "preferred_band": {"low": _round(max(0.01, preferred_low)), "high": _round(max(0.01, preferred_high))},
        "deeper_band": {"low": _round(max(0.01, deeper_low)), "high": _round(max(0.01, deeper_high))},
        "status": status,
        "price_kind": price_kind,
        "market_status": market_status or None,
        "score": _round(technical_score, 1),
    }


def _model_status(ticker: str, overview: dict[str, Any]) -> tuple[str, str]:
    security_type = str(overview.get("type") or "").upper()
    locale = str(overview.get("locale") or "").lower()
    if ticker == "TSM" or security_type.startswith("ADR") or (locale and locale != "us"):
        return "unsupported_foreign", "Foreign issuers and ADRs require currency, filing-frequency and share-ratio normalization."
    sic_value = str(overview.get("sic_code") or overview.get("sic") or "")
    try:
        sic = int(sic_value)
    except ValueError:
        sic = None
    if sic is not None and 6000 <= sic <= 6799:
        return "unsupported_sector", "Banks, insurers, brokers and REITs require sector-specific accounting models."
    if sic is None:
        return "unknown", "The company could not be classified, so a positive action is withheld."
    return "supported", "Generic operating-company model applied."


def _strength_score(
    net_cash: float | None,
    market_cap: float | None,
    current_ratio: float | None,
    debt_to_equity: float | None,
    negative_equity: bool,
) -> tuple[float | None, float]:
    net_cash_ratio = _ratio(net_cash, market_cap)
    debt_score: float | None
    if negative_equity or (debt_to_equity is not None and debt_to_equity < 0):
        debt_score = 0.0
    else:
        debt_score = _interpolate_score(debt_to_equity, [(0, 100), (0.3, 85), (0.75, 65), (1.5, 40), (3, 15), (5, 0)])
    score, coverage = _weighted_score([
        (_interpolate_score(net_cash_ratio, [(-0.25, 0), (-0.05, 30), (0, 55), (0.10, 80), (0.30, 100)]), 0.45),
        (debt_score, 0.35),
        (_interpolate_score(current_ratio, [(0.5, 10), (1, 50), (1.5, 75), (2.5, 95), (5, 100)]), 0.20),
    ])
    if negative_equity and score is not None:
        score = min(score, 25.0)
    return score, coverage


def _business_score(metrics: dict[str, float | None], strength: float | None, earnings_reliable: bool) -> tuple[float | None, float]:
    growth_anchors = [(-20, 0), (0, 30), (10, 55), (20, 72), (40, 90), (75, 100)]
    return _weighted_score([
        (_interpolate_score(metrics["revenue_growth_yoy"], growth_anchors), 0.22),
        (_interpolate_score(metrics["eps_growth_yoy"], growth_anchors) if earnings_reliable else None, 0.12),
        (_interpolate_score(metrics["gross_margin"], [(10, 20), (25, 42), (40, 63), (60, 84), (75, 100)]), 0.10),
        (_interpolate_score(metrics["operating_margin"], [(-5, 0), (0, 25), (10, 48), (20, 70), (35, 90), (50, 100)]), 0.20),
        (_interpolate_score(metrics["fcf_margin"], [(-5, 0), (0, 25), (8, 50), (18, 72), (30, 90), (45, 100)]), 0.16),
        (_interpolate_score(metrics["roe"], [(-10, 0), (0, 25), (10, 50), (20, 70), (35, 88), (60, 100)]) if earnings_reliable else None, 0.08),
        (strength, 0.12),
    ])


def _valuation_score(metrics: dict[str, float | None], earnings_reliable: bool) -> tuple[float | None, float]:
    pe = metrics["pe"] if earnings_reliable and metrics["pe"] is not None and metrics["pe"] > 0 else None
    ps = metrics["ps"] if metrics["ps"] is not None and metrics["ps"] > 0 else None
    pfcf = metrics["pfcf"] if metrics["pfcf"] is not None and metrics["pfcf"] > 0 else None
    ev_ebitda = metrics["ev_ebitda"] if metrics["ev_ebitda"] is not None and metrics["ev_ebitda"] > 0 else None
    return _weighted_score([
        (_interpolate_score(pe, [(10, 100), (15, 90), (20, 80), (25, 70), (30, 60), (40, 42), (60, 20), (100, 0)]), 0.28),
        (_interpolate_score(ps, [(1, 100), (3, 82), (6, 62), (10, 42), (18, 20), (30, 0)]), 0.12),
        (_interpolate_score(pfcf, [(8, 100), (15, 90), (25, 72), (35, 55), (50, 35), (80, 10), (120, 0)]), 0.24),
        (_interpolate_score(ev_ebitda, [(6, 100), (10, 90), (15, 76), (20, 63), (28, 47), (40, 25), (60, 0)]), 0.18),
        (_interpolate_score(metrics["fcf_yield"], [(0, 0), (1, 20), (2, 40), (3, 60), (5, 82), (8, 100)]), 0.18),
    ])


def _data_completeness(metrics: dict[str, float | None], technical_score: float | None, overview: dict[str, Any]) -> float:
    evidence = [
        (metrics["revenue_growth_yoy"], 0.11),
        (metrics["eps_growth_yoy"], 0.06),
        (metrics["gross_margin"], 0.07),
        (metrics["operating_margin"], 0.10),
        (metrics["fcf_margin"], 0.11),
        (metrics["roe"], 0.05),
        (metrics["net_cash"], 0.07),
        (metrics["current_ratio"], 0.04),
        (metrics["debt_to_equity"], 0.05),
        (metrics["pe"], 0.07),
        (metrics["ps"], 0.04),
        (metrics["pfcf"], 0.06),
        (metrics["ev_ebitda"], 0.05),
        (metrics["fcf_yield"], 0.05),
        (technical_score, 0.10),
        (1.0 if overview.get("name") and (overview.get("sic_code") or overview.get("sic")) else None, 0.02),
    ]
    return sum(weight for value, weight in evidence if value is not None) / sum(weight for _, weight in evidence) * 100.0


def _action_for(
    data_status: str,
    business: float | None,
    valuation: float | None,
    technical_score: float | None,
    technical_status: str,
) -> tuple[str, str, str]:
    if data_status.startswith("unsupported"):
        return "UNSUPPORTED MODEL", "neutral", "This security needs a sector, foreign-issuer or ADR-specific model before an investment view is responsible."
    if data_status == "stale":
        return "STALE DATA", "neutral", "At least one required price or financial source is too old for a current investment view."
    if data_status != "ready" or None in (business, valuation, technical_score):
        return "INSUFFICIENT DATA", "neutral", "Recent, complete business, valuation and price evidence is required before ranking this stock."
    assert business is not None and valuation is not None and technical_score is not None
    if business < 45:
        return "AVOID FOR NOW", "negative", "The current operating-company evidence is below the model's minimum business threshold."
    if technical_status == "TREND BROKEN":
        return "WATCH PRICE", "caution", "The long-term price trend needs to stabilize before new capital is considered."
    if valuation < 35:
        return "WATCH VALUATION", "caution", "Business evidence may be sound, but valuation leaves too little margin for error."
    if business >= 75 and valuation >= 55 and technical_score >= 60 and technical_status != "ABOVE PREFERRED BAND":
        return "ATTRACTIVE NOW", "positive", "Business quality, valuation and the current technical level support staged research accumulation."
    if business >= 68 and valuation >= 42 and technical_score >= 48 and technical_status != "ABOVE PREFERRED BAND":
        return "START SMALL", "positive", "The evidence supports a starter position, with later additions conditioned on new filings and price discipline."
    if technical_status == "ABOVE PREFERRED BAND":
        return "WATCH PRICE", "caution", "The stock is above its preferred technical band; wait for price or fundamentals to catch up."
    if business >= 58:
        return "WATCH", "neutral", "Evidence is mixed; keep the company on the research list until valuation or price improves."
    return "AVOID FOR NOW", "negative", "The current evidence does not justify a new long-term position."


def _freshness(
    technical: dict[str, Any],
    ratios: dict[str, Any],
    latest_income: dict[str, Any],
    latest_balance: dict[str, Any],
    latest_cash: dict[str, Any],
    quarters_complete: bool,
    as_of: date,
) -> dict[str, Any]:
    price_asof = technical.get("asof")
    ratio_asof = ratios.get("date")
    financial_period_end = latest_income.get("period_end")
    balance_period_end = latest_balance.get("period_end")
    cash_flow_period_end = latest_cash.get("period_end")
    filing_date = latest_income.get("filing_date")
    price_age = _age_days(price_asof, as_of)
    ratio_age = _age_days(ratio_asof, as_of)
    financial_age = _age_days(financial_period_end, as_of)
    balance_age = _age_days(balance_period_end, as_of)
    cash_flow_age = _age_days(cash_flow_period_end, as_of)
    price_fresh = price_age is not None and -1 <= price_age <= PRICE_MAX_AGE_DAYS
    ratios_fresh = ratio_age is not None and -1 <= ratio_age <= RATIO_MAX_AGE_DAYS
    income_fresh = financial_age is not None and -1 <= financial_age <= FINANCIAL_MAX_AGE_DAYS
    balance_fresh = balance_age is not None and -1 <= balance_age <= FINANCIAL_MAX_AGE_DAYS
    cash_flow_fresh = cash_flow_age is not None and -1 <= cash_flow_age <= FINANCIAL_MAX_AGE_DAYS
    financials_fresh = income_fresh and balance_fresh and cash_flow_fresh
    missing: list[str] = []
    if not price_fresh:
        missing.append("price")
    if not ratios_fresh:
        missing.append("ratios")
    if not financials_fresh:
        missing.append("financial statements")
    if not quarters_complete:
        missing.append("four contiguous quarters")
    note = "All required sources pass freshness checks." if not missing else f"Needs current {', '.join(missing)}."
    return {
        "price_asof": price_asof,
        "ratio_asof": ratio_asof,
        "financial_period_end": financial_period_end,
        "balance_period_end": balance_period_end,
        "cash_flow_period_end": cash_flow_period_end,
        "filing_date": filing_date,
        "price_age_days": price_age,
        "ratio_age_days": ratio_age,
        "financial_age_days": financial_age,
        "balance_age_days": balance_age,
        "cash_flow_age_days": cash_flow_age,
        "price_fresh": price_fresh,
        "ratios_fresh": ratios_fresh,
        "income_fresh": income_fresh,
        "balance_fresh": balance_fresh,
        "cash_flow_fresh": cash_flow_fresh,
        "financials_fresh": financials_fresh,
        "quarters_complete": quarters_complete,
        "note": note,
    }


def _build_reasons(
    ticker: str,
    metrics: dict[str, float | None],
    technical: dict[str, Any],
    negative_equity: bool,
    earnings_reliable: bool,
    earnings_distorted: bool,
    freshness: dict[str, Any],
) -> tuple[list[str], list[str]]:
    catalysts: list[str] = []
    risks = list(RISK_LIBRARY.get(ticker, []))
    revenue_growth = metrics["revenue_growth_yoy"]
    operating_margin = metrics["operating_margin"]
    fcf_margin = metrics["fcf_margin"]
    net_cash = metrics["net_cash"]
    if revenue_growth is not None and revenue_growth >= 15:
        catalysts.append(f"Latest quarterly revenue grew {revenue_growth:.1f}% year over year.")
    elif revenue_growth is not None and revenue_growth > 0:
        catalysts.append(f"Latest quarterly revenue growth remained positive at {revenue_growth:.1f}% year over year.")
    if operating_margin is not None and operating_margin >= 20:
        catalysts.append(f"Latest-quarter operating margin is strong at {operating_margin:.1f}%.")
    if fcf_margin is not None and fcf_margin >= 10:
        catalysts.append(f"Trailing free-cash-flow margin is {fcf_margin:.1f}%.")
    if net_cash is not None and net_cash > 0:
        catalysts.append("Cash and short-term investments exceed reported current and long-term debt.")
    if technical.get("status") == "INSIDE PREFERRED BAND":
        catalysts.append("Price is inside the model's preferred technical accumulation band.")

    if revenue_growth is not None and revenue_growth < 0:
        risks.append(f"Latest quarterly revenue contracted {abs(revenue_growth):.1f}% year over year.")
    if fcf_margin is not None and fcf_margin < 0:
        risks.append("Trailing free cash flow is negative relative to the latest four-quarter revenue base.")
    if negative_equity:
        risks.append("Reported shareholders' equity is negative; debt-to-equity is not interpreted as cheap or strong.")
    if earnings_distorted:
        risks.append("Large non-operating items make EPS, ROE and simple earnings multiples less representative of operations.")
    elif not earnings_reliable:
        risks.append("Non-operating detail is incomplete, so EPS, ROE and P/E are excluded from scoring.")
    if technical.get("status") == "ABOVE PREFERRED BAND":
        risks.append("Price is above the preferred technical band, increasing chase risk.")
    if not all((freshness["price_fresh"], freshness["ratios_fresh"], freshness["financials_fresh"])):
        risks.append(freshness["note"])
    return catalysts[:5] or ["No quantitative catalyst cleared the current rules."], risks[:6] or ["Every single-stock investment carries business, valuation and market risk."]


def assemble_company(
    ticker: str,
    overview: dict[str, Any],
    ratios: dict[str, Any],
    income_rows: list[dict[str, Any]],
    balance_rows: list[dict[str, Any]],
    cash_rows: list[dict[str, Any]],
    price_rows: list[dict[str, Any]],
    snapshot: dict[str, Any] | None,
    news: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    theme: str | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    as_of = now.date()
    income = _unique_periods(income_rows)
    balances = _unique_periods(balance_rows)
    cash_flows = _unique_periods(cash_rows)
    latest_income = income[0] if income else {}
    prior_income = _year_ago(income, latest_income) if latest_income else None
    latest_balance = balances[0] if balances else {}
    latest_cash = cash_flows[0] if cash_flows else {}
    contiguous = _four_contiguous_quarters(income)
    technical = _technical_snapshot(price_rows, snapshot=snapshot)

    revenue = _safe_float(latest_income.get("revenue"))
    prior_revenue = _safe_float((prior_income or {}).get("revenue"))
    eps = _safe_float(latest_income.get("diluted_earnings_per_share"))
    prior_eps = _safe_float((prior_income or {}).get("diluted_earnings_per_share"))
    gross_profit = _safe_float(latest_income.get("gross_profit"))
    operating_income = _safe_float(latest_income.get("operating_income"))
    ttm_revenue = _sum_if_complete(contiguous, "revenue")
    ttm_operating_income = _sum_if_complete(contiguous, "operating_income")
    latest_fcf = _free_cash_flow(latest_cash)
    cash_period = _parse_date(latest_cash.get("period_end"))
    income_period = _parse_date(latest_income.get("period_end"))
    cash_aligned = bool(cash_period and income_period and abs((cash_period - income_period).days) <= 45)

    non_operating_values: list[float] = []
    non_operating_complete = bool(contiguous)
    for row in contiguous or []:
        total_other = _safe_float(row.get("total_other_income_expense"))
        value = total_other if total_other is not None else _safe_float(row.get("other_income_expense"))
        if value is None:
            non_operating_complete = False
            break
        non_operating_values.append(value)
    ttm_non_operating = sum(non_operating_values) if non_operating_complete else None
    non_operating_share = _ratio(ttm_non_operating, ttm_operating_income)
    earnings_distorted = non_operating_share is not None and abs(non_operating_share) >= 0.25
    earnings_reliable = non_operating_share is not None and not earnings_distorted

    cash_total = _sum_present(latest_balance, ("cash_and_equivalents", "short_term_investments"))
    debt_total = _sum_present(latest_balance, ("debt_current", "long_term_debt_and_capital_lease_obligations"))
    net_cash = cash_total - debt_total if cash_total is not None and debt_total is not None else None
    total_equity = _safe_float(latest_balance.get("total_equity_attributable_to_parent"))
    if total_equity is None:
        total_equity = _safe_float(latest_balance.get("total_equity"))
    debt_to_equity = _safe_float(ratios.get("debt_to_equity"))
    negative_equity = bool((total_equity is not None and total_equity < 0) or (debt_to_equity is not None and debt_to_equity < 0))
    market_cap = _safe_float(ratios.get("market_cap")) or _safe_float(overview.get("market_cap"))
    fcf_for_yield = latest_fcf if latest_fcf is not None and cash_aligned else _safe_float(ratios.get("free_cash_flow"))
    roe_ratio = _safe_float(ratios.get("return_on_equity"))

    metrics: dict[str, float | None] = {
        "revenue_growth_yoy": _pct(revenue, prior_revenue),
        "eps_growth_yoy": _pct(eps, prior_eps) if prior_eps is not None and prior_eps > 0 else None,
        "gross_margin": (_ratio(gross_profit, revenue) * 100) if gross_profit is not None and revenue not in (None, 0) else None,
        "operating_margin": (_ratio(operating_income, revenue) * 100) if operating_income is not None and revenue not in (None, 0) else None,
        "fcf_margin": (_ratio(latest_fcf, ttm_revenue) * 100) if contiguous and cash_aligned and latest_fcf is not None and ttm_revenue not in (None, 0) else None,
        "roe": None if negative_equity or roe_ratio is None else roe_ratio * 100,
        "net_cash": net_cash,
        "current_ratio": _safe_float(ratios.get("current")),
        "debt_to_equity": debt_to_equity,
        "pe": _safe_float(ratios.get("price_to_earnings")),
        "ps": _safe_float(ratios.get("price_to_sales")),
        "pfcf": _safe_float(ratios.get("price_to_free_cash_flow")),
        "ev_ebitda": _safe_float(ratios.get("ev_to_ebitda")),
        "fcf_yield": (fcf_for_yield / market_cap * 100) if fcf_for_yield is not None and market_cap not in (None, 0) else None,
    }
    metrics = {key: _round(value, 1 if key not in {"net_cash", "debt_to_equity", "pe", "ps", "pfcf", "ev_ebitda", "fcf_yield"} else 2) for key, value in metrics.items()}
    if metrics["net_cash"] is not None:
        metrics["net_cash"] = _round(metrics["net_cash"], 0)

    strength, _strength_coverage = _strength_score(net_cash, market_cap, metrics["current_ratio"], debt_to_equity, negative_equity)
    business, business_coverage = _business_score(metrics, strength, earnings_reliable)
    valuation, valuation_coverage = _valuation_score(metrics, earnings_reliable)
    technical_score = _safe_float(technical.get("score"))
    completeness = _data_completeness(metrics, technical_score, overview)
    model_status, model_note = _model_status(ticker, overview)
    freshness = _freshness(
        technical,
        ratios,
        latest_income,
        latest_balance,
        latest_cash,
        contiguous is not None,
        as_of,
    )

    stale = not all((freshness["price_fresh"], freshness["ratios_fresh"], freshness["financials_fresh"]))
    required_complete = bool(
        contiguous
        and cash_aligned
        and metrics["revenue_growth_yoy"] is not None
        and metrics["operating_margin"] is not None
        and metrics["fcf_margin"] is not None
        and metrics["net_cash"] is not None
        and business_coverage >= 0.65
        and valuation_coverage >= 0.45
        and technical_score is not None
        and completeness >= 65
    )
    if model_status in {"unsupported_foreign", "unsupported_sector"}:
        data_status = model_status
    elif model_status != "supported" or not required_complete:
        data_status = "insufficient"
    elif stale:
        data_status = "stale"
    else:
        data_status = "ready"

    overall: float | None = None
    if data_status == "ready" and None not in (business, valuation, technical_score):
        overall = 0.50 * float(business) + 0.25 * float(valuation) + 0.25 * float(technical_score)
    action, tone, conclusion = _action_for(data_status, business, valuation, technical_score, str(technical.get("status") or "UNAVAILABLE"))
    catalysts, risks = _build_reasons(
        ticker,
        metrics,
        technical,
        negative_equity,
        earnings_reliable,
        earnings_distorted,
        freshness,
    )

    source_cik = str(overview.get("cik") or ratios.get("cik") or "").lstrip("0")
    filing_links: list[dict[str, str]] = []
    if source_cik.isdigit():
        filing_links.append({
            "label": "SEC company filings",
            "url": f"https://www.sec.gov/edgar/browse/?CIK={source_cik}&owner=exclude&action=getcompany",
        })

    public_technical = {key: value for key, value in technical.items() if key != "score"}
    return {
        "ticker": ticker,
        "name": overview.get("name") or ticker,
        "sector": overview.get("sic_description") or "Sector unavailable",
        "theme": theme or "Fundamental research candidate",
        "action": action,
        "tone": tone,
        "conclusion": conclusion,
        "scores": {
            "overall": _round(overall, 1),
            "business": _round(business, 1),
            "valuation": _round(valuation, 1),
            "technical": _round(technical_score, 1),
            "data_completeness": _round(completeness, 0),
        },
        "technical": public_technical,
        "metrics": metrics,
        "catalysts": catalysts,
        "risks": risks,
        "news": news,
        "filing_links": filing_links,
        "data_status": data_status,
        "freshness": freshness,
        "model_note": model_note,
    }


def _daily_history(ticker: str, fetcher: JsonFetcher, now: datetime) -> list[dict[str, Any]]:
    end = now.date()
    start = end - timedelta(days=470)
    encoded = urllib.parse.quote(ticker, safe=".")
    payload = fetcher(
        f"/v2/aggs/ticker/{encoded}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
        35,
    )
    return [
        row for row in payload.get("results") or []
        if _safe_float(row.get("c")) is not None and row.get("t") is not None
    ]


def _company_overview(ticker: str, fetcher: JsonFetcher) -> dict[str, Any]:
    encoded = urllib.parse.quote(ticker, safe=".")
    payload = fetcher(f"/v3/reference/tickers/{encoded}", {}, 25)
    result = payload.get("results") or {}
    return result if isinstance(result, dict) else {}


def _company_news(ticker: str, fetcher: JsonFetcher, now: datetime) -> list[dict[str, Any]]:
    since = (now - timedelta(days=30)).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = fetcher(
        "/v2/reference/news",
        {"ticker": ticker, "published_utc.gte": since, "sort": "published_utc", "order": "desc", "limit": 8},
        25,
    )
    output: list[dict[str, Any]] = []
    for article in payload.get("results") or []:
        url = _safe_url(article.get("article_url"))
        if not url:
            continue
        insight = next((row for row in article.get("insights") or [] if row.get("ticker") == ticker), {})
        output.append({
            "title": str(article.get("title") or "Untitled article")[:240],
            "url": url,
            "publisher": str((article.get("publisher") or {}).get("name") or "Unknown publisher")[:100],
            "published_utc": article.get("published_utc"),
            "sentiment": str(insight.get("sentiment") or "neutral").lower(),
        })
    return output[:5]


def _sanitized_error(label: str, exc: Exception, *secrets: str) -> str:
    message = str(exc)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return f"{label}: {message[:180]}"


def build_fundamentals_feed(
    *,
    output_path: Path = OUTPUT_PATH,
    api_key: str | None = None,
    fetcher: JsonFetcher | None = None,
    now: datetime | None = None,
    minimum_ranked: int = 0,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    api_key = get_api_key() if api_key is None else api_key.strip()
    tickers = [item["ticker"] for item in RESEARCH_UNIVERSE]
    themes = {item["ticker"]: item["theme"] for item in RESEARCH_UNIVERSE}
    errors: list[str] = []
    if fetcher is None:
        if api_key:
            fetcher = _default_fetcher(api_key)
        else:
            errors.append("MASSIVE_API_KEY is missing; the scheduled fundamentals feed is unavailable.")

            def empty_fetcher(_path: str, _params: dict[str, Any], _timeout: int) -> dict[str, Any]:
                return {}

            fetcher = empty_fetcher

    joined = ",".join(tickers)
    batch_jobs: dict[str, Callable[[], Any]] = {
        "ratios": lambda: fetcher("/stocks/financials/v1/ratios", {"ticker.any_of": joined, "limit": 100}, 35).get("results") or [],
        "income": lambda: fetcher("/stocks/financials/v1/income-statements", {"tickers.any_of": joined, "timeframe": "quarterly", "sort": "period_end.desc", "limit": 500}, 40).get("results") or [],
        "balance": lambda: fetcher("/stocks/financials/v1/balance-sheets", {"tickers.any_of": joined, "timeframe": "quarterly", "sort": "period_end.desc", "limit": 250}, 40).get("results") or [],
        "cash": lambda: fetcher("/stocks/financials/v1/cash-flow-statements", {"tickers.any_of": joined, "timeframe": "trailing_twelve_months", "sort": "period_end.desc", "limit": 250}, 40).get("results") or [],
        "snapshot": lambda: fetcher("/v3/snapshot", {"ticker.any_of": joined, "limit": 100}, 30).get("results") or [],
    }
    batch: dict[str, Any] = {name: [] for name in batch_jobs}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(job): name for name, job in batch_jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                batch[name] = future.result()
            except Exception as exc:
                errors.append(_sanitized_error(name, exc, api_key))

    ratios_by_ticker = {row.get("ticker"): row for row in batch["ratios"] if row.get("ticker") in tickers}
    income_by_ticker = _group_statement_rows(batch["income"], tickers)
    balance_by_ticker = _group_statement_rows(batch["balance"], tickers)
    cash_by_ticker = _group_statement_rows(batch["cash"], tickers)
    snapshot_by_ticker = {row.get("ticker"): row for row in batch["snapshot"] if row.get("ticker") in tickers}
    overviews: dict[str, dict[str, Any]] = {}
    histories: dict[str, list[dict[str, Any]]] = {}
    news_by_ticker: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker in tickers}

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs: dict[Any, tuple[str, str]] = {}
        for ticker in tickers:
            jobs[pool.submit(_company_overview, ticker, fetcher)] = (ticker, "overview")
            jobs[pool.submit(_daily_history, ticker, fetcher, now)] = (ticker, "prices")
            jobs[pool.submit(_company_news, ticker, fetcher, now)] = (ticker, "news")
        for future in as_completed(jobs):
            ticker, kind = jobs[future]
            try:
                result = future.result()
                if kind == "overview":
                    overviews[ticker] = result
                elif kind == "prices":
                    histories[ticker] = result
                else:
                    news_by_ticker[ticker] = result
            except Exception as exc:
                errors.append(_sanitized_error(f"{ticker} {kind}", exc, api_key))

    rankings: list[dict[str, Any]] = []
    for ticker in tickers:
        rankings.append(assemble_company(
            ticker,
            overviews.get(ticker, {}),
            ratios_by_ticker.get(ticker, {}),
            income_by_ticker.get(ticker, []),
            balance_by_ticker.get(ticker, []),
            cash_by_ticker.get(ticker, []),
            histories.get(ticker, []),
            snapshot_by_ticker.get(ticker),
            news_by_ticker.get(ticker, []),
            now=now,
            theme=themes[ticker],
        ))
    rankings.sort(
        key=lambda item: (
            _safe_float(item["scores"].get("overall")) is not None,
            _safe_float(item["scores"].get("overall")) or -1,
            _safe_float(item["scores"].get("data_completeness")) or -1,
        ),
        reverse=True,
    )
    for rank, item in enumerate(rankings, start=1):
        item["rank"] = rank

    ranked = [item for item in rankings if item["scores"]["overall"] is not None]
    actionable = [item for item in rankings if item["action"] in {"ATTRACTIVE NOW", "START SMALL"}]
    watch = [item for item in rankings if item["action"].startswith("WATCH")]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "methodology": {
            "universe": tickers,
            "score_weights": {"business": 50, "valuation": 25, "technical": 25},
            "requirements": [
                "Recent price, ratios and quarterly financial statements",
                "Four complete, contiguous quarters for TTM-derived margins",
                "At least 65% business evidence and 45% valuation evidence",
                "Supported U.S. operating-company accounting model",
            ],
            "limitations": [
                "Fixed score anchors are subjective and are not sector-normalized or trained on future returns.",
                "Foreign issuers, ADRs, banks, insurers, brokers and REITs are withheld pending dedicated models.",
                "Prices are scheduled snapshots and can be delayed; preferred bands are technical references, not fair value.",
                "The tracked list is curated and is not an exhaustive whole-market screen.",
            ],
            "source": "Massive market and financial data, with SEC filing links",
        },
        "errors": errors[:30],
        "summary": {
            "tracked": len(rankings),
            "ranked": len(ranked),
            "actionable": len(actionable),
            "watch": len(watch),
            "unavailable": len(rankings) - len(ranked),
            "top_ticker": ranked[0]["ticker"] if ranked else None,
            "top_score": ranked[0]["scores"]["overall"] if ranked else None,
        },
        "rankings": rankings,
        "items": {item["ticker"]: item for item in rankings},
    }
    required_ranked = max(0, int(minimum_ranked))
    if len(ranked) < required_ranked:
        detail = errors[0] if errors else "required data did not pass the completeness and freshness gates"
        raise RuntimeError(
            f"Only {len(ranked)} companies ranked; at least {required_ranked} are required. {detail}"
        )
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> int:
    payload = build_fundamentals_feed(minimum_ranked=6)
    print(f"Built {OUTPUT_PATH} with {payload['summary']['ranked']} ranked companies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
