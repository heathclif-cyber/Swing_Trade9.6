# Post-SSOT Refinement Plan

Berdasarkan analisis struktur proyek setelah SSOT restructuring, berikut prioritas perbaikan lanjutan.

## ✅ Already Good
- SSOT ditegakkan — enrichment, normalize, config semua punya satu titik otoritatif
- `core/` dan `ml/` dipisah dengan baik dari UI
- `claude.md` sebagai AI context preservation
- Resilience patterns (multi-endpoint, synthetic OI) terdokumentasi

## 🔴 Priority 1: Pecah protocol_96_ui.py (2.187 baris / 100KB)

Flask Blueprints adalah solusi standar. Usulan struktur:

```
app/
  __init__.py                    # create_app() factory
  blueprints/
    __init__.py
    scanner.py                   # /api/scanner routes
    signals.py                   # /api/test-signal, /api/ping-telegram, /api/test-pendle
    dashboard.py                 # /api/data, HTML render routes
    trades.py                    # /api/trade-entries CRUD, /api/trade-sales
    export.py                    # /api/export-csv, /api/export-excel, /api/analyze-csv
    system.py                    # /api/system_health, /api/models, /api/logbook
  helpers/
    __init__.py
    telegram.py                  # send_telegram_message()
    storage.py                   # load/save trade entries, kv_store helpers
    cache.py                     # _get_enriched_data(), _enrichment_cache
    ml.py                        # _ui_ml_engine initialization
```

**Langkah:**
1. Buat `app/__init__.py` dengan `create_app()` factory
2. Pindahkan setiap grup route ke blueprint masing-masing
3. Pindahkan helper functions ke `app/helpers/`
4. Update `Procfile` dan `gunicorn_config.py` jika perlu
5. Hapus `protocol_96_ui.py` setelah semua route termigrasi

## 🔴 Priority 2: Pindahkan runtime state files ke data/

```
alert_state.json  →  data/alert_state.json
trade_entries.json → data/trade_entries.json
```

Update referensi di:
- `signal_monitor.py` — `_load_alert_state()`, `_save_alert_state()`
- `protocol_96_ui.py` — `load_trade_entries()`, `save_trade_entries()`

## 🟡 Priority 3: Rename core/models.py → core/compat.py

`core/models.py` saat ini hanya berisi `ProbabilityCalibrator` — pickle compatibility stub.
Nama `models.py` menyesatkan (kesannya data models).

**Langkah:**
1. `git mv core/models.py core/compat.py`
2. Update import di `ml/ml_signal.py` (atau file lain yang meng-import)
3. Update `claude.md`

## 🟡 Priority 4: Buat tests/ folder

```
tests/
  __init__.py
  conftest.py                    # Fixtures bersama
  test_normalize.py              # Test core/normalize.py
  test_helpers.py                # Test core/helpers.py
  test_feature_calculator.py     # Test ml_feature_calculator (mock data)
  test_ml_signal.py              # Integration test MLSignalEngine
  test_algo_scoring.py           # Test scoring pipeline
```

## 🟡 Priority 5: Pisah config dari model artifacts

```
config/
  inference_config.json          # Pindah dari models/
  model_registry.json            # Pindah dari models/
  feature_cols_v2.json           # Pindah dari models/
  shap_ranking.json              # Pindah dari models/

models/                          # Hanya binary artifacts (.pkl, .pt)
  lgbm_baseline.pkl
  lstm_best.pt
  lstm_scaler.pkl
  ensemble_meta.pkl
  calibrator.pkl
```

Update `core/helpers.py` — `load_inference_config()` path.

## 🔵 Long-term: Git LFS untuk model artifacts

File `.pkl` dan `.pt` akan membengkakkan repo seiring waktu.
Solusi: Git LFS atau external storage (S3, HuggingFace Hub).

## 🔵 Long-term: Root-level cleanup

| File sekarang | Sebaiknya di |
|---------------|-------------|
| `algo_scoring.py` | `core/scoring.py` atau `ml/scoring.py` |
| `signal_monitor.py` | `services/monitor.py` atau `workers/signal_monitor.py` |
| `trade_logger.py` | `services/trade_logger.py` |
| `protocol_96_enrichment.py` | `core/data/enrichment.py` atau `services/enrichment.py` |

---

## Dependency Impact Matrix

| Perubahan | File yang perlu diupdate | Risiko |
|-----------|-------------------------|--------|
| Blueprint refactor | protocol_96_ui.py → app/ | **High** — 2187 line refactor |
| Pindah state files | signal_monitor.py, protocol_96_ui.py | **Low** — path string change |
| Rename core/models.py | ml/ml_signal.py (import) | **Low** — single import |
| Pisah config | core/helpers.py | **Low** — path config change |
| Root cleanup | Banyak imports | **Medium** — banyak file kena |
