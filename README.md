# Swing Trading Mag7

Static HTML swing-trading reports for:

- `qqq` for QQQ constituents
- `mag7` for the Mag 7 basket
- `market` for the top 500 liquid U.S. stocks from a broader dollar-volume universe

The installable `swing-command-center.html` app combines the Weekly Plan, Long-term Portfolio,
Positions, Alerts, Returns, and Fundamentals tabs. The Portfolio tab keeps the monthly
ETF/stock allocation separate from swing trades, tracks contribution lots and free cash,
uses scheduled delayed market marks, and requires actual broker fills before calculating P/L.
Its private ledger is stored on the current device and is included in the app's JSON backup.
The Fundamentals tab ranks a curated 12-company
research universe, separates business quality from valuation and technical setup, and
shows current reference levels, financial strength, catalysts, risks, recent news, and
SEC filing links. Missing, stale, foreign-issuer, and unsupported-sector data is withheld
instead of being converted into a false positive score.

`fundamentals-data.json` is generated during the scheduled GitHub Actions build. The
Massive API key remains a server-side build secret and is never sent to the browser.

## Local use

Generate a local report:

```bat
generate_local_report.cmd qqq
generate_local_report.cmd market
generate_local_report.cmd mag7
```

The report is written to:

- `swing_trading_mag7_report.html`
- `index.html`

`index.html` is the GitHub Pages entry file.

## GitHub Pages setup

1. Push this project to your GitHub repository.
2. In GitHub, go to `Settings -> Secrets and variables -> Actions`.
3. Add a repository secret named `MASSIVE_API_KEY`.
4. In `Settings -> Pages`, set the source to `GitHub Actions`.
5. Run the `Publish Swing Report` workflow once manually.

After that, GitHub Actions will:

- regenerate the report on schedule
- rebuild and test the Fundamentals research snapshot
- publish the latest `index.html` to GitHub Pages
- show the latest report time directly in the page header

## Sharing

The GitHub Pages URL will be shareable with your friends.

Important: GitHub Pages is effectively public unless you use GitHub Enterprise-style access controls. Treat the shared link as public.

## Schedule

The workflow currently refreshes on weekdays at:

- `13:00 UTC`
- `18:00 UTC`
- `23:00 UTC`

You can also trigger it manually from the GitHub Actions tab and choose `qqq`, `mag7`, or `market`.
