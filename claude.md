# DO NOT IGNORE — required for AI context preservation in this project

# =============================================================================
# KNOWN TECHNICAL DEBT — identified 2026-04-29, not yet addressed
# =============================================================================
# These are acknowledged issues that should be fixed in future sessions.
# See plans/post-ssot-refinement-plan.md for full details.
#
# 🔴 Priority 1: protocol_96_ui.py is 2,187 lines / 100KB
#   Flask best practice: split into Blueprints under app/blueprints/
#   Proposed: scanner.py, signals.py, dashboard.py, trades.py, export.py, system.py
#
# 🔴 Priority 2: Runtime state files (alert_state.json, trade_entries.json)
#   should be in data/ subfolder, not project root
#
# 🟡 Priority 3: core/models.py is misleadingly named
#   It only contains ProbabilityCalibrator (pickle compat stub).
#   Should be renamed to core/compat.py
#
# 🟡 Priority 4: No tests/ directory
#   All test_*.py files were one-time diagnostics (deleted).
#   Need proper test suite for safe refactoring.
#
# 🟡 Priority 5: Config files mixed with model artifacts
#   inference_config.json, model_registry.json etc. in models/ folder
#   alongside .pkl/.pt binaries. Should split into config/ directory.
#
# 🔵 Long-term: Git LFS for .pkl/.pt model artifacts
# 🔵 Long-term: Move root files into subdirectories
#   algo_scoring.py → core/scoring.py
#   signal_monitor.py → services/monitor.py
#   trade_logger.py → services/trade_logger.py
#   protocol_96_enrichment.py → core/data/enrichment.py

# =============================================================================
# 1. PROJECT METADATA
# =============================================================================
# Project Name : Swing_Trade9.6
# Language     : Python 3.12
# Framework    : Flask 3.0.3 (web dashboard), PyTorch 2.x (LSTM), LightGBM
# Purpose      : Automated cryptocurrency trading signal system using stacking
#                ensemble ML (LightGBM + LSTM + Logistic Regression meta-learner
#                with isotonic calibration). 85 Smart Money features (v4).
#                Protocol 9.6 — 7-priority scoring gates.
# Base Path    : d:\Apps-Dev\Swing_Trade9.6

# =============================================================================
# 2. DIRECTORY STRUCTURE
# =============================================================================
# Root files (production):
#   protocol_96_enrichment.py   — SSOT data enrichment factory (get_fully_enriched_data)
#   protocol_96_ui.py           — Flask dashboard server (2289 lines, main web entry point)
#   signal_monitor.py           — Background signal monitoring thread (Telegram alerts)
#   algo_scoring.py             — ML scoring engine (71-point score entry point)
#   trade_logger.py             — Supabase trade logging (entry/exit/PnL)
#   requirements.txt            — Python dependencies
#   Procfile                    — Gunicorn web process (for Railway deployment)
#   gunicorn_config.py          — Gunicorn worker configuration
#   db_init.sql                 — PostgreSQL schema (kv_store, trade_log tables)
#   package.json                — Tailwind CSS build pipeline
#   tailwind.config.js          — Tailwind configuration
#   claude.md                   — THIS FILE — AI context preservation

# core/                         — Shared business logic (SSOT modules)
#   __init__.py
#   helpers.py                  — Config loader (load_inference_config), utilities
#   levels.py                   — TP/SL level calculations (ATR-based)
#   momentum.py                 — Momentum analysis, exit signals, trailing SL
#   models.py                   — Pickle compatibility stub (ProbabilityCalibrator)
#   normalize.py                — SSOT: normalize_columns, BINANCE_KLINE_URLS, SYMBOL_MAP

# ml/                           — ML pipeline
#   __init__.py
#   ml_feature_calculator.py    — 85-feature engineering pipeline (v3+v4 Smart Money)
#   ml_signal.py                — ML inference engine (TradingLSTM, MLSignalEngine)

