from __future__ import annotations

from datetime import date, datetime, timedelta


LEVERAGED_ETFS = {"TQQQ", "SQQQ", "SOXL", "SOXS", "SPXL", "SPXS", "UPRO", "TMF", "TECL", "FAS", "LABU", "LABD"}
INVERSE_ETFS = {"SQQQ", "SOXS", "SPXS", "SH", "PSQ", "RWM", "DOG", "SDS", "QID", "LABD"}

SECTOR_ETFS = {
    "XLB": "Materials",
    "XLC": "Communication Services",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLK": "Technology",
    "XLP": "Consumer Staples",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
    "XLV": "Healthcare",
    "XLY": "Consumer Discretionary",
    "SMH": "Semiconductors",
    "SOXX": "Semiconductors",
    "IGV": "Software",
    "XBI": "Biotech",
    "KRE": "Regional Banks",
}

REGIME_TICKERS = ("SPY", "QQQ", "IWM", "RSP", "HYG", "TLT", "VXX")
MANDATORY_CONTEXT_TICKERS = tuple(dict.fromkeys((*REGIME_TICKERS, *SECTOR_ETFS.keys(), *LEVERAGED_ETFS, *INVERSE_ETFS)))


def next_weekday_on_or_after(target: date, weekday: int) -> date:
    delta = (weekday - target.weekday()) % 7
    return target + timedelta(days=delta)


def first_friday(year: int, month: int) -> date:
    return next_weekday_on_or_after(date(year, month, 1), 4)


def cpi_proxy_date(year: int, month: int) -> date:
    candidate = date(year, month, 11)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def fomc_dates_for_year(year: int) -> list[date]:
    known = {
        2026: [
            date(2026, 1, 28),
            date(2026, 3, 18),
            date(2026, 4, 29),
            date(2026, 6, 17),
            date(2026, 7, 29),
            date(2026, 9, 16),
            date(2026, 10, 28),
            date(2026, 12, 9),
        ]
    }
    return known.get(year, [])


def earnings_windows_for_year(year: int) -> list[tuple[str, date, date]]:
    return [
        ("Q4 earnings season", date(year, 1, 10), date(year, 2, 15)),
        ("Q1 earnings season", date(year, 4, 10), date(year, 5, 15)),
        ("Q2 earnings season", date(year, 7, 10), date(year, 8, 15)),
        ("Q3 earnings season", date(year, 10, 10), date(year, 11, 15)),
    ]


def event_calendar(as_of: datetime) -> dict:
    today = as_of.date()
    years = {today.year, today.year + 1}
    upcoming: list[dict] = []
    active_windows: list[str] = []

    for year in sorted(years):
        for month in range(1, 13):
            jobs_day = first_friday(year, month)
            if jobs_day >= today - timedelta(days=2):
                upcoming.append(
                    {
                        "name": "Jobs report",
                        "date": jobs_day,
                        "impact": "High",
                        "notes": "Payrolls, unemployment, and wage growth can move index, rate, and financial ETFs quickly.",
                        "exact": True,
                    }
                )
            cpi_day = cpi_proxy_date(year, month)
            if cpi_day >= today - timedelta(days=2):
                upcoming.append(
                    {
                        "name": "CPI release window",
                        "date": cpi_day,
                        "impact": "High",
                        "notes": "Mid-month inflation prints often reshape rate expectations. Date is a proxy window, not an official release calendar.",
                        "exact": False,
                    }
                )

        for fomc_day in fomc_dates_for_year(year):
            if fomc_day >= today - timedelta(days=3):
                upcoming.append(
                    {
                        "name": "FOMC decision",
                        "date": fomc_day,
                        "impact": "High",
                        "notes": "Rates, dot plot, and Powell commentary tend to hit broad, tech, and bond ETFs at once.",
                        "exact": True,
                    }
                )

        for label, start, end in earnings_windows_for_year(year):
            if end >= today - timedelta(days=1):
                if start <= today <= end:
                    active_windows.append(label)
                upcoming.append(
                    {
                        "name": label,
                        "date": start,
                        "impact": "Medium",
                        "notes": "Sector ETFs tied to tech, banks, semis, and consumer names often become more headline-sensitive during earnings season.",
                        "exact": False,
                    }
                )

    deduped: list[dict] = []
    seen: set[tuple[str, date]] = set()
    for event in sorted(upcoming, key=lambda item: item["date"]):
        key = (event["name"], event["date"])
        if key in seen:
            continue
        seen.add(key)
        event["daysAway"] = (event["date"] - today).days
        deduped.append(event)

    next_events = [event for event in deduped if event["daysAway"] >= 0][:6]
    pressure_score = 0
    trigger_names: list[str] = []
    for event in next_events:
        if event["daysAway"] <= 1:
            pressure_score += 3 if event["impact"] == "High" else 2
            trigger_names.append(event["name"])
        elif event["daysAway"] <= 3:
            pressure_score += 2 if event["impact"] == "High" else 1
            trigger_names.append(event["name"])
        elif event["daysAway"] <= 7:
            pressure_score += 1

    if pressure_score >= 5 or active_windows:
        level = "High"
    elif pressure_score >= 2:
        level = "Medium"
    else:
        level = "Low"

    if level == "High":
        summary = "Event risk is elevated. Favor smaller size, wider patience, and more caution around leveraged ETFs."
    elif level == "Medium":
        summary = "Event risk is rising. Good setups can still work, but gaps and reversals are more likely."
    else:
        summary = "No major near-term event cluster. Trend and sector rotation matter more than the macro calendar right now."

    return {
        "asOf": today.isoformat(),
        "level": level,
        "summary": summary,
        "triggers": trigger_names,
        "activeWindows": active_windows,
        "upcoming": [
            {
                **event,
                "date": event["date"].isoformat(),
            }
            for event in next_events
        ],
    }


