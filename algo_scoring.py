"""
Protocol 9.6 — 78-Point Quantitative Swing Trading Scoring Engine
Full spec-compliant implementation (Bagian 0–10).

Refactored to orchestrator pattern.
"""
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

from core.helpers import (
    safe_float, json_safe, _has_col, _last_val,
    get_atr_multiplier, get_market_session, get_macro_trend, get_aging_status
)
from core.momentum import (
    check_momentum_hold, evaluate_exit_signals,
    calculate_trailing_sl_long, calculate_trailing_sl_short, detect_sl_wick_fakeout
)
from core.scoring_long import calculate_long_score
from core.scoring_short import calculate_short_score

def calculate_71point_score(df: pd.DataFrame, meta: dict) -> dict | None:
    """78-point scoring engine (was 71-point, upgraded v2.0)."""
    try:
        return _calculate_score_internal(df, meta)
    except Exception as e:
        logger.error(f"[Scoring] Crash saat kalkulasi skor: {e}", exc_info=True)
        return None

def _calculate_score_internal(df: pd.DataFrame, meta: dict) -> dict | None:
    if len(df) < 22:
        return None

    # ── Ensure CVD ─────────────────────────────────────────────
    if 'CVD' not in df.columns:
        df = df.copy()
        if 'Buy_Volume' in df.columns and 'Total_Volume' in df.columns:
            sell = df['Total_Volume'] - df['Buy_Volume']
            df['CVD'] = (df['Buy_Volume'] - sell).cumsum()
        else:
            df['CVD'] = 0.0

    last  = df.iloc[-1]
    prev1 = df.iloc[-2] if len(df) >= 2 else last
    prev2 = df.iloc[-3] if len(df) >= 3 else prev1
    close_price = safe_float(last.get('Close', 0))
    low_price   = safe_float(last.get('Low', close_price))
    high_price  = safe_float(last.get('High', close_price))

    # ── Metadata ───────────────────────────────────────────────
    symbol = str(meta.get('Symbol', '')).upper()
    avg_entry = meta.get('AVG_ENTRY_PRICE')
    is_active = avg_entry is not None and float(avg_entry) > 0
    entry_val = float(avg_entry) if is_active else close_price
    entry_date_str = meta.get('ENTRY_DATE')

    # ── Slices ─────────────────────────────────────────────────
    s20 = df.iloc[-21:-1]
    has100 = len(df) >= 101
    s100 = df.iloc[-101:-1] if has100 else s20
    candle_21_ago = df.iloc[-21] if len(df) >= 21 else df.iloc[0]

    # [P1] DYNAMIC BUY_LIQ
    _low_window = df['Low'].iloc[-20:] if 'Low' in df.columns and len(df) >= 20 else None
    if _low_window is not None and len(_low_window) >= 5:
        swing_low_20 = float(_low_window.min())
        dyn_buy_liq  = swing_low_20 * 0.995
        dist_to_liq  = (close_price - dyn_buy_liq) / dyn_buy_liq * 100
        has_dyn_liq  = True
    else:
        swing_low_20 = None
        dyn_buy_liq  = None
        dist_to_liq  = None
        has_dyn_liq  = False

    # [P1] DYNAMIC SELL_LIQ
    _high_window = df['High'].iloc[-20:] if 'High' in df.columns and len(df) >= 20 else None
    if _high_window is not None and len(_high_window) >= 5:
        swing_high_20 = float(_high_window.max())
        dyn_sell_liq  = swing_high_20 * 1.005
        dist_to_sell_liq = (dyn_sell_liq - close_price) / close_price * 100 if close_price else 0.0
        has_dyn_sell_liq = True
    else:
        swing_high_20    = None
        dyn_sell_liq     = None
        dist_to_sell_liq = None
        has_dyn_sell_liq = False

    # ── OI & Vol ─────────────────────────────────────────────
    A = safe_float(last.get('Open_Interest', 0))
    B20 = s20['Open_Interest'].mean() if _has_col(df, 'Open_Interest') else (A or 1.0)
    B100 = s100['Open_Interest'].mean() if _has_col(df, 'Open_Interest') and has100 else B20
    C = ((A - B20) / B20 * 100) if B20 else 0.0
    C2 = ((A - B100) / B100 * 100) if B100 else 0.0
    C_final = (C + C2) / 2

    # oi_change: perubahan OI 1 candle (untuk Liquidation Hunter)
    A_prev = safe_float(prev1.get('Open_Interest', A))
    oi_change = ((A - A_prev) / A_prev * 100) if A_prev else 0.0

    D = safe_float(last.get('Total_Volume', 0))
    E20 = s20['Total_Volume'].mean() if _has_col(df, 'Total_Volume') else (D or 1.0)
    E100 = s100['Total_Volume'].mean() if _has_col(df, 'Total_Volume') and has100 else E20
    F = ((D - E20) / E20 * 100) if E20 else 0.0
    F2 = ((D - E100) / E100 * 100) if E100 else 0.0
    F_final = (F + F2) / 2

    buy_vol = safe_float(last.get('Buy_Volume', 0))
    G = (buy_vol / D * 100) if D else 50.0

    atr = safe_float(last.get('ATR_14', 0))
    H = (atr / close_price * 100) if close_price else 0.0

    I_cvd = safe_float(last.get('CVD', 0))
    J_cvd = safe_float(candle_21_ago.get('CVD', 0))
    K = ((I_cvd - J_cvd) / abs(J_cvd) * 100) if J_cvd != 0 else 0.0
    close_21 = safe_float(candle_21_ago.get('Close', 0))
    cvd_div_bull = bool((I_cvd > J_cvd) and (close_price < close_21))
    cvd_div_bear = bool((I_cvd < J_cvd) and (close_price > close_21))

    stoch_k      = _last_val(last,  'StochRSI_K')
    stoch_d      = _last_val(last,  'StochRSI_D')
    stoch_k_prev = _last_val(prev1, 'StochRSI_K')
    stoch_d_prev = _last_val(prev1, 'StochRSI_D')
    has_stoch = stoch_k is not None and stoch_d is not None
    stoch_cross_up   = False
    stoch_cross_down = False
    if has_stoch and stoch_k_prev is not None and stoch_d_prev is not None:
        stoch_cross_up   = bool((stoch_k > stoch_d)   and (stoch_k_prev <= stoch_d_prev))
        stoch_cross_down = bool((stoch_k < stoch_d)   and (stoch_k_prev >= stoch_d_prev))

    ema21 = safe_float(last.get('EMA_21', close_price)) or close_price
    ema50 = safe_float(last.get('EMA_50', close_price)) or close_price
    ema200 = safe_float(last.get('EMA_200', close_price)) or close_price
    O_rsi   = safe_float(last.get('RSI_6',  50))
    O_rsi_1 = safe_float(prev1.get('RSI_6', 50))
    O_rsi_2 = safe_float(prev2.get('RSI_6', 50))

    ref_long = close_price if is_active else low_price
    L = (ref_long - ema21) / ema21 * 100 if ema21 else 0.0
    M = (ref_long - ema50) / ema50 * 100 if ema50 else 0.0
    N = (ref_long - ema200) / ema200 * 100 if ema200 else 0.0
    Lp = (high_price - ema21) / ema21 * 100 if ema21 else 0.0
    Mp = (high_price - ema50) / ema50 * 100 if ema50 else 0.0
    Np = (high_price - ema200) / ema200 * 100 if ema200 else 0.0
    # dist_ema21_close: jarak Close ke EMA21 (selalu berbasis close, bukan low/high)
    dist_ema21_close = (close_price - ema21) / ema21 * 100 if ema21 else 0.0

    # ATR helpers
    atr_data = get_atr_multiplier(symbol, df, last)
    ATR_MULT = atr_data['ATR_MULT']
    atr_mult_reason = atr_data['atr_mult_reason']
    atr_score_sweet_lo = atr_data['atr_score_sweet_lo']
    atr_score_sweet_hi = atr_data['atr_score_sweet_hi']

    # Session helpers
    sess_data = get_market_session(last)

    # Macro trend
    macro_data = get_macro_trend(df, ema200)

    # Aging
    aging_status, candles_since_entry = get_aging_status(df, is_active, entry_date_str)

    bos_val     = _last_val(last, 'BOS')
    funding_val = _last_val(last, 'Funding_Rate')
    buy_liq_val = _last_val(last, 'Buy_Liq')
    sell_liq_val= _last_val(last, 'Sell_Liq')
    has_bos     = bos_val is not None
    has_funding = funding_val is not None
    has_buy_liq = buy_liq_val is not None and buy_liq_val > 0
    has_sell_liq= sell_liq_val is not None and sell_liq_val > 0

    # Adaptive Thresholds
    _atr_avg_20 = None
    _atr_extreme = False
    if 'ATR_14' in df.columns and len(df) >= 20:
        _atr_series = df['ATR_14'].iloc[-20:]
        _atr_close  = df['Close'].iloc[-20:]
        _atr_pct_series = (_atr_series / _atr_close * 100).dropna()
        if len(_atr_pct_series) >= 5:
            _atr_avg_20 = float(_atr_pct_series.mean())
            _atr_extreme = bool(H > _atr_avg_20 * 2.0)

    if macro_data['macro_trend'] == 'UPTREND':
        _thr_full, _thr_half, _thr_wait = 53, 36, 22  # [FIX v2.0] ×1.098: was 48,33,20
        _thr_full_S, _thr_half_S = 58, 48             # [FIX 4] SHORT lebih ketat saat UPTREND
        threshold_regime = "BULL"
    else:
        _thr_full, _thr_half, _thr_wait = 64, 46, 31  # [FIX v2.0] ×1.098: was 58,42,28
        _thr_full_S, _thr_half_S = _thr_full, _thr_half  # [FIX 4] sama saat BEAR/SIDEWAYS
        threshold_regime = "BEAR/SIDEWAYS"

    if _atr_extreme:
        _thr_full += 5; _thr_half += 5; _thr_wait += 5
        _thr_full_S += 5; _thr_half_S += 5             # [FIX 4] juga naikkan SHORT threshold saat volatil
        threshold_regime += " + VOLATILITAS EKSTREM (+5)"

    # Fallback SL limits
    sl_atr1_L = close_price - atr * 1.0; sl_atr15_L = close_price - atr * 1.5; sl_atr2_L = close_price - atr * 2.0
    sl_atr1_S = close_price + atr * 1.0; sl_atr15_S = close_price + atr * 1.5; sl_atr2_S = close_price + atr * 2.0

    # Context dictionary creation
    ctx = {
        'last': last, 'close_price': close_price, 'low_price': low_price, 'high_price': high_price,
        'entry_val': entry_val, 'is_active': is_active, 'aging_status': aging_status,
        'SESSION_MULT': sess_data['SESSION_MULT'], 'session_label': sess_data['session_label'],
        'session_block': sess_data['session_block'], 'session_block_type': sess_data['session_block_type'],
        'session_block_reason': sess_data['session_block_reason'],
        'macro_slope': macro_data['macro_slope'], 'macro_trend': macro_data['macro_trend'],
        'C_final': C_final, 'F_final': F_final, 'F': F, 'F2': F2, 'G': G, 'H': H, 'K': K, 'I_cvd': I_cvd, 'J_cvd': J_cvd,
        'cvd_div_bull': cvd_div_bull, 'cvd_div_bear': cvd_div_bear,
        'L': L, 'M': M, 'N': N, 'Lp': Lp, 'Mp': Mp, 'Np': Np, 'O_rsi': O_rsi,
        'has_bos': has_bos, 'bos_val': bos_val, 'has_funding': has_funding, 'funding_val': funding_val,
        'has_buy_liq': has_buy_liq, 'buy_liq_val': buy_liq_val, 'dyn_buy_liq': dyn_buy_liq,
        'has_dyn_liq': has_dyn_liq, 'dist_to_liq': dist_to_liq, 'swing_low_20': swing_low_20,
        'has_sell_liq': has_sell_liq, 'sell_liq_val': sell_liq_val, 'dyn_sell_liq': dyn_sell_liq,
        'has_dyn_sell_liq': has_dyn_sell_liq, 'dist_to_sell_liq': dist_to_sell_liq, 'swing_high_20': swing_high_20,
        'has_stoch': has_stoch, 'stoch_k': stoch_k, 'stoch_d': stoch_d,
        'stoch_k_prev': stoch_k_prev, 'stoch_d_prev': stoch_d_prev,
        'stoch_cross_up': stoch_cross_up, 'stoch_cross_down': stoch_cross_down,
        'atr': atr, 'ATR_MULT': ATR_MULT, 'atr_mult_reason': atr_mult_reason,
        'sl_atr1_L': sl_atr1_L, 'sl_atr15_L': sl_atr15_L, 'sl_atr2_L': sl_atr2_L,
        'sl_atr1_S': sl_atr1_S, 'sl_atr15_S': sl_atr15_S, 'sl_atr2_S': sl_atr2_S,
        '_thr_full': _thr_full, '_thr_half': _thr_half, '_thr_wait': _thr_wait,
        '_thr_full_S': _thr_full_S, '_thr_half_S': _thr_half_S,  # [FIX 4] threshold SHORT terpisah
        'ema21': ema21, 'ema50': ema50, 'ema200': ema200,
        # ── Variabel baru untuk 3 improvisasi ──────────────────
        'oi_change': oi_change, 'O_rsi_1': O_rsi_1, 'O_rsi_2': O_rsi_2,
        'dist_ema21_close': dist_ema21_close,
    }
    ctx.update(atr_data) # Include all ATR sweet spots

    # ── Orchestrator calls ─────────────────────────────────────
    res_L = calculate_long_score(df, ctx)
    res_S = calculate_short_score(df, ctx)

    def dist_pct(target):
        return round((target - close_price) / close_price * 100, 4) if close_price else 0.0

    def rr_long_atr(tp, sl):
        d = close_price - sl
        return round((tp - close_price) / d, 2) if d > 0 else 0.0
    def rr_short_atr(tp, sl):
        d = sl - close_price
        return round((close_price - tp) / d, 2) if d > 0 else 0.0

    rr_matrix_L = [[rr_long_atr(tp, sl) for tp in [res_L['tp1'][0], res_L['tp2'][0], res_L['tp3'][0]]] for sl in [sl_atr1_L, sl_atr15_L, sl_atr2_L]]
    rr_matrix_S = [[rr_short_atr(tp, sl) for tp in [res_S['tp1'][0], res_S['tp2'][0], res_S['tp3'][0]]] for sl in [sl_atr1_S, sl_atr15_S, sl_atr2_S]]

    # Momentum hold
    momentum_hold = check_momentum_hold(K, G, O_rsi, C_final, L)

    # Fakeout check
    sl_wick_result = detect_sl_wick_fakeout(is_active, close_price, low_price, last, res_L['sl_struct'], K, D, E20)

    # Trailing SL
    trailing_sl_long = calculate_trailing_sl_long(is_active, high_price, res_L['tp1'][0], res_L['tp2'][0], res_L['tp1'][1], entry_val, res_L['sl_struct'], res_L['sl_label'], close_price)
    trailing_sl_short = calculate_trailing_sl_short(is_active, low_price, res_S['tp1'][0], res_S['tp2'][0], res_S['tp1'][1], entry_val, res_S['sl_struct'], res_S['sl_label'], close_price)

    # Exit Signals
    exit_signals, exit_reco, exit_hard, exit_warn = evaluate_exit_signals(is_active, close_price, ema21, ema50, O_rsi, G, last, aging_status, candles_since_entry, res_L['tp1'][0])

    # Validation
    validations = []
    if not (0 <= res_L['raw_score'] <= 78): validations.append("⚠️ V1: Skor long anomali")   # [FIX v2.0] was 71
    if not (0 <= res_S['raw_score'] <= 78): validations.append("⚠️ V2: Skor short anomali")  # [FIX v2.0] was 71
    for k, (pts, mx, _, _) in res_L['scores'].items():
        if pts > mx: validations.append(f"⚠️ V3: Overflow {k} (L)")
    for k, (pts, mx, _, _) in res_S['scores'].items():
        if pts > mx: validations.append(f"⚠️ V3: Overflow {k} (S)")
    if res_L['scores']['TakerBuy'][0] > 6 or res_S['scores']['TakerBuy'][0] > 6:  # [FIX v2.0] was 8
        validations.append("⚠️ V4: TakerBuy overflow (maks 6)")
    if res_L['sl_struct'] >= close_price: validations.append("⚠️ V5: SL long di atas harga")
    if res_S['sl_struct'] <= close_price: validations.append("⚠️ V6: SL short di bawah harga")
    if not (res_L['tp1'][0] <= res_L['tp2'][0] <= res_L['tp3'][0]): validations.append("⚠️ V7: Urutan TP long terbalik")
    if not (res_S['tp1'][0] >= res_S['tp2'][0] >= res_S['tp3'][0]): validations.append("⚠️ V8: Urutan TP short terbalik")
    if any(t <= close_price for t in [res_L['tp1'][0], res_L['tp2'][0], res_L['tp3'][0]]): validations.append("⚠️ V9: Ada TP long di bawah harga")
    if any(t >= close_price for t in [res_S['tp1'][0], res_S['tp2'][0], res_S['tp3'][0]]): validations.append("⚠️ V10: Ada TP short di atas harga")
    if res_L['rr1'] <= 0: validations.append("⚠️ V11: RR long negatif")
    if res_S['rr1'] <= 0: validations.append("⚠️ V11: RR short negatif")
    if round(res_L['adj_score'], 1) != round(res_L['raw_score'] * sess_data['SESSION_MULT'], 1): validations.append("⚠️ V12: Session mult tidak diterapkan (L)")
    if (atr_score_sweet_lo == 1.5 * ATR_MULT or atr_score_sweet_lo == 2.0 * ATR_MULT or atr_score_sweet_lo == 1.0):
        validations.append(f"⚠️ V13: ATR pakai threshold flat — cek ATR_MULT scoring (dipakai: {atr_score_sweet_lo:.2f}%–{atr_score_sweet_hi:.2f}%)")
    if abs(I_cvd) > 0 and abs(K) == abs(I_cvd): validations.append(f"⚠️ V14: CVD scoring salah formula (CVD_norm K={K:.2f}%)")
    if M < -4.0 and res_L['scores']['EMA50'][3] != 3: validations.append("⚠️ V15: EMA50 scoring salah tier")
    if has_buy_liq and has_dyn_liq and buy_liq_val == df['Buy_Liq'].iloc[-101:-1].mean():
        validations.append("⚠️ V16: Buy_Liq CSV kemungkinan statis — pakai dynamic version")
    valid_ok = len(validations) == 0

    # Market Context
    ctx_out = {}
    ctx_cols = ['MSB','BOS','CHoCH','SFP_Sweep','FVG_Up_Top','FVG_Up_Bottom',
                'FVG_Down_Top','FVG_Down_Bottom','OB_Price','Fib_0.618','Fib_0.786',
                'POC','VAH','VAL','Buy_Liq','Sell_Liq','PDH','PDL','PWH','PWL',
                'EMA_7','EMA_7_H4','EMA_21_H4','EMA_50_H4','EMA_200_H4',
                'StochRSI_K','StochRSI_D','Funding_Rate','BTC_Price','BTC_Dominance','Altcoin_Index']
    for col in ctx_cols:
        v = _last_val(last, col)
        if v is not None:
            ctx_out[col] = v

    pnl_pct = round((close_price / entry_val - 1) * 100, 4) if is_active and entry_val else None

    result = {
        'long': {
            'raw': res_L['raw_score'], 'total': res_L['adj_score'],
            'pct': round(res_L['adj_score'] / 78 * 100, 2),  # [FIX v2.0] was /71
            'decision': res_L['dec'], 'code': res_L['code'],
            'gate': res_L['gate'],
            'scores': res_L['scores'], 'narrative': res_L['narrative'],
            'ppi': res_L['ppi'],
            'levels': {
                'sl_structure': round(res_L['sl_struct'], 8), 'sl_label': res_L['sl_label'],
                'sl_ketat': round(sl_atr1_L, 8), 'sl_normal': round(sl_atr15_L, 8), 'sl_lebar': round(sl_atr2_L, 8),
                'tp1': round(res_L['tp1'][0], 8), 'tp1_label': res_L['tp1'][1],
                'tp2': round(res_L['tp2'][0], 8), 'tp2_label': res_L['tp2'][1],
                'tp3': round(res_L['tp3'][0], 8), 'tp3_label': res_L['tp3'][1],
                'rr1': res_L['rr1'], 'rr2': res_L['rr2'], 'rr3': res_L['rr3'],
                'rr_matrix': rr_matrix_L,
                'dist_sl': dist_pct(res_L['sl_struct']),
                'dist_sl_ketat': dist_pct(sl_atr1_L), 'dist_sl_normal': dist_pct(sl_atr15_L), 'dist_sl_lebar': dist_pct(sl_atr2_L),
                'dist_tp1': dist_pct(res_L['tp1'][0]), 'dist_tp2': dist_pct(res_L['tp2'][0]), 'dist_tp3': dist_pct(res_L['tp3'][0]),
            },
            'sl_candidates': [(round(p, 8), l) for p, l in res_L['sl_cands']],
        },
        'short': {
            'raw': res_S['raw_score'], 'total': res_S['adj_score'],
            'pct': round(res_S['adj_score'] / 78 * 100, 2),  # [FIX v2.0] was /71
            'decision': res_S['dec'], 'code': res_S['code'],
            'gate': res_S['gate'],
            'scores': res_S['scores'], 'narrative': res_S['narrative'],
            'ppi': res_S['ppi'],
            'levels': {
                'sl_structure': round(res_S['sl_struct'], 8), 'sl_label': res_S['sl_label'],
                'sl_ketat': round(sl_atr1_S, 8), 'sl_normal': round(sl_atr15_S, 8), 'sl_lebar': round(sl_atr2_S, 8),
                'tp1': round(res_S['tp1'][0], 8), 'tp1_label': res_S['tp1'][1],
                'tp2': round(res_S['tp2'][0], 8), 'tp2_label': res_S['tp2'][1],
                'tp3': round(res_S['tp3'][0], 8), 'tp3_label': res_S['tp3'][1],
                'rr1': res_S['rr1'], 'rr2': res_S['rr2'], 'rr3': res_S['rr3'],
                'rr_matrix': rr_matrix_S,
                'dist_sl': dist_pct(res_S['sl_struct']),
                'dist_sl_ketat': dist_pct(sl_atr1_S), 'dist_sl_normal': dist_pct(sl_atr15_S), 'dist_sl_lebar': dist_pct(sl_atr2_S),
                'dist_tp1': dist_pct(res_S['tp1'][0]), 'dist_tp2': dist_pct(res_S['tp2'][0]), 'dist_tp3': dist_pct(res_S['tp3'][0]),
            },
            'sl_candidates': [(round(p, 8), l) for p, l in res_S['sl_cands']],
        },
        'emergency': {
            'sl_touched': bool(is_active and (close_price < res_L['sl_struct'])),
            'rsi_ob': bool(O_rsi > 75),
            'stale': bool(aging_status == "STALE"),
        },
        'exit': {
            'signals': exit_signals, 'recommendation': exit_reco,
            'hard_count': exit_hard, 'warn_count': exit_warn,
        },
        'momentum_hold': momentum_hold,
        'sl_wick':       sl_wick_result,
        'trailing_sl': {
            'long':  trailing_sl_long,
            'short': trailing_sl_short,
        },
        'validation': {
            'ok': bool(valid_ok),
            'issues': validations,
            'badge': '✅ Kalkulasi v12 valid' if valid_ok else f'⚠️ {len(validations)} isu validasi'
        },
        'market_context': ctx_out,
        'variables': {
            'C_oi_short': round(C, 2), 'C_oi_long': round(C2, 2), 'C_final': round(C_final, 2),
            'F_vol_short': round(F, 2), 'F_vol_long': round(F2, 2), 'F_final': round(F_final, 2),
            'C_oi_norm': round(C_final, 2), 'F_vol_norm': round(F_final, 2),
            'G_taker_buy': round(G, 2), 'H_atr_pct': round(H, 2),
            'K_cvd_norm': round(K, 2), 'cvd_div_bull': cvd_div_bull, 'cvd_div_bear': cvd_div_bear,
            'I_cvd_abs': round(I_cvd, 2), 'J_cvd_abs': round(J_cvd, 2),
            'L_ema21': round(L, 2), 'M_ema50': round(M, 2), 'N_ema200': round(N, 2),
            'Lp_ema21': round(Lp, 2), 'Mp_ema50': round(Mp, 2), 'Np_ema200': round(Np, 2),
            'O_rsi': round(O_rsi, 2),
            'stoch_k': round(stoch_k, 2) if has_stoch else None,
            'stoch_d': round(stoch_d, 2) if has_stoch else None,
            'stoch_cross_up': stoch_cross_up,
            'stoch_cross_down': stoch_cross_down,
            'close_price': round(close_price, 8), 'low_price': round(low_price, 8), 'high_price': round(high_price, 8),
            'ema21': round(ema21, 8), 'ema50': round(ema50, 8), 'ema200': round(ema200, 8),
            'atr': round(atr, 8), 'ATR_MULT': ATR_MULT, 'atr_mult_reason': atr_mult_reason,
            'atr_thresholds': {
                'sweet_lo': round(atr_data['sweet_lo'], 2), 'sweet_hi': round(atr_data['sweet_hi'], 2),
                't2_lo': round(atr_data['t2_lo'], 2), 't2_hi': round(atr_data['t2_hi'], 2),
                't1_lo': round(atr_data['t1_lo'], 2), 't1_hi': round(atr_data['t1_hi'], 2),
                'score_sweet_lo': round(atr_score_sweet_lo, 2), 'score_sweet_hi': round(atr_score_sweet_hi, 2),
                'score_t2_lo': round(atr_data['atr_score_t2_lo'], 2), 'score_t2_hi': round(atr_data['atr_score_t2_hi'], 2),
                'score_t1_lo': round(atr_data['atr_score_t1_lo'], 2), 'score_t1_hi': round(atr_data['atr_score_t1_hi'], 2),
            },
            'SESSION_MULT': sess_data['SESSION_MULT'], 'session': sess_data['session_label'],
            'session_block': sess_data['session_block'],
            'session_block_type': sess_data['session_block_type'],
            'session_block_reason': sess_data['session_block_reason'],
            'session_override_reason': "", # Set dynamically below if we want to mimic the old exact but skipping for brevity
            'is_altcoin': bool(not ('BTC' in symbol or 'ETH' in symbol)),
            'is_active_pos': bool(is_active), 'entry_price': entry_val if is_active else None,
            'aging_status': aging_status, 'candles_since_entry': int(candles_since_entry),
            'pnl_pct': pnl_pct,
            'bos_val': bos_val, 'funding_val': funding_val,
            'buy_liq_val': buy_liq_val, 'sell_liq_val': sell_liq_val,
            'dyn_buy_liq': round(dyn_buy_liq, 8) if dyn_buy_liq is not None else None,
            'swing_low_20': round(swing_low_20, 8) if swing_low_20 is not None else None,
            'dist_to_liq': round(dist_to_liq, 4) if dist_to_liq is not None else None,
            'l2_zone': (
                'SKIP' if (dist_to_liq is not None and dist_to_liq < 1.0)
                else 'SWEET_SPOT' if (dist_to_liq is not None and dist_to_liq <= 5.0)
                else 'WARNING' if (dist_to_liq is not None and dist_to_liq <= 10.0)
                else 'GAGAL' if (dist_to_liq is not None and dist_to_liq > 10.0)
                else 'N/A'
            ),
            'dyn_sell_liq': round(dyn_sell_liq, 8) if dyn_sell_liq is not None else None,
            'swing_high_20': round(swing_high_20, 8) if swing_high_20 is not None else None,
            'dist_to_sell_liq': round(dist_to_sell_liq, 4) if dist_to_sell_liq is not None else None,
            's2_zone': (
                'SKIP' if (dist_to_sell_liq is not None and dist_to_sell_liq < 1.0)
                else 'SWEET_SPOT' if (dist_to_sell_liq is not None and dist_to_sell_liq <= 5.0)
                else 'WARNING' if (dist_to_sell_liq is not None and dist_to_sell_liq <= 10.0)
                else 'GAGAL' if (dist_to_sell_liq is not None and dist_to_sell_liq > 10.0)
                else 'N/A'
            ),
            'stoch_gatekeeper_ok': res_L['stoch_gatekeeper_ok'],
            'stoch_gatekeeper_skip': res_L['stoch_gatekeeper_skip'],
            'stoch_gatekeeper_reason': res_L['stoch_gatekeeper_reason'],
            'stoch_bonus_points': res_L['stoch_bonus_points'],
            'stoch_gate_override': res_L['stoch_gate_override'],
            # [PERBAIKAN] Tambahkan data override spesifik untuk SHORT
            'stoch_gatekeeper_ok_s': res_S.get('stoch_gatekeeper_ok_s', True),
            'stoch_gatekeeper_skip_s': res_S.get('stoch_gatekeeper_skip_s', False),
            'stoch_gate_override_s': res_S.get('stoch_gate_override_s', ''),
            'macro_slope': round(macro_data['macro_slope'], 4) if macro_data['macro_slope'] is not None else None,
            'macro_trend': macro_data['macro_trend'],
            'macro_trend_reason': macro_data['macro_trend_reason'],
            'threshold_regime': threshold_regime,
            'thr_full': _thr_full, 'thr_half': _thr_half, 'thr_wait': _thr_wait,
            'thr_full_S': _thr_full_S, 'thr_half_S': _thr_half_S,  # [FIX 4] SHORT thresholds
            'atr_extreme': _atr_extreme,
            'atr_avg_20': round(_atr_avg_20, 4) if _atr_avg_20 is not None else None,
        },
    }
    
    # Optional logic to perfectly match session override string which is just cosmetic:
    session_override_reason = ""
    if sess_data['session_block']: session_override_reason = sess_data['session_block_reason']
    elif sess_data['session_block_type'] == 'CONDITIONAL_NY':
        if res_L['adj_score'] < 40: session_override_reason += f"LONG skip: Sesi NY skor {res_L['adj_score']:.1f} < 40. "
        if res_S['adj_score'] < 40: session_override_reason += f"SHORT skip: Sesi NY skor {res_S['adj_score']:.1f} < 40. "
    elif sess_data['session_block_type'] == 'CONDITIONAL_OTHER':
        if res_L['adj_score'] < 45: session_override_reason += f"LONG skip: Sesi Lainnya skor {res_L['adj_score']:.1f} < 45. "
        if res_S['adj_score'] < 45: session_override_reason += f"SHORT skip: Sesi Lainnya skor {res_S['adj_score']:.1f} < 45. "
    result['variables']['session_override_reason'] = session_override_reason

    return json_safe(result)