# models/                       — Model artifacts (SSOT)
#   inference_config.json       — Central configuration (ALL parameters)
#   model_registry.json         — Active model tracking
#   feature_cols_v2.json        — Canonical feature columns for LSTM
#   shap_ranking.json           — SHAP feature importance
#   lgbm_baseline.pkl           — LightGBM model
#   lstm_best.pt                — PyTorch LSTM weights
#   lstm_scaler.pkl             — LSTM feature scaler
#   ensemble_meta.pkl           — Meta-learner (Logistic Regression)
#   calibrator.pkl              — Isotonic probability calibrator
#   cv_results.json             — Cross-validation results
#   ensemble_cv_results.json    — Ensemble CV results
#   lstm_cv_results.json        — LSTM CV results
#   runs/                       — (empty) orphaned run dirs deleted

# static/                       — Frontend assets
#   dashboard.js                — Dashboard JavaScript
#   css/input.css               — Tailwind CSS input
#   css/tailwind.css            — Compiled Tailwind output

# templates/
#   dashboard.html              — Main dashboard template

# plans/                        — Documentation
#   tailwind-migration-plan.md
#   ssot-restructuring-plan.md
#   post-ssot-refinement-plan.md — Technical debt + best practice improvements

# =============================================================================
# 3. NAMING & CODE CONVENTIONS
# =============================================================================
# Imports:
#   - Standard library first, then third-party, then local
#   - Local: from core.normalize import normalize_columns, SYMBOL_MAP
#   - Local: import protocol_96_enrichment as enrichment
#   - Local: from ml.ml_signal import MLSignalEngine
#
# Files:
#   - snake_case.py for modules
#   - Prefix "protocol_96_" for Protocol 9.6 core system files
#   - Prefix "test_", "debug_", "check_", "diag_", "patch_", "refit_" for
#     one-time diagnostic scripts (these are candidates for deletion)
#
# Classes:
#   - PascalCase: TradingLSTM, MLSignalEngine, ProbabilityCalibrator, BotState
#
# Functions:
#   - snake_case: get_fully_enriched_data(), normalize_columns()
#   - Private (module-internal): _fetch_klines_raw(), _normalize_h1_columns()
#   - Flask routes: api_data(), api_test_signal(), api_scanner()
#
# Docstrings:
#   - Triple-quote docstrings on all public functions/classes
#   - Indonesian or English — both are used in this codebase
#   - Type hints encouraged but not strictly enforced everywhere

# =============================================================================
# 4. EXTERNAL DEPENDENCIES
# =============================================================================
# Core:
#   flask==3.0.3, pandas, numpy, requests
#
# ML:
#   torch (PyTorch), scikit-learn, lightgbm, shap
#
# Technical Analysis:
#   pandas_ta
#
# Binance API:
#   python-binance
#
# Database:
#   psycopg2-binary, sqlalchemy (for PostgreSQL on Railway/Supabase)
#
# Utilities:
#   python-dotenv (for .env), openpyxl (Excel export), gunicorn (production WSGI)
#
# Configuration:
#   .env file in root — contains TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
#   DATABASE_URL (PostgreSQL), and other secrets
#   models/inference_config.json — ALL ML parameters (SSOT)

# =============================================================================
# 5. IMPORTANT PATHS
# =============================================================================
# Project Root : d:\Apps-Dev\Swing_Trade9.6
# Config       : models/inference_config.json
# Registry     : models/model_registry.json
# Feature Cols : models/feature_cols_v2.json
# SHAP Ranking : models/shap_ranking.json
# Model Files  : models/lgbm_baseline.pkl, models/lstm_best.pt, etc.
# Env File     : .env (root, gitignored)
# DB Schema    : db_init.sql

