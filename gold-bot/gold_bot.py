#!/usr/bin/env python3
"""
Gold Signal Bot
===============
Watches a live gold (XAU/USD) chart and tells you exactly when to BUY, when to
CLOSE, and when to DO NOTHING. It does NOT place trades; you decide.

Strategy: 4-hour breakout trend-following, chosen by backtesting over 100 variations on
9 years of real XAUUSD data (see research/RESULTS.md):
  * BUY      : a 4-hour candle closes above the highest high of the previous
               55 candles, while price is above the 200 EMA (uptrend).
               Enter at the next candle's open.
  * Stop     : starts 2 x ATR(14) below entry, then trails: highest high since
               entry minus 3 x ATR (ATR fixed at entry). It only ever moves up.
  * CLOSE    : when price touches the stop. No fixed take profit, so winners run.
  * NO SELLS : every short-selling rule tested lost money on gold.
  * Otherwise: DO NOTHING.

Backtest 2017-04 .. 2026-08 (Exness XAUUSD, $0.35 cost/trade): 169 trades,
46% wins, avg win $45/oz vs avg loss $19/oz, profit factor 2.09,
9 of 10 years profitable. Past results do not guarantee future results.

Data sources (no API key needed):
  * binance : PAXG/USDT, a gold-backed token (1 token = 1 oz), real time, 24/7
  * yahoo   : GC=F gold futures (may be delayed ~10 min, closed on weekends)

Usage:
  python gold_bot.py                    # live, 4-hour signals, checks every 5s
  python gold_bot.py --backtest         # test on the last ~1000 4-hour candles
  python gold_bot.py --csv XAUUSD.csv   # test on your own MT5 history export
  python gold_bot.py --demo             # offline test with fake prices

Optional phone alerts via Telegram: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
Only uses the Python standard library - nothing to install.
"""

from __future__ import annotations

import argparse
import csv
import json
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
# Settings (these are the tested values; changing them voids the backtest)
# ----------------------------------------------------------------------------
BREAKOUT_N = 55     # candles in the breakout window
EMA_TREND = 200
ATR_LEN = 14
SL_ATR = 2.0        # first stop distance in ATRs
TRAIL_ATR = 3.0     # trailing distance in ATRs
COST = 0.35         # $/oz spread + slippage, used in backtests

