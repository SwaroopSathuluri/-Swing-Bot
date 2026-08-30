from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fundamentals_research import (
    RESEARCH_UNIVERSE,
    _four_contiguous_quarters,
    _strength_score,
    _sum_present,
    _technical_snapshot,
    _valuation_score,
    assemble_company,
    build_fundamentals_feed,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def price_rows(count: int = 280) -> list[dict]:
    first = NOW - timedelta(days=count)
    rows: list[dict] = []
    for index in range(count):
        close = 100.0 + index * 0.18 + (index % 7 - 3) * 0.10
        at = first + timedelta(days=index + 1)
        rows.append({
            "t": int(at.timestamp() * 1000),
            "o": close - 0.4,
            "h": close + 1.0,
            "l": close - 1.0,
            "c": close,
            "v": 1_000_000,
        })
    return rows


def income_rows(year: int = 2026) -> list[dict]:
    return [
        {"period_end": f"{year}-06-30", "filing_date": f"{year}-07-25", "revenue": 120, "gross_profit": 72, "operating_income": 30, "net_income_loss_attributable_common_shareholders": 24, "diluted_earnings_per_share": 1.20, "total_other_income_expense": 0},
        {"period_end": f"{year}-03-31", "filing_date": f"{year}-04-25", "revenue": 112, "gross_profit": 66, "operating_income": 27, "net_income_loss_attributable_common_shareholders": 21, "diluted_earnings_per_share": 1.05, "total_other_income_expense": 0},
        {"period_end": f"{year - 1}-12-31", "filing_date": f"{year}-01-25", "revenue": 108, "gross_profit": 63, "operating_income": 25, "net_income_loss_attributable_common_shareholders": 19, "diluted_earnings_per_share": 0.95, "total_other_income_expense": 0},
        {"period_end": f"{year - 1}-09-30", "filing_date": f"{year - 1}-10-25", "revenue": 104, "gross_profit": 60, "operating_income": 23, "net_income_loss_attributable_common_shareholders": 18, "diluted_earnings_per_share": 0.90, "total_other_income_expense": 0},
        {"period_end": f"{year - 1}-06-30", "filing_date": f"{year - 1}-07-25", "revenue": 100, "gross_profit": 56, "operating_income": 20, "net_income_loss_attributable_common_shareholders": 16, "diluted_earnings_per_share": 0.80, "total_other_income_expense": 0},
    ]


def complete_inputs() -> dict:
    snapshot_time = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)
    return {
        "overview": {"ticker": "TEST", "name": "Test Company", "locale": "us", "type": "CS", "sic_code": "3571", "sic_description": "Technology", "cik": "0000123456", "market_cap": 1_000},
        "ratios": {"ticker": "TEST", "date": "2026-08-28", "market_cap": 1_000, "free_cash_flow": 40, "return_on_equity": 0.25, "debt_to_equity": 0.30, "current": 2.0, "price_to_earnings": 24, "price_to_sales": 6, "price_to_free_cash_flow": 25, "ev_to_ebitda": 18, "cik": "123456"},
        "income": income_rows(),
        "balance": [{"period_end": "2026-06-30", "filing_date": "2026-07-25", "cash_and_equivalents": 50, "short_term_investments": 20, "debt_current": 4, "long_term_debt_and_capital_lease_obligations": 16, "total_equity": 100}],
        "cash": [
            {"period_end": "2026-06-30", "filing_date": "2026-07-25", "net_cash_from_operating_activities": 50, "purchase_of_property_plant_and_equipment": -10},
            {"period_end": "2025-06-30", "filing_date": "2025-07-25", "net_cash_from_operating_activities": 35, "purchase_of_property_plant_and_equipment": -10},
        ],
        "prices": price_rows(),
        "snapshot": {"ticker": "TEST", "market_status": "closed", "session": {"close": 150, "previous_close": 148, "last_updated": int(snapshot_time.timestamp() * 1e9)}},
    }


def assemble(ticker: str = "TEST", **overrides: object) -> dict:
    values = complete_inputs()
    values.update(overrides)
    return assemble_company(
        ticker,
        values["overview"],
        values["ratios"],
        values["income"],
        values["balance"],
        values["cash"],
        values["prices"],
        values["snapshot"],
        [],
        now=NOW,
        theme="Test theme",
    )