# =============================================================================
# 6. ARCHITECTURE & DESIGN DECISIONS
# =============================================================================
# SSOT (Single Source of Truth) — implemented via restructuring:
#   - protocol_96_enrichment.get_fully_enriched_data() is the SSOT for ALL
#     enriched market data (klines, OI, funding rate, macro, fear & greed,
#     liquidity walls, market session, H4 EMAs)
#   - core/normalize.py is the SSOT for:
#       * normalize_columns(df, timeframe) — unified column normalization
#       * BINANCE_KLINE_URLS — 7 Binance endpoints (ISP-resilient ordering)
#       * SYMBOL_MAP — 22 symbol→integer encodings for ML
#   - models/inference_config.json is the SSOT for ALL ML/trading parameters
#   - core/helpers.load_inference_config() is the SSOT config loader
#
# Data Flow:
#   Binance API → protocol_96_enrichment._fetch_klines_raw()
#              → protocol_96_enrichment.get_fully_enriched_data()
#              → algo_scoring._score() / signal_monitor._evaluate_pair()
#              → protocol_96_ui (Flask dashboard)
#
# ML Pipeline:
#   ml_feature_calculator.calculate_features_realtime() → 85 features
#   ml_signal.MLSignalEngine._predict_ensemble() → LGBM + LSTM → meta → calibrator
#
# Storage:
#   PostgreSQL (Railway/Supabase) primary, JSON file fallback for dev
#   kv_store table for trade entries + alert state
#   trade_log table for entry/exit logging
#
# Resilience:
#   Multi-endpoint kline fetching with last-working-URL caching
#   Synthetic OI when fapi.binance.com is blocked
#   Binance Client with REST fallback
#   Signal stability gate (anti-flip) with cooldown periods
#   Volatility Circuit Breaker (ATR-based)
#
# Frontend:
#   Flask server-side rendering + Tailwind CSS
#   Compiled via package.json scripts (build:css, watch:css)

# =============================================================================
# 7. DELETED FILES (no longer in project — do not reference or import)
# =============================================================================
# data_engine.py                 — Deprecated; all functions in enrichment SSOT
# check_macro_pred.py            — One-time diagnostic
# check_scaler.py                — One-time diagnostic
# check_vcb.py                   — One-time diagnostic
# check_zeros.py                 — One-time diagnostic
# debug_coins.py                 — One-time diagnostic
# debug_model_compare.py         — One-time diagnostic
# debug_probas.py                — One-time diagnostic
# debug_wait.py                  — One-time diagnostic
# diag_cal.py                    — One-time diagnostic
# diag_e2e.py                    — One-time diagnostic
# diag_features.py               — One-time diagnostic
# diag_tg.py                     — Diagnostic with exposed Telegram tokens
# patch_scaler.py                — One-time patch
# refit_scaler.py                — One-time refit
# protocol_96_tracker.py         — Standalone tracker, no production imports
# test_atr_verify.py             — One-time test
# test_feat_order.py             — One-time test
# test_live.py                   — One-time test
# test_ml_features.py            — One-time test
# test_ml_load.py                — One-time test
# test_shap_debug.py             — One-time test
# models/runs/run_*              — 9 orphaned run directories (all contents
#                                  duplicated in root models/)

# =============================================================================
# 8. AI EDITING RULES
# =============================================================================
# 1. Do NOT delete or modify claude.md without explicit user approval.
# 2. Before deleting any file, verify no production code imports it.
# 3. Before modifying a function, check all callers for compatibility.
# 4. All data fetching MUST go through protocol_96_enrichment (SSOT).
# 5. All shared constants MUST go through core/normalize.py (SSOT).
# 6. All config parameters MUST go through models/inference_config.json (SSOT).
# 7. Do NOT create duplicate kline-fetching, normalization, or SYMBOL_MAP.
# 8. Use consistent import style: "from core.normalize import ..."
# 9. Commit changes with descriptive messages in Indonesian or English.
# 10. Run system integrity verification after any restructuring.

# =============================================================================
# 9. GIT INFORMATION
# =============================================================================
# claude.md is NOT in .gitignore — it MUST be tracked in version control
# so that all AI agents (Claude, Cursor, etc.) can access project context.
# If you add claude.md to .gitignore, AI agents will lose project context.
#
# .gitignore currently ignores:
#   .env, __pycache__/, *.pyc, node_modules/, .claude/
#
# .gitignore does NOT ignore:
#   claude.md, *.py, *.json, *.sql, *.html, *.js, *.css, *.md
