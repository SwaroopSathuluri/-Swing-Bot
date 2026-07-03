from __future__ import annotations

import json
import statistics
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import mag7_scanner as scanner


BACKTEST_TRADING_DAYS = 90
MATURITY_BUFFER_DAYS = 18
TOP_N = 5
MIN_EDGE_SCORE = 55
MAX_ATR_PCT = 6.0


def utc_date(row: dict) -> str:
    return datetime.fromtimestamp(row["t"] / 1000, UTC).strftime("%Y-%m-%d")


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_spy_dates(api_key: str) -> list[str]:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=760)
    url = (
        "https://api.massive.com/v2/aggs/ticker/SPY/range/1/day/"
        f"{start_date:%Y-%m-%d}/{end_date:%Y-%m-%d}?adjusted=true&sort=asc&limit=5000&apiKey={urllib.parse.quote(api_key)}"
    )
    payload = fetch_json(url)
    return [utc_date(row) for row in payload.get("results", [])]


def fetch_history_map(ticker: str, start_date: str, end_date: str, api_key: str) -> dict[str, dict]:
    rows = scanner.fetch_history(ticker, start_date, end_date, api_key)
    return {utc_date(row): row for row in rows}


def resolve_outcome(signal: dict, future_rows: list[dict]) -> dict:
    max_days = scanner.max_hold_days(signal["setup"], signal["atrPct"], signal["rsVsSpy20d"])
    entry = signal["close"]
    target = signal["target1"]
    stop = signal["stopLoss"]
    for offset, bar in enumerate(future_rows[:max_days], start=1):
        high = float(bar["h"])
        low = float(bar["l"])
        close = float(bar["c"])
        date = utc_date(bar)
        if low <= stop:
            return {
                "outcome": "loss",
                "exit_date": date,
                "days_held": offset,
                "return_pct": scanner.pct_change(stop, entry),
            }
        if high >= target:
            return {
                "outcome": "win",
                "exit_date": date,
                "days_held": offset,
                "return_pct": scanner.pct_change(target, entry),
            }
    if future_rows:
        last = future_rows[min(len(future_rows), max_days) - 1]
        return {
            "outcome": "neutral",
            "exit_date": utc_date(last),
            "days_held": min(len(future_rows), max_days),
            "return_pct": scanner.pct_change(float(last["c"]), entry),
        }
    return {"outcome": "open", "exit_date": "", "days_held": 0, "return_pct": 0.0}


