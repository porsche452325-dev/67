import os, sys, itertools
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from bt import *
from run1 import get, cross_up, cross_dn  # noqa (run1 prints once on import; guarded below)

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 500)
rows = []


def add(tr, label):
    a, b = split_stats(tr, label)
    rows.append({**{k: v for k, v in a.items()}, **{"o_" + k: v for k, v in b.items() if k != "label"}})


# ---------- 1) Donchian/trend family on 1h and 4h, both directions vs long-only ----------
for tf in ["1h", "4h"]:
    df = get(tf); c = df.close.values; A = atr(df, 14); inf = np.full(len(c), np.inf)
    e200 = ema(c, 200); e50 = ema(c, 50)
    for N, sl, trl, side in itertools.product((20, 40, 55, 100), (2.0, 3.0), (3.0, 4.0, 5.0), ("both", "long")):
        hh = pd.Series(df.high.values).rolling(N).max().shift(1).values
        ll = pd.Series(df.low.values).rolling(N).min().shift(1).values
        L = (c > hh) & (c > e200)
        S = (c < ll) & (c < e200) if side == "both" else np.zeros(len(c), bool)
        tr = simulate_fast(df, L, S, sl * A, inf, trail=trl * A)
        add(tr, f"{tf} donch{N} sl{sl} tr{trl} {side}")

# ---------- 2) London breakout of the Asian range on M15 ----------
df = get("15min"); c = df.close.values; A = atr(df, 14)
idx = df.index; day = idx.normalize(); hr = idx.hour
asia = (hr < 7)
g = pd.DataFrame({"h": np.where(asia, df.high, np.nan), "l": np.where(asia, df.low, np.nan), "d": day})
rng_h = g.groupby("d").h.transform("max").values; rng_l = g.groupby("d").l.transform("min").values
win = (hr >= 7) & (hr < 12)
e200_h1 = pd.Series(get("1h").close.values, index=get("1h").index)
h1e = ema(e200_h1.values, 200)
h1trend = pd.Series(h1e, index=e200_h1.index).reindex(idx, method="ffill").shift(4).values  # last closed H1 bar
first_break = np.zeros(len(c), bool)
# only the first break of the day counts
seen = {}
for i in np.where(win & ((c > rng_h) | (c < rng_l)))[0]:
    if day[i] not in seen:
        seen[day[i]] = 1; first_break[i] = True
eod = hr == 20
width = rng_h - rng_l
for tp_r, filt in itertools.product((1.0, 1.5, 2.0), ("none", "h1trend")):
    L = first_break & (c > rng_h); S = first_break & (c < rng_l)
    if filt == "h1trend":
        L &= c > h1trend; S &= c < h1trend
    sl = np.clip(width * 0.5, 0.5 * A, 3 * A)  # stop at middle of the range, bounded by ATR
    tr = simulate_fast(df, L, S, sl, tp_r * sl, exit_long=eod, exit_short=eod)
    add(tr, f"15m london-break tp{tp_r}R {filt}")

# ---------- 3) M15 pullback entries with H4 trend filter ----------
h4 = get("4h"); h4c = h4.close.values
h4up = pd.Series((ema(h4c, 50) > ema(h4c, 200)) & (h4c > ema(h4c, 50)), index=h4.index)
h4dn = pd.Series((ema(h4c, 50) < ema(h4c, 200)) & (h4c < ema(h4c, 50)), index=h4.index)
# shift so only closed H4 bars are used
up = h4up.shift(1).reindex(idx, method="ffill").fillna(False).values.astype(bool)
dn = h4dn.shift(1).reindex(idx, method="ffill").fillna(False).values.astype(bool)
R = rsi(c, 14); e21 = ema(c, 21)
for lvl, tp in itertools.product((30, 35, 40), (1.5, 2.0, 3.0)):
    L = up & cross_up(R, np.full(len(R), float(lvl)))
    S = dn & cross_dn(R, np.full(len(R), float(100 - lvl)))
    tr = simulate_fast(df, L, S, 1.5 * A, tp * A, max_bars=96)
    add(tr, f"15m H4trend rsi{lvl} tp{tp}")

out = pd.DataFrame(rows)
out.to_csv(os.path.join(os.path.dirname(__file__), "run2.csv"), index=False)
cols = ["label", "n", "win%", "PF", "total$", "maxDD$", "o_n", "o_win%", "o_PF", "o_total$", "o_maxDD$"]
good = out[(out.PF > 1.05) & (out.o_PF > 1.05)].sort_values("PF", ascending=False)
print("Both periods profitable (PF>1.05):", len(good), "of", len(out))
print(good[cols].head(40).to_string())
print("\nLondon + M15 families:")
print(out[out.label.str.startswith("15m")][cols].to_string())
