from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import report_freshness as freshness
import verify_publication


def bar(day: str) -> dict:
    return {"t": int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)}


class FreshnessTests(unittest.TestCase):
    def test_completed_sessions(self):
        cases = [
            ("2026-11-06T20:59:00+00:00", "2026-11-05"),
            ("2026-11-06T21:14:00+00:00", "2026-11-05"),
            ("2026-11-06T21:15:00+00:00", "2026-11-06"),
            ("2026-11-07T16:00:00+00:00", "2026-11-06"),
            ("2026-11-09T15:00:00+00:00", "2026-11-06"),
            ("2026-10-30T20:15:00+00:00", "2026-10-30"),
            ("2026-11-26T23:00:00+00:00", "2026-11-25"),
            ("2026-11-27T18:14:00+00:00", "2026-11-25"),
            ("2026-11-27T18:15:00+00:00", "2026-11-27"),
        ]
        for observed, expected in cases:
            with self.subTest(observed=observed):
                self.assertEqual(freshness.latest_completed_session(datetime.fromisoformat(observed)).isoformat(), expected)

    def test_rejects_naive_clock(self):
        with self.assertRaises(ValueError):
            freshness.latest_completed_session(datetime(2026, 11, 6))

    def test_missing_or_stale_benchmark_fails(self):
        for history in ([], [bar("2026-11-02")]):
            with self.assertRaisesRegex(RuntimeError, "missing the completed session"):
                freshness.require_current_history("SPY", history, "2026-11-06")
        freshness.require_current_history("SPY", [bar("2026-11-06")], "2026-11-06")

    def test_report_dates_fail_closed(self):
        for rows in ([], [{"date": "2026-11-02"}], [{"date": "2026-11-06"}, {"date": "2026-11-05"}]):
            with self.assertRaises(RuntimeError):
                freshness.validate_report(f"const rows = {json.dumps(rows)};", "2026-11-06")

    def test_manifest_hashes_and_weekend_expiry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for filename in freshness.REPORTS:
                (root / filename).write_text('const rows = [{"date":"2026-11-06"}];</body>', encoding="utf-8")
            (root / "portfolio-market-data.json").write_text(json.dumps({"market_date": "2026-11-06", "quotes": {"SPY": {"market_date": "2026-11-06"}}}), encoding="utf-8")
            status = freshness.write_publication_status(root, date(2026, 11, 6))
            self.assertEqual(status["next_refresh_due_at"], "2026-11-10T01:00:00+00:00")
            for filename in freshness.REPORTS:
                content = (root / filename).read_bytes()
                self.assertEqual(status["reports"][filename]["sha256"], hashlib.sha256(content).hexdigest())
                self.assertIn(b'data-market-date="2026-11-06"', content)

    def test_verifier_rejects_old_deployment(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"market_date":"2026-11-02"}'
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "manifest does not match"):
                verify_publication.verify_once("https://example.com", {"market_date": "2026-11-06"})


if __name__ == "__main__":
    unittest.main()
