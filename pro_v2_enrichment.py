from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path


OUTPUT = Path(__file__).with_name("swingbot-pro-v2-data.json")


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=6) as response:
        return json.loads(response.read().decode("utf-8"))


def safe_fetch(url: str) -> dict | None:
    try:
        return fetch_json(url)
    except Exception:
        return None


def score_news(results: list[dict]) -> dict:
    positive_words = {
        "beat",
        "beats",
        "raise",
        "raised",
        "upgrade",
        "upgraded",
        "growth",
        "record",
        "approval",
        "partnership",
        "buyback",
    }
    negative_words = {
        "miss",
        "misses",
        "cut",
        "downgrade",
        "downgraded",
        "lawsuit",
        "probe",
        "investigation",
        "offering",
        "dilution",
        "recall",
    }
    score = 0
    headlines: list[str] = []
    for item in results[:8]:
        title = str(item.get("title") or "").strip()
        if title and len(headlines) < 3:
            headlines.append(title)
        text = f"{title} {item.get('description') or ''}".lower()
        sentiment = str(item.get("insights", [{}])[0].get("sentiment", "")).lower()
        if "positive" in sentiment:
            score += 2
        elif "negative" in sentiment:
            score -= 2
        score += sum(1 for word in positive_words if word in text)
        score -= sum(1 for word in negative_words if word in text)
    label = "Positive" if score >= 3 else "Negative" if score <= -3 else "Neutral"
    return {"score": max(-10, min(10, score)), "label": label, "headlines": headlines}


def fetch_news(ticker: str, api_key: str) -> dict:
    published_after = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d")
    query = urllib.parse.urlencode(
        {
            "ticker": ticker,
            "published_utc.gte": published_after,
            "order": "desc",
            "limit": "10",
            "sort": "published_utc",
            "apiKey": api_key,
        }
    )
    payload = safe_fetch(f"https://api.massive.com/v2/reference/news?{query}")
    if not payload:
        return {"available": False, "score": 0, "label": "Unavailable", "headlines": []}
    return {"available": True, **score_news(payload.get("results", []))}


def fetch_options_snapshot(ticker: str, api_key: str) -> dict:
    query = urllib.parse.urlencode({"limit": "80", "apiKey": api_key})
    payload = safe_fetch(f"https://api.massive.com/v3/snapshot/options/{urllib.parse.quote(ticker)}?{query}")
    if not payload:
        return {"available": False, "score": 0, "label": "Unavailable"}
    contracts = payload.get("results", [])
    call_oi = put_oi = call_vol = put_vol = 0.0
    for row in contracts:
        details = row.get("details") or {}
        typ = str(details.get("contract_type") or "").lower()
        oi = float(row.get("open_interest") or 0)
        day = row.get("day") or {}
        vol = float(day.get("volume") or 0)
        if typ == "call":
            call_oi += oi
            call_vol += vol
        elif typ == "put":
            put_oi += oi
            put_vol += vol
    oi_ratio = call_oi / put_oi if put_oi else 0.0
    vol_ratio = call_vol / put_vol if put_vol else 0.0
    score = 0
    if oi_ratio >= 1.5:
        score += 5
    elif oi_ratio >= 1.1:
        score += 2
    elif 0 < oi_ratio < 0.8:
        score -= 4
    if vol_ratio >= 1.5:
        score += 5
    elif vol_ratio >= 1.1:
        score += 2
    elif 0 < vol_ratio < 0.8:
        score -= 4
    label = "Bullish" if score >= 5 else "Bearish" if score <= -4 else "Mixed"
    return {
        "available": bool(contracts),
        "score": max(-10, min(10, score)),
        "label": label,
        "callOi": round(call_oi),
        "putOi": round(put_oi),
        "callPutOi": round(oi_ratio, 2) if oi_ratio else None,
        "callPutVolume": round(vol_ratio, 2) if vol_ratio else None,
    }


def quality_from_row(row: dict) -> dict:
    score = 0
    if row.get("score", 0) >= 90:
        score += 4
    if row.get("rsVsSpy20d", 0) >= 7:
        score += 3
    if 50 <= row.get("rsi14", 0) <= 68:
        score += 2
    if row.get("volumeRatio", 0) >= 1:
        score += 1
    if row.get("atrPct", 99) > 6:
        score -= 4
    if row.get("entryChangePct", 0) > 3 or row.get("entryChangePct", 0) < -3:
        score -= 4
    label = "Strong" if score >= 6 else "Weak" if score <= 1 else "Neutral"
    return {"available": True, "score": max(-10, min(10, score)), "label": label}


def build(rows: list[dict], limit: int = 35) -> dict:
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    payload = {
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "massive" if api_key else "technical-only",
        "items": {},
    }
    candidates = [
        row
        for row in rows
        if row.get("goodForSwing") and row.get("setup") != "Avoid"
    ][:limit]
    for row in candidates:
        ticker = row["ticker"]
        item = {"quality": quality_from_row(row)}
        if api_key:
            item["news"] = fetch_news(ticker, api_key)
            time.sleep(0.12)
            item["options"] = fetch_options_snapshot(ticker, api_key)
            time.sleep(0.12)
        else:
            item["news"] = {"available": False, "score": 0, "label": "No API key", "headlines": []}
            item["options"] = {"available": False, "score": 0, "label": "No API key"}
        payload["items"][ticker] = item
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