def main() -> int:
    api_key = scanner.get_api_key()
    all_dates = fetch_spy_dates(api_key)
    latest_date = all_dates[-1]
    matured_signal_dates = all_dates[-(BACKTEST_TRADING_DAYS + MATURITY_BUFFER_DAYS) : -MATURITY_BUFFER_DAYS]
    start_date = all_dates[max(0, all_dates.index(matured_signal_dates[0]) - scanner.LOOKBACK_DAYS - 5)]

    metadata = scanner.fetch_reference_tickers(api_key)
    candidates_by_date: dict[str, list[dict]] = {}
    unique_tickers: set[str] = {"SPY"}
    for date in matured_signal_dates:
        day_rows = scanner.fetch_grouped_day(date, api_key)
        candidates: list[dict] = []
        for row in day_rows:
            ticker = row.get("T")
            if ticker not in metadata:
                continue
            close = float(row.get("c", 0))
            volume = float(row.get("v", 0))
            dollar_volume = close * volume
            if close < scanner.MIN_PRICE or dollar_volume < scanner.MIN_DOLLAR_VOLUME:
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
        candidates = candidates[: scanner.MARKET_CANDIDATE_BUFFER]
        candidates_by_date[date] = candidates
        unique_tickers.update(item["ticker"] for item in candidates)

    histories = {
        ticker: fetch_history_map(ticker, start_date, latest_date, api_key)
        for ticker in sorted(unique_tickers)
    }
    spy_by_date = histories["SPY"]
    date_index = {date: idx for idx, date in enumerate(all_dates)}

    trades: list[dict] = []
    daily_returns: list[dict] = []
    for date in matured_signal_dates:
        idx = date_index[date]
        spy_history = [spy_by_date[d] for d in all_dates[: idx + 1] if d in spy_by_date]
        spy_closes = [float(row["c"]) for row in spy_history]
        rows: list[dict] = []
        for candidate in candidates_by_date[date]:
            ticker = candidate["ticker"]
            history = [histories[ticker][d] for d in all_dates[: idx + 1] if d in histories[ticker]]
            row = scanner.classify_stock(ticker, candidate, history, spy_closes, date)
            if row and row["goodForSwing"]:
                rows.append(scanner.add_edge_score(row))
        rows = [row for row in rows if row["edgeScore"] >= MIN_EDGE_SCORE and row["atrPct"] <= MAX_ATR_PCT]
        rows.sort(key=lambda row: (row["edgeScore"], row["score"], row["rsVsSpy20d"], row["volumeRatio"]), reverse=True)
        picks = rows[:TOP_N]
        day_trade_returns = []
        for rank, pick in enumerate(picks, start=1):
            ticker = pick["ticker"]
            future_rows = [histories[ticker][d] for d in all_dates[idx + 1 :] if d in histories[ticker]]
            result = resolve_outcome(pick, future_rows)
            trade = {**pick, **result, "rank": rank, "signal_date": date}
            trades.append(trade)
            day_trade_returns.append(trade["return_pct"])
        if day_trade_returns:
            daily_returns.append({"date": date, "avg_return_pct": statistics.mean(day_trade_returns), "picks": len(day_trade_returns)})

    completed = [trade for trade in trades if trade["outcome"] in {"win", "loss", "neutral"}]
    decided = [trade for trade in completed if trade["outcome"] in {"win", "loss"}]
    wins = [trade for trade in completed if trade["outcome"] == "win"]
    losses = [trade for trade in completed if trade["outcome"] == "loss"]
    neutrals = [trade for trade in completed if trade["outcome"] == "neutral"]
    avg_trade_return = statistics.mean(trade["return_pct"] for trade in completed) if completed else 0.0
    median_trade_return = statistics.median(trade["return_pct"] for trade in completed) if completed else 0.0
    avg_daily_return = statistics.mean(day["avg_return_pct"] for day in daily_returns) if daily_returns else 0.0
    positive_days = [day for day in daily_returns if day["avg_return_pct"] > 0]
    negative_days = [day for day in daily_returns if day["avg_return_pct"] < 0]

    setup_counts = Counter(trade["setup"] for trade in completed)
    setup_outcomes: dict[str, Counter] = defaultdict(Counter)
    for trade in completed:
        setup_outcomes[trade["setup"]][trade["outcome"]] += 1

    ticker_returns: dict[str, list[float]] = defaultdict(list)
    for trade in completed:
        ticker_returns[trade["ticker"]].append(trade["return_pct"])
    top_tickers = sorted(
        (
            {
                "ticker": ticker,
                "trades": len(values),
                "avg_return_pct": round(statistics.mean(values), 2),
            }
            for ticker, values in ticker_returns.items()
            if len(values) >= 2
        ),
        key=lambda row: (row["avg_return_pct"], row["trades"]),
        reverse=True,
    )[:10]

    result = {
            "latest_available_date": latest_date,
            "tested_signal_start": matured_signal_dates[0],
            "tested_signal_end": matured_signal_dates[-1],
            "signal_days": len(matured_signal_dates),
            "trades": len(completed),
            "wins": len(wins),
            "losses": len(losses),
            "neutral": len(neutrals),
            "win_rate_excluding_neutral_pct": round((len(wins) / len(decided)) * 100, 1) if decided else 0,
            "target_hit_rate_including_neutral_pct": round((len(wins) / len(completed)) * 100, 1) if completed else 0,
            "avg_trade_return_pct": round(avg_trade_return, 2),
            "median_trade_return_pct": round(median_trade_return, 2),
            "avg_daily_top5_return_pct": round(avg_daily_return, 2),
            "positive_days": len(positive_days),
            "negative_days": len(negative_days),
            "positive_day_rate_pct": round((len(positive_days) / len(daily_returns)) * 100, 1) if daily_returns else 0,
            "best_day_pct": round(max((day["avg_return_pct"] for day in daily_returns), default=0), 2),
            "worst_day_pct": round(min((day["avg_return_pct"] for day in daily_returns), default=0), 2),
            "avg_win_pct": round(statistics.mean(trade["return_pct"] for trade in wins), 2) if wins else 0,
            "avg_loss_pct": round(statistics.mean(trade["return_pct"] for trade in losses), 2) if losses else 0,
            "avg_days_held": round(statistics.mean(trade["days_held"] for trade in completed), 1) if completed else 0,
            "setup_counts": dict(setup_counts),
            "setup_outcomes": {setup: dict(counts) for setup, counts in setup_outcomes.items()},
            "top_repeat_tickers": top_tickers,
            "recent_sample": completed[-10:],
            "trades_detail": completed,
            "daily_returns": daily_returns,
        }
    Path("top5_backtest_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"trades_detail", "daily_returns"}}, indent=2))
    print("Saved full trade details to top5_backtest_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
