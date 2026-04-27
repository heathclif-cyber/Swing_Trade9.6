import json
import logging
import sys
import warnings
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import joblib
from pathlib import Path

try:
    import shap
except ImportError:
    raise ImportError("Jalankan: pip install shap")

warnings.filterwarnings("ignore")

logger = logging.getLogger("ml_signal")

# ── Pastikan project root di sys.path ──
_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.helpers import load_inference_config

INFERENCE_CFG = load_inference_config()
ML_DIR   = Path(__file__).parent.parent / "models"
SEQ_LEN  = INFERENCE_CFG["inference"]["seq_len"]   # 32

SYMBOL_MAP = {
    'SOLUSDT': 0, 'ETHUSDT': 1, 'BNBUSDT': 2, 'XRPUSDT': 3, 'DOGEUSDT': 4,
    'TONUSDT': 5, 'ADAUSDT': 6, 'TRXUSDT': 7, 'SHIBUSDT': 8, 'AVAXUSDT': 9,
    'LINKUSDT': 10, 'DOTUSDT': 11, 'SUIUSDT': 12, 'POLUSDT': 13, 'NEARUSDT': 14,
    'PEPEUSDT': 15, 'TAOUSDT': 16, 'APTOSUSDT': 17, 'ARBUSDT': 18, 'WLFIUSDT': 19,
    '1000SHIBUSDT': 8, '1000PEPEUSDT': 15,
}

LABEL_MAP_INV = INFERENCE_CFG["inference"]["label_map_inv"]
LABEL_MAP = {int(k): v for k, v in LABEL_MAP_INV.items()}


# ── LSTM Architecture (harus identik dengan training v3) ──
class TradingLSTM(nn.Module):
    def __init__(self, n_features=71, hidden_size=128, num_layers=2, dropout=0.3):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size    = n_features,
            hidden_size   = hidden_size,
            num_layers    = num_layers,
            batch_first   = True,
            dropout       = dropout if num_layers > 1 else 0.0,
            bidirectional = False,
        )
        self.dropout = nn.Dropout(dropout)
        self.norm    = nn.LayerNorm(hidden_size)
        self.fc      = nn.Linear(hidden_size, 3)

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        last_out    = lstm_out[:, -1, :]
        last_out    = self.norm(last_out)
        last_out    = self.dropout(last_out)
        return self.fc(last_out)


