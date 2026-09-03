from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import mag7_scanner as scanner
from offline_top2_backtest import DATA_DIR, load_daily_from_5m, pct_change


OUTPUT_JSON = Path(__file__).with_name("offline_portfolio_strategy_backtest.json")
OUTPUT_MD = Path(__file__).with_name("OFFLINE_PORTFOLIO_STRATEGY_BACKTEST.md")
START_CAPITAL = 100_000.0
MATURITY_BUFFER_DAYS = 18


def max_drawdown_pct(values: list[float]) -> float:
    peak = values[0] if values else 1.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, (value / peak - 1) * 100)
    return worst


def load_histories() -> tuple[dict[str, list[dict]], list[str], list[str]]:
    histories = {path.stem: load_daily_from_5m(path) for path in sorted(DATA_DIR.glob("*.json"))}
    if "SPY" not in histories:
        raise RuntimeError("SPY history is required")
    dates = [row["date"] for row in histories["SPY"]]
    tradable = [ticker for ticker in histories if ticker != "SPY"]
    return histories, dates, tradable


def aligned_history(histories_by_date: dict[str, dict[str, dict]], ticker: str, dates: list[str]) -> list[dict]:
    return [histories_by_date[ticker][date] for date in dates if date in histories_by_date[ticker]]


def signal_rows(histories: dict[str, list[dict]], dates: list[str], tradable: list[str]) -> dict[str, list[dict]]:
    histories_by_date = {ticker: {row["date"]: row for row in rows} for ticker, rows in histories.items()}
    spy_closes_by_date = {row["date"]: float(row["c"]) for row in histories["SPY"]}
    rows_by_date: dict[str, list[dict]] = {}
    active_key: dict[str, tuple[str, str] | None] = {ticker: None for ticker in tradable}
    active_start: dict[str, str | None] = {ticker: None for ticker in tradable}
    active_entry: dict[str, float | None] = {ticker: None for ticker in tradable}

    for idx, date in enumerate(dates):
        if idx < scanner.LOOKBACK_DAYS:
            continue
        so_far = dates[: idx + 1]
        spy_closes = [spy_closes_by_date[d] for d in so_far if d in spy_closes_by_date]
        day_rows: list[dict] = []
        seen_today: set[str] = set()
        for ticker in tradable:
            history = aligned_history(histories_by_date, ticker, so_far)
            if len(history) < scanner.LOOKBACK_DAYS:
                continue
            meta = {"name": ticker, "exchange": "LOCAL"}
            row = scanner.classify_stock(ticker, meta, history, spy_closes, date)
            if not row or not row["goodForSwing"]:
                active_key[ticker] = None
                active_start[ticker] = None
                active_entry[ticker] = None
                continue
            key = (row["setup"], "good")
            if active_key[ticker] != key:
                active_key[ticker] = key
                active_start[ticker] = date
                active_entry[ticker] = row["close"]
            row["firstSeenDate"] = active_start[ticker] or date
            row["daysInList"] = max(1, idx - dates.index(row["firstSeenDate"]) + 1)
            row["entryChangePct"] = round(pct_change(row["close"], active_entry[ticker] or row["close"]), 2)
            row["similarSetups"] = 0
            row["setupWins"] = 0
            row["setupLosses"] = 0
            row["setupNeutral"] = 0
            row["setupConfidence"] = None
            row["confidenceLabel"] = "N/A"
            row = scanner.add_edge_score(row)
            day_rows.append(row)
            seen_today.add(ticker)
        for ticker in tradable:
            if ticker not in seen_today:
                active_key[ticker] = None
                active_start[ticker] = None
                active_entry[ticker] = None
        rows_by_date[date] = day_rows
    return rows_by_date


def row_passes(row: dict, cfg: dict) -> bool:
    if row["score"] < cfg["min_score"]:
        return False
    if row["edgeScore"] < cfg["min_edge"]:
        return False
    if row["atrPct"] > cfg["max_atr"]:
        return False
    if row["daysInList"] != 1:
        return False
    if row["entryChangePct"] < cfg["entry_min"] or row["entryChangePct"] > cfg["entry_max"]:
        return False
    if cfg["setups"] != "all" and row["setup"] not in cfg["setups"]:
        return False
    return True


def rank_rows(rows: list[dict], rank_by: str) -> list[dict]:
    if rank_by == "edge":
        key = lambda r: (r["edgeScore"], r["score"], r["rsVsSpy20d"], r["volumeRatio"])
    elif rank_by == "rs":
        key = lambda r: (r["rsVsSpy20d"], r["edgeScore"], r["score"], r["volumeRatio"])
    else:
        key = lambda r: (r["score"], r["edgeScore"], r["rsVsSpy20d"], r["volumeRatio"])
    return sorted(rows, key=key, reverse=True)


