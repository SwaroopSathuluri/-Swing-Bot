from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import portfolio_market_data as portfolio


def _timestamp(day: str) -> int:
    return int(datetime.fromisoformat(f"{day}T00:00:00+00:00").timestamp() * 1000)


class PortfolioMarketDataTests(unittest.TestCase):
    def test_completed_market_date_excludes_a_forming_session(self) -> None:
        eastern = ZoneInfo("America/New_York")
        before_settle = datetime(2026, 9, 3, 15, 0, tzinfo=eastern)
        after_settle = datetime(2026, 9, 3, 17, 0, tzinfo=eastern)
        self.assertEqual(portfolio.completed_market_date(before_settle), date(2026, 9, 2))
        self.assertEqual(portfolio.completed_market_date(after_settle), date(2026, 9, 3))

    def test_allocation_matches_current_five_thousand_dollar_plan(self) -> None:
        self.assertEqual(sum(asset["target_pct"] for asset in portfolio.PORTFOLIO_ASSETS), 84)
        self.assertEqual(sum(asset["monthly_target"] for asset in portfolio.PORTFOLIO_ASSETS), 4200)
        self.assertEqual({asset["ticker"] for asset in portfolio.PORTFOLIO_ASSETS}, {"QQQM", "XMMO", "SCHA", "GOOGL", "AMZN"})

    def test_feed_uses_latest_two_valid_daily_closes(self) -> None:
        calls: list[str] = []

        def fetch(path: str, params: dict, timeout: int) -> dict:
            calls.append(path)
            self.assertEqual(params["adjusted"], "true")
            self.assertEqual(params["sort"], "asc")
            self.assertEqual(timeout, 30)
            return {
                "results": [
                    {"c": 100, "t": _timestamp("2026-09-01")},
                    {"c": 102, "t": _timestamp("2026-09-02")},
                ]
            }

        payload = portfolio.build_portfolio_market_feed(as_of=date(2026, 9, 3), fetcher=fetch)
        self.assertEqual(payload["market_date"], "2026-09-02")
        self.assertEqual(len(calls), 5)
        self.assertEqual(payload["quotes"]["QQQM"]["price"], 102)
        self.assertAlmostEqual(payload["quotes"]["QQQM"]["change_pct"], 2.0)
        self.assertIn("delayed", payload["price_type"].lower())

    def test_feed_rejects_missing_prices(self) -> None:
        def fetch(path: str, params: dict, timeout: int) -> dict:
            return {"results": []}

        with self.assertRaisesRegex(RuntimeError, "No usable daily price"):
            portfolio.build_portfolio_market_feed(as_of=date(2026, 9, 3), fetcher=fetch)

    def test_feed_rejects_mixed_market_dates(self) -> None:
        def fetch(path: str, params: dict, timeout: int) -> dict:
            market_day = "2026-09-01" if "/AMZN/" in path else "2026-09-02"
            return {"results": [{"c": 100, "t": _timestamp(market_day)}]}

        with self.assertRaisesRegex(RuntimeError, "not aligned"):
            portfolio.build_portfolio_market_feed(as_of=date(2026, 9, 3), fetcher=fetch)


if __name__ == "__main__":
    unittest.main()
