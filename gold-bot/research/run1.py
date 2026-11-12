import os, sys, time, itertools
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from bt import *

pd.set_option("display.width", 220); pd.set_option("display.max_columns", 30)
CACHE = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE, exist_ok=True)


def get(tf):
    f = os.path.join(CACHE, f"{tf}.parquet")
    if not os.path.exists(f):
        load(tf).to_parquet(f)
    return pd.read_parquet(f)


def cross_up(a, b):
    return np.r_[False, (a[:-1] <= b[:-1]) & (a[1:] > b[1:])]


def cross_dn(a, b):
    return np.r_[False, (a[:-1] >= b[:-1]) & (a[1:] < b[1:])]


def main():
  rows = []
  for tf in ["15min", "1h"]:
      df = get(tf)
      c = df.close.values
      A = atr(df, 14); R = rsi(c, 14)
      e9, e21, e50, e200 = ema(c, 9), ema(c, 21), ema(c, 50), ema(c, 200)
      inf = np.full(len(c), np.inf)

      # A) current app strategy
      L = cross_up(e9, e21) & (c > e200) & (R >= 50) & (R <= 70)
      S = cross_dn(e9, e21) & (c < e200) & (R >= 30) & (R <= 50)
      tr = simulate_fast(df, L, S, 1.5 * A, 3.0 * A, exit_long=cross_dn(e9, e21), exit_short=cross_up(e9, e21))
      rows += split_stats(tr, f"{tf} A current")

      # B) Donchian breakout + ATR trailing stop (trend following)
      for N in (20, 55):
          hh = pd.Series(df.high.values).rolling(N).max().shift(1).values
          ll = pd.Series(df.low.values).rolling(N).min().shift(1).values
          L = (c > hh) & (c > e200); S = (c < ll) & (c < e200)
          tr = simulate_fast(df, L, S, 2.0 * A, inf, trail=3.0 * A)
          rows += split_stats(tr, f"{tf} B donchian{N} trail3")

      # C) pullback in trend: e50>e200, RSI dips <40 then crosses back above 40
      up = (e50 > e200) & (c > e200); dn = (e50 < e200) & (c < e200)
      L = up & cross_up(R, np.full(len(R), 40.0)); S = dn & cross_dn(R, np.full(len(R), 60.0))
      for tp in (1.5, 2.0, 3.0):
          tr = simulate_fast(df, L, S, 1.5 * A, tp * A)
          rows += split_stats(tr, f"{tf} C pullback tp{tp}")

      # D) EMA 21/50 cross with trailing
      L = cross_up(e21, e50) & (c > e200); S = cross_dn(e21, e50) & (c < e200)
      tr = simulate_fast(df, L, S, 2.0 * A, inf, trail=3.0 * A)
      rows += split_stats(tr, f"{tf} D ema21/50 trail")

  print(pd.DataFrame(rows).to_string())


if __name__ == '__main__':
    main()
