from __future__ import annotations

import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from universe_lists import UNIVERSE_CONFIG


MIN_PRICE = 10.0
MIN_LATEST_DOLLAR_VOLUME = 50_000_000
MIN_AVG_DOLLAR_VOLUME = 75_000_000
LOOKBACK_DAYS = 220
HISTORY_DAYS = 520
OUTPUT_FILENAME = "swing_trading_mag7_report.html"
PAGES_FILENAME = "index.html"
MAX_MARKET_TICKERS = 500
MARKET_CANDIDATE_BUFFER = 750
_HISTORY_CACHE: dict[tuple[str, str, str], list[dict]] = {}


def load_local_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line or line.startswith("export "):
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def get_api_key() -> str:
    load_local_env(Path(__file__).with_name(".env"))
    api_key = os.getenv("MASSIVE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing MASSIVE_API_KEY in .env")
    return api_key


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_spy_dates(api_key: str) -> list[str]:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=420)
    url = (
        "https://api.massive.com/v2/aggs/ticker/SPY/range/1/day/"
        f"{start_date:%Y-%m-%d}/{end_date:%Y-%m-%d}?adjusted=true&sort=asc&limit=5000&apiKey={urllib.parse.quote(api_key)}"
    )
    payload = fetch_json(url)
    dates = [datetime.fromtimestamp(row["t"] / 1000, UTC).strftime("%Y-%m-%d") for row in payload.get("results", [])]
    return dates[-HISTORY_DAYS:]


def fetch_reference_tickers(api_key: str) -> dict[str, dict]:
    metadata: dict[str, dict] = {}
    url = (
        "https://api.massive.com/v3/reference/tickers"
        f"?market=stocks&active=true&type=CS&limit=1000&sort=ticker&apiKey={urllib.parse.quote(api_key)}"
    )
    while url:
        payload = fetch_json(url)
        for row in payload.get("results", []):
            name = row.get("name", "")
            lowered = name.lower()
            if any(token in lowered for token in ["warrant", "right", "unit", "acquisition", "trust"]):
                continue
            metadata[row["ticker"]] = {"name": name, "exchange": row.get("primary_exchange", "")}
        next_url = payload.get("next_url")
        url = f"{next_url}&apiKey={urllib.parse.quote(api_key)}" if next_url else ""
    return metadata


def fetch_grouped_day(date_str: str, api_key: str) -> list[dict]:
    url = (
        "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/"
        f"{date_str}?adjusted=true&apiKey={urllib.parse.quote(api_key)}"
    )
    return fetch_json(url).get("results", [])


def fetch_history(ticker: str, start_date: str, end_date: str, api_key: str) -> list[dict]:
    cache_key = (ticker, start_date, end_date)
    if cache_key in _HISTORY_CACHE:
        return _HISTORY_CACHE[cache_key]
    url = (
        "https://api.massive.com/v2/aggs/ticker/"
        f"{urllib.parse.quote(ticker)}/range/1/day/{start_date}/{end_date}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={urllib.parse.quote(api_key)}"
    )
    results = fetch_json(url).get("results", [])
    _HISTORY_CACHE[cache_key] = results
    return results


def sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    seed = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    result = [seed]
    for value in values[period:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def rsi(values: list[float], period: int = 14) -> float:
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, period + 1):
        change = values[idx] - values[idx - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for idx in range(period + 1, len(values)):
        change = values[idx] - values[idx - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(values: list[float]) -> tuple[float, float]:
    ema12 = ema_series(values, 12)
    ema26 = ema_series(values, 26)
    offset = len(ema12) - len(ema26)
    line = [ema12[idx + offset] - ema26[idx] for idx in range(len(ema26))]
    signal = ema_series(line, 9)
    return line[-1], signal[-1]


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    ranges: list[float] = []
    for idx, close in enumerate(closes):
        if idx == 0:
            ranges.append(highs[idx] - lows[idx])
            continue
        prev_close = closes[idx - 1]
        ranges.append(max(highs[idx] - lows[idx], abs(highs[idx] - prev_close), abs(lows[idx] - prev_close)))
    value = sum(ranges[:period]) / period
    for current in ranges[period:]:
        value = ((value * (period - 1)) + current) / period
    return value


def pct_change(new: float, old: float) -> float:
    return 0.0 if old == 0 else ((new - old) / old) * 100


def hold_window(setup: str, atr_pct: float, rs_value: float) -> str:
    if setup == "Avoid":
        return "No swing setup"
    low, high = {"Breakout": (5, 12), "Pullback": (4, 10), "Trend Continuation": (7, 15)}.get(setup, (3, 8))
    if atr_pct > 5:
        high -= 2
    if rs_value > 5:
        high += 3
    if rs_value < 0:
        low = max(2, low - 1)
        high = max(low + 2, high - 2)
    return f"{low}-{high} trading days"


def max_hold_days(setup: str, atr_pct: float, rs_value: float) -> int:
    if setup == "Avoid":
        return 0
    _low, high = {"Breakout": (5, 12), "Pullback": (4, 10), "Trend Continuation": (7, 15)}.get(setup, (3, 8))
    if atr_pct > 5:
        high -= 2
    if rs_value > 5:
        high += 3
    if rs_value < 0:
        high = max(4, high - 2)
    return max(2, high)


def setup_outcome(signal: dict, future_history: list[dict]) -> str:
    for bar in future_history[: max_hold_days(signal["setup"], signal["atrPct"], signal["rsVsSpy20d"])]:
        high = float(bar["h"])
        low = float(bar["l"])
        if low <= signal["stopLoss"]:
            return "loss"
        if high >= signal["target1"]:
            return "win"
    return "neutral"


def enrich_signal_context(row: dict, history: list[dict], spy_closes: list[float], meta: dict) -> dict:
    signal_points: list[tuple[int, dict]] = []
    for idx in range(LOOKBACK_DAYS - 1, len(history)):
        date = datetime.fromtimestamp(history[idx]["t"] / 1000, UTC).strftime("%Y-%m-%d")
        signal = classify_stock(row["ticker"], meta, history[: idx + 1], spy_closes[: idx + 1], date)
        if signal and signal["goodForSwing"]:
            signal_points.append((idx, signal))

    current_setup = row["setup"]
    current_idx = len(history) - 1
    entry_idx = current_idx
    matching_points = {idx: signal for idx, signal in signal_points}
    if entry_idx in matching_points:
        while entry_idx in matching_points and matching_points[entry_idx]["setup"] == current_setup:
            entry_idx -= 1
        entry_idx += 1
    entry_close = float(history[entry_idx]["c"]) if entry_idx < len(history) else row["close"]
    first_seen = datetime.fromtimestamp(history[entry_idx]["t"] / 1000, UTC).strftime("%Y-%m-%d")
    days_in_list = current_idx - entry_idx + 1

    wins = losses = neutral = previous = 0
    last_event_idx = -999
    for idx, signal in signal_points:
        if idx >= entry_idx or signal["setup"] != current_setup:
            continue
        if idx <= last_event_idx + max_hold_days(signal["setup"], signal["atrPct"], signal["rsVsSpy20d"]):
            continue
        previous += 1
        last_event_idx = idx
        outcome = setup_outcome(signal, history[idx + 1 :])
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        else:
            neutral += 1

    decided = wins + losses
    confidence = round((wins / decided) * 100, 1) if decided else None
    row.update(
        {
            "firstSeenDate": first_seen,
            "daysInList": days_in_list,
            "entryChangePct": round(pct_change(row["close"], entry_close), 2),
            "similarSetups": previous,
            "setupWins": wins,
            "setupLosses": losses,
            "setupNeutral": neutral,
            "setupConfidence": confidence,
            "confidenceLabel": f"{confidence:.1f}%" if confidence is not None else "N/A",
        }
    )
    return row


def calculate_edge_score(row: dict) -> int:
    edge = 0
    score = row["score"]
    rs_value = row["rsVsSpy20d"]
    volume_ratio = row["volumeRatio"]
    rsi_value = row["rsi14"]
    atr_pct = row["atrPct"]
    setup = row["setup"]
    confidence = row.get("setupConfidence")
    prior_setups = row.get("similarSetups", 0)

    if score >= 110:
        edge += 18
    elif score >= 100:
        edge += 22
    elif score >= 90:
        edge += 6
    else:
        edge -= 8

    if rs_value < 0:
        edge -= 25
    elif rs_value < 3:
        edge -= 10
    elif rs_value < 7:
        edge += 4
    elif rs_value < 15:
        edge += 18
    elif rs_value < 30:
        edge += 24
    else:
        edge += 28

    if volume_ratio < 0.8:
        edge -= 15
    elif volume_ratio < 1.0:
        edge -= 8
    elif volume_ratio < 1.3:
        edge += 4
    elif volume_ratio < 1.8:
        edge += 8
    else:
        edge += 14

    if 65 <= rsi_value <= 68:
        edge += 18
    elif 60 <= rsi_value < 65:
        edge += 8
    elif 68 < rsi_value <= 72:
        edge += 4
    elif 55 <= rsi_value < 60:
        edge -= 4
    else:
        edge -= 10

    if setup == "Trend Continuation":
        edge += 12
    elif setup == "Pullback":
        edge += 10
    elif setup == "Breakout":
        edge -= 5
        if rs_value > 15 and volume_ratio >= 1.3 and 65 <= rsi_value <= 68:
            edge += 16
        if rs_value < 7:
            edge -= 10

    if 4.5 <= atr_pct < 6:
        edge -= 10
    elif atr_pct < 2:
        edge -= 6
    elif 6 <= atr_pct <= 8 and rs_value > 15:
        edge += 6
    elif atr_pct > 8:
        edge -= 8

    if confidence is not None and prior_setups >= 2:
        if confidence >= 70:
            edge += 14
        elif confidence >= 55:
            edge += 6
        elif confidence < 40:
            edge -= 12

    if row.get("daysInList", 1) > 5:
        edge -= 8

    return edge


def add_edge_score(row: dict) -> dict:
    row["edgeScore"] = calculate_edge_score(row)
    return row


def classify_stock(ticker: str, meta: dict, history: list[dict], spy_closes: list[float], latest_date: str) -> dict | None:
    if len(history) < LOOKBACK_DAYS or len(spy_closes) < LOOKBACK_DAYS:
        return None
    closes = [float(bar["c"]) for bar in history]
    highs = [float(bar["h"]) for bar in history]
    lows = [float(bar["l"]) for bar in history]
    volumes = [float(bar["v"]) for bar in history]
    latest_close = closes[-1]
    avg_dollar_volume = statistics.mean(c * v for c, v in zip(closes[-20:], volumes[-20:]))
    if latest_close < MIN_PRICE or avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
        return None

    ema20 = ema_series(closes, 20)[-1]
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    rsi14 = rsi(closes, 14)
    macd_line, macd_signal = macd(closes)
    atr14 = atr(highs, lows, closes, 14)
    atr_pct = (atr14 / latest_close) * 100 if latest_close else 0.0
    avg_volume20 = statistics.mean(volumes[-20:])
    volume_ratio = volumes[-1] / avg_volume20 if avg_volume20 else 0.0
    prev_20_high = max(highs[-21:-1])
    prev_20_low = min(lows[-21:-1])
    rs_vs_spy = pct_change(closes[-1], closes[-21]) - pct_change(spy_closes[-1], spy_closes[-21])

    trend_aligned = ema20 > sma50 > sma200
    above50 = latest_close > sma50
    above200 = latest_close > sma200
    near_breakout = latest_close >= prev_20_high * 0.985
    pullback_hold = latest_close > ema20 and min(closes[-5:]) <= ema20 * 1.01
    bullish_rsi = 50 <= rsi14 <= 68
    extended_rsi = rsi14 > 72
    bullish_macd = macd_line > macd_signal
    healthy_volume = volume_ratio >= 1.0
    strong_rs = rs_vs_spy > 0

    score = 0
    score += 15 if above50 else 0
    score += 10 if above200 else 0
    score += 20 if trend_aligned else 0
    score += 15 if bullish_rsi else 0
    score += 10 if bullish_macd else 0
    score += 10 if healthy_volume else 0
    score += 10 if near_breakout else 0
    score += 10 if pullback_hold else 0
    score += 10 if strong_rs else 0
    score += 5 if atr_pct <= 4.5 else 0
    score -= 10 if extended_rsi else 0
    score -= 15 if latest_close < prev_20_low else 0

    setup = "Watch"
    if score >= 75 and near_breakout:
        setup = "Breakout"
    elif score >= 65 and pullback_hold:
        setup = "Pullback"
    elif score >= 55:
        setup = "Trend Continuation"
    elif latest_close < sma50 or latest_close < sma200:
        setup = "Avoid"

    notes = []
    if trend_aligned:
        notes.append("20 EMA > 50 SMA > 200 SMA")
    if near_breakout:
        notes.append("near 20-day high")
    if pullback_hold:
        notes.append("held 20 EMA on pullback")
    if bullish_macd:
        notes.append("MACD above signal")
    if bullish_rsi:
        notes.append("RSI in bullish zone")
    if healthy_volume:
        notes.append("volume above 20-day average")
    if strong_rs:
        notes.append("beating SPY over 20 days")
    if extended_rsi:
        notes.append("RSI extended")

    return {
        "ticker": ticker,
        "name": meta["name"],
        "exchange": meta["exchange"],
        "date": latest_date,
        "setup": setup,
        "score": score,
        "close": round(latest_close, 2),
        "ema20": round(ema20, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "rsi14": round(rsi14, 1),
        "macd": round(macd_line, 2),
        "macdSignal": round(macd_signal, 2),
        "atrPct": round(atr_pct, 2),
        "volumeRatio": round(volume_ratio, 2),
        "rsVsSpy20d": round(rs_vs_spy, 2),
        "stopLoss": round(max(ema20 - (0.5 * atr14), latest_close - (1.5 * atr14)), 2),
        "target1": round(latest_close + (1.5 * atr14), 2),
        "target2": round(latest_close + (3.0 * atr14), 2),
        "holdWindow": hold_window(setup, atr_pct, rs_vs_spy),
        "goodForSwing": setup != "Avoid" and score >= 55,
        "notes": "; ".join(notes[:6]),
    }


def build_html(rows: list[dict], generated_at: datetime, coverage_note: str, page_title: str, include_edge: bool = False) -> str:
    mode_note = (
        " Edge Score mode applies the learned filters from the historical top-5 test."
        if include_edge
        else ""
    )
    edge_control = (
        '<div><label for="minEdge">Min Edge Score</label><input id="minEdge" type="number" value="45" min="-100" max="120" step="5"></div>'
        if include_edge
        else ""
    )
    sort_options = (
        '<option value="edgeScore">Edge Score</option><option value="score">Technical Score</option>'
        if include_edge
        else '<option value="score">Score</option>'
    )
    edge_header = '<th data-sort="edgeScore">Edge Score</th>' if include_edge else ""
    edge_cell = '<td><strong>${row.edgeScore ?? 0}</strong></td>' if include_edge else ""
    initial_sort = "edgeScore" if include_edge else "score"
    min_score_default = 75 if include_edge else 55
    max_atr_default = 8 if include_edge else 6
    edge_ref = '      minEdge: document.getElementById("minEdge"),\n' if include_edge else ""
    edge_value = '      const minEdgeValue = Number(els.minEdge.value || -999);\n' if include_edge else ""
    edge_filter = " && (row.edgeScore ?? 0) >= minEdgeValue" if include_edge else ""
    filter_inputs = (
        "[els.search, els.setupFilter, els.qualityFilter, els.minScore, els.minEdge, els.minConfidence, els.maxDaysInList, els.maxAtr]"
        if include_edge
        else "[els.search, els.setupFilter, els.qualityFilter, els.minScore, els.minConfidence, els.maxDaysInList, els.maxAtr]"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <style>
    :root {{ --bg:#f3efe4; --panel:rgba(255,252,246,.92); --ink:#1e2f39; --muted:#60717a; --line:#d8cfbf; --green:#0b7a68; --amber:#bd7c2f; --red:#a3423f; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); font-family:Georgia, "Times New Roman", serif; background:radial-gradient(circle at 10% 0%, rgba(11,122,104,.12), transparent 28%), radial-gradient(circle at 90% 10%, rgba(189,124,47,.12), transparent 24%), linear-gradient(180deg,#f5f0e5 0%,var(--bg) 100%); }}
    .wrap {{ max-width:1440px; margin:0 auto; padding:28px 18px 42px; }}
    .hero {{ background:linear-gradient(135deg, rgba(15,56,64,.98), rgba(11,122,104,.92)); color:#fffaf2; border-radius:24px; padding:30px; box-shadow:0 20px 45px rgba(30,47,57,.18); }}
    .hero h1 {{ margin:0 0 10px; font-size:clamp(2rem,4vw,3.5rem); }}
    .hero p {{ margin:0; line-height:1.55; max-width:980px; color:rgba(255,250,242,.88); }}
    .hero-tools {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:18px; align-items:center; }}
    .stamp {{ display:inline-flex; align-items:center; gap:8px; padding:10px 14px; border-radius:999px; background:rgba(255,250,242,.14); color:#fffaf2; font-size:.95rem; }}
    .refresh-btn {{ appearance:none; border:none; border-radius:999px; padding:10px 16px; background:#fffaf2; color:#113840; font-weight:700; cursor:pointer; }}
    .metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; margin-top:20px; }}
    .metric,.controls,.table-wrap {{ background:var(--panel); border:1px solid var(--line); border-radius:20px; box-shadow:0 12px 30px rgba(30,47,57,.08); }}
    .metric {{ padding:18px; }}
    .label {{ color:var(--muted); text-transform:uppercase; letter-spacing:.08em; font-size:.78rem; }}
    .value {{ font-size:1.8rem; margin-top:8px; }}
    .controls {{ margin-top:20px; padding:18px; }}
    .control-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }}
    label {{ font-size:.84rem; color:var(--muted); display:block; margin-bottom:6px; text-transform:uppercase; letter-spacing:.07em; }}
    input,select {{ width:100%; padding:11px 12px; border-radius:12px; border:1px solid var(--line); background:#fffdf8; color:var(--ink); font-size:.96rem; }}
    .table-wrap {{ margin-top:20px; overflow:hidden; }}
    .table-scroll {{ overflow:auto; max-height:70vh; }}
    table {{ width:100%; border-collapse:collapse; min-width:1400px; }}
    th,td {{ padding:11px 10px; border-bottom:1px solid var(--line); text-align:left; font-size:.93rem; vertical-align:top; }}
    th {{ position:sticky; top:0; background:#ece4d3; cursor:pointer; z-index:1; }}
    tbody tr:hover {{ background:rgba(11,122,104,.06); }}
    .tag {{ display:inline-block; padding:4px 10px; border-radius:999px; font-size:.78rem; font-weight:700; }}
    .Breakout {{ background:rgba(11,122,104,.15); color:var(--green); }}
    .Pullback {{ background:rgba(189,124,47,.16); color:#8a561a; }}
    .Trend {{ background:rgba(109,127,137,.16); color:#43545e; }}
    .Avoid {{ background:rgba(163,66,63,.15); color:var(--red); }}
    .yes {{ color:var(--green); font-weight:700; }}
    .no {{ color:var(--red); font-weight:700; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>{page_title}</h1>
      <p>Daily swing-trade scanner using your Massive data. It ranks names by 20 EMA, 50 SMA, 200 SMA, RSI, MACD, ATR, volume ratio, and relative strength vs SPY.{mode_note} Generated {generated_at.strftime("%Y-%m-%d %H:%M")}.</p>
      <div class="hero-tools">
        <div class="stamp">Last Updated: {generated_at.strftime("%Y-%m-%d %H:%M")}</div>
        <button class="refresh-btn" type="button" onclick="window.location.reload()">Refresh View</button>
      </div>
    </section>
    <section class="metrics">
      <article class="metric"><div class="label">Coverage</div><div class="value" id="coverageCount">-</div><div>{coverage_note}</div></article>
      <article class="metric"><div class="label">Good Swing Setups</div><div class="value" id="goodCount">-</div><div>Score 55+ and not marked Avoid.</div></article>
      <article class="metric"><div class="label">Breakouts</div><div class="value" id="breakoutCount">-</div><div>Near 20-day highs with strong alignment.</div></article>
      <article class="metric"><div class="label">Pullbacks</div><div class="value" id="pullbackCount">-</div><div>Held the 20 EMA inside a larger uptrend.</div></article>
    </section>
    <section class="controls">
      <div class="control-grid">
        <div><label for="search">Search</label><input id="search" type="text" placeholder="Ticker or company name"></div>
        <div><label for="setupFilter">Setup</label><select id="setupFilter"><option value="All">All</option><option value="Breakout">Breakout</option><option value="Pullback">Pullback</option><option value="Trend Continuation">Trend Continuation</option><option value="Avoid">Avoid</option></select></div>
        <div><label for="qualityFilter">Swing Quality</label><select id="qualityFilter"><option value="All">All</option><option value="Yes">Good for swing trade</option><option value="No">Not good right now</option></select></div>
        <div><label for="minScore">Minimum Score</label><input id="minScore" type="number" value="{min_score_default}" min="0" max="100" step="5"></div>
        {edge_control}
        <div><label for="minConfidence">Min Confidence %</label><input id="minConfidence" type="number" value="0" min="0" max="100" step="5"></div>
        <div><label for="maxDaysInList">Max Days In List</label><input id="maxDaysInList" type="number" value="99" min="1" max="260" step="1"></div>
        <div><label for="maxAtr">Max ATR %</label><input id="maxAtr" type="number" value="{max_atr_default}" min="1" max="20" step="0.5"></div>
        <div><label for="sortBy">Sort</label><select id="sortBy">{sort_options}<option value="setupConfidence">Backtest Win Rate</option><option value="similarSetups">Prior Setups</option><option value="entryChangePct">% Since Entry</option><option value="daysInList">Days In List</option><option value="rsVsSpy20d">Relative Strength</option><option value="volumeRatio">Volume Ratio</option><option value="atrPct">ATR %</option><option value="close">Price</option></select></div>
      </div>
    </section>
    <section class="table-wrap"><div class="table-scroll"><table><thead><tr><th data-sort="ticker">Ticker</th><th data-sort="name">Name</th><th data-sort="setup">Setup</th><th data-sort="goodForSwing">Good For Swing?</th>{edge_header}<th data-sort="firstSeenDate">First In List</th><th data-sort="daysInList">Days In List</th><th data-sort="entryChangePct">% Since Entry</th><th data-sort="setupConfidence">Backtest Win Rate</th><th data-sort="similarSetups">Prior Setups</th><th data-sort="setupWins">Wins</th><th data-sort="setupLosses">Losses</th><th data-sort="setupNeutral">Neutral</th><th data-sort="holdWindow">Hold Window</th><th data-sort="score">Tech Score</th><th data-sort="close">Close</th><th data-sort="ema20">20 EMA</th><th data-sort="sma50">50 SMA</th><th data-sort="sma200">200 SMA</th><th data-sort="rsi14">RSI</th><th data-sort="macd">MACD</th><th data-sort="atrPct">ATR %</th><th data-sort="volumeRatio">Vol Ratio</th><th data-sort="rsVsSpy20d">RS vs SPY</th><th data-sort="stopLoss">Stop</th><th data-sort="target1">T1</th><th data-sort="target2">T2</th><th data-sort="notes">Notes</th></tr></thead><tbody id="reportBody"></tbody></table></div></section>
  </div>
  <script>
    const rows = {json.dumps(rows)};
    const state = {{ sortBy: "{initial_sort}", sortDir: "desc" }};
    const els = {{
      body: document.getElementById("reportBody"),
      search: document.getElementById("search"),
      setupFilter: document.getElementById("setupFilter"),
      qualityFilter: document.getElementById("qualityFilter"),
      minScore: document.getElementById("minScore"),
{edge_ref.rstrip()}
      minConfidence: document.getElementById("minConfidence"),
      maxDaysInList: document.getElementById("maxDaysInList"),
      maxAtr: document.getElementById("maxAtr"),
      sortBy: document.getElementById("sortBy"),
      coverageCount: document.getElementById("coverageCount"),
      goodCount: document.getElementById("goodCount"),
      breakoutCount: document.getElementById("breakoutCount"),
      pullbackCount: document.getElementById("pullbackCount")
    }};
    function compare(a,b,key) {{ const av=a[key], bv=b[key]; if (av === null || av === undefined) return -1; if (bv === null || bv === undefined) return 1; if (typeof av === "number" && typeof bv === "number") return av-bv; return String(av).localeCompare(String(bv)); }}
    function setupClass(setup) {{ return setup === "Trend Continuation" ? "Trend" : setup; }}
    function render(filtered) {{
      els.body.innerHTML = filtered.map(row => `<tr><td><strong>${{row.ticker}}</strong><br><span style="color:var(--muted)">${{row.exchange}}</span></td><td>${{row.name}}</td><td><span class="tag ${{setupClass(row.setup)}}">${{row.setup}}</span></td><td class="${{row.goodForSwing ? "yes" : "no"}}">${{row.goodForSwing ? "Yes" : "No"}}</td>{edge_cell}<td>${{row.firstSeenDate || "-"}}</td><td>${{row.daysInList || "-"}}</td><td>${{Number(row.entryChangePct || 0).toFixed(2)}}%</td><td>${{row.confidenceLabel || "N/A"}}</td><td>${{row.similarSetups || 0}}</td><td class="yes">${{row.setupWins || 0}}</td><td class="no">${{row.setupLosses || 0}}</td><td>${{row.setupNeutral || 0}}</td><td>${{row.holdWindow}}</td><td>${{row.score}}</td><td>${{row.close.toFixed(2)}}</td><td>${{row.ema20.toFixed(2)}}</td><td>${{row.sma50.toFixed(2)}}</td><td>${{row.sma200.toFixed(2)}}</td><td>${{row.rsi14.toFixed(1)}}</td><td>${{row.macd.toFixed(2)}} / ${{row.macdSignal.toFixed(2)}}</td><td>${{row.atrPct.toFixed(2)}}%</td><td>${{row.volumeRatio.toFixed(2)}}x</td><td>${{row.rsVsSpy20d.toFixed(2)}}%</td><td>${{row.stopLoss.toFixed(2)}}</td><td>${{row.target1.toFixed(2)}}</td><td>${{row.target2.toFixed(2)}}</td><td>${{row.notes}}</td></tr>`).join("");
      els.coverageCount.textContent = `${{filtered.length}}`;
      els.goodCount.textContent = `${{filtered.filter(row => row.goodForSwing).length}}`;
      els.breakoutCount.textContent = `${{filtered.filter(row => row.setup === "Breakout").length}}`;
      els.pullbackCount.textContent = `${{filtered.filter(row => row.setup === "Pullback").length}}`;
    }}
    function applyFilters() {{
      const term = els.search.value.trim().toLowerCase();
      const setup = els.setupFilter.value;
      const quality = els.qualityFilter.value;
      const minScoreValue = Number(els.minScore.value || 0);
{edge_value.rstrip()}
      const minConfidenceValue = Number(els.minConfidence.value || 0);
      const maxDaysValue = Number(els.maxDaysInList.value || 999);
      const maxAtrValue = Number(els.maxAtr.value || 99);
      const filtered = rows.filter(row => (!term || row.ticker.toLowerCase().includes(term) || row.name.toLowerCase().includes(term)) && (setup === "All" || row.setup === setup) && !(quality === "Yes" && !row.goodForSwing) && !(quality === "No" && row.goodForSwing) && row.score >= minScoreValue{edge_filter} && (row.setupConfidence === null || row.setupConfidence >= minConfidenceValue) && (row.daysInList || 999) <= maxDaysValue && row.atrPct <= maxAtrValue).sort((a,b) => state.sortDir === "asc" ? compare(a,b,state.sortBy) : compare(b,a,state.sortBy));
      render(filtered);
    }}
    document.querySelectorAll("th[data-sort]").forEach(th => th.addEventListener("click", () => {{ const key = th.dataset.sort; if (state.sortBy === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc"; else {{ state.sortBy = key; state.sortDir = "desc"; els.sortBy.value = key; }} applyFilters(); }}));
    {filter_inputs}.forEach(el => {{ el.addEventListener("input", applyFilters); el.addEventListener("change", applyFilters); }});
    els.sortBy.value = state.sortBy;
    els.sortBy.addEventListener("change", () => {{ state.sortBy = els.sortBy.value; state.sortDir = "desc"; applyFilters(); }});
    applyFilters();
  </script>
</body>
</html>"""


def resolve_universe(universe: str) -> tuple[str, dict]:
    key = universe.lower()
    if key not in UNIVERSE_CONFIG:
        key = "qqq"
    return key, UNIVERSE_CONFIG[key]


def generate_report(
    universe: str = "qqq",
    include_edge: bool = False,
    output_filename: str = OUTPUT_FILENAME,
    pages_filename: str | None = PAGES_FILENAME,
) -> dict:
    universe_key, universe_config = resolve_universe(universe)
    api_key = get_api_key()
    trading_dates = fetch_spy_dates(api_key)
    start_date = trading_dates[0]
    latest_date = trading_dates[-1]
    metadata = fetch_reference_tickers(api_key)
    latest_day = fetch_grouped_day(latest_date, api_key)

    tradable_candidates: list[dict] = []
    selected_members = universe_config["members"]
    for row in latest_day:
        ticker = row.get("T")
        if ticker not in metadata:
            continue
        if selected_members is not None and ticker not in selected_members:
            continue
        close = float(row.get("c", 0))
        volume = float(row.get("v", 0))
        dollar_volume = close * volume
        if close < MIN_PRICE or dollar_volume < MIN_LATEST_DOLLAR_VOLUME:
            continue
        tradable_candidates.append(
            {
                "ticker": ticker,
                "name": metadata[ticker]["name"],
                "exchange": metadata[ticker]["exchange"],
                "dollar_volume": dollar_volume,
            }
        )

    if selected_members is None:
        tradable_candidates.sort(key=lambda item: item["dollar_volume"], reverse=True)
        tradable_candidates = tradable_candidates[:MARKET_CANDIDATE_BUFFER]

    tradable = {
        item["ticker"]: {"name": item["name"], "exchange": item["exchange"]}
        for item in tradable_candidates
    }

    spy_history = fetch_history("SPY", start_date, latest_date, api_key)
    spy_closes = [float(row["c"]) for row in spy_history]

    rows = []
    for ticker, payload in tradable.items():
        history = fetch_history(ticker, start_date, latest_date, api_key)
        row = classify_stock(ticker, payload, history, spy_closes, latest_date)
        if row:
            rows.append(add_edge_score(enrich_signal_context(row, history, spy_closes, payload)))
    if include_edge:
        rows.sort(key=lambda row: (row["edgeScore"], row["score"], row["rsVsSpy20d"]), reverse=True)
    else:
        rows.sort(key=lambda row: (row["score"], row["rsVsSpy20d"]), reverse=True)
    if selected_members is None:
        rows = rows[:MAX_MARKET_TICKERS]

    coverage_note = f"{universe_config['label']} scanned on {latest_date}. {universe_config['description']}"
    html = build_html(rows, datetime.now(), coverage_note, universe_config["label"], include_edge=include_edge)
    output_path = Path(__file__).with_name(output_filename)
    output_path.write_text(html, encoding="utf-8")
    if pages_filename:
        Path(__file__).with_name(pages_filename).write_text(html, encoding="utf-8")
    return {
        "output_path": str(output_path),
        "latest_date": latest_date,
        "universe_key": universe_key,
        "universe_label": universe_config["label"],
        "universe_size": len(rows),
        "good_setups": sum(1 for row in rows if row["goodForSwing"]),
        "top_rows": rows[:10],
    }


def main() -> int:
    try:
        universe = sys.argv[1] if len(sys.argv) > 1 else "qqq"
        result = generate_report(universe)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc.reason}", file=sys.stderr)
        return 1
    print(f"Saved report to {result['output_path']}")
    print(f"Universe: {result['universe_label']}")
    print(f"Universe size: {result['universe_size']}")
    print(f"Good setups: {result['good_setups']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
