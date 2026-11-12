"""Backtest research on real XAUUSD M1 data (Exness MT5, 2017-04 .. 2026-08)."""
import numpy as np
import pandas as pd

import os
# Real XAUUSD M1 bars: github.com/sherwynjoel/xauusd-historical-data (xauusd_m1_full.parquet)
SRC = os.environ.get("GOLD_M1_PARQUET", "xauusd_m1_full.parquet")
COST = 0.35          # $/oz round trip: typical spread (~0.26) + slippage
SPLIT = "2023-01-01"  # tune before, judge after


def load(tf: str) -> pd.DataFrame:
    m1 = pd.read_parquet(SRC, columns=["open", "high", "low", "close"])
    df = m1.resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return df


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.maximum(d, 0)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.maximum(-d, 0)).ewm(alpha=1 / n, adjust=False).mean().values
    return 100 - 100 / (1 + up / np.where(dn == 0, 1e-12, dn))


def atr(df, n=14):
    h, l, c = df.high.values, df.low.values, df.close.values
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def simulate_fast(df, long_sig, short_sig, sl_dist, tp_dist, **kw):
    """Signals are known at bar close i -> enter at the open of bar i+1. One trade at a time.
    sl_dist/tp_dist: price distances fixed at the signal bar (tp may be inf).
    trail: trailing-stop distance; be_at: breakeven after be_at x risk.
    If stop and target are hit in the same bar, the stop is assumed first (conservative)."""
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    n = len(c); t = df.index
    exit_long, exit_short = kw.get("exit_long"), kw.get("exit_short")
    trail, be_at, max_bars, cost = kw.get("trail"), kw.get("be_at"), kw.get("max_bars"), kw.get("cost", COST)
    allow = kw.get("allow")  # optional bool array: bar allowed for entries
    trades = []
    i = 0
    while i < n - 1:
        if not (long_sig[i] or short_sig[i]) or (allow is not None and not allow[i]) \
                or not np.isfinite(sl_dist[i]) or sl_dist[i] <= 0:
            i += 1; continue
        pos = 1 if long_sig[i] else -1
        j = i + 1; entry = o[j]; risk = sl_dist[i]
        stop = entry - pos * risk; target = entry + pos * tp_dist[i]
        tr_d = trail[i] if trail is not None else None
        best = entry; k = j; why = None; px = None
        while k < n:
            if pos == 1:
                if l[k] <= stop: px, why = min(o[k], stop), "sl"; break
                if h[k] >= target: px, why = max(o[k], target), "tp"; break
                best = max(best, h[k])
            else:
                if h[k] >= stop: px, why = max(o[k], stop), "sl"; break
                if l[k] <= target: px, why = min(o[k], target), "tp"; break
                best = min(best, l[k])
            if be_at is not None and pos * (best - entry) >= be_at * risk:
                stop = max(stop, entry + pos * 0.1) if pos == 1 else min(stop, entry + pos * 0.1)
            if tr_d is not None:
                ns = best - pos * tr_d
                stop = max(stop, ns) if pos == 1 else min(stop, ns)
            ex = exit_long if pos == 1 else exit_short
            if k + 1 < n and ((ex is not None and ex[k]) or (max_bars and k - j + 1 >= max_bars)):
                k += 1; px, why = o[k], "sig"; break
            k += 1
        if px is None:
            break
        pnl = pos * (px - entry) - cost
        trades.append((t[j], t[k], pos, entry, px, pnl, pnl / risk, why))
        i = k  # flat again from the exit bar
    return pd.DataFrame(trades, columns=["entry_t", "exit_t", "side", "entry", "exit", "pnl", "R", "why"])


def stats(tr: pd.DataFrame, label="") -> dict:
    if len(tr) == 0:
        return {"label": label, "n": 0}
    p = tr.pnl.values
    eq = np.cumsum(p)
    dd = np.max(np.maximum.accumulate(np.r_[0, eq])[1:] - eq) if len(eq) else 0
    gw, gl = p[p > 0].sum(), -p[p < 0].sum()
    yrs = max((tr.exit_t.iloc[-1] - tr.entry_t.iloc[0]).days / 365.25, 0.1)
    return {"label": label, "n": len(p), "per_yr": round(len(p) / yrs), "win%": round(100 * (p > 0).mean(), 1),
            "PF": round(gw / gl, 2) if gl else np.inf, "total$": round(p.sum(), 1), "avgR": round(tr.R.mean(), 3),
            "maxDD$": round(dd, 1), "ret/DD": round(p.sum() / dd, 2) if dd else np.inf}


def split_stats(tr, label=""):
    ins = tr[tr.entry_t < SPLIT]; oos = tr[tr.entry_t >= SPLIT]
    return stats(ins, label + " IN"), stats(oos, label + " OUT")
