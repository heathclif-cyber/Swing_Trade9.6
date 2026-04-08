import pandas as pd
import numpy as np

def safe_float(val, default=0.0):
    try:
        if pd.isna(val):
            return default
        return float(val)
    except Exception:
        return default

def json_safe(obj):
    """Recursively convert numpy/pandas types to JSON-serializable Python natives."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        converted = [json_safe(i) for i in obj]
        return tuple(converted) if isinstance(obj, tuple) else converted
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj

def _has_col(df, col):
    return col in df.columns and df[col].notna().any()

def _last_val(last, col, default=None):
    v = last.get(col) if col in last.index else None
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return default
    try:
        return float(v)
    except Exception:
        return default

def get_atr_multiplier(symbol, df, last):
    is_major = ('BTC' in symbol or 'ETH' in symbol)
    ai_val = _last_val(last, 'Altcoin_Index')

    if is_major:
        ATR_MULT = 1.0
        atr_mult_reason = "BTC/ETH"
    elif ai_val is not None:
        if ai_val > 5000:
            ATR_MULT = 1.5
        elif ai_val >= 2000:
            ATR_MULT = 2.0
        else:
            ATR_MULT = 2.5
        atr_mult_reason = f"Altcoin_Index={ai_val:.0f}"
    else:
        ATR_MULT = 2.0
        atr_mult_reason = "default (no AI data)"

    _has_atr = 'ATR_14' in df.columns
    _has_close = 'Close' in df.columns
    if _has_atr and _has_close and len(df) >= 101:
        _atr_hist = df['ATR_14'].iloc[-101:-1]
        _close_hist = df['Close'].iloc[-101:-1]
        _atr_pct_hist = (_atr_hist / _close_hist * 100).dropna()
        
        if len(_atr_pct_hist) >= 10:
            atr_score_sweet_lo = float(np.percentile(_atr_pct_hist, 25))
            atr_score_sweet_hi = float(np.percentile(_atr_pct_hist, 75))
            atr_score_t2_lo = atr_score_sweet_lo * 0.70
            atr_score_t2_hi = atr_score_sweet_hi * 1.40
            atr_score_t1_lo = atr_score_sweet_lo * 0.55
            atr_score_t1_hi = atr_score_sweet_hi * 1.80
        else:
            atr_score_sweet_lo, atr_score_sweet_hi = 1.5 * ATR_MULT, 2.5 * ATR_MULT
            atr_score_t2_lo, atr_score_t2_hi     = 1.0 * ATR_MULT, 3.5 * ATR_MULT
            atr_score_t1_lo, atr_score_t1_hi     = 0.9 * ATR_MULT, 5.0 * ATR_MULT
    else:
        atr_score_sweet_lo, atr_score_sweet_hi = 1.5 * ATR_MULT, 2.5 * ATR_MULT
        atr_score_t2_lo, atr_score_t2_hi     = 1.0 * ATR_MULT, 3.5 * ATR_MULT
        atr_score_t1_lo, atr_score_t1_hi     = 0.9 * ATR_MULT, 5.0 * ATR_MULT

    sweet_lo, sweet_hi = 3.0 * ATR_MULT, 5.0 * ATR_MULT
    t2_lo, t2_hi = 2.0 * ATR_MULT, 7.0 * ATR_MULT
    t1_lo, t1_hi = 1.8 * ATR_MULT, 10.0 * ATR_MULT

    return {
        'ATR_MULT': ATR_MULT,
        'atr_mult_reason': atr_mult_reason,
        'atr_score_sweet_lo': atr_score_sweet_lo,
        'atr_score_sweet_hi': atr_score_sweet_hi,
        'atr_score_t2_lo': atr_score_t2_lo,
        'atr_score_t2_hi': atr_score_t2_hi,
        'atr_score_t1_lo': atr_score_t1_lo,
        'atr_score_t1_hi': atr_score_t1_hi,
        'sweet_lo': sweet_lo,
        'sweet_hi': sweet_hi,
        't2_lo': t2_lo,
        't2_hi': t2_hi,
        't1_lo': t1_lo,
        't1_hi': t1_hi
    }

def get_market_session(last):
    sess_raw = str(last.get('Market_Session', '')) if 'Market_Session' in last.index else ''
    sess_upper = sess_raw.strip().upper()
    session_label = sess_raw.strip() if sess_raw.strip() else 'UNKNOWN'

    _is_london_ny = ('LONDON' in sess_upper and 'NEW YORK' in sess_upper)
    _is_london_only = (sess_upper == 'LONDON')
    _is_ny_only = (sess_upper == 'NEW YORK')
    _is_asian = (sess_upper == 'ASIAN')
    _is_off_market = (sess_upper == 'OFF-MARKET' or sess_upper == '')

    if _is_london_ny:
        SESSION_MULT = 1.05
        session_block = False
        session_block_reason = ""
        session_block_type = "NONE"
    elif _is_london_only:
        SESSION_MULT = 1.00
        session_block = False
        session_block_reason = ""
        session_block_type = "NONE"
    elif _is_ny_only:
        SESSION_MULT = 1.00
        session_block = False
        session_block_reason = "NEW YORK (tanpa London): WAJIB score ≥ 40"
        session_block_type = "CONDITIONAL_NY"
    elif _is_asian:
        SESSION_MULT = 0.85
        session_block = True
        session_block_reason = "❌ Sesi ASIAN. Entry diblokir total (Hard Block v13)."
        session_block_type = "HARD_BLOCK_ASIAN"
    elif _is_off_market:
        SESSION_MULT = 0.90
        session_block = True
        session_block_reason = f"❌ Sesi OFF-MARKET. Entry diblokir total."
        session_block_type = "HARD_BLOCK"
    else:
        SESSION_MULT = 0.90
        session_block = False
        session_block_reason = f"Sesi Lainnya ({session_label}): WAJIB score ≥ 45"
        session_block_type = "CONDITIONAL_OTHER"

    return {
        'session_label': session_label,
        'SESSION_MULT': SESSION_MULT,
        'session_block': session_block,
        'session_block_reason': session_block_reason,
        'session_block_type': session_block_type
    }

def get_macro_trend(df, ema200_base):
    ema200_macro_col = 'EMA_200_H4'
    macro_slope = None
    macro_trend = 'UNKNOWN'
    macro_trend_reason = 'Data EMA_200_H4 tidak tersedia'

    if ema200_macro_col in df.columns and df[ema200_macro_col].notna().sum() >= 31:
        _ema200_series = df[ema200_macro_col].dropna()
        if len(_ema200_series) >= 30:
            _ema200_now  = float(_ema200_series.iloc[-1])
            _ema200_ago  = float(_ema200_series.iloc[-30])
            macro_slope  = (_ema200_now - _ema200_ago) / _ema200_ago * 100 if _ema200_ago else 0.0
            if macro_slope > 0.5:
                macro_trend = 'UPTREND'
                macro_trend_reason = f"Slope EMA200_H4 = +{macro_slope:.2f}% (> +0.5%)"
            elif macro_slope >= -0.5:
                macro_trend = 'SIDEWAYS'
                macro_trend_reason = f"Slope EMA200_H4 = {macro_slope:.2f}% (-0.5% s/d +0.5%)"
            else:
                macro_trend = 'DOWNTREND'
                macro_trend_reason = f"Slope EMA200_H4 = {macro_slope:.2f}% (< -0.5%)"
    else:
        if 'EMA_200' in df.columns and len(df) >= 30:
            _ema200_now = float(df['EMA_200'].iloc[-1]) if not pd.isna(df['EMA_200'].iloc[-1]) else ema200_base
            _ema200_ago = float(df['EMA_200'].iloc[-30]) if not pd.isna(df['EMA_200'].iloc[-30]) else ema200_base
            macro_slope = (_ema200_now - _ema200_ago) / _ema200_ago * 100 if _ema200_ago else 0.0
            if macro_slope > 0.5:
                macro_trend = 'UPTREND'
                macro_trend_reason = f"Slope EMA200_base = +{macro_slope:.2f}% [fallback]"
            elif macro_slope >= -0.5:
                macro_trend = 'SIDEWAYS'
                macro_trend_reason = f"Slope EMA200_base = {macro_slope:.2f}% [fallback]"
            else:
                macro_trend = 'DOWNTREND'
                macro_trend_reason = f"Slope EMA200_base = {macro_slope:.2f}% [fallback]"

    return {
        'macro_slope': macro_slope,
        'macro_trend': macro_trend,
        'macro_trend_reason': macro_trend_reason
    }

def get_aging_status(df, is_active, entry_date_str):
    aging_status = "N/A"
    candles_since_entry = 0
    if is_active and entry_date_str:
        try:
            ts_col = df['Timestamp'] if 'Timestamp' in df.columns else None
            if ts_col is not None:
                entry_dt = pd.to_datetime(entry_date_str)
                diffs = (pd.to_datetime(ts_col) - entry_dt).abs()
                entry_idx = diffs.idxmin()
                candles_since_entry = len(df) - 1 - entry_idx
                if candles_since_entry <= 42:
                    aging_status = "NORMAL"
                elif candles_since_entry <= 84:
                    aging_status = "AGING"
                else:
                    aging_status = "STALE"
        except Exception:
            aging_status = "NORMAL"
    elif is_active:
        aging_status = "NORMAL"
    return aging_status, candles_since_entry
