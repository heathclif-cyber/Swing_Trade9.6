import json
import logging
import warnings
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import joblib
from pathlib import Path

warnings.filterwarnings("ignore")

logger = logging.getLogger("ml_signal")

# ── Constants ──
ML_DIR   = Path(__file__).parent / "models"
SEQ_LEN  = 32

SYMBOL_MAP = {
    'SOLUSDT': 0, 'ETHUSDT': 1, 'BNBUSDT': 2, 'XRPUSDT': 3, 'DOGEUSDT': 4,
    'TONUSDT': 5, 'ADAUSDT': 6, 'TRXUSDT': 7, 'SHIBUSDT': 8, 'AVAXUSDT': 9,
    'LINKUSDT': 10, 'DOTUSDT': 11, 'SUIUSDT': 12, 'POLUSDT': 13, 'NEARUSDT': 14,
    'PEPEUSDT': 15, 'TAOUSDT': 16, 'APTOSUSDT': 17, 'ARBUSDT': 18, 'WLFIUSDT': 19,
}

LABEL_MAP = {0: 'SHORT', 1: 'FLAT', 2: 'LONG'}


# ── LSTM Architecture (hardcoded, harus identik dengan training) ──
class TradingLSTM(nn.Module):
    def __init__(self, n_features=58, hidden_size=128, num_layers=2, dropout=0.3):
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
    Ganti model aktif = edit model_registry.json, panggil reload().
    """

    def __init__(self):
        self._load_from_registry()

    def _load_from_registry(self):
        registry_path = ML_DIR / 'model_registry.json'
        with open(registry_path) as f:
            registry = json.load(f)

        active     = registry['active_model']
        cfg        = registry['models'][active]
        self.cfg   = cfg
        model_type = cfg['type']

        self.lgbm_model   = joblib.load(ML_DIR / cfg['lgbm'])
        self.lstm_scaler  = joblib.load(ML_DIR / cfg['scaler'])
        self.meta_learner = joblib.load(ML_DIR / cfg['meta'])
        self.lstm_model   = self._load_lstm(ML_DIR / cfg['lstm'], cfg.get('n_features', 58))
        self.device       = torch.device('cpu')

        print(f"[MLSignalEngine] Active: {active} | type={model_type} | F1={cfg.get('f1_macro', '?')}")

    def _load_lstm(self, path: Path, n_features: int) -> TradingLSTM:
        model = TradingLSTM(n_features=n_features)
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
        df_m15: pd.DataFrame,
        funding_rate: float = None,
        btc_dominance: float = None,
        fear_greed: float = None,
    ) -> dict:
        """
        Input : df_m15 — DataFrame M15 minimal 300 bar
                         kolom wajib: open/high/low/close/volume/taker_buy_volume
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
            }

    def _predict_ensemble(self, symbol, df_m15, funding_rate, btc_dominance, fear_greed):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from ml_feature_calculator import calculate_features_realtime

        # 1. Feature engineering
        features_df = calculate_features_realtime(
            symbol, df_m15, funding_rate, btc_dominance, fear_greed
        )

        # 2. Handle NaN
        features_df = features_df.ffill().fillna(0)

        # 3. Tambah symbol encoding
        features_df['symbol'] = SYMBOL_MAP.get(symbol, 0)

        # 4. LightGBM — 1 row terakhir
        X_lgbm = features_df.iloc[[-1]].copy()
        if 'OB_price' not in X_lgbm.columns:
            X_lgbm['OB_price'] = 0.0
        lgbm_feat_cols = self.lgbm_model.feature_name_
        # Tambah kolom yang missing dengan 0
        for col in lgbm_feat_cols:
            if col not in X_lgbm.columns:
                X_lgbm[col] = 0.0
        lgbm_proba = self.lgbm_model.predict_proba(
            X_lgbm[lgbm_feat_cols]
        )  # (1, 3)

        # 5. LSTM — seq_len row terakhir
        seq_len    = self.cfg.get('seq_len', SEQ_LEN)
        n_features = self.cfg.get('n_features', 58)

        # Ambil kolom fitur tanpa OB_price (58 kolom)
        lstm_cols = [c for c in features_df.columns if c != 'OB_price'][:n_features]
        X_seq     = features_df[lstm_cols].iloc[-seq_len:].values.astype(np.float32)

        if len(X_seq) < seq_len:
            logger.warning(f"[{symbol}] Tidak cukup bar untuk LSTM ({len(X_seq)}/{seq_len})")
            # Pad dengan row pertama
            pad = np.repeat(X_seq[:1], seq_len - len(X_seq), axis=0)
            X_seq = np.vstack([pad, X_seq])

        X_seq_scaled = self.lstm_scaler.transform(X_seq)           # (seq_len, n_features)
        seq_tensor   = torch.FloatTensor(X_seq_scaled).unsqueeze(0) # (1, seq_len, n_features)

        with torch.no_grad():
            logits     = self.lstm_model(seq_tensor)
            lstm_proba = torch.softmax(logits, dim=1).numpy()       # (1, 3)

        # 6. Ensemble meta-learner
        meta_input   = np.hstack([lgbm_proba, lstm_proba])          # (1, 6)
        signal_int   = self.meta_learner.predict(meta_input)[0]
        signal_proba = self.meta_learner.predict_proba(meta_input)[0]  # (3,)

        signal     = LABEL_MAP[signal_int]
        confidence = float(signal_proba[signal_int])

        # 7. Position size berdasarkan confidence
        if signal == 'FLAT' or confidence < 0.60:
            size = 'SKIP'
        elif confidence >= 0.75:
            size = 'FULL'
        else:
            size = 'HALF'

        return {
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
        }