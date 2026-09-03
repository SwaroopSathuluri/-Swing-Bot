# Offline Portfolio Strategy Backtest

Dataset: `C:\Users\swaro\OneDrive\Desktop\Trading\portfolio_recovery_agent\data\historical_massive\2024-08-16_2026-08-14\stock_5m`
Symbols: AAPL, MSFT, NVDA, QQQ, SMH, SPY, XLK

Enter only when a ticker first appears in the bot list; hold until stop, target1, or max hold days; replace only when a position slot opens.

Important limitation: this local dataset contains only the symbols above, not the full 500-stock Swing Bot universe.

## Best Return

- slots=1 rank=rs hold=5d score>=55 edge>=0 atr<=4.5: annual=76.5%, total=80.52%, trades=50, win=62.0%, avg_hold=3.2d, avg_trade=1.24%, dd=-10.12%
- slots=1 rank=rs hold=8d score>=55 edge>=0 atr<=4.5: annual=75.93%, total=79.92%, trades=46, win=63.0%, avg_hold=3.5d, avg_trade=1.34%, dd=-10.12%
- slots=1 rank=rs hold=10d score>=55 edge>=0 atr<=4.5: annual=74.94%, total=78.87%, trades=45, win=62.2%, avg_hold=3.7d, avg_trade=1.36%, dd=-10.12%
- slots=1 rank=rs hold=13d score>=55 edge>=0 atr<=4.5: annual=71.56%, total=75.27%, trades=44, win=61.4%, avg_hold=3.8d, avg_trade=1.34%, dd=-12.2%
- slots=1 rank=rs hold=15d score>=55 edge>=0 atr<=4.5: annual=71.56%, total=75.27%, trades=44, win=61.4%, avg_hold=3.8d, avg_trade=1.34%, dd=-12.2%
- slots=1 rank=rs hold=18d score>=55 edge>=0 atr<=4.5: annual=71.56%, total=75.27%, trades=44, win=61.4%, avg_hold=3.8d, avg_trade=1.34%, dd=-12.2%
- slots=1 rank=rs hold=3d score>=55 edge>=0 atr<=4.5: annual=68.6%, total=72.13%, trades=59, win=62.7%, avg_hold=2.5d, avg_trade=0.96%, dd=-13.25%
- slots=1 rank=rs hold=3d score>=75 edge>=-20 atr<=4.5: annual=63.25%, total=66.46%, trades=68, win=66.2%, avg_hold=2.4d, avg_trade=0.78%, dd=-9.87%
- slots=1 rank=rs hold=3d score>=75 edge>=-20 atr<=6.0: annual=58.4%, total=61.32%, trades=71, win=64.8%, avg_hold=2.4d, avg_trade=0.72%, dd=-16.16%
- slots=1 rank=rs hold=3d score>=75 edge>=-20 atr<=8.0: annual=58.4%, total=61.32%, trades=71, win=64.8%, avg_hold=2.4d, avg_trade=0.72%, dd=-16.16%

## Best Risk Adjusted

- slots=1 rank=rs hold=5d score>=55 edge>=0 atr<=4.5: annual=76.5%, total=80.52%, trades=50, win=62.0%, avg_hold=3.2d, avg_trade=1.24%, dd=-10.12%
- slots=1 rank=rs hold=8d score>=55 edge>=0 atr<=4.5: annual=75.93%, total=79.92%, trades=46, win=63.0%, avg_hold=3.5d, avg_trade=1.34%, dd=-10.12%
- slots=1 rank=rs hold=10d score>=55 edge>=0 atr<=4.5: annual=74.94%, total=78.87%, trades=45, win=62.2%, avg_hold=3.7d, avg_trade=1.36%, dd=-10.12%
- slots=1 rank=rs hold=3d score>=75 edge>=-20 atr<=4.5: annual=63.25%, total=66.46%, trades=68, win=66.2%, avg_hold=2.4d, avg_trade=0.78%, dd=-9.87%
- slots=1 rank=rs hold=13d score>=55 edge>=0 atr<=4.5: annual=71.56%, total=75.27%, trades=44, win=61.4%, avg_hold=3.8d, avg_trade=1.34%, dd=-12.2%
- slots=1 rank=rs hold=15d score>=55 edge>=0 atr<=4.5: annual=71.56%, total=75.27%, trades=44, win=61.4%, avg_hold=3.8d, avg_trade=1.34%, dd=-12.2%
- slots=1 rank=rs hold=18d score>=55 edge>=0 atr<=4.5: annual=71.56%, total=75.27%, trades=44, win=61.4%, avg_hold=3.8d, avg_trade=1.34%, dd=-12.2%
- slots=1 rank=rs hold=15d score>=55 edge>=-20 atr<=4.5: annual=52.14%, total=54.7%, trades=52, win=55.8%, avg_hold=4.0d, avg_trade=0.89%, dd=-9.19%
- slots=1 rank=rs hold=18d score>=55 edge>=-20 atr<=4.5: annual=52.14%, total=54.7%, trades=52, win=55.8%, avg_hold=4.0d, avg_trade=0.89%, dd=-9.19%
- slots=1 rank=rs hold=13d score>=55 edge>=-20 atr<=4.5: annual=51.37%, total=53.88%, trades=52, win=55.8%, avg_hold=4.0d, avg_trade=0.88%, dd=-9.19%

## Recommended Rule From This Test

- slots=1 rank=rs hold=5d score>=55 edge>=0 atr<=4.5: annual=76.5%, total=80.52%, trades=50, win=62.0%, avg_hold=3.2d, avg_trade=1.24%, dd=-10.12%

Operational rule: do not buy two new stocks every day. Enter only fresh first-day names, keep a fixed number of slots, and replace only after stop, target, or time exit.