def should_exit(position: dict, bar: dict, cfg: dict) -> tuple[bool, float, str]:
    high = float(bar["h"])
    low = float(bar["l"])
    close = float(bar["c"])
    days_held = position["days_held"] + 1
    if low <= position["stopLoss"]:
        return True, position["stopLoss"], "stop"
    if cfg["target"] == "target2" and high >= position["target2"]:
        return True, position["target2"], "target2"
    if high >= position["target1"]:
        return True, position["target1"], "target1"
    if days_held >= cfg["max_hold"]:
        return True, close, "time"
    return False, close, "hold"


def simulate(cfg: dict, histories: dict[str, list[dict]], dates: list[str], rows_by_date: dict[str, list[dict]]) -> dict:
    histories_by_date = {ticker: {row["date"]: row for row in rows} for ticker, rows in histories.items()}
    test_dates = [date for date in dates if date in rows_by_date][:-MATURITY_BUFFER_DAYS]
    cash = START_CAPITAL
    open_positions: list[dict] = []
    closed: list[dict] = []
    equity_curve: list[dict] = []

    for date in test_dates:
        still_open: list[dict] = []
        for pos in open_positions:
            bar = histories_by_date[pos["ticker"]].get(date)
            if not bar:
                still_open.append(pos)
                continue
            do_exit, exit_price, reason = should_exit(pos, bar, cfg)
            pos["days_held"] += 1
            if do_exit:
                exit_value = pos["shares"] * exit_price
                cash += exit_value
                ret = pct_change(exit_price, pos["entryPrice"])
                closed.append(
                    {
                        **pos,
                        "exitDate": date,
                        "exitPrice": round(exit_price, 2),
                        "exitReason": reason,
                        "return_pct": round(ret, 2),
                        "pnl": round(exit_value - pos["capital"], 2),
                    }
                )
            else:
                still_open.append(pos)
        open_positions = still_open

        available_slots = cfg["max_positions"] - len(open_positions)
        if available_slots > 0:
            held = {pos["ticker"] for pos in open_positions}
            candidates = [row for row in rows_by_date[date] if row["ticker"] not in held and row_passes(row, cfg)]
            entries = rank_rows(candidates, cfg["rank_by"])[:available_slots]
            for row in entries:
                slots_left = cfg["max_positions"] - len(open_positions)
                if slots_left <= 0 or cash <= 0:
                    break
                allocation = cash / slots_left
                shares = allocation / row["close"]
                cash -= allocation
                open_positions.append(
                    {
                        "ticker": row["ticker"],
                        "setup": row["setup"],
                        "signalDate": date,
                        "firstSeenDate": row["firstSeenDate"],
                        "entryPrice": row["close"],
                        "shares": shares,
                        "capital": allocation,
                        "stopLoss": row["stopLoss"],
                        "target1": row["target1"],
                        "target2": row["target2"],
                        "score": row["score"],
                        "edgeScore": row["edgeScore"],
                        "rsVsSpy20d": row["rsVsSpy20d"],
                        "atrPct": row["atrPct"],
                        "days_held": 0,
                    }
                )

        marked_open = 0.0
        for pos in open_positions:
            bar = histories_by_date[pos["ticker"]].get(date)
            price = float(bar["c"]) if bar else pos["entryPrice"]
            marked_open += pos["shares"] * price
        equity_curve.append({"date": date, "equity": cash + marked_open, "cash": cash, "open": len(open_positions)})

    final_equity = equity_curve[-1]["equity"] if equity_curve else START_CAPITAL
    wins = [t for t in closed if t["return_pct"] > 0]
    losses = [t for t in closed if t["return_pct"] < 0]
    by_reason = Counter(t["exitReason"] for t in closed)
    by_setup = Counter(t["setup"] for t in closed)
    days_held = [t["days_held"] for t in closed]
    exposure_days = sum(1 for row in equity_curve if row["open"] > 0)
    years = max(len(test_dates) / 252, 1 / 252)
    total_return = (final_equity / START_CAPITAL - 1) * 100
    annual_return = ((final_equity / START_CAPITAL) ** (1 / years) - 1) * 100 if final_equity > 0 else -100
    return {
        "config": cfg,
        "summary": {
            "start": test_dates[0] if test_dates else None,
            "end": test_dates[-1] if test_dates else None,
            "trading_days": len(test_dates),
            "starting_capital": START_CAPITAL,
            "ending_value": round(final_equity, 2),
            "total_return_pct": round(total_return, 2),
            "annualized_return_pct": round(annual_return, 2),
            "max_drawdown_pct": round(max_drawdown_pct([row["equity"] for row in equity_curve]), 2),
            "trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "avg_trade_return_pct": round(statistics.mean(t["return_pct"] for t in closed), 2) if closed else 0.0,
            "median_trade_return_pct": round(statistics.median(t["return_pct"] for t in closed), 2) if closed else 0.0,
            "avg_days_held": round(statistics.mean(days_held), 1) if days_held else 0.0,
            "median_days_held": round(statistics.median(days_held), 1) if days_held else 0.0,
            "exposure_days": exposure_days,
            "exposure_rate_pct": round(exposure_days / len(test_dates) * 100, 1) if test_dates else 0.0,
            "exit_reasons": dict(by_reason),
            "setup_counts": dict(by_setup),
        },
        "trades": closed,
        "equity_curve": equity_curve,
    }


