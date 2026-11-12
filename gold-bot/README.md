# Gold Signal Bot

Watches a live gold chart and tells you when to **BUY**, **SELL** or **CLOSE**.
It never places trades for you. It only shows signals, and you decide what to do.

## Run it

You need Python 3.9 or newer. There is nothing to install.

```bash
cd gold-bot
python gold_bot.py                    # live 15-minute chart, checks every 5 seconds
python gold_bot.py --interval 1h      # use the 1-hour chart (fewer, stronger signals)
python gold_bot.py --source yahoo     # gold futures (GC=F) instead of PAXG
python gold_bot.py --backtest         # how the strategy did on the last ~1000 candles
python gold_bot.py --demo             # offline test with fake prices
```

Example alert:

```
============================================================
BUY GOLD @ 2,684.20   (2026-11-12 14:15 UTC)
Why: Uptrend (above EMA200), EMA9 crossed above EMA21, RSI 58
Stop loss: 2,676.90   Take profit: 2,698.80
============================================================
```

Between alerts it prints one status line per check, showing price, trend, RSI and whether a trade is open.
Every signal is also saved to `signals_log.csv`.

## The strategy

| Step | Rule |
|---|---|
| Trend | Price above the 200 EMA means only look for buys. Below it means only look for sells. |
| **BUY** | 9 EMA crosses above 21 EMA, in an uptrend, with RSI between 50 and 70 |
| **SELL** | 9 EMA crosses below 21 EMA, in a downtrend, with RSI between 30 and 50 |
| Stop loss | 1.5 × ATR from entry |
| Take profit | 3 × ATR from entry, so you risk 1 to make 2 |
| **CLOSE** | Stop loss hit, take profit hit, or the 9/21 EMAs cross back the other way |

Signals are only based on **closed** candles, so they never change after they appear.
You can change any of these settings at the top of `gold_bot.py`.

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
Run `--backtest` first, then paper trade or use a demo account before you risk real money.
Never risk more than you can afford to lose.

## iPhone app

`app/` holds the same robot as a web app that runs in Safari, with no install and no computer needed.
It reads the live gold price, draws the chart with the EMAs and signal arrows, and shows a big **BUY / SELL / CLOSE / HOLD / WAIT** card.
When a trade is open, the card also shows entry, stop loss, take profit and your live P/L.
It is **real time**: every gold trade streams in over a live connection, so the price, chart and signal update instantly.
A new BUY or SELL appears within about 2 seconds of the candle closing. It warns you to **CLOSE** the moment price touches your stop or target, without waiting for the candle to finish.
If the live stream drops, it reconnects by itself and refreshes prices every 10 seconds in the meantime.

**Match my broker:** type the XAUUSD price from MT5 and tap **Match**. Every price in the app, including entry, stop loss and take profit, then shifts to your broker's quote.

1. Host the `app/` folder on any static host, such as Netlify (drag the folder onto app.netlify.com/drop) or GitHub Pages.
2. Open the link in Safari on your iPhone, tap **Share**, then **Add to Home Screen**. It opens full screen like an app.
3. Tap **Turn on sound alerts** to get a chime on each new signal. This also keeps the screen awake.

iPhone only runs web apps while they are on screen. To get alerts while the phone is locked, run `gold_bot.py` on a computer or server with Telegram set up (see above).
