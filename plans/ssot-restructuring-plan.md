# SSOT Restructuring Plan — Swing_Trade9.6

## Current Architecture Analysis

### ✅ Already Good (SSOT-Aligned)
- **`protocol_96_enrichment.py`** — Centralized data factory with `get_fully_enriched_data()` as the master function
- **`core/helpers.py`** — Central config loader via `load_inference_config()` reading `models/inference_config.json`
- **`core/levels.py`** — TP/SL calculations (single source)
- **`core/momentum.py`** — Momentum/exit analysis (single source)
- **`core/models.py`** — Pickle compatibility stub (single source)
- **`trade_logger.py`** — Trade logging to Supabase (single source)
- **`algo_scoring.py`** — ML scoring engine (single source, uses core modules)
- **`ml/ml_feature_calculator.py`** — 85-feature engineering pipeline (single source)
- **`ml/ml_signal.py`** — ML inference engine (single source)

### ❌ Redundant / Duplicate Files

| # | Issue | Files Involved | Action |
|---|-------|---------------|--------|
| 1 | **Deprecated data_engine.py** | `data_engine.py` — all functions duplicated in `protocol_96_enrichment.py` | DELETE after migrating imports |
| 2 | **Duplicate kline fetching** | `data_engine.get_klines_rest()` / `protocol_96_ui.get_klines_rest()` / `protocol_96_enrichment._fetch_klines_raw()` | Consolidate into enrichment only |
| 3 | **Duplicate BINANCE_KLINE_URLS** | `data_engine.py` (line 30), `protocol_96_enrichment.py` (line 57), `protocol_96_ui.py` (line 164) | Single source in enrichment |
| 4 | **Duplicate column normalization** | `signal_monitor._normalize_h1_columns()` / `protocol_96_ui._normalize_m15_columns()` | Extract to shared utility |
| 5 | **Duplicate SYMBOL_MAP** | `ml/ml_feature_calculator.py` / `ml/ml_signal.py` | Single source in feature calculator |
| 6 | **Duplicate OI fetching** | `data_engine.fetch_oi()` / `protocol_96_enrichment._fetch_oi()` | Delete data_engine version |
| 7 | **Duplicate funding rate fetching** | `data_engine.fetch_funding_rate()` / `protocol_96_enrichment._fetch_funding_rate()` | Delete data_engine version |
| 8 | **Duplicate macro data** | `data_engine.get_macro_data()` / `protocol_96_enrichment._fetch_macro_cmc()` | Delete data_engine version |
| 9 | **Duplicate indicators** | `data_engine.apply_base_indicators()` / `protocol_96_enrichment._apply_indicators()` | Delete data_engine version |

### 🗑️ Dead / Unused Debug Files

| File | Likely Purpose | Action |
|------|---------------|--------|
| `check_macro_pred.py` | One-time macro prediction check | DELETE |
| `check_scaler.py` | One-time scaler verification | DELETE |
| `check_vcb.py` | One-time VCB check | DELETE |
| `check_zeros.py` | One-time zero-value check | DELETE |
| `debug_coins.py` | One-time coin debug | DELETE |
| `debug_model_compare.py` | One-time model comparison | DELETE |
| `debug_probas.py` | One-time probability debug | DELETE |
| `debug_wait.py` | One-time wait debug | DELETE |
| `diag_cal.py` | One-time calibration diagnostic | DELETE |
| `diag_e2e.py` | One-time end-to-end diagnostic | DELETE |
| `diag_features.py` | One-time feature diagnostic | DELETE |
| `patch_scaler.py` | One-time scaler patch | DELETE |
| `refit_scaler.py` | One-time scaler refit | DELETE |
| `test_atr_verify.py` | One-time ATR verification | DELETE |
| `test_feat_order.py` | One-time feature order test | DELETE |
| `test_live.py` | One-time live test | DELETE |
| `test_ml_features.py` | One-time ML features test | DELETE |
| `test_ml_load.py` | One-time ML load test | DELETE |
| `test_shap_debug.py` | One-time SHAP debug | DELETE |

### 🗑️ Orphaned Model Run Directories

| Directory | Contents | Action |
|-----------|----------|--------|
| `models/runs/run_20260424_165633/` | lgbm_cv_results.json, lgbm.pkl | DELETE (duplicate of root models/) |
| `models/runs/run_20260424_171644/` | (empty) | DELETE |
| `models/runs/run_20260424_172321/` | lstm_cv_results.json, lstm_scaler.pkl, lstm.pt | DELETE (duplicate of root models/) |
| `models/runs/run_20260424_182730/` | calibrator.pkl, ensemble_cv_results.json, ensemble_meta.pkl | DELETE (duplicate of root models/) |
| `models/runs/run_20260424_182901/` | shap_ranking.json, trading_metrics.json | DELETE (duplicate of root models/) |
| `models/runs/run_20260424_183207/` | backtest_results.json | DELETE (duplicate of root models/) |
| `models/runs/run_20260424_234400/` | shap_ranking.json, trading_metrics.json | DELETE (duplicate of root models/) |
| `models/runs/run_20260424_235028/` | shap_importance.png, shap_ranking.json, trading_metrics.json | DELETE (duplicate of root models/) |
| `models/runs/run_20260424_235932/` | backtest_results.json | DELETE (duplicate of root models/) |

