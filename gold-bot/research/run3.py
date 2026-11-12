import os, sys, itertools
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from bt import *
from run1 import get

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
df = get("4h"); c = df.close.values; A = atr(df, 14); inf = np.full(len(c), np.inf)
e200 = ema(c, 200)


def donch(N, sl, trl, side, cost=COST, filt=None):
    hh = pd.Series(df.high.values).rolling(N).max().shift(1).values
    ll = pd.Series(df.low.values).rolling(N).min().shift(1).values
    L = (c > hh) & (c > e200)
    S = (c < ll) & (c < e200)
    if filt is not None:
        L &= filt
    if side == "long": S = np.zeros(len(c), bool)
    if side == "short": L = np.zeros(len(c), bool)
    return simulate_fast(df, L, S, sl * A, inf, trail=trl * A, cost=cost)


base = donch(55, 2.0, 3.0, "long")
print("CHOSEN: H4 donchian55 breakout, long only above EMA200, SL 2xATR, trail 3xATR")
for s in split_stats(base, "chosen"): print(s)
print("\nshort side only:", split_stats(donch(55, 2.0, 3.0, "short"), "short"))
print("\ncost x2 ($0.70):", split_stats(donch(55, 2.0, 3.0, "long", cost=0.70), "cost2"))
print("cost x4 ($1.40):", split_stats(donch(55, 2.0, 3.0, "long", cost=1.40), "cost4"))

base["year"] = base.entry_t.dt.year
yr = base.groupby("year").agg(trades=("pnl", "size"), wins=("pnl", lambda p: int((p > 0).sum())),
                              pnl=("pnl", "sum"), avgR=("R", "mean"))
# buy & hold per year for comparison
cl = df.close.groupby(df.index.year)
yr["gold_move$"] = (cl.last() - cl.first()).round(1)
yr["win%"] = (100 * yr.wins / yr.trades).round(0)
print("\nYear by year:\n", yr.round(2).to_string())
print("\nexit reasons:", base.why.value_counts().to_dict())
print("avg hold (days):", round(((base.exit_t - base.entry_t).dt.total_seconds() / 86400).mean(), 1))
print("avg win $:", round(base.pnl[base.pnl > 0].mean(), 1), " avg loss $:", round(base.pnl[base.pnl < 0].mean(), 1))
print("largest loss $:", round(base.pnl.min(), 1), " largest win $:", round(base.pnl.max(), 1))
# losing streak
s = (base.pnl < 0).astype(int).values; best = cur = 0
for v in s:
    cur = cur + 1 if v else 0; best = max(best, cur)
print("longest losing streak:", best)

# plateau check: neighbours of the chosen point
print("\nNeighbourhood (IN PF / OUT PF):")
for N in (40, 50, 55, 60, 70):
    row = []
    for trl in (2.5, 3.0, 3.5, 4.0):
        a, b = split_stats(donch(N, 2.0, trl, "long"), "")
        row.append(f"tr{trl}: {a['PF']:.2f}/{b['PF']:.2f}")
    print(f"N={N}: ", "  ".join(row))

# last 20 trades
print("\nLast 12 trades:\n", base.tail(12)[["entry_t", "exit_t", "entry", "exit", "pnl", "why"]].round(2).to_string())
print("\nLast bar in data:", df.index[-1], df.close.iloc[-1])
