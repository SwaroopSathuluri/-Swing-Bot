from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from report_freshness import REPORTS, validate_report


def verify_once(base_url: str, expected: dict) -> None:
    def fetch(filename: str) -> bytes:
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/{filename}?verify={time.time_ns()}",
            headers={"Cache-Control": "no-cache", "User-Agent": "SwingBot-Publish-Check/1.0"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    actual = json.loads(fetch("report-status.json"))
    if actual != expected:
        raise RuntimeError("Live publication manifest does not match this build")
    for filename in REPORTS:
        content = fetch(filename)
        validate_report(content.decode("utf-8"), expected["market_date"])
        if hashlib.sha256(content).hexdigest() != expected["reports"][filename]["sha256"]:
            raise RuntimeError(f"Live {filename} does not match the validated build")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--status", type=Path, default=Path("report-status.json"))
    parser.add_argument("--attempts", type=int, default=10)
    args = parser.parse_args()
    expected = json.loads(args.status.read_text(encoding="utf-8"))
    for attempt in range(args.attempts):
        try:
            verify_once(args.url, expected)
            print(f"Verified all live reports for {expected['market_date']}")
            return 0
        except (RuntimeError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            print(f"Publication check {attempt + 1}/{args.attempts}: {type(exc).__name__}", flush=True)
            if attempt + 1 < args.attempts:
                time.sleep(30)
    raise SystemExit("Live publication did not pass freshness and content verification")


if __name__ == "__main__":
    raise SystemExit(main())
