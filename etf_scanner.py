from __future__ import annotations

import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from market_context import (
    INVERSE_ETFS,
    LEVERAGED_ETFS,
    MANDATORY_CONTEXT_TICKERS,
    SECTOR_ETFS,
    build_market_regime,
    build_sector_rotation,
    classify_event_sensitivity,
    event_calendar,
)


MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 10_000_000
LOOKBACK_DAYS = 220
OUTPUT_FILENAME = "swing_trading_etf_report.html"
PAGES_FILENAME = "etf_index.html"
MAX_ETF_TICKERS = 150
HIGHLIGHT_ETFS = (
    "SPY",
    "QQQ",
    "TQQQ",
    "SQQQ",
    "IWM",
    "DIA",
    "SMH",
    "SOXX",
    "XLK",
    "XLF",
    "XLE",
    "ARKK",
)


def infer_etf_category(ticker: str, name: str) -> str:
    upper = ticker.upper()
    lowered = name.lower()
    if upper in LEVERAGED_ETFS or "3x" in lowered or "2x" in lowered or "ultrapro" in lowered or "ultra" in lowered:
        return "Leveraged"
    if upper in INVERSE_ETFS or "inverse" in lowered or "short" in lowered or "bear" in lowered:
        return "Inverse"
    if any(token in lowered for token in ["treasury", "bond", "income", "municipal", "mortgage", "tips"]):
        return "Fixed Income"
    if any(token in lowered for token in ["gold", "silver", "oil", "commodity", "metals", "uranium", "bitcoin", "crypto"]):
        return "Commodity / Alternative"
    if any(token in lowered for token in ["technology", "semiconductor", "software", "cloud", "cyber"]):
        return "Technology / Growth"
    if any(token in lowered for token in ["financial", "bank", "insurance"]):
        return "Financials"
    if any(token in lowered for token in ["energy", "oil", "gas"]):
        return "Energy"
    if any(token in lowered for token in ["health", "biotech", "pharma", "medical"]):
        return "Healthcare"
    if any(token in lowered for token in ["real estate", "reit"]):
        return "Real Estate"
    if any(token in lowered for token in ["consumer", "retail", "discretionary", "staples"]):
        return "Consumer"
    if any(token in lowered for token in ["industrial", "infrastructure", "transport", "aerospace", "defense"]):
        return "Industrials"
    if any(token in lowered for token in ["china", "emerging", "europe", "japan", "international", "world", "developed", "foreign"]):
        return "International"
    if any(token in lowered for token in ["small cap", "mid cap", "russell", "s&p 500", "nasdaq", "dow", "total stock", "index"]):
        return "Broad Equity"
    if any(token in lowered for token in ["innovation", "robot", "ai", "genomic", "clean", "disruptive", "theme"]):
        return "Thematic"
    return "Other"


def infer_benchmark(ticker: str, category: str, name: str) -> str:
    upper = ticker.upper()
    lowered = name.lower()
    if upper in {"QQQ", "TQQQ", "SQQQ", "SOXX", "SMH", "XLK", "TECL", "SOXL", "SOXS"} or category == "Technology / Growth":
        return "QQQ"
    if category in {"Broad Equity", "Financials", "Energy", "Healthcare", "Consumer", "Industrials", "Real Estate", "Thematic"}:
        return "SPY"
    if category == "International":
        return "ACWI"
    if category == "Fixed Income":
        return "TLT" if "treasury" in lowered else "AGG"
    if category == "Commodity / Alternative":
        return "GLD" if "gold" in lowered else "DBC"
    return "SPY"


def strategy_idea(setup: str, category: str, ticker: str) -> str:
    if setup == "Avoid":
        return "Wait for trend repair or a cleaner pullback."
    if ticker in LEVERAGED_ETFS:
        return "Use smaller size and shorter hold because leverage amplifies decay and gap risk."
    if category == "Fixed Income":
        return "Treat as a macro swing and watch rates plus risk-on/risk-off confirmation."
    if category == "Commodity / Alternative":
        return "Pair the chart with macro catalysts like dollar, yields, and commodity supply news."
    if category == "International":
        return "Check dollar trend and foreign-market leadership before entry."
    if setup == "Breakout":
        return "Favor breakout continuation only if volume confirms and the broad tape is supportive."
    if setup == "Pullback":
        return "Best used after a controlled retracement into trend support with improving momentum."
    return "Use as a trend-following swing and trail risk under moving-average support."


