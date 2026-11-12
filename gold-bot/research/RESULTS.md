# Gold strategy test results

**Data:** real XAUUSD 1-minute bars from Exness MT5, 28 Apr 2017 to 18 Aug 2026 (3,276,127 bars).
Source: [sherwynjoel/xauusd-historical-data](https://github.com/sherwynjoel/xauusd-historical-data).

**Method**
- Signals use closed candles only. Each trade enters at the next candle's open.
- Every trade pays $0.35/oz for spread and slippage. The broker's median spread was $0.16–0.30.
- If the stop and target are both hit in the same candle, the stop is assumed first.
- Rules were chosen using **2017–2022 only (in-sample)**, then checked on **2023–Aug 2026 (out-of-sample)**, which was never used for tuning.

## What lost money

| Setup | 2017–2022 PF | 2023–2026 PF |
|---|---|---|
| Old app rules: M15 EMA 9/21 cross + RSI, SL 1.5 ATR / TP 3 ATR | 0.78 | 0.89 |
| Same rules on H1 | 0.82 | 0.96 |
| M15 RSI pullbacks in trend | 0.77–0.86 | 0.88–0.95 |
| M15 pullbacks with an H4 trend filter | 0.79–0.90 | 0.92–1.29 |
| London breakout of the Asian range (M15) | 0.75–0.93 | 1.13–1.28 |
| Short selling (H4 breakout to new lows) | 0.74 | 0.90 |

PF means profit factor: money won ÷ money lost. Below 1.00 loses money.
On M15, costs and noise eat the edge. Selling gold lost money in both periods.

## What worked: H4 breakout, buy only

- **BUY:** a 4-hour candle closes above the highest high of the previous 55 candles, and price is above the 200 EMA.
- **Stop:** 2 × ATR(14) below entry, then a trailing stop at the highest high minus 3 × ATR. It only moves up.
- **Exit:** when price touches the stop. There is no fixed target.

| | Trades | Win % | Profit factor | Total $/oz | Max drawdown $/oz |
|---|---|---|---|---|---|
| 2017–2022 (tuning) | 89 | 41.6% | 1.59 | +323 | 114 |
| 2023–2026 (unseen) | 80 | 51.2% | 2.34 | +1,528 | 356 |
| All | 169 | 46.2% | 2.09 | +1,850 | 356 |

Year by year ($/oz): 2017 +22 · 2018 +18 · 2019 +111 · 2020 +164 · **2021 −61** · 2022 +69 · 2023 +266 · 2024 +173 · 2025 +594 · 2026 (to Aug) +495.

- **Average trade:** average win $45.4/oz, average loss $18.6/oz.
- **Holding time:** about 3.9 days on average.
- **Longest losing streak:** 7 trades.

**Robustness**
- **Nearby settings:** every breakout window from 40 to 70 candles and every trailing distance from 2.5 to 4 ATR was profitable in both periods (PF 1.15–1.59 before, 2.0–3.9 after). The result doesn't depend on one lucky number.
- **Higher costs:** at 4× the cost ($1.40/oz per trade), PF is still 1.38 before and 2.22 after.

**Limits**
- **Rising market:** part of the 2023–2026 profit comes from gold's big rise. Buy-only trend following does best in strong uptrends. It earns less in sideways years, and 2021 lost money.
- **Wide stops:** 4-hour stops are around $40–80/oz at today's prices. Size positions with the app's lot calculator. One standard lot is 100 oz.
- **No guarantee:** past results do not guarantee future results.

## Reproduce

```bash
pip install pandas pyarrow numpy
git clone --depth 1 https://github.com/sherwynjoel/xauusd-historical-data
export GOLD_M1_PARQUET=xauusd-historical-data/xauusd_m1_full.parquet
python run1.py   # first comparison
python run2.py   # 111-variation grid (writes run2.csv)
python run3.py   # stress tests of the chosen rules
```

`gold_bot.py --csv <MT5 export>` runs the same rules on your own broker's history.
On the dataset above it gives the same trades: 166 after a 200-candle warm-up, profit factor 2.11.
