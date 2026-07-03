from __future__ import annotations

import json
import statistics
from pathlib import Path

import backtest_top5
import mag7_scanner as scanner


EDGE_THRESHOLDS = [20, 25, 30, 35, 40, 45, 50, 55]
ATR_CAPS = [6.0, 7.0, 8.0, 10.0, 99.0]
MIN_SCORE_VALUES = [55, 65, 75]
TOP_N = 5


def summarize(completed: list[dict], daily_returns: list[dict]) -> dict:
    decided = [trade for trade in completed if trade["outcome"] in {"win", "loss"}]
    wins = [trade for trade in completed if trade["outcome"] == "win"]
    losses = [trade for trade in completed if trade["outcome"] == "loss"]
    positive_days = [day for day in daily_returns if day["avg_return_pct"] > 0]
    negative_days = [day for day in daily_returns if day["avg_return_pct"] < 0]
    return {
        "trades": len(completed),
        "avg_picks_per_signal_day": round(len(completed) / backtest_top5.BACKTEST_TRADING_DAYS, 2),
        "wins": len(wins),
        "losses": len(losses),
        "neutral": len([trade for trade in completed if trade["outcome"] == "neutral"]),
        "win_rate_ex_neutral_pct": round((len(wins) / len(decided)) * 100, 1) if decided else 0,
        "avg_trade_return_pct": round(statistics.mean(trade["return_pct"] for trade in completed), 2) if completed else 0,
        "median_trade_return_pct": round(statistics.median(trade["return_pct"] for trade in completed), 2) if completed else 0,
        "positive_day_rate_pct": round((len(positive_days) / len(daily_returns)) * 100, 1) if daily_returns else 0,
        "positive_days": len(positive_days),
        "negative_days": len(negative_days),
        "worst_day_pct": round(min((day["avg_return_pct"] for day in daily_returns), default=0), 2),
    }


def main() -> int:
    api_key = scanner.get_api_key()
    all_dates = backtest_top5.fetch_spy_dates(api_key)
    latest_date = all_dates[-1]
    matured_signal_dates = all_dates[
        -(backtest_top5.BACKTEST_TRADING_DAYS + backtest_top5.MATURITY_BUFFER_DAYS) : -backtest_top5.MATURITY_BUFFER_DAYS
    ]
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
        ticker: backtest_top5.fetch_history_map(ticker, start_date, latest_date, api_key)
        for ticker in sorted(unique_tickers)
    }
    spy_by_date = histories["SPY"]
    date_index = {date: idx for idx, date in enumerate(all_dates)}

    signal_rows: dict[str, list[dict]] = {}
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
        signal_rows[date] = rows

    sweep: list[dict] = []
    for min_score in MIN_SCORE_VALUES:
        for edge in EDGE_THRESHOLDS:
            for max_atr in ATR_CAPS:
                completed: list[dict] = []
                daily_returns: list[dict] = []
                for date in matured_signal_dates:
                    idx = date_index[date]
                    rows = [
                        row
                        for row in signal_rows[date]
                        if row["score"] >= min_score and row["edgeScore"] >= edge and row["atrPct"] <= max_atr
                    ]
                    rows.sort(
                        key=lambda row: (row["edgeScore"], row["score"], row["rsVsSpy20d"], row["volumeRatio"]),
                        reverse=True,
                    )
                    day_returns: list[float] = []
                    for rank, pick in enumerate(rows[:TOP_N], start=1):
                        ticker = pick["ticker"]
                        future_rows = [histories[ticker][d] for d in all_dates[idx + 1 :] if d in histories[ticker]]
                        result = backtest_top5.resolve_outcome(pick, future_rows)
                        if result["outcome"] in {"win", "loss", "neutral"}:
                            trade = {**pick, **result, "rank": rank, "signal_date": date}
                            completed.append(trade)
                            day_returns.append(trade["return_pct"])
                    if day_returns:
                        daily_returns.append({"date": date, "avg_return_pct": statistics.mean(day_returns), "picks": len(day_returns)})
                summary = summarize(completed, daily_returns)
                sweep.append({"min_score": min_score, "min_edge": edge, "max_atr": max_atr, **summary})

    sweep.sort(
        key=lambda row: (
            row["avg_trade_return_pct"],
            row["positive_day_rate_pct"],
            row["avg_picks_per_signal_day"],
        ),
        reverse=True,
    )
    practical = [
        row
        for row in sweep
        if row["avg_picks_per_signal_day"] >= 3.0
        and row["trades"] >= 250
        and row["avg_trade_return_pct"] > 0
        and row["win_rate_ex_neutral_pct"] >= 50
    ]
    practical.sort(
        key=lambda row: (
            row["avg_trade_return_pct"],
            row["positive_day_rate_pct"],
            row["win_rate_ex_neutral_pct"],
        ),
        reverse=True,
    )

    result = {
        "latest_available_date": latest_date,
        "tested_signal_start": matured_signal_dates[0],
        "tested_signal_end": matured_signal_dates[-1],
        "best_overall": sweep[:15],
        "best_practical": practical[:15],
    }
    Path("filter_sweep_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