class FundamentalsResearchTests(unittest.TestCase):
    def test_four_quarters_must_be_complete_and_contiguous(self) -> None:
        rows = income_rows()
        self.assertEqual(len(_four_contiguous_quarters(rows) or []), 4)
        missing_middle = [rows[0], rows[1], rows[3], rows[4]]
        self.assertIsNone(_four_contiguous_quarters(missing_middle))

    def test_ttm_fcf_margin_uses_four_quarters(self) -> None:
        result = assemble()
        self.assertEqual(result["data_status"], "ready")
        self.assertAlmostEqual(result["metrics"]["fcf_margin"], 9.0)
        self.assertIsNotNone(result["scores"]["overall"])

    def test_closed_snapshot_change_uses_prior_completed_bar(self) -> None:
        rows = price_rows(30)
        rows[-2]["c"] = 100.0
        rows[-1]["c"] = 150.0
        snapshot = {
            "market_status": "closed",
            "session": {
                "close": 150.0,
                "previous_close": 150.0,
                "last_updated": int(NOW.timestamp() * 1e9),
            },
        }
        technical = _technical_snapshot(rows, snapshot)
        self.assertEqual(technical["price"], 150.0)
        self.assertEqual(technical["change_pct"], 50.0)

    def test_incomplete_quarters_cannot_produce_fcf_margin_or_action(self) -> None:
        rows = income_rows()
        result = assemble(income=[rows[0], rows[-1]])
        self.assertIsNone(result["metrics"]["fcf_margin"])
        self.assertEqual(result["data_status"], "insufficient")
        self.assertEqual(result["action"], "INSUFFICIENT DATA")
        self.assertIsNone(result["scores"]["overall"])

    def test_missing_balance_components_stay_missing(self) -> None:
        self.assertIsNone(_sum_present({"total_assets": 500}, ("cash_and_equivalents", "short_term_investments")))
        self.assertIsNone(_sum_present({"cash_and_equivalents": 50}, ("cash_and_equivalents", "short_term_investments")))
        self.assertIsNone(_sum_present({"debt_current": 4}, ("debt_current", "long_term_debt_and_capital_lease_obligations")))
        result = assemble(balance=[{"period_end": "2026-06-30", "total_assets": 500}])
        self.assertIsNone(result["metrics"]["net_cash"])
        self.assertEqual(result["data_status"], "insufficient")

    def test_negative_equity_is_penalized_not_rewarded(self) -> None:
        healthy, _ = _strength_score(-50, 1_000, 1.1, 0.3, False)
        negative, _ = _strength_score(-50, 1_000, 1.1, -5.05, True)
        self.assertIsNotNone(healthy)
        self.assertIsNotNone(negative)
        self.assertLess(negative, healthy)
        self.assertLessEqual(negative, 25)
        balance = [dict(complete_inputs()["balance"][0], total_equity=-20)]
        ratios = dict(complete_inputs()["ratios"], debt_to_equity=-5.05, return_on_equity=2.0)
        result = assemble(balance=balance, ratios=ratios)
        self.assertIsNone(result["metrics"]["roe"])

    def test_non_operating_distortion_excludes_pe_from_valuation(self) -> None:
        distorted_income = [
            dict(row, total_other_income_expense=row["operating_income"] * 0.5)
            for row in income_rows()
        ]
        result = assemble(income=distorted_income)
        distorted_score, _ = _valuation_score(result["metrics"], False)
        naive_score, _ = _valuation_score(result["metrics"], True)
        self.assertAlmostEqual(result["scores"]["valuation"], distorted_score, places=1)
        self.assertNotAlmostEqual(distorted_score, naive_score, places=1)
        self.assertTrue(any("non-operating" in risk.lower() for risk in result["risks"]))

    def test_missing_non_operating_detail_is_not_assumed_reliable(self) -> None:
        incomplete_income = [
            {
                key: value
                for key, value in row.items()
                if key not in {"total_other_income_expense", "other_income_expense"}
            }
            for row in income_rows()
        ]
        result = assemble(income=incomplete_income)
        conservative_score, _ = _valuation_score(result["metrics"], False)
        self.assertAlmostEqual(result["scores"]["valuation"], conservative_score, places=1)
        self.assertTrue(any("incomplete" in risk.lower() for risk in result["risks"]))

    def test_stale_complete_financials_are_withheld(self) -> None:
        old_income = income_rows(2015)
        old_balance = [{"period_end": "2015-06-30", "cash_and_equivalents": 50, "short_term_investments": 0, "debt_current": 4, "long_term_debt_and_capital_lease_obligations": 16, "total_equity": 100}]
        old_cash = [{"period_end": "2015-06-30", "net_cash_from_operating_activities": 50, "purchase_of_property_plant_and_equipment": -10}]
        old_ratios = dict(complete_inputs()["ratios"], date="2015-08-28")
        result = assemble(income=old_income, balance=old_balance, cash=old_cash, ratios=old_ratios)
        self.assertEqual(result["data_status"], "stale")
        self.assertEqual(result["action"], "STALE DATA")
        self.assertIsNone(result["scores"]["overall"])

    def test_foreign_issuer_is_withheld_even_with_complete_data(self) -> None:
        overview = dict(complete_inputs()["overview"], ticker="TSM", name="Taiwan Semiconductor", type="ADRC")
        result = assemble("TSM", overview=overview)
        self.assertEqual(result["data_status"], "unsupported_foreign")
        self.assertEqual(result["action"], "UNSUPPORTED MODEL")
        self.assertIsNone(result["scores"]["overall"])

    def test_static_build_has_schema_and_never_serializes_secret(self) -> None:
        def empty_fetcher(path: str, _params: dict, _timeout: int) -> dict:
            if path.endswith("/ratios"):
                raise RuntimeError("provider rejected DO_NOT_PUBLISH_THIS_KEY")
            return {"results": []}

        output = Path(__file__).with_name("_test_fundamentals_data.json")
        try:
            payload = build_fundamentals_feed(
                output_path=output,
                api_key="DO_NOT_PUBLISH_THIS_KEY",
                fetcher=empty_fetcher,
                now=NOW,
            )
            serialized = output.read_text(encoding="utf-8")
        finally:
            output.unlink(missing_ok=True)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["summary"]["tracked"], len(RESEARCH_UNIVERSE))
        self.assertEqual(set(payload["items"]), {item["ticker"] for item in RESEARCH_UNIVERSE})
        self.assertNotIn("DO_NOT_PUBLISH_THIS_KEY", serialized)
        json.loads(serialized)

    def test_strict_build_preserves_last_good_feed(self) -> None:
        def empty_fetcher(_path: str, _params: dict, _timeout: int) -> dict:
            return {"results": []}

        output = Path(__file__).with_name("_test_strict_fundamentals_data.json")
        output.write_text("last-good-feed", encoding="utf-8")
        try:
            with self.assertRaisesRegex(RuntimeError, "at least 1"):
                build_fundamentals_feed(
                    output_path=output,
                    api_key="BUILD_ONLY_KEY",
                    fetcher=empty_fetcher,
                    now=NOW,
                    minimum_ranked=1,
                )
            self.assertEqual(output.read_text(encoding="utf-8"), "last-good-feed")
        finally:
            output.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
