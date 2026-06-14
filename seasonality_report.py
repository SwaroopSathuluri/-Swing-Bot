from __future__ import annotations

import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


OUTPUT_FILENAME = "qqq_tqqq_seasonality.html"
PAGES_FILENAME = "seasonality.html"


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


def fetch_history(ticker: str, start_date: str, end_date: str, api_key: str) -> list[dict]:
    url = (
        "https://api.massive.com/v2/aggs/ticker/"
        f"{urllib.parse.quote(ticker)}/range/1/day/{start_date}/{end_date}"
        f"?adjusted=true&sort=asc&limit=5000&apiKey={urllib.parse.quote(api_key)}"
    )
    return fetch_json(url).get("results", [])


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


def pct_change(new: float, old: float) -> float:
    return 0.0 if old == 0 else ((new - old) / old) * 100


def month_name(month: int) -> str:
    return datetime(2000, month, 1).strftime("%B")


def compute_monthly_stats(history: list[dict]) -> list[dict]:
    monthly = defaultdict(list)
    by_month_end = {}
    for row in history:
        dt = datetime.fromtimestamp(row["t"] / 1000)
        key = (dt.year, dt.month)
        by_month_end[key] = float(row["c"])
    sorted_keys = sorted(by_month_end)
    prev_close = None
    for key in sorted_keys:
        close = by_month_end[key]
        if prev_close is not None:
            ret = pct_change(close, prev_close)
            monthly[key[1]].append(ret)
        prev_close = close
    rows = []
    for month in range(1, 13):
        values = monthly.get(month, [])
        if not values:
            continue
        rows.append(
            {
                "month": month,
                "name": month_name(month),
                "avg_return": round(statistics.mean(values), 2),
                "median_return": round(statistics.median(values), 2),
                "win_rate": round((sum(1 for value in values if value > 0) / len(values)) * 100, 1),
                "samples": len(values),
            }
        )
    return rows