STATE_FILE = Path(__file__).with_name("bot_state.json")
LOG_FILE = Path(__file__).with_name("signals_log.csv")
INTERVALS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


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
    req = urllib.request.Request(url, headers={"User-Agent": "gold-signal-bot/2.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fetch_binance(interval: str, limit: int = 1000) -> list[Candle]:
    q = urllib.parse.urlencode({"symbol": "PAXGUSDT", "interval": interval, "limit": limit})
    last = None
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            rows = _get_json(f"{host}/api/v3/klines?{q}")
            return [Candle(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]
        except Exception as e:  # try the next host
            last = e
    raise last


def resample(candles: list[Candle], seconds: int) -> list[Candle]:
    """Group candles into UTC-aligned buckets (e.g. 4h = 00:00, 04:00, ...)."""
    out: list[Candle] = []
    for c in candles:
        b = c.time - c.time % seconds
        if out and out[-1].time == b:
            o = out[-1]
            o.high, o.low, o.close = max(o.high, c.high), min(o.low, c.low), c.close
        else:
            out.append(Candle(b, c.open, c.high, c.low, c.close))
    return out


def fetch_yahoo(interval: str, limit: int = 1000) -> list[Candle]:
    yi = {"15m": "15m", "1h": "60m", "4h": "60m", "1d": "1d"}[interval]
    rng = {"15m": "30d", "60m": "730d", "1d": "10y"}[yi]
    q = urllib.parse.urlencode({"interval": yi, "range": rng})
    data = _get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?{q}")
    res = data["chart"]["result"][0]
    quote = res["indicators"]["quote"][0]
    candles = [Candle(int(t), o, h, l, c) for t, o, h, l, c in
               zip(res["timestamp"], quote["open"], quote["high"], quote["low"], quote["close"])
               if None not in (o, h, l, c)]
    if interval == "4h":
        candles = resample(candles, 14400)
    return candles[-limit:]


def load_csv(path: str) -> list[Candle]:
    """Read an MT5 'Export bars' file (or any CSV with date/time/open/high/low/close)
    and turn it into 4-hour candles."""
    rows = []
    with open(path, newline="") as f:
        sample = f.read(4096); f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        rd = csv.reader(f, dialect)
        header = [h.strip("<>").lower() for h in next(rd)]
        col = {name: header.index(name) for name in header}
        for r in rd:
            if not r:
                continue
            if "date" in col and "time" in col:
                stamp = f"{r[col['date']]} {r[col['time']]}"
            else:
                stamp = r[col.get("date", col.get("time", 0))]
            stamp = stamp.replace(".", "-").replace("T", " ")[:19]
            fmt = "%Y-%m-%d %H:%M:%S" if stamp.count(":") == 2 else "%Y-%m-%d %H:%M" if ":" in stamp else "%Y-%m-%d"
            t = int(datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc).timestamp())
            rows.append(Candle(t, float(r[col["open"]]), float(r[col["high"]]), float(r[col["low"]]), float(r[col["close"]])))
    rows.sort(key=lambda c: c.time)
    return resample(rows, 14400)


def fake_candles(n: int = 1500, step: int = 14400, seed: int = 7) -> list[Candle]:
    """Random-walk prices with trending phases, for offline testing only."""
    rnd = random.Random(seed)
    price, drift = 2650.0, 0.0
    t0 = int(time.time()) - n * step
    out = []
    for i in range(n):
        if i % 120 == 0:
            drift = rnd.choice([-2.0, -1.0, 1.0, 2.0, 3.0])
        o = price
        c = max(100.0, o + drift + rnd.gauss(0, 12))
        h = max(o, c) + abs(rnd.gauss(0, 6))
        l = min(o, c) - abs(rnd.gauss(0, 6))
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


def atr(candles: list[Candle], length: int) -> list[float]:
    out, prev = [], None
    for i, c in enumerate(candles):
        pc = candles[i - 1].close if i else c.close
        tr = max(c.high - c.low, abs(c.high - pc), abs(c.low - pc))
        prev = tr if prev is None else (prev * (length - 1) + tr) / length
        out.append(prev)
    return out


def indicators(candles: list[Candle]) -> dict:
    highs = [c.high for c in candles]
    hh = [float("nan")] * len(candles)
    for i in range(BREAKOUT_N, len(candles)):
        hh[i] = max(highs[i - BREAKOUT_N:i])
    return {"trend": ema([c.close for c in candles], EMA_TREND), "atr": atr(candles, ATR_LEN), "hh": hh}


# ----------------------------------------------------------------------------
# Strategy
# ----------------------------------------------------------------------------
@dataclass
class Position:
    entry: float
    stop: float
    atr: float
    best: float
    opened: int         # time of the entry candle
    first_bar: int      # time of the first candle the trade is managed on


@dataclass
class Signal:
    action: str        # BUY, CLOSE
    price: float
    time: int
    reason: str
    stop: float | None = None
    pnl: float | None = None
    opened: int | None = None   # for CLOSE: which trade it closes


class Strategy:
    def __init__(self) -> None:
        self.position: Position | None = None

    def step(self, candles: list[Candle], i: int, ind: dict, next_open: float | None) -> list[Signal]:
        """Process closed candle i. next_open is the open of candle i+1 (the entry price)."""
        c, out = candles[i], []
        p = self.position
        if p and c.time >= p.first_bar:
            if c.low <= p.stop:
                px = min(c.open, p.stop)
                why = "Trailing stop hit, profit locked in" if px >= p.entry else "Stop loss hit"
                out.append(Signal("CLOSE", px, c.time, why, pnl=px - p.entry, opened=p.opened))
                self.position = None
            else:
                p.best = max(p.best, c.high)
                p.stop = max(p.stop, p.best - TRAIL_ATR * p.atr)
        hh, tr, a = ind["hh"][i], ind["trend"][i], ind["atr"][i]
        if self.position is None and i >= EMA_TREND and c.close > hh and c.close > tr:
            entry = next_open if next_open is not None else c.close
            step = candles[1].time - candles[0].time
            self.position = Position(entry, entry - SL_ATR * a, a, entry, c.time + step, c.time + step)
            out.append(Signal("BUY", entry, c.time,
                              f"4-hour candle closed at {c.close:,.2f}, above the {BREAKOUT_N}-candle high "
                              f"{hh:,.2f}, in an uptrend", stop=self.position.stop))
        return out


# ----------------------------------------------------------------------------
# Alerts / output
# ----------------------------------------------------------------------------
COLORS = {"BUY": "\033[92m", "CLOSE": "\033[93m", "WAIT": "\033[90m"}
RESET = "\033[0m"


def ts(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def format_signal(sig: Signal) -> str:
    lines = [f"{sig.action} GOLD @ {sig.price:,.2f}   ({ts(sig.time)})", f"Why: {sig.reason}"]
    if sig.stop is not None:
        lines.append(f"Stop loss: {sig.stop:,.2f}   (no take profit - the stop trails up)")
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
    bar = "=" * 60
    print(f"\a\n{COLORS.get(sig.action, '')}{bar}\n{msg}\n{bar}{RESET}\n", flush=True)
    send_telegram("🟡 Gold bot\n" + msg)
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a") as f:
        if new:
            f.write("time,action,price,stop,pnl,reason\n")
        stop = "" if sig.stop is None else f"{sig.stop:.2f}"
        pnl = "" if sig.pnl is None else f"{sig.pnl:.2f}"
        f.write(f"{ts(sig.time)},{sig.action},{sig.price:.2f},{stop},{pnl},\"{sig.reason}\"\n")


# ----------------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------------
def backtest(candles: list[Candle], quiet: bool = False) -> None:
    ind = indicators(candles)
    strat = Strategy()
    pnls: list[float] = []
    print(f"Backtest on {len(candles)} 4-hour candles: {ts(candles[0].time)} -> {ts(candles[-1].time)}\n")
    for i in range(len(candles)):
        nxt = candles[i + 1].open if i + 1 < len(candles) else None
        for sig in strat.step(candles, i, ind, nxt):
            if not quiet:
                print(format_signal(sig) + "\n")
            if sig.pnl is not None:
                pnls.append(sig.pnl - COST)
    print("-" * 60)
    if not pnls:
        print("No completed trades in this period.")
        return
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    pf = sum(wins) / -sum(losses) if losses and sum(losses) else float("inf")
    print(f"Trades: {len(pnls)}   Wins: {len(wins)} ({100 * len(wins) / len(pnls):.0f}%)   Profit factor: {pf:.2f}")
    print(f"Avg win: {sum(wins) / max(len(wins), 1):+,.2f}   Avg loss: {sum(losses) / max(len(losses), 1):+,.2f} $/oz")
    print(f"Total after ${COST} costs: {sum(pnls):+,.2f} $/oz")
    print("Past results do not guarantee future results.")


def describe_now(candles: list[Candle], closed: list[Candle], ind: dict, strat: Strategy) -> str:
    j, live = len(closed) - 1, candles[-1]
    trigger = max(c.high for c in closed[-BREAKOUT_N:])
    trend_up = closed[j].close > ind["trend"][j]
    p = strat.position
    if p:
        return f"HOLD BUY from {p.entry:,.2f} | stop {p.stop:,.2f} | P/L {live.close - p.entry:+,.2f} $/oz"
    if not trend_up:
        return f"DO NOTHING - downtrend (below EMA200 {ind['trend'][j]:,.2f}); no buys, and selling lost money in testing"
    if live.close > trigger:
        return f"GET READY - price is above the trigger {trigger:,.2f}; BUY if this 4h candle closes above it"
    return f"DO NOTHING - uptrend, waiting for a 4h close above {trigger:,.2f} ({trigger - live.close:,.2f} away)"


def load_state(strat: Strategy, source: str) -> int:
    try:
        s = json.loads(STATE_FILE.read_text())
        if s.get("source") == source and s.get("version") == 2:
            strat.position = Position(**s["position"]) if s.get("position") else None
            return int(s.get("last_time", 0))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return 0


def save_state(strat: Strategy, source: str, last_time: int) -> None:
    STATE_FILE.write_text(json.dumps({
        "version": 2, "source": source, "last_time": last_time,
        "position": asdict(strat.position) if strat.position else None,
    }, indent=2))


def live(source: str, every: int) -> None:
    fetch = fetch_binance if source == "binance" else fetch_yahoo
    strat = Strategy()
    last = load_state(strat, source)
    print(f"Gold Signal Bot v2 - {source} - 4-hour breakout signals - checking every {every}s. Ctrl+C to stop.")
    first, warned = last == 0, None
    while True:
        try:
            candles = fetch("4h")
            closed = candles[:-1]  # last candle is still forming
            if len(closed) < EMA_TREND + 2:
                print("Not enough history yet."); time.sleep(every); continue
            ind = indicators(closed)
            for i in range(len(closed)):
                if first or closed[i].time > last:
                    nxt = candles[i + 1].open
                    for sig in strat.step(closed, i, ind, nxt):
                        if not first and not (sig.action == "CLOSE" and sig.opened == warned):
                            alert(sig)  # skip a CLOSE we already announced live
            if first and strat.position:
                p = strat.position
                print(f"Note: the strategy is already in a BUY from {p.entry:,.2f}. Stop is now {p.stop:,.2f}.")
            first, last = False, closed[-1].time

            # Real time: has the live candle already touched the stop?
            p, lc = strat.position, candles[-1]
            if p and warned != p.opened and lc.low <= p.stop:
                warned = p.opened
                alert(Signal("CLOSE", lc.close, int(time.time()),
                             f"Close the buy NOW: price touched the stop at {p.stop:,.2f}", pnl=lc.close - p.entry))
            save_state(strat, source, last)
            print(f"[{datetime.now():%H:%M:%S}] price {lc.close:,.2f} | {describe_now(candles, closed, ind, strat)}", flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] data error: {e} (will retry)", flush=True)
        time.sleep(every)


def main() -> None:
    ap = argparse.ArgumentParser(description="Gold BUY / CLOSE / DO NOTHING signal bot (4-hour breakout)")
    ap.add_argument("--source", choices=["binance", "yahoo"], default="binance")
    ap.add_argument("--every", type=int, default=5, help="seconds between checks (live mode)")
    ap.add_argument("--backtest", action="store_true", help="test the strategy on recent history")
    ap.add_argument("--csv", help="test the strategy on an MT5 history export (any timeframe up to 4h)")
    ap.add_argument("--demo", action="store_true", help="backtest on fake prices (no internet)")
    ap.add_argument("--quiet", action="store_true", help="backtest summary only")
    args = ap.parse_args()

    if os.name == "nt":
        os.system("")  # enable colours on Windows terminals
    try:
        if args.demo:
            backtest(fake_candles(), args.quiet)
        elif args.csv:
            backtest(load_csv(args.csv), args.quiet)
        elif args.backtest:
            fetch = fetch_binance if args.source == "binance" else fetch_yahoo
            backtest(fetch("4h", 1000)[:-1], args.quiet)
        else:
            live(args.source, args.every)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
