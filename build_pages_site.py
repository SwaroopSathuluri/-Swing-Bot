from __future__ import annotations

from datetime import datetime
from pathlib import Path

from etf_scanner import generate_report as generate_etf_report
from fundamentals_research import build_fundamentals_feed
from mag7_scanner import generate_report as generate_stock_report
from portfolio_market_data import build_portfolio_market_feed, write_portfolio_market_feed
from pro_v2_enrichment import build as build_pro_v2_data
from seasonality_report import generate_report as generate_seasonality_report


PROJECT_DIR = Path(__file__).parent
PAGES_INDEX = PROJECT_DIR / "index.html"
STOCKS_INDEX = PROJECT_DIR / "stocks.html"
STOCKS_EDGE_INDEX = PROJECT_DIR / "stocks-edge.html"
ETFS_INDEX = PROJECT_DIR / "etfs.html"


def build_home_page(stock_result: dict, etf_result: dict, seasonality_result: dict) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    asset_version = datetime.now().strftime("%Y%m%d%H%M")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Swing Bot Reports</title>
  <style>
    :root {{
      --bg: #eef4f7;
      --panel: rgba(255,255,255,.95);
      --ink: #17303c;
      --muted: #647682;
      --line: #d8e1e6;
      --green: #0c7a69;
      --blue: #1d4ed8;
      --shadow: 0 14px 34px rgba(23,48,60,.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: Georgia, "Times New Roman", serif;
      background:
        radial-gradient(circle at top left, rgba(12,122,105,.10), transparent 28%),
        radial-gradient(circle at top right, rgba(29,78,216,.10), transparent 26%),
        linear-gradient(180deg, #f8fbfc 0%, var(--bg) 100%);
    }}
    .wrap {{ max-width: 1480px; margin: 0 auto; padding: 24px 16px 40px; }}
    .hero {{
      background: linear-gradient(135deg, rgba(14,57,65,.98), rgba(21,94,117,.92));
      color: #f8fbfd;
      border-radius: 24px;
      padding: 28px;
      box-shadow: 0 20px 45px rgba(23,48,60,.18);
    }}
    .hero h1 {{ margin: 0 0 10px; font-size: clamp(2rem, 4vw, 3.4rem); }}
    .hero p {{ margin: 0; line-height: 1.55; max-width: 1020px; color: rgba(248,251,253,.90); }}
    .hero-meta {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 16px;
      color: rgba(248,251,253,.88);
      font-size: .95rem;
    }}
    .metrics, .links {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .metric, .panel, .link-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      box-shadow: var(--shadow);
    }}
    .metric {{ padding: 18px; }}
    .link-card {{ padding: 20px; text-decoration: none; color: var(--ink); }}
    .link-card h2 {{ margin: 0 0 10px; }}
    .link-card p {{ margin: 0; line-height: 1.5; color: var(--muted); }}
    .label {{ color: var(--muted); text-transform: uppercase; letter-spacing: .08em; font-size: .78rem; }}
    .value {{ font-size: 1.8rem; margin-top: 8px; }}
    .note {{
      margin-top: 16px;
      color: var(--muted);
      font-size: .95rem;
      line-height: 1.5;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Swing Bot Reports</h1>
      <p>Combined static dashboard for Stocks and ETFs. The new Pro mode applies the best backtested rule: take only the top 1 setup, rank by relative strength vs SPY, avoid chase entries, and use the bot stop and target. The classic and Edge reports remain available for comparison.</p>
      <div class="hero-meta">
        <div>Published: {generated_at}</div>
        <div>Stock market date: {stock_result['latest_date']}</div>
        <div>ETF market date: {etf_result['latest_date']}</div>
        <div>Seasonality snapshot: {seasonality_result['qqq_latest_date']}</div>
      </div>
    </section>
    <section class="metrics">
      <article class="metric"><div class="label">Best Mode</div><div class="value">Top 1</div></article>
      <article class="metric"><div class="label">Rank By</div><div class="value">RS</div></article>
      <article class="metric"><div class="label">Avg Hold</div><div class="value">3.8d</div></article>
      <article class="metric"><div class="label">Backtest Win Rate</div><div class="value">54.1%</div></article>
    </section>
    <section class="links">
      <a class="link-card" href="swingbot-pro.html?v={asset_version}">
        <h2>Swing Bot Pro</h2>
        <p>A+ mode: current report, top 1 only, ranked by relative strength vs SPY, score 55+, ATR 6% max, and since-entry between -3% and +2%.</p>
      </a>
      <a class="link-card" href="swingbot-pro-v2.html?v={asset_version}">
        <h2>Swing Bot Pro v2</h2>
        <p>Interactive conviction mode: Top 2 by default, with technicals, relative strength, history, entry discipline, risk penalties, and optional Massive news/options enrichment.</p>
      </a>
      <a class="link-card" href="swing-command-center.html?v={asset_version}">
        <h2>Swing Command Center</h2>
        <p>Installable app with the Monday plan, a long-term contribution and cash tracker, positions, target/stop alerts, return journal, and scheduled Fundamentals research.</p>
      </a>
      <a class="link-card" href="stocks.html?v={asset_version}">
        <h2>Stocks Classic</h2>
        <p>Original stock scanner link with the classic technical score ranking kept intact for comparison.</p>
      </a>
      <a class="link-card" href="stocks-edge.html?v={asset_version}">
        <h2>Stocks Edge Score</h2>
        <p>New iteration using the historical win/loss lessons: Edge Score ranking, stricter ATR control, and stronger relative-strength filters.</p>
      </a>
      <a class="link-card" href="etfs.html?v={asset_version}">
        <h2>ETFs</h2>
        <p>Liquid U.S. ETFs with category filters, benchmark context, leveraged/inverse warnings, and ETF-specific strategy notes.</p>
      </a>
      <a class="link-card" href="seasonality.html?v={asset_version}">
        <h2>QQQ / TQQQ Seasonality</h2>
        <p>20-year QQQ seasonality, TQQQ since inception, and a timing view that blends historical month tendencies with current trend factors.</p>
      </a>
    </section>
    <p class="note">These are static GitHub Pages reports. For on-demand fresh runs, trigger the GitHub Action manually. For true refresh-on-open behavior, use the server-backed live dashboard version.</p>
  </div>
</body>
</html>"""


def main() -> int:
    stock_result = generate_stock_report("market", include_edge=False, output_filename="swing_trading_mag7_report.html", pages_filename=None)
    stock_edge_result = generate_stock_report("market", include_edge=True, output_filename="swing_trading_stock_edge_report.html", pages_filename=None)
    build_pro_v2_data(stock_edge_result["top_rows"], limit=10)
    build_fundamentals_feed(minimum_ranked=6)
    write_portfolio_market_feed(build_portfolio_market_feed())
    etf_result = generate_etf_report()
    seasonality_result = generate_seasonality_report()
    PAGES_INDEX.write_text(build_home_page(stock_result, etf_result, seasonality_result), encoding="utf-8")
    STOCKS_INDEX.write_text((PROJECT_DIR / "swing_trading_mag7_report.html").read_text(encoding="utf-8"), encoding="utf-8")
    STOCKS_EDGE_INDEX.write_text((PROJECT_DIR / "swing_trading_stock_edge_report.html").read_text(encoding="utf-8"), encoding="utf-8")
    ETFS_INDEX.write_text((PROJECT_DIR / "swing_trading_etf_report.html").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Built {PAGES_INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