def current_factor_summary(history: list[dict], symbol: str, monthly_stats: list[dict]) -> dict:
    closes = [float(row["c"]) for row in history]
    latest_dt = datetime.fromtimestamp(history[-1]["t"] / 1000)
    latest_close = closes[-1]
    ema20 = ema_series(closes, 20)[-1]
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else sum(closes) / len(closes)
    rsi14 = rsi(closes, 14)
    macd_line, macd_signal = macd(closes)
    ytd_rows = [float(row["c"]) for row in history if datetime.fromtimestamp(row["t"] / 1000).year == latest_dt.year]
    ytd_return = pct_change(ytd_rows[-1], ytd_rows[0]) if len(ytd_rows) > 1 else 0.0
    current_month = next((row for row in monthly_stats if row["month"] == latest_dt.month), None)
    trend = "Bullish" if latest_close > ema20 > sma50 > sma200 else ("Mixed" if latest_close > sma50 else "Weak")
    seasonality = current_month["avg_return"] if current_month else 0.0
    if trend == "Bullish" and seasonality > 0:
        outlook = "Favorable seasonal window if market breadth stays supportive."
    elif trend == "Weak" and seasonality < 0:
        outlook = "Historically softer seasonal window and weak trend, so patience is better."
    else:
        outlook = "Mixed seasonal read; prioritize trend confirmation and risk control."
    return {
        "symbol": symbol,
        "latest_date": latest_dt.strftime("%Y-%m-%d"),
        "latest_close": round(latest_close, 2),
        "trend": trend,
        "ema20": round(ema20, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "rsi14": round(rsi14, 1),
        "macd_line": round(macd_line, 2),
        "macd_signal": round(macd_signal, 2),
        "ytd_return": round(ytd_return, 2),
        "current_month_avg": round(seasonality, 2),
        "outlook": outlook,
    }


def build_table(rows: list[dict]) -> str:
    parts = []
    for row in rows:
        parts.append(
            f"<tr><td>{row['name']}</td><td>{row['avg_return']:.2f}%</td><td>{row['median_return']:.2f}%</td><td>{row['win_rate']:.1f}%</td><td>{row['samples']}</td></tr>"
        )
    return "".join(parts)


def build_html(qqq_months: list[dict], tqqq_months: list[dict], qqq_now: dict, tqqq_now: dict, tqqq_start_year: int) -> str:
    qqq_best = max(qqq_months, key=lambda row: row["avg_return"])
    qqq_worst = min(qqq_months, key=lambda row: row["avg_return"])
    tqqq_best = max(tqqq_months, key=lambda row: row["avg_return"])
    tqqq_worst = min(tqqq_months, key=lambda row: row["avg_return"])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QQQ and TQQQ Seasonality</title>
  <style>
    :root {{ --bg:#eef4f7; --panel:#fffdf8; --ink:#16313d; --muted:#627581; --line:#d8e1e6; --green:#0c7a69; --amber:#b7791f; --red:#b23939; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); font-family:Georgia, "Times New Roman", serif; background:linear-gradient(180deg,#f8fbfc 0%,var(--bg) 100%); }}
    .wrap {{ max-width:1400px; margin:0 auto; padding:24px 16px 40px; }}
    .hero {{ background:linear-gradient(135deg, rgba(14,57,65,.98), rgba(21,94,117,.92)); color:#f8fbfd; border-radius:24px; padding:28px; box-shadow:0 20px 45px rgba(23,48,60,.18); }}
    h1 {{ margin:0 0 10px; font-size:clamp(2rem,4vw,3.4rem); }}
    .hero p {{ margin:0; line-height:1.55; max-width:1020px; color:rgba(248,251,253,.90); }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; margin-top:18px; }}
    .card, .table-card {{ background:var(--panel); border:1px solid var(--line); border-radius:20px; box-shadow:0 12px 30px rgba(23,48,60,.08); }}
    .card {{ padding:18px; }}
    .label {{ color:var(--muted); text-transform:uppercase; letter-spacing:.08em; font-size:.78rem; }}
    .value {{ font-size:1.7rem; margin-top:8px; }}
    .row {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:18px; margin-top:18px; }}
    .table-card {{ padding:18px; overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ padding:10px 8px; border-bottom:1px solid var(--line); text-align:left; }}
    th {{ background:#edf2f6; }}
    .note {{ margin-top:12px; color:var(--muted); line-height:1.5; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>QQQ and TQQQ Seasonality</h1>
      <p>This report looks at QQQ over the last 20 years and TQQQ from its inception in {tqqq_start_year}. It combines monthly seasonality with current trend and momentum factors, so you can judge whether this is historically a favorable time window to lean in or stay patient.</p>
    </section>
    <section class="grid">
      <article class="card"><div class="label">QQQ Best Month</div><div class="value">{qqq_best['name']} {qqq_best['avg_return']:.2f}%</div><div class="note">Worst: {qqq_worst['name']} {qqq_worst['avg_return']:.2f}%</div></article>
      <article class="card"><div class="label">TQQQ Best Month</div><div class="value">{tqqq_best['name']} {tqqq_best['avg_return']:.2f}%</div><div class="note">Worst: {tqqq_worst['name']} {tqqq_worst['avg_return']:.2f}%</div></article>
      <article class="card"><div class="label">QQQ Current Read</div><div class="value">{qqq_now['trend']}</div><div class="note">{qqq_now['outlook']}</div></article>
      <article class="card"><div class="label">TQQQ Current Read</div><div class="value">{tqqq_now['trend']}</div><div class="note">{tqqq_now['outlook']}</div></article>
    </section>
    <section class="row">
      <article class="table-card">
        <h2>QQQ Current Factors</h2>
        <table>
          <tr><th>Latest Date</th><td>{qqq_now['latest_date']}</td></tr>
          <tr><th>Close</th><td>{qqq_now['latest_close']}</td></tr>
          <tr><th>20 EMA / 50 SMA / 200 SMA</th><td>{qqq_now['ema20']} / {qqq_now['sma50']} / {qqq_now['sma200']}</td></tr>
          <tr><th>RSI / MACD</th><td>{qqq_now['rsi14']} / {qqq_now['macd_line']} vs {qqq_now['macd_signal']}</td></tr>
          <tr><th>YTD Return</th><td>{qqq_now['ytd_return']}%</td></tr>
          <tr><th>Current Month Avg</th><td>{qqq_now['current_month_avg']}%</td></tr>
        </table>
      </article>
      <article class="table-card">
        <h2>TQQQ Current Factors</h2>
        <table>
          <tr><th>Latest Date</th><td>{tqqq_now['latest_date']}</td></tr>
          <tr><th>Close</th><td>{tqqq_now['latest_close']}</td></tr>
          <tr><th>20 EMA / 50 SMA / 200 SMA</th><td>{tqqq_now['ema20']} / {tqqq_now['sma50']} / {tqqq_now['sma200']}</td></tr>
          <tr><th>RSI / MACD</th><td>{tqqq_now['rsi14']} / {tqqq_now['macd_line']} vs {tqqq_now['macd_signal']}</td></tr>
          <tr><th>YTD Return</th><td>{tqqq_now['ytd_return']}%</td></tr>
          <tr><th>Current Month Avg</th><td>{tqqq_now['current_month_avg']}%</td></tr>
        </table>
      </article>
    </section>
    <section class="row">
      <article class="table-card">
        <h2>QQQ Monthly Seasonality</h2>
        <table>
          <thead><tr><th>Month</th><th>Avg Return</th><th>Median</th><th>Win Rate</th><th>Samples</th></tr></thead>
          <tbody>{build_table(qqq_months)}</tbody>
        </table>
      </article>
      <article class="table-card">
        <h2>TQQQ Monthly Seasonality</h2>
        <table>
          <thead><tr><th>Month</th><th>Avg Return</th><th>Median</th><th>Win Rate</th><th>Samples</th></tr></thead>
          <tbody>{build_table(tqqq_months)}</tbody>
        </table>
      </article>
    </section>
  </div>
</body>
</html>"""


def generate_report() -> dict:
    api_key = get_api_key()
    end_date = datetime.now()
    qqq_start = end_date - timedelta(days=365 * 20 + 40)
    tqqq_start = datetime(2010, 1, 1)
    qqq_history = fetch_history("QQQ", qqq_start.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"), api_key)
    tqqq_history = fetch_history("TQQQ", tqqq_start.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"), api_key)
    qqq_months = compute_monthly_stats(qqq_history)
    tqqq_months = compute_monthly_stats(tqqq_history)
    qqq_now = current_factor_summary(qqq_history, "QQQ", qqq_months)
    tqqq_now = current_factor_summary(tqqq_history, "TQQQ", tqqq_months)
    html = build_html(qqq_months, tqqq_months, qqq_now, tqqq_now, datetime.fromtimestamp(tqqq_history[0]["t"] / 1000).year)
    output_path = Path(__file__).with_name(OUTPUT_FILENAME)
    output_path.write_text(html, encoding="utf-8")
    Path(__file__).with_name(PAGES_FILENAME).write_text(html, encoding="utf-8")
    return {
        "output_path": str(output_path),
        "qqq_latest_date": qqq_now["latest_date"],
        "tqqq_latest_date": tqqq_now["latest_date"],
        "qqq_outlook": qqq_now["outlook"],
        "tqqq_outlook": tqqq_now["outlook"],
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