---

## Proposed SSOT Architecture

```
Swing_Trade9.6/
├── core/                          # 🟢 SSOT — Shared business logic
│   ├── __init__.py
│   ├── helpers.py                 # Config loader, utilities
│   ├── levels.py                  # TP/SL calculations
│   ├── models.py                  # Pickle compatibility
│   └── momentum.py                # Momentum/exit analysis
│
├── ml/                            # 🟢 SSOT — ML pipeline
│   ├── __init__.py
│   ├── ml_feature_calculator.py   # 85-feature engineering + SYMBOL_MAP
│   └── ml_signal.py               # ML inference engine
│
├── models/                        # 🟢 SSOT — Model artifacts
│   ├── inference_config.json      # Central configuration
│   ├── model_registry.json        # Active model registry
│   ├── feature_cols_v2.json       # Canonical feature columns
│   ├── shap_ranking.json          # SHAP importance
│   ├── lgbm_baseline.pkl
│   ├── lstm_best.pt
│   ├── lstm_scaler.pkl
│   ├── ensemble_meta.pkl
│   ├── calibrator.pkl
│   ├── cv_results.json
│   ├── ensemble_cv_results.json
│   └── runs/                      # 🗑️ DELETE — all orphaned runs
│
├── protocol_96_enrichment.py      # 🟢 SSOT — Data enrichment factory
├── algo_scoring.py                # 🟢 SSOT — ML scoring engine
├── signal_monitor.py              # 🟢 SSOT — Background signal monitor
├── protocol_96_ui.py              # 🟢 SSOT — Flask dashboard server
├── trade_logger.py                # 🟢 SSOT — Trade logging
│
├── static/                        # Frontend assets
│   ├── dashboard.js
│   └── css/
│       ├── input.css
│       └── tailwind.css
├── templates/
│   └── dashboard.html
│
├── plans/                         # Documentation
│   └── tailwind-migration-plan.md
│
├── .gitignore
├── package.json
├── tailwind.config.js
├── requirements.txt
├── Procfile
├── gunicorn_config.py
├── db_init.sql
│
└── 🗑️ DELETE — All debug/test files listed above
```

---

## Step-by-Step Restructuring Plan

### Phase 1: Create Shared Utility Module

**Goal**: Eliminate duplicate column normalization and URL definitions.

1. **Create `core/normalize.py`** — Shared column normalization utility
   - Extract `_normalize_h1_columns()` from `signal_monitor.py` (lines 226-251)
   - Extract `_normalize_m15_columns()` from `protocol_96_ui.py` (lines 26-54)
   - Create unified `normalize_columns(df, timeframe: str = "1h") -> pd.DataFrame`
   - Define `BINANCE_KLINE_URLS` as a module-level constant (single source)
   - Define `SYMBOL_MAP` as a module-level constant (single source)

2. **Update `signal_monitor.py`**:
   - Replace `_normalize_h1_columns()` with `from core.normalize import normalize_columns`
   - Remove local `_normalize_h1_columns` function

3. **Update `protocol_96_ui.py`**:
   - Replace `_normalize_m15_columns()` with `from core.normalize import normalize_columns`
   - Remove local `_normalize_m15_columns` function
   - Replace local `get_klines_rest()` with `protocol_96_enrichment._fetch_klines_raw()`
   - Remove local `BINANCE_KLINE_URLS`

4. **Update `ml/ml_feature_calculator.py`**:
   - Remove local `SYMBOL_MAP` definition
   - Import from `core.normalize import SYMBOL_MAP`

5. **Update `ml/ml_signal.py`**:
   - Remove local `SYMBOL_MAP` definition
   - Import from `core.normalize import SYMBOL_MAP`

### Phase 2: Retire data_engine.py

**Goal**: Eliminate all duplicate functions by routing through enrichment SSOT.

1. **Audit all imports of `data_engine`**:
   - `signal_monitor.py` — imports `data_engine` for H1 data
   - `protocol_96_ui.py` — imports `data_engine` as fallback (line 718)

