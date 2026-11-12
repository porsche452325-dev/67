#!/usr/bin/env python3
"""
Gold Signal Bot
===============
Watches a live gold (XAU/USD) chart and tells you when to BUY, SELL or CLOSE.

It does NOT place trades. It only prints signals so YOU decide.

Strategy (trend-following, uses closed candles only so signals never "repaint"):
  * Trend filter  : price vs. EMA-200 (above = only look for buys, below = only sells)
  * Entry trigger : EMA-9 crosses EMA-21
  * Momentum check: RSI-14 must agree (buy: 50-70, sell: 30-50)
  * Risk          : Stop loss = 1.5 x ATR-14, Take profit = 3 x ATR-14 (1:2 risk/reward)
  * Exit (CLOSE)  : stop loss hit, take profit hit, or EMA-9/21 crosses back the other way

Data sources (no API key needed):
  * binance : PAXG/USDT - a gold-backed token that tracks 1 oz of gold, trades 24/7, real time
  * yahoo   : GC=F gold futures (may be delayed ~10 min, closed on weekends)

Usage:
  python gold_bot.py                      # live, 15-minute chart, checks every 30s
  python gold_bot.py --interval 1h        # live on the 1-hour chart
  python gold_bot.py --source yahoo       # use Yahoo gold futures instead
  python gold_bot.py --backtest           # see how the strategy did on recent candles
  python gold_bot.py --demo               # offline test with fake prices

Optional phone alerts via Telegram: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.

Only uses the Python standard library - nothing to install.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# ----------------------------------------------------------------------------
# Settings (change these to tune the strategy)
# ----------------------------------------------------------------------------
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 200
RSI_LEN = 14
ATR_LEN = 14
SL_ATR = 1.5    # stop loss distance in ATRs
TP_ATR = 3.0    # take profit distance in ATRs
RSI_BUY_MIN, RSI_BUY_MAX = 50, 70
RSI_SELL_MIN, RSI_SELL_MAX = 30, 50

STATE_FILE = Path(__file__).with_name("bot_state.json")
LOG_FILE = Path(__file__).with_name("signals_log.csv")

INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}


@dataclass
class Candle:
    time: int      # open time, unix seconds
    open: float
    high: float
    low: float
    close: float


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "gold-signal-bot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fetch_binance(interval: str, limit: int = 500) -> list[Candle]:
    q = urllib.parse.urlencode({"symbol": "PAXGUSDT", "interval": interval, "limit": limit})
    rows = _get_json(f"https://api.binance.com/api/v3/klines?{q}")
    return [Candle(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]


def fetch_yahoo(interval: str, limit: int = 500) -> list[Candle]:
    yi = {"4h": "1h"}.get(interval, interval).replace("1h", "60m")
    rng = {"1m": "5d", "5m": "30d", "15m": "30d", "30m": "30d", "60m": "180d", "1d": "5y"}[yi]
    q = urllib.parse.urlencode({"interval": yi, "range": rng})
    data = _get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?{q}")
    res = data["chart"]["result"][0]
    quote = res["indicators"]["quote"][0]
    candles = []
    for i, t in enumerate(res["timestamp"]):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        if None not in (o, h, l, c):
            candles.append(Candle(int(t), o, h, l, c))
    return candles[-limit:]


def fake_candles(n: int = 600, step: int = 900, seed: int = 7) -> list[Candle]:
    """Random-walk gold prices with trending phases, for offline testing."""
    rnd = random.Random(seed)
    price, drift = 2650.0, 0.0
    t0 = int(time.time()) - n * step
    out = []
    for i in range(n):
        if i % 80 == 0:
            drift = rnd.choice([-0.6, -0.3, 0.3, 0.6])
        o = price
        c = max(100.0, o + drift + rnd.gauss(0, 3))
        h = max(o, c) + abs(rnd.gauss(0, 1.5))
        l = min(o, c) - abs(rnd.gauss(0, 1.5))
        out.append(Candle(t0 + i * step, o, h, l, c))
        price = c
    return out


# ----------------------------------------------------------------------------
# Indicators
# ----------------------------------------------------------------------------
def ema(values: list[float], length: int) -> list[float]:
    k = 2 / (length + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(values: list[float], length: int) -> list[float]:
    out = [50.0] * len(values)
    gain = loss = 0.0
    for i in range(1, len(values)):
        ch = values[i] - values[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i <= length:
            gain += g / length
            loss += l / length
        else:
            gain = (gain * (length - 1) + g) / length
            loss = (loss * (length - 1) + l) / length
        if i >= length:
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def atr(candles: list[Candle], length: int) -> list[float]:
    trs = []
    for i, c in enumerate(candles):
        pc = candles[i - 1].close if i else c.close
        trs.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    out, prev = [], None
    for i, tr in enumerate(trs):
        prev = tr if prev is None else (prev * (length - 1) + tr) / length
        out.append(prev)
    return out


# ----------------------------------------------------------------------------
# Strategy
# ----------------------------------------------------------------------------
@dataclass
class Position:
    side: str          # "LONG" or "SHORT"
    entry: float
    stop: float
    target: float
    opened: int


@dataclass
class Signal:
    action: str        # BUY, SELL, CLOSE, HOLD
    price: float
    time: int
    reason: str
    stop: float | None = None
    target: float | None = None
    pnl: float | None = None


class Strategy:
    def __init__(self) -> None:
        self.position: Position | None = None

    def evaluate(self, candles: list[Candle], i: int, ind: dict) -> Signal | None:
        """Look at closed candle i and decide what to do."""
        c = candles[i]
        f, s, tr = ind["fast"], ind["slow"], ind["trend"]
        r, a = ind["rsi"][i], ind["atr"][i]
        cross_up = f[i - 1] <= s[i - 1] and f[i] > s[i]
        cross_dn = f[i - 1] >= s[i - 1] and f[i] < s[i]

        # --- manage an open trade first ---
        p = self.position
        if p:
            if p.side == "LONG":
                if c.low <= p.stop:
                    return self._close(p.stop, c.time, "Stop loss hit")
                if c.high >= p.target:
                    return self._close(p.target, c.time, "Take profit hit")
                if cross_dn:
                    return self._close(c.close, c.time, "Trend turned down (EMA cross)")
            else:
                if c.high >= p.stop:
                    return self._close(p.stop, c.time, "Stop loss hit")
                if c.low <= p.target:
                    return self._close(p.target, c.time, "Take profit hit")
                if cross_up:
                    return self._close(c.close, c.time, "Trend turned up (EMA cross)")
            return None

        # --- look for a new trade ---
        if cross_up and c.close > tr[i] and RSI_BUY_MIN <= r <= RSI_BUY_MAX:
            sl, tp = c.close - SL_ATR * a, c.close + TP_ATR * a
            self.position = Position("LONG", c.close, sl, tp, c.time)
            return Signal("BUY", c.close, c.time,
                          f"Uptrend (above EMA{EMA_TREND}), EMA{EMA_FAST} crossed above EMA{EMA_SLOW}, RSI {r:.0f}",
                          sl, tp)
        if cross_dn and c.close < tr[i] and RSI_SELL_MIN <= r <= RSI_SELL_MAX:
            sl, tp = c.close + SL_ATR * a, c.close - TP_ATR * a
            self.position = Position("SHORT", c.close, sl, tp, c.time)
            return Signal("SELL", c.close, c.time,
                          f"Downtrend (below EMA{EMA_TREND}), EMA{EMA_FAST} crossed below EMA{EMA_SLOW}, RSI {r:.0f}",
                          sl, tp)
        return None

    def _close(self, price: float, t: int, reason: str) -> Signal:
        p = self.position
        pnl = (price - p.entry) if p.side == "LONG" else (p.entry - price)
        self.position = None
        return Signal("CLOSE", price, t, f"Close {p.side}: {reason}", pnl=pnl)


def indicators(candles: list[Candle]) -> dict:
    closes = [c.close for c in candles]
    return {
        "fast": ema(closes, EMA_FAST),
        "slow": ema(closes, EMA_SLOW),
        "trend": ema(closes, EMA_TREND),
        "rsi": rsi(closes, RSI_LEN),
        "atr": atr(candles, ATR_LEN),
    }


# ----------------------------------------------------------------------------
# Alerts / output
# ----------------------------------------------------------------------------
COLORS = {"BUY": "\033[92m", "SELL": "\033[91m", "CLOSE": "\033[93m"}
RESET = "\033[0m"


def ts(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_signal(sig: Signal) -> str:
    lines = [f"{sig.action} GOLD @ {sig.price:,.2f}   ({ts(sig.time)})", f"Why: {sig.reason}"]
    if sig.stop is not None:
        lines.append(f"Stop loss: {sig.stop:,.2f}   Take profit: {sig.target:,.2f}")
    if sig.pnl is not None:
        lines.append(f"Result: {sig.pnl:+,.2f} $/oz")
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10)
    except Exception as e:  # never let alerts crash the bot
        print(f"(Telegram alert failed: {e})")


def alert(sig: Signal) -> None:
    msg = format_signal(sig)
    color = COLORS.get(sig.action, "")
    bar = "=" * 60
    print(f"\a\n{color}{bar}\n{msg}\n{bar}{RESET}\n", flush=True)
    send_telegram("🟡 Gold bot\n" + msg)
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a") as f:
        if new:
            f.write("time,action,price,stop,target,pnl,reason\n")
        stop = "" if sig.stop is None else f"{sig.stop:.2f}"
        target = "" if sig.target is None else f"{sig.target:.2f}"
        f.write(f"{ts(sig.time)},{sig.action},{sig.price:.2f},{stop},{target},{'' if sig.pnl is None else round(sig.pnl, 2)},\"{sig.reason}\"\n")


# ----------------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------------
def backtest(candles: list[Candle]) -> None:
    ind = indicators(candles)
    strat = Strategy()
    trades: list[float] = []
    print(f"Backtest on {len(candles)} candles: {ts(candles[0].time)} -> {ts(candles[-1].time)}\n")
    for i in range(EMA_TREND, len(candles)):
        sig = strat.evaluate(candles, i, ind)
        if sig:
            print(format_signal(sig) + "\n")
            if sig.pnl is not None:
                trades.append(sig.pnl)
    wins = [t for t in trades if t > 0]
    print("-" * 60)
    if not trades:
        print("No completed trades in this period.")
        return
    print(f"Trades: {len(trades)}   Wins: {len(wins)} ({100 * len(wins) / len(trades):.0f}%)")
    print(f"Total: {sum(trades):+,.2f} $/oz   Avg: {sum(trades) / len(trades):+,.2f} $/oz per trade")
    print("Past results do not guarantee future results.")


def load_state(strat: Strategy, interval: str, source: str) -> int:
    try:
        s = json.loads(STATE_FILE.read_text())
        if s.get("interval") == interval and s.get("source") == source:
            strat.position = Position(**s["position"]) if s.get("position") else None
            return int(s.get("last_time", 0))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return 0


def save_state(strat: Strategy, interval: str, source: str, last_time: int) -> None:
    STATE_FILE.write_text(json.dumps({
        "interval": interval, "source": source, "last_time": last_time,
        "position": asdict(strat.position) if strat.position else None,
    }, indent=2))


def live(source: str, interval: str, every: int) -> None:
    fetch = fetch_binance if source == "binance" else fetch_yahoo
    strat = Strategy()
    last = load_state(strat, interval, source)
    print(f"Gold Signal Bot - {source} - {interval} chart - checking every {every}s. Ctrl+C to stop.")
    if strat.position:
        p = strat.position
        print(f"Resuming open {p.side} from {p.entry:,.2f} (SL {p.stop:,.2f} / TP {p.target:,.2f})")
    first = last == 0
    while True:
        try:
            candles = fetch(interval)
            closed = candles[:-1]  # last candle is still forming
            if len(closed) < EMA_TREND + 2:
                print("Not enough history yet."); time.sleep(every); continue
            ind = indicators(closed)
            if first:
                # warm up silently: replay history so we know if a trade is already running
                for i in range(EMA_TREND, len(closed)):
                    strat.evaluate(closed, i, ind)
                last, first = closed[-1].time, False
                if strat.position:
                    p = strat.position
                    print(f"Note: strategy is currently in a {p.side} from {p.entry:,.2f} "
                          f"(SL {p.stop:,.2f} / TP {p.target:,.2f}). Waiting for the next signal.")
            else:
                for i in range(EMA_TREND, len(closed)):
                    if closed[i].time > last:
                        sig = strat.evaluate(closed, i, ind)
                        if sig:
                            alert(sig)
                last = closed[-1].time
            save_state(strat, interval, source, last)

            j = len(closed) - 1
            pos = f"{strat.position.side} from {strat.position.entry:,.2f}" if strat.position else "FLAT (no trade)"
            trend = "UP" if closed[j].close > ind["trend"][j] else "DOWN"
            print(f"[{datetime.now():%H:%M:%S}] price {candles[-1].close:,.2f} | trend {trend} | "
                  f"RSI {ind['rsi'][j]:.0f} | {pos}", flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] data error: {e} (will retry)", flush=True)
        time.sleep(every)


def main() -> None:
    ap = argparse.ArgumentParser(description="Gold buy/sell/close signal bot")
    ap.add_argument("--source", choices=["binance", "yahoo"], default="binance")
    ap.add_argument("--interval", choices=list(INTERVALS), default="15m")
    ap.add_argument("--every", type=int, default=30, help="seconds between checks (live mode)")
    ap.add_argument("--backtest", action="store_true", help="test the strategy on recent history")
    ap.add_argument("--demo", action="store_true", help="backtest on fake prices (no internet)")
    args = ap.parse_args()

    if os.name == "nt":
        os.system("")  # enable colours on Windows terminals

    try:
        if args.demo:
            backtest(fake_candles())
        elif args.backtest:
            fetch = fetch_binance if args.source == "binance" else fetch_yahoo
            backtest(fetch(args.interval, 1000)[:-1])
        else:
            live(args.source, args.interval, args.every)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
