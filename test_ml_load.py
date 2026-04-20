import json, sys
sys.path.insert(0, '.')

# Test 1: inference_config exists?
from pathlib import Path
cfg_path = Path("models/inference_config.json")
print(f"inference_config exists: {cfg_path.exists()}")
if cfg_path.exists():
    with open(cfg_path) as f:
        cfg = json.load(f)
    print(f"n_features: {cfg['model_architecture']['n_features']}")
    print(f"seq_len: {cfg['inference']['seq_len']}")
    print(f"model_files: {cfg['model_files']}")

# Test 2: model files exist?
for key, fname in cfg['model_files'].items():
    p = Path("models") / fname
    print(f"  {key}: {fname} — {'EXISTS' if p.exists() else 'MISSING'}")

# Test 3: try load ml_signal
try:
    from ml.ml_signal import MLSignalEngine
    engine = MLSignalEngine()
    print(f"MLSignalEngine loaded OK")
    print(f"n_features in engine: {getattr(engine, 'n_features', 'NOT SET')}")
    print(f"seq_len in engine: {getattr(engine, 'seq_len', 'NOT SET')}")
except Exception as e:
    print(f"MLSignalEngine FAILED: {e}")
