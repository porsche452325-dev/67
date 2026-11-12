# Gold Signal Bot

Watches the live gold chart and tells you exactly what to do: **BUY**, **HOLD**, **CLOSE**, or **DO NOTHING**.
It never places trades for you. You decide.

The rules come from testing over 100 strategy variations on 9 years of real XAUUSD data from an MT5 broker.
Only one kind held up both on the years it was tuned on and on years it had never seen.
See [research/RESULTS.md](research/RESULTS.md) for every number.

| Tested 2017–2026 | |
|---|---|
| Trades | 169, about 18 a year |
| Won | 46% |
| Average win / average loss | $45 / $19 per oz |
| Profit factor | 2.09 (2017–22: 1.59, unseen 2023–26: 2.34) |
| Profitable years | 9 of 10 |

## The rules

| | Rule |
|---|---|
| **BUY** | A **4-hour** candle closes above the highest high of the previous 55 candles, while price is above the 200 EMA |
| Stop loss | Starts 2 × ATR below entry. It then trails: highest price since entry minus 3 × ATR, and only moves up |
| **CLOSE** | Price touches the stop. There is no fixed take profit, so winning trades can run |
| **DO NOTHING** | Every other time. That includes downtrends: every SELL rule tested lost money on gold |

The old 15-minute EMA/RSI rules lost money on real data (profit factor 0.78–0.89), so they were removed.

## Run it on a computer

You need Python 3.9 or newer. There is nothing to install.

```bash
cd gold-bot
python gold_bot.py                      # live; checks every 5 seconds
python gold_bot.py --backtest           # test on the last ~1000 four-hour candles
python gold_bot.py --csv XAUUSD_H1.csv  # test on your own MT5 history (File > Export bars)
python gold_bot.py --demo               # offline test with fake prices
```

Example alert:

```
============================================================
BUY GOLD @ 4,540.01   (2026-01-11 20:00 UTC)
Why: 4-hour candle closed at 4,539.96, above the 55-candle high 4,519.44, in an uptrend
Stop loss: 4,480.18   (no take profit - the stop trails up)
============================================================
```

Between alerts, it prints one status line per check, for example:
`DO NOTHING - uptrend, waiting for a 4h close above 4,519.44 (12.30 away)` or `HOLD BUY from 4,540.01 | stop 4,511.48 | P/L +45.26 $/oz`.
Every signal is saved to `signals_log.csv`.

## Data sources

- `binance` (default): **PAXG/USDT**, a token backed by 1 oz of physical gold. It is real time, free, needs no account, and trades 24/7.
  Its price usually sits within a few dollars of spot XAU/USD. Use the bot's signals, but place your orders at your broker's prices.
- `yahoo`: **GC=F**, COMEX gold futures. The data may be delayed about 10 minutes and the market is closed on weekends.

## Phone alerts (optional, Telegram)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Set both values before you start the bot:

```bash
export TELEGRAM_BOT_TOKEN="123:abc..."
export TELEGRAM_CHAT_ID="123456789"
python gold_bot.py
```

On Windows PowerShell, use `$env:TELEGRAM_BOT_TOKEN="..."` instead.

## Important

This is a learning tool, not financial advice. No strategy wins all the time, and losing streaks will happen.
This strategy loses on more than half its trades and once lost money for a whole year (2021). The longest losing streak was 7 trades.
Try it on a demo account first. Risk 1% or less per trade, and never risk money you can't afford to lose.

## iPhone app

`app/` is the same robot as a web app for Safari. You don't install anything, and it doesn't need a computer.

- **Signal card:** a big **BUY / HOLD BUY / CLOSE / GET READY / DO NOTHING** card says exactly what to do, with the exact price that would trigger a buy and when the current 4-hour candle closes.
- **In a trade:** it shows entry, today's stop loss (it trails up), lot size and live P/L.
- **Real time:** every gold trade streams in live. A BUY appears within about 2 seconds of the 4-hour candle closing. A CLOSE appears the moment price touches the stop.
- **Lot size calculator:** enter your balance and risk %, and it sizes each trade so the stop loss costs that much.
- **Match my broker:** type your MT5 XAUUSD price and every price in the app shifts to your broker's quote.

**Install:**
1. Drop `gold-bot-app.zip` onto app.netlify.com/drop, or host the `app/` folder anywhere.
2. Open the link in Safari, tap **Share**, then **Add to Home Screen**.
3. Tap **Turn on sound alerts**.

iPhone only runs web apps while they are on screen. For alerts with the phone locked, run `gold_bot.py` on a computer with Telegram set up (see above).