def category_benchmark_note(category: str, benchmark: str) -> str:
    if benchmark == "QQQ":
        return "Tech-heavy ETF. Relative strength versus QQQ matters more than SPY here."
    if benchmark == "SPY":
        return "Use SPY as the broad market check before committing capital."
    if benchmark in {"TLT", "AGG"}:
        return "Rates and inflation can overpower pure chart strength."
    if benchmark in {"GLD", "DBC"}:
        return "Macro catalysts and commodity trends matter as much as price structure."
    if category == "International":
        return "Dollar strength and foreign market leadership can change the trade quickly."
    return "Use benchmark confirmation before sizing aggressively."


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
    dates = [datetime.fromtimestamp(row["t"] / 1000).strftime("%Y-%m-%d") for row in payload.get("results", [])]
    return dates[-LOOKBACK_DAYS:]


def fetch_reference_tickers(api_key: str) -> dict[str, dict]:
    metadata: dict[str, dict] = {}
    url = (
        "https://api.massive.com/v3/reference/tickers"
        f"?market=stocks&active=true&type=ETF&limit=1000&sort=ticker&apiKey={urllib.parse.quote(api_key)}"
    )
    while url:
        payload = fetch_json(url)
        for row in payload.get("results", []):
            metadata[row["ticker"]] = {
                "name": row.get("name", ""),
                "exchange": row.get("primary_exchange", ""),
            }
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
    url = (
        "https://api.massive.com/v2/aggs/ticker/"
        f"{urllib.parse.quote(ticker)}/range/1/day/{start_date}/{end_date}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={urllib.parse.quote(api_key)}"
    )
    return fetch_json(url).get("results", [])


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
    for idx, _close in enumerate(closes):
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
    if atr_pct > 8:
        high -= 2
    if rs_value > 5:
        high += 2
    if rs_value < 0:
        low = max(2, low - 1)
        high = max(low + 2, high - 2)
    return f"{low}-{high} trading days"


def classify_etf(
    ticker: str,
    meta: dict,
    history: list[dict],
    spy_closes: list[float],
    qqq_closes: list[float],
    latest_date: str,
) -> dict | None:
    if len(history) < LOOKBACK_DAYS or len(spy_closes) < LOOKBACK_DAYS or len(qqq_closes) < LOOKBACK_DAYS:
        return None
    closes = [float(bar["c"]) for bar in history]
    highs = [float(bar["h"]) for bar in history]
    lows = [float(bar["l"]) for bar in history]
    volumes = [float(bar["v"]) for bar in history]
    latest_close = closes[-1]
    avg_dollar_volume = statistics.mean(c * v for c, v in zip(closes[-20:], volumes[-20:]))
    if latest_close < MIN_PRICE or avg_dollar_volume < MIN_DOLLAR_VOLUME:
        return None
    category = infer_etf_category(ticker, meta["name"])
    benchmark = infer_benchmark(ticker, category, meta["name"])

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
    rs_vs_qqq = pct_change(closes[-1], closes[-21]) - pct_change(qqq_closes[-1], qqq_closes[-21])

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
    score -= 5 if ticker in LEVERAGED_ETFS and atr_pct > 6 else 0

    notes: list[str] = []
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
    if rs_vs_spy > 0:
        notes.append("beating SPY over 20 days")
    if rs_vs_qqq > 0:
        notes.append("beating QQQ over 20 days")
    if ticker in LEVERAGED_ETFS:
        notes.append("leveraged ETF, size smaller")
    if extended_rsi:
        notes.append("RSI extended")

    setup = "Watch"
    if score >= 75 and near_breakout:
        setup = "Breakout"
    elif score >= 65 and pullback_hold:
        setup = "Pullback"
    elif score >= 55:
        setup = "Trend Continuation"
    elif latest_close < sma50 or latest_close < sma200:
        setup = "Avoid"

    return {
        "ticker": ticker,
        "name": meta["name"],
        "exchange": meta["exchange"],
        "category": category,
        "benchmark": benchmark,
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
        "rsVsQqq20d": round(rs_vs_qqq, 2),
        "stopLoss": round(max(ema20 - (0.5 * atr14), latest_close - (1.5 * atr14)), 2),
        "target1": round(latest_close + (1.5 * atr14), 2),
        "target2": round(latest_close + (3.0 * atr14), 2),
        "holdWindow": hold_window(setup, atr_pct, rs_vs_spy),
        "goodForSwing": setup != "Avoid" and score >= 55,
        "strategyIdea": strategy_idea(setup, category, ticker),
        "benchmarkNote": category_benchmark_note(category, benchmark),
        "caution": "High decay / gap risk" if ticker in LEVERAGED_ETFS else ("Inverse fund, watch squeezes" if ticker in INVERSE_ETFS else ("Macro sensitive" if category in {"Fixed Income", "Commodity / Alternative", "International"} else "Standard swing risk")),
        "trendAligned": trend_aligned,
        "leveraged": ticker in LEVERAGED_ETFS,
        "inverse": ticker in INVERSE_ETFS,
        "notes": "; ".join(notes[:7]),
    }


