import numpy as np
import pandas as pd
import pandas_ta as ta
from ml.ml_feature_calculator import calc_atr

np.random.seed(42)
n = 200
close = pd.Series(100 + np.cumsum(np.random.randn(n)))
high  = close + abs(np.random.randn(n))
low   = close - abs(np.random.randn(n))

atr_ta    = ta.atr(high, low, close, length=14)
atr_fixed = calc_atr(high, low, close, 14)

diff = (atr_fixed - atr_ta).dropna().abs().mean()
print("pandas_ta (ref):", atr_ta.dropna().tail(5).values.round(6))
print("calc_atr (fixed):", atr_fixed.dropna().tail(5).values.round(6))
print("Mean abs diff:", round(diff, 10))
if diff < 1e-8:
    print("Result: MATCH PERFECT")
else:
    print("Result: STILL MISMATCH")