def configs() -> list[dict]:
    out = []
    for max_positions in [1, 2, 3]:
        for rank_by in ["edge", "rs", "score"]:
            for max_hold in [3, 5, 8, 10, 13, 15, 18]:
                for min_score in [55, 75, 95]:
                    for min_edge in [-20, 0, 25, 45, 60]:
                        for max_atr in [4.5, 6.0, 8.0]:
                            out.append(
                                {
                                    "name": f"{max_positions}slot {rank_by} hold{max_hold} score{min_score} edge{min_edge} atr{max_atr}",
                                    "max_positions": max_positions,
                                    "rank_by": rank_by,
                                    "max_hold": max_hold,
                                    "target": "target1",
                                    "min_score": min_score,
                                    "min_edge": min_edge,
                                    "max_atr": max_atr,
                                    "entry_min": -3.0,
                                    "entry_max": 2.0,
                                    "setups": "all",
                                }
                            )
    return out


def main() -> int:
    histories, dates, tradable = load_histories()
    rows_by_date = signal_rows(histories, dates, tradable)
    results = [simulate(cfg, histories, dates, rows_by_date) for cfg in configs()]
    viable = [r for r in results if r["summary"]["trades"] >= 8]
    best_return = sorted(
        viable,
        key=lambda r: (r["summary"]["annualized_return_pct"], r["summary"]["total_return_pct"], -abs(r["summary"]["max_drawdown_pct"])),
        reverse=True,
    )[:20]
    best_risk = sorted(
        viable,
        key=lambda r: (
            r["summary"]["annualized_return_pct"] / abs(r["summary"]["max_drawdown_pct"])
            if r["summary"]["max_drawdown_pct"]
            else math.inf,
            r["summary"]["annualized_return_pct"],
        ),
        reverse=True,
    )[:20]
    payload = {
        "dataset": str(DATA_DIR),
        "symbols": sorted(histories),
        "tradable_symbols": sorted(tradable),
        "assumption": "Enter only when a ticker first appears in the bot list; hold until stop, target1, or max hold days; replace only when a position slot opens.",
        "configs_tested": len(results),
        "viable_configs": len(viable),
        "best_return": best_return,
        "best_risk_adjusted": best_risk,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def one_line(r: dict) -> str:
        c = r["config"]
        s = r["summary"]
        return (
            f"- slots={c['max_positions']} rank={c['rank_by']} hold={c['max_hold']}d "
            f"score>={c['min_score']} edge>={c['min_edge']} atr<={c['max_atr']}: "
            f"annual={s['annualized_return_pct']}%, total={s['total_return_pct']}%, "
            f"trades={s['trades']}, win={s['win_rate_pct']}%, avg_hold={s['avg_days_held']}d, "
            f"avg_trade={s['avg_trade_return_pct']}%, dd={s['max_drawdown_pct']}%"
        )

    md = [
        "# Offline Portfolio Strategy Backtest",
        "",
        f"Dataset: `{DATA_DIR}`",
        f"Symbols: {', '.join(sorted(histories))}",
        "",
        payload["assumption"],
        "",
        "Important limitation: this local dataset contains only the symbols above, not the full 500-stock Swing Bot universe.",
        "",
        "## Best Return",
        "",
        *[one_line(r) for r in best_return[:10]],
        "",
        "## Best Risk Adjusted",
        "",
        *[one_line(r) for r in best_risk[:10]],
        "",
    ]
    if best_return:
        best = best_return[0]
        md.extend(
            [
                "## Recommended Rule From This Test",
                "",
                one_line(best),
                "",
                "Operational rule: do not buy two new stocks every day. Enter only fresh first-day names, keep a fixed number of slots, and replace only after stop, target, or time exit.",
            ]
        )
    OUTPUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ["symbols", "tradable_symbols", "configs_tested", "viable_configs"]}, indent=2))
    print("BEST_RETURN")
    for row in best_return[:10]:
        print(one_line(row))
    print("BEST_RISK")
    for row in best_risk[:10]:
        print(one_line(row))
    print(f"Saved {OUTPUT_JSON.name} and {OUTPUT_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
