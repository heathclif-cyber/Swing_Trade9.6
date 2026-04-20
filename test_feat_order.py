import json
from pathlib import Path
from ml.ml_feature_calculator import FEATURE_COLS

with open("models/feature_cols_v2.json") as f:
    expected = json.load(f)

mismatched = [
    (i, expected[i], FEATURE_COLS[i])
    for i in range(min(len(expected), len(FEATURE_COLS)))
    if expected[i] != FEATURE_COLS[i]
]

print(f"Total expected : {len(expected)}")
print(f"Total actual   : {len(FEATURE_COLS)}")
print(f"Mismatched     : {len(mismatched)}")
for i, exp, act in mismatched:
    print(f"  [{i}] expected={exp}, actual={act}")