2. **Update `signal_monitor.py`**:
   - Replace `data_engine.get_klines_rest()` with `protocol_96_enrichment._fetch_klines_raw()`
   - Replace `data_engine.apply_base_indicators()` with `protocol_96_enrichment._apply_indicators()`
   - Replace `data_engine.fetch_oi()` with `protocol_96_enrichment._fetch_oi()`
   - Replace `data_engine.fetch_funding_rate()` with `protocol_96_enrichment._fetch_funding_rate()`
   - Replace `data_engine.get_macro_data()` with `protocol_96_enrichment._fetch_macro_cmc()`

3. **Update `protocol_96_ui.py`**:
   - Remove the `data_engine` import fallback (line 718-720)
   - Replace any usage with enrichment equivalents

4. **Delete `data_engine.py`** after all imports are migrated

### Phase 3: Remove Dead Debug/Test Files

**Goal**: Clean up 19 unused files.

1. **Verify each file has no imports from production code**:
   - Search for `import check_macro_pred`, `from check_macro_pred`, etc. across all production files
   - If no imports found, safe to delete

2. **Delete all 19 debug/test files** (see table above)

### Phase 4: Clean Up Orphaned Model Runs

**Goal**: Remove 9 redundant run directories.

1. **Verify no code references `models/runs/`**:
   - Search for `models/runs/` or `runs/` in all production files
   - If no references, safe to delete

2. **Delete all 9 run directories** (see table above)

### Phase 5: Consolidate Kline Fetching in protocol_96_ui.py

**Goal**: Eliminate duplicate `get_klines_rest()` in the UI layer.

1. **Update `protocol_96_ui.py`**:
   - Replace local `get_klines_rest()` calls with `protocol_96_enrichment._fetch_klines_raw()`
   - Remove the local `get_klines_rest()` function definition (lines 177-231)
   - Remove local `BINANCE_KLINE_URLS` (lines 164-172)

### Phase 6: Final Verification

**Goal**: Ensure system integrity after all changes.

1. **Run `python protocol_96_ui.py`** — Verify Flask app starts without import errors
2. **Run `python signal_monitor.py`** — Verify signal monitor starts without import errors
3. **Run `python algo_scoring.py`** — Verify scoring engine loads without import errors
4. **Run `python trade_logger.py`** — Verify trade logger loads without import errors
5. **Run `python -c "from ml.ml_signal import MLSignalEngine; print('OK')"`** — Verify ML engine loads
6. **Run `python -c "from ml.ml_feature_calculator import calculate_features_realtime; print('OK')"`** — Verify feature calculator loads

---

## Dependency Graph (Before)

```
data_engine.py ──┬──> signal_monitor.py
                 └──> protocol_96_ui.py

protocol_96_enrichment.py ──> algo_scoring.py ──> signal_monitor.py
                                                  └──> protocol_96_ui.py

core/
├── helpers.py ──> algo_scoring.py, signal_monitor.py, protocol_96_ui.py
├── levels.py ───> algo_scoring.py
├── momentum.py ─> algo_scoring.py
└── models.py ───> (pickle compatibility, loaded by ml_signal.py)

ml/
├── ml_feature_calculator.py ──> ml_signal.py
└── ml_signal.py ──────────────> algo_scoring.py
```

## Dependency Graph (After SSOT)

```
protocol_96_enrichment.py ──> algo_scoring.py ──> signal_monitor.py
                                                  └──> protocol_96_ui.py

core/
├── helpers.py ──> algo_scoring.py, signal_monitor.py, protocol_96_ui.py
├── levels.py ───> algo_scoring.py
├── momentum.py ─> algo_scoring.py
├── models.py ───> (pickle compatibility)
└── normalize.py ─> signal_monitor.py, protocol_96_ui.py, ml_feature_calculator.py, ml_signal.py

ml/
├── ml_feature_calculator.py ──> ml_signal.py
└── ml_signal.py ──────────────> algo_scoring.py

(data_engine.py 🗑️ DELETED)
(19 debug files 🗑️ DELETED)
(9 run directories 🗑️ DELETED)
```

---

## Risk Assessment

| Change | Risk Level | Mitigation |
|--------|-----------|------------|
| Delete `data_engine.py` | **Medium** | Audit all imports first; update signal_monitor.py and protocol_96_ui.py before deletion |
| Delete debug files | **Low** | No production imports found; all are standalone scripts |
| Delete model runs | **Low** | No code references to `models/runs/` in production files |
| Consolidate normalization | **Low** | Pure function extraction; no side effects |
| Consolidate SYMBOL_MAP | **Low** | Dictionary constant; import-only change |
| Consolidate BINANCE_KLINE_URLS | **Low** | List constant; import-only change |
| Replace get_klines_rest() in UI | **Medium** | Must verify function signature matches; enrichment version may need to be made public (remove `_` prefix) |