def build_sector_rotation(rows: list[dict]) -> dict:
    sector_rows = [row for row in rows if row["ticker"] in SECTOR_ETFS]
    scored = []
    for row in sector_rows:
        strength = row["score"] + (row["rsVsSpy20d"] * 2.0) + (6 if row["goodForSwing"] else 0)
        scored.append({**row, "strengthScore": round(strength, 2), "sectorLabel": SECTOR_ETFS[row["ticker"]]})
    scored.sort(key=lambda row: row["strengthScore"], reverse=True)
    strongest = scored[:5]
    weakest = list(reversed(scored[-3:])) if len(scored) >= 3 else list(reversed(scored))
    booming = strongest[0] if strongest else None
    improving = sorted(sector_rows, key=lambda row: row["rsVsSpy20d"], reverse=True)[:5]
    return {
        "booming": booming,
        "strongest": strongest,
        "weakest": weakest,
        "improving": improving,
    }


def build_market_regime(rows_by_ticker: dict[str, dict]) -> dict:
    spy = rows_by_ticker.get("SPY")
    qqq = rows_by_ticker.get("QQQ")
    iwm = rows_by_ticker.get("IWM")
    rsp = rows_by_ticker.get("RSP")
    hyg = rows_by_ticker.get("HYG")
    tlt = rows_by_ticker.get("TLT")
    vxx = rows_by_ticker.get("VXX")

    def long_gate(row: dict | None) -> bool:
        return bool(row and row["close"] > row["ema20"] and row["close"] > row["sma50"])

    bullish_points = 0
    notes: list[str] = []
    if long_gate(spy):
        bullish_points += 1
        notes.append("SPY above 20 EMA and 50 SMA")
    if long_gate(qqq):
        bullish_points += 1
        notes.append("QQQ above 20 EMA and 50 SMA")
    if iwm and iwm["rsVsSpy20d"] > 0:
        bullish_points += 1
        notes.append("Small caps outperforming SPY")
    if rsp and rsp["rsVsSpy20d"] > 0:
        bullish_points += 1
        notes.append("Equal-weight breadth improving")
    if hyg and tlt and hyg["score"] >= tlt["score"]:
        bullish_points += 1
        notes.append("Credit acting at least as well as duration")
    if vxx and vxx["setup"] == "Avoid":
        bullish_points += 1
        notes.append("Volatility products not in leadership")

    if bullish_points >= 5:
        regime = "Risk-On"
        summary = "Broad tape, tech, and breadth are aligned. Trend-following longs have the wind at their back."
    elif bullish_points >= 3:
        regime = "Balanced"
        summary = "Tape is workable, but leadership is uneven. Favor higher-quality setups over aggressive leverage."
    else:
        regime = "Defensive"
        summary = "Breadth or credit is weak. Capital preservation matters more than forcing new swings."

    leveraged_longs_ok = long_gate(spy) and long_gate(qqq) and regime != "Defensive"
    return {
        "regime": regime,
        "summary": summary,
        "leveragedLongsOk": leveraged_longs_ok,
        "notes": notes[:5],
    }


def classify_event_sensitivity(row: dict, calendar: dict, regime: dict) -> dict:
    score = 0
    notes: list[str] = []
    ticker = row["ticker"]
    category = row.get("category", "")

    if ticker in LEVERAGED_ETFS or ticker in INVERSE_ETFS:
        score += 2
        notes.append("leveraged/inverse product")
    if category in {"Technology / Growth", "Thematic"} and calendar["activeWindows"]:
        score += 1
        notes.append("earnings season sensitivity")
    if category in {"Financials", "Fixed Income"} and any(trigger in {"Jobs report", "CPI release window", "FOMC decision"} for trigger in calendar["triggers"]):
        score += 1
        notes.append("macro-rate sensitivity")
    if category in {"Commodity / Alternative", "Energy"} and regime["regime"] == "Defensive":
        score += 1
        notes.append("headline-driven when risk appetite weakens")
    if calendar["level"] == "High":
        score += 1

    if score >= 4:
        level = "High"
    elif score >= 2:
        level = "Medium"
    else:
        level = "Low"

    if ticker in LEVERAGED_ETFS and not regime["leveragedLongsOk"]:
        notes.append("leveraged longs not favored by current regime")

    note = ", ".join(notes) if notes else "normal event sensitivity"
    return {"level": level, "note": note}