# ── Main Engine ──
class MLSignalEngine:
    """
    Load semua model sekali saat startup, reuse untuk setiap prediksi.
    v3: n_features=71, seq_len=32, tambah calibrator.pkl.
    """

    def __init__(self):
        self._load_from_registry()

    def _load_from_registry(self):
        registry_path = ML_DIR / 'model_registry.json'
        with open(registry_path) as f:
            registry = json.load(f)

        active   = registry.get('active_model', registry.get('active'))
        cfg      = registry['models'][active]
        self.cfg = cfg

        # File paths dari inference_config (source of truth)
        mf = INFERENCE_CFG.get("model_files", {})
        lgbm_file       = mf.get("lgbm",       cfg.get('lgbm',       'lgbm_baseline.pkl'))
        scaler_file     = mf.get("scaler",      cfg.get('scaler',     'lstm_scaler.pkl'))
        meta_file       = mf.get("meta",        cfg.get('meta',       'ensemble_meta.pkl'))
        lstm_file       = mf.get("lstm",        cfg.get('lstm',       'lstm_best.pt'))
        calibrator_file = mf.get("calibrator",  cfg.get('calibrator', 'calibrator.pkl'))

        self.lgbm_model   = joblib.load(ML_DIR / lgbm_file)
        self.lstm_scaler  = joblib.load(ML_DIR / scaler_file)
        self.meta_learner = joblib.load(ML_DIR / meta_file)

        # [v3] Calibrator (isotonic)
        cal_path = ML_DIR / calibrator_file
        if cal_path.exists():
            self.calibrator = joblib.load(cal_path)
            logger.info(f"[MLSignalEngine] Calibrator loaded: {calibrator_file}")
        else:
            self.calibrator = None
            logger.warning("[MLSignalEngine] calibrator.pkl tidak ditemukan — calibration dilewati")

        # n_features dari inference_config (71 untuk v3)
        n_feats = INFERENCE_CFG.get("model_architecture", {}).get("n_features", 71)
        self.lstm_model = self._load_lstm(ML_DIR / lstm_file, n_feats)
        self.device     = torch.device('cpu')

        try:
            self.shap_explainer = shap.TreeExplainer(self.lgbm_model)
        except Exception as e:
            logger.warning(f"Gagal inisialisasi SHAP explainer: {e}")
            self.shap_explainer = None

        logger.info(
            f"[MLSignalEngine] Active: {active} | n_features={n_feats} "
            f"| seq_len={SEQ_LEN} | F1={cfg.get('f1_macro', '?')}"
        )

    def _load_lstm(self, path: Path, n_features: int) -> TradingLSTM:
        arch = INFERENCE_CFG.get("model_architecture", {})
        model = TradingLSTM(
            n_features  = n_features,
            hidden_size = arch.get("lstm_hidden",  128),
            num_layers  = arch.get("lstm_layers",  2),
            dropout     = arch.get("lstm_dropout", 0.3),
        )
        state = torch.load(path, map_location='cpu', weights_only=True)
        model.load_state_dict(state)
        model.eval()
        return model

    def reload(self):
        """Reload model dari registry tanpa restart aplikasi."""
        logger.info("Reloading model dari registry...")
        self._load_from_registry()

    def predict(
        self,
        symbol: str,
        df_m15: pd.DataFrame,      # nama parameter dipertahankan untuk kompatibilitas caller
        funding_rate: float = None,
        btc_dominance: float = None,
        fear_greed: float = None,
    ) -> dict:
        """
        Input : df_m15 — DataFrame H1 minimal 300 bar
                         (nama parameter lama dipertahankan agar caller tidak perlu diubah)
        Output: dict {
            'signal':     'LONG' / 'SHORT' / 'FLAT',
            'confidence': float (0-1),
            'size':       'FULL' / 'HALF' / 'SKIP',
            'proba':      {'SHORT': x, 'FLAT': y, 'LONG': z},
            'model_type': str,
            'symbol':     str,
        }
        """
        try:
            return self._predict_ensemble(
                symbol, df_m15, funding_rate, btc_dominance, fear_greed
            )
        except Exception as e:
            logger.error(f"[{symbol}] predict() error: {e}", exc_info=True)
            return {
                'signal': 'FLAT', 'confidence': 0.0, 'size': 'SKIP',
                'proba': {}, 'model_type': 'error', 'symbol': symbol,
                'shap_top_features': [],
            }

    def _predict_ensemble(self, symbol, df_h1, funding_rate, btc_dominance, fear_greed):
        from ml.ml_feature_calculator import calculate_features_realtime

        # 1. Feature engineering (71 fitur H1)
        features_df = calculate_features_realtime(
            symbol, df_h1, funding_rate, btc_dominance, fear_greed
        )

        # 2. Handle NaN
        features_df = features_df.ffill().fillna(0)

        # 3. Symbol encoding
        features_df['symbol'] = SYMBOL_MAP.get(symbol.upper(), 0)

        # 4. LightGBM — 1 row terakhir
        X_lgbm         = features_df.iloc[[-1]].copy()
        lgbm_feat_cols = self.lgbm_model.feature_name_
        for col in lgbm_feat_cols:
            if col not in X_lgbm.columns:
                logger.warning(f"[{symbol}] Kolom LGBM '{col}' tidak ada — diisi 0.0")
                X_lgbm[col] = 0.0
        lgbm_proba = self.lgbm_model.predict_proba(X_lgbm[lgbm_feat_cols])  # (1, 3)

        # 5. LSTM — seq_len row terakhir, kolom harus sesuai urutan canonical
        # WAJIB pakai feat_cols dari feature_cols_v2.json, bukan slice df.columns
        feat_cols_path = ML_DIR / INFERENCE_CFG.get("model_files", {}).get("features", "feature_cols_v2.json")
        try:
            with open(feat_cols_path) as _f:
                feat_cols_canonical = json.load(_f)
        except Exception as _e:
            logger.warning(f"[{symbol}] Gagal baca feat_cols_canonical ({_e}) — fallback ke columns[:n_features]")
            n_features = INFERENCE_CFG.get("model_architecture", {}).get("n_features", 85)
            feat_cols_canonical = features_df.columns.tolist()[:n_features]

        # Pastikan semua kolom canonical ada di features_df
        missing_cols = [c for c in feat_cols_canonical if c not in features_df.columns]
        if missing_cols:
            logger.warning(f"[{symbol}] {len(missing_cols)} kolom canonical tidak ada di features_df: {missing_cols[:5]} — diisi 0.0")
            for c in missing_cols:
                features_df[c] = 0.0

        X_seq = features_df[feat_cols_canonical].iloc[-SEQ_LEN:].values.astype(np.float32)

        if len(X_seq) < SEQ_LEN:
            logger.warning(
                f"[{symbol}] Bar tidak cukup untuk LSTM ({len(X_seq)}/{SEQ_LEN}) — zero-padding"
            )
            pad   = np.zeros((SEQ_LEN - len(X_seq), X_seq.shape[1]), dtype=np.float32)
            X_seq = np.vstack([pad, X_seq])

        X_seq_scaled = self.lstm_scaler.transform(X_seq)             # (seq_len, n_features)
        seq_tensor   = torch.FloatTensor(X_seq_scaled).unsqueeze(0)  # (1, seq_len, n_features)

        with torch.no_grad():
            logits     = self.lstm_model(seq_tensor)
            lstm_proba = torch.softmax(logits, dim=1).numpy()        # (1, 3)

        # 6. Ensemble meta-learner
        meta_input = np.hstack([lgbm_proba, lstm_proba])             # (1, 6)
        meta_proba = self.meta_learner.predict_proba(meta_input)     # (1, 3)

        # 7. [v3] Calibrator (isotonic)
        if self.calibrator is not None:
            try:
                cal_proba = self.calibrator.transform(meta_proba)    # (1, 3)
                # Normalisasi agar sum = 1
                row_sum   = cal_proba.sum(axis=1, keepdims=True)
                cal_proba = cal_proba / np.where(row_sum > 0, row_sum, 1)
                signal_proba = cal_proba[0]
            except Exception as e:
                logger.warning(f"[{symbol}] Calibrator.transform gagal ({e}) — pakai meta_proba")
                signal_proba = meta_proba[0]
        else:
            signal_proba = meta_proba[0]

        # 8. Signal = argmax probabilitas terkalibrasi
        signal_int = int(np.argmax(signal_proba))
        signal     = LABEL_MAP[signal_int]
        confidence = float(signal_proba[signal_int])

        # 9. Position size berdasarkan confidence
        conf_half = INFERENCE_CFG["inference"]["confidence_half_size"]   # 0.60
        conf_full = INFERENCE_CFG["inference"]["confidence_full_size"]   # 0.75

        if signal == 'FLAT' or confidence < conf_half:
            size = 'SKIP'
        elif confidence >= conf_full:
            size = 'FULL'
        else:
            size = 'HALF'

        # 8. Calculate SHAP values
        shap_top_features = []
        if getattr(self, "shap_explainer", None) is not None:
            try:
                shap_vals = self.shap_explainer.shap_values(X_lgbm[lgbm_feat_cols])
                
                # Multiclass SHAP usually returns a list of arrays (one per class)
                if isinstance(shap_vals, list):
                    target_shap = shap_vals[signal_int][0]
                elif len(shap_vals.shape) == 3:
                    target_shap = shap_vals[signal_int][0]
                else:
                    target_shap = shap_vals[0]
                
                feature_shap = []
                for feat, s_val in zip(lgbm_feat_cols, target_shap):
                    feature_shap.append({
                        "feature": feat,
                        "shap_value": round(float(s_val), 2),
                        "direction": "positive" if s_val > 0 else "negative",
                        "abs_val": abs(float(s_val))
                    })
                
                feature_shap.sort(key=lambda x: x["abs_val"], reverse=True)
                shap_top_features = [{"feature": f["feature"], "shap_value": f["shap_value"], "direction": f["direction"]} for f in feature_shap[:8]]
            except Exception as e:
                logger.error(f"[{symbol}] Error calculating SHAP: {e}")

        result = {
            'signal':     signal,
            'confidence': round(confidence, 4),
            'size':       size,
            'proba': {
                'SHORT': round(float(signal_proba[0]), 4),
                'FLAT':  round(float(signal_proba[1]), 4),
                'LONG':  round(float(signal_proba[2]), 4),
            },
            'model_type': self.cfg.get('type', 'ensemble'),
            'symbol':     symbol,
            'shap_top_features': shap_top_features,
        }
        
        logging.info(f"[SHAP DEBUG] shap_top_features count: {len(result.get('shap_top_features', []))}")
        logging.info(f"[SHAP DEBUG] sample: {result.get('shap_top_features', [])[:2]}")
        
        return result