def build_html(
    rows: list[dict],
    generated_at: datetime,
    coverage_note: str,
    featured_rows: list[dict],
    regime: dict,
    calendar: dict,
    sector_rotation: dict,
    top_ideas: list[dict],
) -> str:
    featured_cards = "".join(
        f"""
        <article class="focus-card">
          <div class="focus-ticker">{row['ticker']}</div>
          <div class="focus-setup {('Trend' if row['setup'] == 'Trend Continuation' else row['setup'])}">{row['setup']}</div>
          <div class="focus-line">Score {row['score']} | Hold {row['holdWindow']}</div>
          <div class="focus-line">Close {row['close']:.2f} | RS vs SPY {row['rsVsSpy20d']:.2f}%</div>
        </article>
        """
        for row in featured_rows
    )
    event_cards = "".join(
        f"""
        <article class="event-card">
          <div class="event-name">{event['name']}</div>
          <div class="event-date">{event['date']} | {event['impact']} impact | {event['daysAway']} day(s)</div>
          <div class="event-note">{event['notes']}</div>
        </article>
        """
        for event in calendar["upcoming"][:4]
    )
    strongest_cards = "".join(
        f"""
        <article class="rotation-card">
          <div class="rotation-ticker">{row['ticker']}</div>
          <div class="rotation-title">{row['sectorLabel']}</div>
          <div class="rotation-line">{row['setup']} | Score {row['score']} | RS {row['rsVsSpy20d']:.2f}%</div>
        </article>
        """
        for row in sector_rotation["strongest"]
    )
    weakest_cards = "".join(
        f"""
        <article class="rotation-card weak">
          <div class="rotation-ticker">{row['ticker']}</div>
          <div class="rotation-title">{row['sectorLabel']}</div>
          <div class="rotation-line">{row['setup']} | Score {row['score']} | RS {row['rsVsSpy20d']:.2f}%</div>
        </article>
        """
        for row in sector_rotation["weakest"]
    )
    idea_cards = "".join(
        f"""
        <article class="idea-card">
          <div class="idea-head">
            <div class="idea-ticker">{row['ticker']}</div>
            <div class="tag {('Trend' if row['setup'] == 'Trend Continuation' else row['setup'])}">{row['setup']}</div>
          </div>
          <div class="idea-line">{row['name']}</div>
          <div class="idea-line">Category: {row['category']} | Hold: {row['holdWindow']}</div>
          <div class="idea-line">Why now: {row['strategyIdea']}</div>
          <div class="idea-line">Event risk: {row['eventRisk']} | {row['regimeNote']}</div>
        </article>
        """
        for row in top_ideas
    )
    booming = sector_rotation["booming"]
    booming_text = (
        f"{booming['sectorLabel']} via {booming['ticker']} looks strongest right now with score {booming['score']} and RS vs SPY of {booming['rsVsSpy20d']:.2f}%."
        if booming
        else "No clear booming sector yet."
    )
    regime_notes = " | ".join(regime["notes"]) if regime["notes"] else "Trend confirmation is mixed."
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>US ETF Swing Report</title>
  <style>
    :root {{ --bg:#eef3f6; --panel:rgba(255,255,255,.94); --ink:#18303f; --muted:#60717a; --line:#d6dde3; --green:#0b7a68; --amber:#bd7c2f; --red:#a3423f; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); font-family:Georgia, "Times New Roman", serif; background:radial-gradient(circle at 10% 0%, rgba(11,122,104,.10), transparent 28%), radial-gradient(circle at 90% 10%, rgba(37,99,235,.10), transparent 24%), linear-gradient(180deg,#f7fafc 0%,var(--bg) 100%); }}
    .wrap {{ max-width:1460px; margin:0 auto; padding:28px 18px 42px; }}
    .hero {{ background:linear-gradient(135deg, rgba(15,56,64,.98), rgba(21,94,117,.92)); color:#f8fbfd; border-radius:24px; padding:30px; box-shadow:0 20px 45px rgba(30,47,57,.18); }}
    .hero h1 {{ margin:0 0 10px; font-size:clamp(2rem,4vw,3.5rem); }}
    .hero p {{ margin:0; line-height:1.55; max-width:1000px; color:rgba(248,251,253,.88); }}
    .focus-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-top:18px; }}
    .focus-card {{ background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18); border-radius:18px; padding:14px; }}
    .focus-ticker {{ font-size:1.2rem; font-weight:700; }}
    .focus-setup {{ display:inline-block; margin-top:8px; padding:4px 10px; border-radius:999px; font-size:.78rem; font-weight:700; }}
    .focus-line {{ margin-top:8px; font-size:.92rem; }}
    .Breakout {{ background:rgba(11,122,104,.15); color:var(--green); }}
    .Pullback {{ background:rgba(189,124,47,.16); color:#8a561a; }}
    .Trend {{ background:rgba(109,127,137,.16); color:#43545e; }}
    .Avoid {{ background:rgba(163,66,63,.15); color:var(--red); }}
    .metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; margin-top:20px; }}
    .metric,.controls,.table-wrap {{ background:var(--panel); border:1px solid var(--line); border-radius:20px; box-shadow:0 12px 30px rgba(30,47,57,.08); }}
    .metric {{ padding:18px; }}
    .label {{ color:var(--muted); text-transform:uppercase; letter-spacing:.08em; font-size:.78rem; }}
    .value {{ font-size:1.8rem; margin-top:8px; }}
    .panel {{ margin-top:20px; padding:18px; }}
    .panel h2 {{ margin:0 0 10px; }}
    .event-grid,.rotation-grid,.idea-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }}
    .event-card,.rotation-card,.idea-card {{ background:#fffdf8; border:1px solid var(--line); border-radius:18px; padding:14px; }}
    .rotation-card.weak {{ background:#fff8f7; }}
    .event-name,.rotation-ticker,.idea-ticker {{ font-size:1.04rem; font-weight:700; }}
    .event-date,.rotation-title,.idea-line,.event-note {{ margin-top:7px; font-size:.93rem; line-height:1.45; }}
    .split-grid {{ display:grid; grid-template-columns:1.15fr .85fr; gap:18px; margin-top:20px; }}
    .status-pill {{ display:inline-block; padding:5px 10px; border-radius:999px; font-size:.78rem; font-weight:700; }}
    .status-high {{ background:rgba(163,66,63,.15); color:var(--red); }}
    .status-medium {{ background:rgba(189,124,47,.16); color:#8a561a; }}
    .status-low {{ background:rgba(11,122,104,.15); color:var(--green); }}
    .controls {{ margin-top:20px; padding:18px; }}
    .control-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }}
    label {{ font-size:.84rem; color:var(--muted); display:block; margin-bottom:6px; text-transform:uppercase; letter-spacing:.07em; }}
    input,select {{ width:100%; padding:11px 12px; border-radius:12px; border:1px solid var(--line); background:#fffdf8; color:var(--ink); font-size:.96rem; }}
    .table-wrap {{ margin-top:20px; overflow:hidden; }}
    .table-scroll {{ overflow:auto; max-height:70vh; }}
    table {{ width:100%; border-collapse:collapse; min-width:1480px; }}
    th,td {{ padding:11px 10px; border-bottom:1px solid var(--line); text-align:left; font-size:.93rem; vertical-align:top; }}
    th {{ position:sticky; top:0; background:#e7eef3; cursor:pointer; z-index:1; }}
    tbody tr:hover {{ background:rgba(21,94,117,.06); }}
    .tag {{ display:inline-block; padding:4px 10px; border-radius:999px; font-size:.78rem; font-weight:700; }}
    .yes {{ color:var(--green); font-weight:700; }}
    .no {{ color:var(--red); font-weight:700; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>US ETF Swing Report</h1>
      <p>Interactive ETF swing scanner using your Massive data. This report covers liquid U.S.-listed ETFs, including broad-market, sector, leveraged, inverse, commodity, and thematic funds. It now layers in regime filters, macro event risk, sector rotation, and benchmark-aware logic so you can tell not just what looks strong, but when the backdrop is actually supportive. Generated {generated_at.strftime("%Y-%m-%d %H:%M")}.</p>
      <div class="focus-grid">{featured_cards}</div>
    </section>
    <section class="metrics">
      <article class="metric"><div class="label">Coverage</div><div class="value" id="coverageCount">-</div><div>{coverage_note}</div></article>
      <article class="metric"><div class="label">Good ETF Setups</div><div class="value" id="goodCount">-</div><div>Score 55+ and not marked Avoid.</div></article>
      <article class="metric"><div class="label">Breakouts</div><div class="value" id="breakoutCount">-</div><div>Near 20-day highs with strong alignment.</div></article>
      <article class="metric"><div class="label">Pullbacks</div><div class="value" id="pullbackCount">-</div><div>Held the 20 EMA inside a larger uptrend.</div></article>
    </section>
    <section class="split-grid">
      <article class="panel metric">
        <div class="label">Market Regime</div>
        <div class="value">{regime['regime']}</div>
        <div>{regime['summary']}</div>
        <div class="note" style="margin-top:10px;">{regime_notes}</div>
        <div class="note" style="margin-top:10px;">Leveraged long gate: <strong>{"Open" if regime['leveragedLongsOk'] else "Closed / selective"}</strong></div>
      </article>
      <article class="panel metric">
        <div class="label">Current Event Risk</div>
        <div class="value">{calendar['level']}</div>
        <div>{calendar['summary']}</div>
        <div class="note" style="margin-top:10px;">Active windows: {", ".join(calendar['activeWindows']) if calendar['activeWindows'] else "None"}</div>
      </article>
    </section>
    <section class="panel">
      <h2>Upcoming Macro and Earnings Windows</h2>
      <div class="event-grid">{event_cards}</div>
    </section>
    <section class="split-grid">
      <article class="panel">
        <h2>Sector Rotation</h2>
        <div class="note">{booming_text}</div>
        <div class="rotation-grid" style="margin-top:12px;">{strongest_cards}</div>
      </article>
      <article class="panel">
        <h2>Weakest Sectors</h2>
        <div class="note">These are the areas I would avoid forcing long swings unless trend improves.</div>
        <div class="rotation-grid" style="margin-top:12px;">{weakest_cards}</div>
      </article>
    </section>
    <section class="panel">
      <h2>ETF Ideas Right Now</h2>
      <div class="note">These names already clear the swing filter, but the notes below also show whether event risk or market regime should make you more selective.</div>
      <div class="idea-grid" style="margin-top:12px;">{idea_cards}</div>
    </section>
    <section class="controls">
      <div class="control-grid">
        <div><label for="search">Search</label><input id="search" type="text" placeholder="Ticker or ETF name"></div>
        <div><label for="setupFilter">Setup</label><select id="setupFilter"><option value="All">All</option><option value="Breakout">Breakout</option><option value="Pullback">Pullback</option><option value="Trend Continuation">Trend Continuation</option><option value="Avoid">Avoid</option></select></div>
        <div><label for="categoryFilter">ETF Category</label><select id="categoryFilter"><option value="All">All</option><option value="Broad Equity">Broad Equity</option><option value="Technology / Growth">Technology / Growth</option><option value="Leveraged">Leveraged</option><option value="Inverse">Inverse</option><option value="Fixed Income">Fixed Income</option><option value="Commodity / Alternative">Commodity / Alternative</option><option value="International">International</option><option value="Financials">Financials</option><option value="Energy">Energy</option><option value="Healthcare">Healthcare</option><option value="Thematic">Thematic</option><option value="Other">Other</option></select></div>
        <div><label for="qualityFilter">Swing Quality</label><select id="qualityFilter"><option value="All">All</option><option value="Yes">Good for swing trade</option><option value="No">Not good right now</option></select></div>
        <div><label for="minScore">Minimum Score</label><input id="minScore" type="number" value="55" min="0" max="100" step="5"></div>
        <div><label for="maxAtr">Max ATR %</label><input id="maxAtr" type="number" value="8" min="1" max="30" step="0.5"></div>
        <div><label for="sortBy">Sort</label><select id="sortBy"><option value="score">Score</option><option value="rsVsSpy20d">RS vs SPY</option><option value="rsVsQqq20d">RS vs QQQ</option><option value="volumeRatio">Volume Ratio</option><option value="atrPct">ATR %</option><option value="close">Price</option></select></div>
      </div>
    </section>
    <section class="table-wrap"><div class="table-scroll"><table><thead><tr><th data-sort="ticker">Ticker</th><th data-sort="name">Name</th><th data-sort="category">Category</th><th data-sort="benchmark">Benchmark</th><th data-sort="setup">Setup</th><th data-sort="goodForSwing">Good For Swing?</th><th data-sort="holdWindow">Hold Window</th><th data-sort="score">Score</th><th data-sort="close">Close</th><th data-sort="ema20">20 EMA</th><th data-sort="sma50">50 SMA</th><th data-sort="sma200">200 SMA</th><th data-sort="rsi14">RSI</th><th data-sort="macd">MACD</th><th data-sort="atrPct">ATR %</th><th data-sort="volumeRatio">Vol Ratio</th><th data-sort="rsVsSpy20d">RS vs SPY</th><th data-sort="rsVsQqq20d">RS vs QQQ</th><th data-sort="eventRisk">Event Risk</th><th data-sort="stopLoss">Stop</th><th data-sort="target1">T1</th><th data-sort="target2">T2</th><th data-sort="strategyIdea">Strategy</th><th data-sort="benchmarkNote">Benchmark Logic</th><th data-sort="caution">Caution</th><th data-sort="notes">Notes</th></tr></thead><tbody id="reportBody"></tbody></table></div></section>
  </div>
  <script>
    const rows = {json.dumps(rows)};
    const state = {{ sortBy: "score", sortDir: "desc" }};
    const els = {{
      body: document.getElementById("reportBody"),
      search: document.getElementById("search"),
      setupFilter: document.getElementById("setupFilter"),
      categoryFilter: document.getElementById("categoryFilter"),
      qualityFilter: document.getElementById("qualityFilter"),
      minScore: document.getElementById("minScore"),
      maxAtr: document.getElementById("maxAtr"),
      sortBy: document.getElementById("sortBy"),
      coverageCount: document.getElementById("coverageCount"),
      goodCount: document.getElementById("goodCount"),
      breakoutCount: document.getElementById("breakoutCount"),
      pullbackCount: document.getElementById("pullbackCount")
    }};
    function compare(a,b,key) {{ const av=a[key], bv=b[key]; if (typeof av === "number" && typeof bv === "number") return av-bv; return String(av).localeCompare(String(bv)); }}
    function setupClass(setup) {{ return setup === "Trend Continuation" ? "Trend" : setup; }}
    function render(filtered) {{
      els.body.innerHTML = filtered.map(row => `<tr><td><strong>${{row.ticker}}</strong><br><span style="color:var(--muted)">${{row.exchange}}</span></td><td>${{row.name}}</td><td>${{row.category}}</td><td>${{row.benchmark}}</td><td><span class="tag ${{setupClass(row.setup)}}">${{row.setup}}</span></td><td class="${{row.goodForSwing ? "yes" : "no"}}">${{row.goodForSwing ? "Yes" : "No"}}</td><td>${{row.holdWindow}}</td><td>${{row.score}}</td><td>${{row.close.toFixed(2)}}</td><td>${{row.ema20.toFixed(2)}}</td><td>${{row.sma50.toFixed(2)}}</td><td>${{row.sma200.toFixed(2)}}</td><td>${{row.rsi14.toFixed(1)}}</td><td>${{row.macd.toFixed(2)}} / ${{row.macdSignal.toFixed(2)}}</td><td>${{row.atrPct.toFixed(2)}}%</td><td>${{row.volumeRatio.toFixed(2)}}x</td><td>${{row.rsVsSpy20d.toFixed(2)}}%</td><td>${{row.rsVsQqq20d.toFixed(2)}}%</td><td><span class="status-pill status-${{row.eventRisk.toLowerCase()}}">${{row.eventRisk}}</span><br><span style="color:var(--muted)">${{row.regimeNote}}</span></td><td>${{row.stopLoss.toFixed(2)}}</td><td>${{row.target1.toFixed(2)}}</td><td>${{row.target2.toFixed(2)}}</td><td>${{row.strategyIdea}}</td><td>${{row.benchmarkNote}}</td><td>${{row.caution}}</td><td>${{row.notes}}</td></tr>`).join("");
      els.coverageCount.textContent = `${{filtered.length}}`;
      els.goodCount.textContent = `${{filtered.filter(row => row.goodForSwing).length}}`;
      els.breakoutCount.textContent = `${{filtered.filter(row => row.setup === "Breakout").length}}`;
      els.pullbackCount.textContent = `${{filtered.filter(row => row.setup === "Pullback").length}}`;
    }}
    function applyFilters() {{
      const term = els.search.value.trim().toLowerCase();
      const setup = els.setupFilter.value;
      const category = els.categoryFilter.value;
      const quality = els.qualityFilter.value;
      const minScoreValue = Number(els.minScore.value || 0);
      const maxAtrValue = Number(els.maxAtr.value || 99);
      const filtered = rows.filter(row => (!term || row.ticker.toLowerCase().includes(term) || row.name.toLowerCase().includes(term)) && (setup === "All" || row.setup === setup) && (category === "All" || row.category === category) && !(quality === "Yes" && !row.goodForSwing) && !(quality === "No" && row.goodForSwing) && row.score >= minScoreValue && row.atrPct <= maxAtrValue).sort((a,b) => state.sortDir === "asc" ? compare(a,b,state.sortBy) : compare(b,a,state.sortBy));
      render(filtered);
    }}
    document.querySelectorAll("th[data-sort]").forEach(th => th.addEventListener("click", () => {{ const key = th.dataset.sort; if (state.sortBy === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc"; else {{ state.sortBy = key; state.sortDir = "desc"; els.sortBy.value = key; }} applyFilters(); }}));
    [els.search, els.setupFilter, els.categoryFilter, els.qualityFilter, els.minScore, els.maxAtr].forEach(el => {{ el.addEventListener("input", applyFilters); el.addEventListener("change", applyFilters); }});
    els.sortBy.addEventListener("change", () => {{ state.sortBy = els.sortBy.value; state.sortDir = "desc"; applyFilters(); }});
    applyFilters();
  </script>
</body>
</html>"""


def generate_report() -> dict:
    api_key = get_api_key()
    trading_dates = fetch_spy_dates(api_key)
    start_date = trading_dates[0]
    latest_date = trading_dates[-1]
    metadata = fetch_reference_tickers(api_key)
    latest_day = fetch_grouped_day(latest_date, api_key)

    candidates: list[dict] = []
    for row in latest_day:
        ticker = row.get("T")
        if ticker not in metadata:
            continue
        close = float(row.get("c", 0))
        volume = float(row.get("v", 0))
        dollar_volume = close * volume
        if close < MIN_PRICE or dollar_volume < MIN_DOLLAR_VOLUME:
            continue
        candidates.append(
            {
                "ticker": ticker,
                "name": metadata[ticker]["name"],
                "exchange": metadata[ticker]["exchange"],
                "dollar_volume": dollar_volume,
            }
        )

    candidates.sort(key=lambda item: item["dollar_volume"], reverse=True)
    candidates = candidates[:MAX_ETF_TICKERS]
    candidate_map = {item["ticker"]: item for item in candidates}
    for ticker in set((*HIGHLIGHT_ETFS, *MANDATORY_CONTEXT_TICKERS)):
        if ticker in metadata and ticker not in candidate_map:
            candidate_map[ticker] = {
                "ticker": ticker,
                "name": metadata[ticker]["name"],
                "exchange": metadata[ticker]["exchange"],
                "dollar_volume": 0.0,
            }

    spy_history = fetch_history("SPY", start_date, latest_date, api_key)
    qqq_history = fetch_history("QQQ", start_date, latest_date, api_key)
    spy_closes = [float(row["c"]) for row in spy_history]
    qqq_closes = [float(row["c"]) for row in qqq_history]

    rows = []
    for ticker, payload in candidate_map.items():
        history = fetch_history(ticker, start_date, latest_date, api_key)
        row = classify_etf(ticker, payload, history, spy_closes, qqq_closes, latest_date)
        if row:
            rows.append(row)
    rows.sort(key=lambda row: (row["score"], row["rsVsSpy20d"]), reverse=True)

    rows_by_ticker = {row["ticker"]: row for row in rows}
    regime = build_market_regime(rows_by_ticker)
    calendar = event_calendar(datetime.now())
    sector_rotation = build_sector_rotation(rows)

    for row in rows:
        event_view = classify_event_sensitivity(row, calendar, regime)
        row["eventRisk"] = event_view["level"]
        row["regimeNote"] = event_view["note"]
        row["caution"] = f"{row['caution']}; {event_view['note']}"

    featured_rows = [row for row in rows if row["ticker"] in HIGHLIGHT_ETFS]
    featured_rows.sort(key=lambda row: HIGHLIGHT_ETFS.index(row["ticker"]) if row["ticker"] in HIGHLIGHT_ETFS else 999)
    top_ideas = [
        row
        for row in rows
        if row["goodForSwing"] and not row["inverse"] and row["setup"] != "Avoid"
    ][:6]
    coverage_note = f"Top {MAX_ETF_TICKERS} liquid U.S. ETFs plus highlighted benchmark and leveraged funds, scanned on {latest_date}."
    html = build_html(rows, datetime.now(), coverage_note, featured_rows[:8], regime, calendar, sector_rotation, top_ideas)
    output_path = Path(__file__).with_name(OUTPUT_FILENAME)
    output_path.write_text(html, encoding="utf-8")
    Path(__file__).with_name(PAGES_FILENAME).write_text(html, encoding="utf-8")
    return {
        "output_path": str(output_path),
        "latest_date": latest_date,
        "universe_size": len(rows),
        "good_setups": sum(1 for row in rows if row["goodForSwing"]),
        "top_rows": rows[:10],
        "featured_rows": featured_rows,
        "sector_rotation": sector_rotation,
        "regime": regime,
        "calendar": calendar,
    }


def main() -> int:
    try:
        result = generate_report()
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
    print(f"Universe size: {result['universe_size']}")
    print(f"Good setups: {result['good_setups']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
