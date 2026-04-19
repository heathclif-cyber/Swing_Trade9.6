"""
Protocol 9.6 — ML-Only Scoring Engine (v3.0)
Sumber sinyal: Stacking Ensemble (LightGBM + LSTM + Logistic Regression meta-learner)
Tidak ada rule-based scoring. Tidak ada core.scoring_long / core.scoring_short.
Output format dipertahankan agar protocol_96_ui.py tidak perlu diubah.
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
    calculate_trailing_sl_long, calculate_trailing_sl_short,
    detect_sl_wick_fakeout
)
from core.levels import get_atr_projections_long, get_atr_projections_short, get_entry_based_sl


def calculate_71point_score(df: pd.DataFrame, meta: dict, df_m15=None, ml_engine=None) -> dict | None:
    """Entry point — nama dipertahankan untuk kompatibilitas downstream."""
    try:
        return _score(df, meta, df_m15=df_m15, ml_engine=ml_engine)
    except Exception as e:
        logger.error(f"[Scoring] Crash: {e}", exc_info=True)
        return None


def _score(df: pd.DataFrame, meta: dict, df_m15=None, ml_engine=None) -> dict | None:
    if len(df) < 22:
        return None

    # ── CVD fallback ────────────────────────────────────────────────────────
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
    low_price   = safe_float(last.get('Low',   close_price))
    high_price  = safe_float(last.get('High',  close_price))

    # ── Meta ────────────────────────────────────────────────────────────────
    symbol         = str(meta.get('Symbol', '')).upper()
    avg_entry      = meta.get('AVG_ENTRY_PRICE')
    is_active      = avg_entry is not None and float(avg_entry) > 0
    entry_val      = float(avg_entry) if is_active else close_price
    entry_date_str = meta.get('ENTRY_DATE')

    # ── Slices ──────────────────────────────────────────────────────────────
    s20           = df.iloc[-21:-1]
    has100        = len(df) >= 101
    s100          = df.iloc[-101:-1] if has100 else s20
    candle_21_ago = df.iloc[-21] if len(df) >= 21 else df.iloc[0]

    # ── Dynamic liquidity ───────────────────────────────────────────────────
    _lw = df['Low'].iloc[-20:] if 'Low' in df.columns and len(df) >= 20 else None
    if _lw is not None and len(_lw) >= 5:
        swing_low_20 = float(_lw.min())
        dyn_buy_liq  = swing_low_20 * 0.995
        dist_to_liq  = (close_price - dyn_buy_liq) / dyn_buy_liq * 100
        has_dyn_liq  = True
    else:
        swing_low_20 = dyn_buy_liq = dist_to_liq = None
        has_dyn_liq  = False

    _hw = df['High'].iloc[-20:] if 'High' in df.columns and len(df) >= 20 else None
    if _hw is not None and len(_hw) >= 5:
        swing_high_20    = float(_hw.max())
        dyn_sell_liq     = swing_high_20 * 1.005
        dist_to_sell_liq = (dyn_sell_liq - close_price) / close_price * 100 if close_price else 0.0
        has_dyn_sell_liq = True
    else:
        swing_high_20 = dyn_sell_liq = dist_to_sell_liq = None
        has_dyn_sell_liq = False

    # ── OI / Volume ─────────────────────────────────────────────────────────
    A    = safe_float(last.get('Open_Interest', 0))
    B20  = s20['Open_Interest'].mean()  if _has_col(df, 'Open_Interest') else (A or 1.0)
    B100 = s100['Open_Interest'].mean() if _has_col(df, 'Open_Interest') and has100 else B20
    C    = ((A - B20)  / B20  * 100) if B20  else 0.0
    C2   = ((A - B100) / B100 * 100) if B100 else 0.0
    C_final = (C + C2) / 2

    A_prev    = safe_float(prev1.get('Open_Interest', A))
    oi_change = ((A - A_prev) / A_prev * 100) if A_prev else 0.0

    D    = safe_float(last.get('Total_Volume', 0))
    E20  = s20['Total_Volume'].mean()  if _has_col(df, 'Total_Volume') else (D or 1.0)
    E100 = s100['Total_Volume'].mean() if _has_col(df, 'Total_Volume') and has100 else E20
    F    = ((D - E20)  / E20  * 100) if E20  else 0.0
    F2   = ((D - E100) / E100 * 100) if E100 else 0.0
    F_final = (F + F2) / 2

    buy_vol = safe_float(last.get('Buy_Volume', 0))
    G = (buy_vol / D * 100) if D else 50.0

    atr = safe_float(last.get('ATR_14', 0))
    H   = (atr / close_price * 100) if close_price else 0.0

    I_cvd        = safe_float(last.get('CVD', 0))
    J_cvd        = safe_float(candle_21_ago.get('CVD', 0))
    K            = ((I_cvd - J_cvd) / abs(J_cvd) * 100) if J_cvd != 0 else 0.0
    close_21     = safe_float(candle_21_ago.get('Close', 0))
    cvd_div_bull = bool((I_cvd > J_cvd) and (close_price < close_21))
    cvd_div_bear = bool((I_cvd < J_cvd) and (close_price > close_21))

    stoch_k      = _last_val(last,  'StochRSI_K')
    stoch_d      = _last_val(last,  'StochRSI_D')
    stoch_k_prev = _last_val(prev1, 'StochRSI_K')
    stoch_d_prev = _last_val(prev1, 'StochRSI_D')
    has_stoch    = stoch_k is not None and stoch_d is not None
    stoch_cross_up = stoch_cross_down = False
    if has_stoch and stoch_k_prev is not None and stoch_d_prev is not None:
        stoch_cross_up   = bool((stoch_k > stoch_d)   and (stoch_k_prev <= stoch_d_prev))
        stoch_cross_down = bool((stoch_k < stoch_d)   and (stoch_k_prev >= stoch_d_prev))

    ema21  = safe_float(last.get('EMA_21',  close_price)) or close_price
    ema50  = safe_float(last.get('EMA_50',  close_price)) or close_price
    ema200 = safe_float(last.get('EMA_200', close_price)) or close_price
    O_rsi  = safe_float(last.get('RSI_6',   50))

    ref_long = close_price if is_active else low_price
    L  = (ref_long   - ema21)  / ema21  * 100 if ema21  else 0.0
    M  = (ref_long   - ema50)  / ema50  * 100 if ema50  else 0.0
    N  = (ref_long   - ema200) / ema200 * 100 if ema200 else 0.0
    Lp = (high_price - ema21)  / ema21  * 100 if ema21  else 0.0
    Mp = (high_price - ema50)  / ema50  * 100 if ema50  else 0.0
    Np = (high_price - ema200) / ema200 * 100 if ema200 else 0.0

    # ── Helpers ─────────────────────────────────────────────────────────────
    atr_data        = get_atr_multiplier(symbol, df, last)
    ATR_MULT        = atr_data['ATR_MULT']
    atr_mult_reason = atr_data['atr_mult_reason']
    sess_data       = get_market_session(last)
    macro_data      = get_macro_trend(df, ema200)
    aging_status, candles_since_entry = get_aging_status(df, is_active, entry_date_str)

    bos_val      = _last_val(last, 'BOS')
    funding_val  = _last_val(last, 'Funding_Rate')
    buy_liq_val  = _last_val(last, 'Buy_Liq')
    sell_liq_val = _last_val(last, 'Sell_Liq')

    _atr_avg_20  = None
    _atr_extreme = False
    if 'ATR_14' in df.columns and len(df) >= 20:
        _ap = (df['ATR_14'].iloc[-20:] / df['Close'].iloc[-20:] * 100).dropna()
        if len(_ap) >= 5:
            _atr_avg_20  = float(_ap.mean())
            _atr_extreme = bool(H > _atr_avg_20 * 2.0)

    # ── SL levels ───────────────────────────────────────────────────────────
    sl_atr1_L  = close_price - atr * 1.0
    sl_atr15_L = close_price - atr * 1.5
    sl_atr2_L  = close_price - atr * 2.0
    sl_atr1_S  = close_price + atr * 1.0
    sl_atr15_S = close_price + atr * 1.5
    sl_atr2_S  = close_price + atr * 2.0

    # [UPDATE] Saat posisi aktif: gunakan SL berbasis entry (entry ± 1×ATR).
    # Saat tidak ada posisi: gunakan dynamic buy/sell liq sebagai SL struktural.
    if is_active:
        _sl_entry_L, _sl_entry_label_L = get_entry_based_sl(entry_val, atr, ATR_MULT, direction='LONG')
        _sl_entry_S, _sl_entry_label_S = get_entry_based_sl(entry_val, atr, ATR_MULT, direction='SHORT')
        sl_struct_L = _sl_entry_L
        sl_struct_S = _sl_entry_S
        sl_label_L  = _sl_entry_label_L
        sl_label_S  = _sl_entry_label_S
    else:
        sl_struct_L = dyn_buy_liq  if dyn_buy_liq  is not None else sl_atr15_L
        sl_struct_S = dyn_sell_liq if dyn_sell_liq is not None else sl_atr15_S
        sl_label_L  = "Dynamic Buy Liq"  if dyn_buy_liq  is not None else "ATR×1.5"
        sl_label_S  = "Dynamic Sell Liq" if dyn_sell_liq is not None else "ATR×1.5"

    # ── TP levels ───────────────────────────────────────────────────────────
    tp_long  = get_atr_projections_long(
        entry_val, atr, ATR_MULT,
        close_price=close_price, macro_trend=macro_data['macro_trend']
    )
    tp_short = get_atr_projections_short(
        entry_val, atr, ATR_MULT, close_price=close_price
    )

    # ── ML Prediction ────────────────────────────────────────────────────────
    ml_signal = 'FLAT'
    ml_size   = 'SKIP'
    ml_conf   = 0.0
    ml_proba  = {}
    ml_error  = None

    if ml_engine is not None and df_m15 is not None:
        try:
            # Inject derivatives into df_m15 from df (4H base) to prevent NaN/zeros
            if _has_col(df, 'Open_Interest'):
                df_m15['open_interest'] = float(last['Open_Interest'])
            if _has_col(df, 'Long_Short_Ratio'):
                df_m15['long_short_ratio'] = float(last['Long_Short_Ratio'])
            
            # Extract macro
            fr = safe_float(last.get('Funding_Rate')) if _has_col(df, 'Funding_Rate') else None
            btcd = safe_float(last.get('BTC_Dominance')) if _has_col(df, 'BTC_Dominance') else None
            fg = safe_float(last.get('Fear_Greed')) if _has_col(df, 'Fear_Greed') else None

            r = ml_engine.predict(
                symbol=symbol, 
                df_m15=df_m15, 
                funding_rate=fr, 
                btc_dominance=btcd, 
                fear_greed=fg
            )
            ml_signal = r.get('signal',     'FLAT')
            ml_size   = r.get('size',       'SKIP')
            ml_conf   = r.get('confidence', 0.0)
            ml_proba  = r.get('proba',      {})
            logger.info(f"[{symbol}] ML → {ml_signal} {ml_size} conf={ml_conf:.4f}")
        except Exception as e:
            ml_error = str(e)
            logger.warning(f"[{symbol}] ML error: {e}")
    else:
        ml_error = "ml_engine tidak tersedia" if ml_engine is None else "df_m15 tidak tersedia"

    # ── Decision ────────────────────────────────────────────────────────────
    long_active  = ml_signal == 'LONG'  and ml_size != 'SKIP'
    short_active = ml_signal == 'SHORT' and ml_size != 'SKIP'
    conf_pct_L   = round(ml_conf * 100, 2) if long_active  else 0.0
    conf_pct_S   = round(ml_conf * 100, 2) if short_active else 0.0

    # ── RR helpers ──────────────────────────────────────────────────────────
    def rr_l(tp, sl):
        d = close_price - sl
        return round((tp - close_price) / d, 2) if d > 0 else 0.0

    def rr_s(tp, sl):
        d = sl - close_price
        return round((close_price - tp) / d, 2) if d > 0 else 0.0

    def dist(target):
        return round((target - close_price) / close_price * 100, 4) if close_price else 0.0

    rr_matrix_L = [
        [rr_l(tp, sl) for tp in [tp_long[0][0], tp_long[1][0], tp_long[2][0]]]
        for sl in [sl_atr1_L, sl_atr15_L, sl_atr2_L]
    ]
    rr_matrix_S = [
        [rr_s(tp, sl) for tp in [tp_short[0][0], tp_short[1][0], tp_short[2][0]]]
        for sl in [sl_atr1_S, sl_atr15_S, sl_atr2_S]
    ]

    # ── Momentum / Exit / Trailing ───────────────────────────────────────────
    momentum_hold = check_momentum_hold(K, G, O_rsi, C_final, L)

    sl_wick_result = detect_sl_wick_fakeout(
        is_active, close_price, low_price, last, sl_struct_L, K, D, E20
    )

    trailing_sl_long = calculate_trailing_sl_long(
        is_active, high_price,
        tp_long[0][0], tp_long[1][0], tp_long[0][1],
        entry_val, sl_struct_L, sl_label_L, close_price,
        atr=atr, tp3_val=tp_long[2][0],
    )
    trailing_sl_short = calculate_trailing_sl_short(
        is_active, low_price,
        tp_short[0][0], tp_short[1][0], tp_short[0][1],
        entry_val, sl_struct_S, sl_label_S, close_price,
        atr=atr, tp3_val=tp_short[2][0],
    )

    exit_signals, exit_reco, exit_hard, exit_warn = evaluate_exit_signals(
        is_active, close_price, ema21, ema50, O_rsi, G, last,
        aging_status, candles_since_entry, tp_long[0][0]
    )

    # ── Market context ───────────────────────────────────────────────────────
    ctx_out = {}
    for col in [
        'MSB','BOS','CHoCH','SFP_Sweep','FVG_Up_Top','FVG_Up_Bottom',
        'FVG_Down_Top','FVG_Down_Bottom','OB_Price','Fib_0.618','Fib_0.786',
        'POC','VAH','VAL','Buy_Liq','Sell_Liq','PDH','PDL','PWH','PWL',
        'EMA_7','EMA_7_H4','EMA_21_H4','EMA_50_H4','EMA_200_H4',
        'StochRSI_K','StochRSI_D','Funding_Rate','BTC_Price','BTC_Dominance','Altcoin_Index'
    ]:
        v = _last_val(last, col)
        if v is not None:
            ctx_out[col] = v

    pnl_pct  = round((close_price / entry_val - 1) * 100, 4) if is_active and entry_val else None
    narrative = [
        f"ML Signal: {ml_signal} | Confidence: {ml_conf:.2%} | Size: {ml_size}",
        f"Proba → LONG:{ml_proba.get('LONG',0):.2%}  FLAT:{ml_proba.get('FLAT',0):.2%}  SHORT:{ml_proba.get('SHORT',0):.2%}",
    ]

    # ── Final result ─────────────────────────────────────────────────────────
    result = {
        'long': {
            'raw':      conf_pct_L,
            'total':    conf_pct_L,
            'pct':      conf_pct_L,
            'decision': 'LONG' if long_active else 'SKIP',
            'code':     ml_size if long_active else 'SKIP',
            'gate':     {'status': 'CLEAR' if long_active else 'FLAT/SHORT', 'reason': f"ML→{ml_signal}"},
            'scores':   {'ML': (conf_pct_L, 100, 'Meta-Learner confidence', 'ML')},
            'narrative': narrative,
            'ppi':       round(ml_conf * 100, 1),
            'ml_signal':     ml_signal,
            'ml_confidence': ml_conf,
            'ml_size':       ml_size if long_active else 'SKIP',
            'ml_proba':      ml_proba,
            'time_limit':    240 if macro_data['macro_trend'] == 'UPTREND' else 180,
            'time_limit_reason': (
                'LONG UPTREND: 240 candles' if macro_data['macro_trend'] == 'UPTREND'
                else f"LONG {macro_data['macro_trend']}: 180 candles"
            ),
            'levels': {
                'sl_structure':   round(sl_struct_L, 8), 'sl_label': sl_label_L,
                'sl_ketat':       round(sl_atr1_L,   8),
                'sl_normal':      round(sl_atr15_L,  8),
                'sl_lebar':       round(sl_atr2_L,   8),
                'tp1':            round(tp_long[0][0], 8), 'tp1_label': tp_long[0][1],
                'tp2':            round(tp_long[1][0], 8), 'tp2_label': tp_long[1][1],
                'tp3':            round(tp_long[2][0], 8), 'tp3_label': tp_long[2][1],
                'rr1': rr_l(tp_long[0][0], sl_struct_L),
                'rr2': rr_l(tp_long[1][0], sl_struct_L),
                'rr3': rr_l(tp_long[2][0], sl_struct_L),
                'rr_matrix':      rr_matrix_L,
                'dist_sl':        dist(sl_struct_L),
                'dist_sl_ketat':  dist(sl_atr1_L),
                'dist_sl_normal': dist(sl_atr15_L),
                'dist_sl_lebar':  dist(sl_atr2_L),
                'dist_tp1': dist(tp_long[0][0]),
                'dist_tp2': dist(tp_long[1][0]),
                'dist_tp3': dist(tp_long[2][0]),
                'sl_capped': False, 'leverage_mode': False, 'max_sl_pct': None,
            },
            'sl_candidates': [
                (round(sl_struct_L,8), sl_label_L),
                (round(sl_atr1_L,8),  "ATR×1.0"),
                (round(sl_atr15_L,8), "ATR×1.5"),
            ],
        },
        'short': {
            'raw':      conf_pct_S,
            'total':    conf_pct_S,
            'pct':      conf_pct_S,
            'decision': 'SHORT' if short_active else 'SKIP',
            'code':     ml_size if short_active else 'SKIP',
            'gate':     {'status': 'CLEAR' if short_active else 'FLAT/LONG', 'reason': f"ML→{ml_signal}"},
            'scores':   {'ML': (conf_pct_S, 100, 'Meta-Learner confidence', 'ML')},
            'narrative': narrative,
            'ppi':       round(ml_conf * 100, 1),
            'ml_signal':     ml_signal,
            'ml_confidence': ml_conf,
            'ml_size':       ml_size if short_active else 'SKIP',
            'ml_proba':      ml_proba,
            'time_limit':    120,
            'time_limit_reason': 'SHORT: 120 candles',
            'levels': {
                'sl_structure':   round(sl_struct_S, 8), 'sl_label': sl_label_S,
                'sl_ketat':       round(sl_atr1_S,   8),
                'sl_normal':      round(sl_atr15_S,  8),
                'sl_lebar':       round(sl_atr2_S,   8),
                'tp1':            round(tp_short[0][0], 8), 'tp1_label': tp_short[0][1],
                'tp2':            round(tp_short[1][0], 8), 'tp2_label': tp_short[1][1],
                'tp3':            round(tp_short[2][0], 8), 'tp3_label': tp_short[2][1],
                'rr1': rr_s(tp_short[0][0], sl_struct_S),
                'rr2': rr_s(tp_short[1][0], sl_struct_S),
                'rr3': rr_s(tp_short[2][0], sl_struct_S),
                'rr_matrix':      rr_matrix_S,
                'dist_sl':        dist(sl_struct_S),
                'dist_sl_ketat':  dist(sl_atr1_S),
                'dist_sl_normal': dist(sl_atr15_S),
                'dist_sl_lebar':  dist(sl_atr2_S),
                'dist_tp1': dist(tp_short[0][0]),
                'dist_tp2': dist(tp_short[1][0]),
                'dist_tp3': dist(tp_short[2][0]),
                'sl_capped': False, 'leverage_mode': False, 'max_sl_pct': None,
            },
            'sl_candidates': [
                (round(sl_struct_S,8), sl_label_S),
                (round(sl_atr1_S,8),  "ATR×1.0"),
                (round(sl_atr15_S,8), "ATR×1.5"),
            ],
        },
        'emergency': {
            'sl_touched': bool(is_active and close_price < sl_struct_L),
            'rsi_ob':     bool(O_rsi > 75),
            'stale':      bool(aging_status == "STALE"),
        },
        'exit': {
            'signals':        exit_signals,
            'recommendation': exit_reco,
            'hard_count':     exit_hard,
            'warn_count':     exit_warn,
        },
        'momentum_hold': momentum_hold,
        'sl_wick':       sl_wick_result,
        'trailing_sl':  {'long': trailing_sl_long, 'short': trailing_sl_short},
        'validation': {
            'ok':     ml_error is None,
            'issues': [] if ml_error is None else [f"⚠ ML: {ml_error}"],
            'badge':  '✅ ML v3.0' if ml_error is None else f'⚠ {ml_error}',
        },
        'market_context': ctx_out,
        'variables': {
            'C_oi_short': round(C,2),   'C_oi_long': round(C2,2),  'C_final': round(C_final,2),
            'F_vol_short': round(F,2),  'F_vol_long': round(F2,2), 'F_final': round(F_final,2),
            'C_oi_norm': round(C_final,2), 'F_vol_norm': round(F_final,2),
            'G_taker_buy': round(G,2),  'H_atr_pct': round(H,2),
            'K_cvd_norm': round(K,2),
            'cvd_div_bull': cvd_div_bull, 'cvd_div_bear': cvd_div_bear,
            'I_cvd_abs': round(I_cvd,2), 'J_cvd_abs': round(J_cvd,2),
            'L_ema21': round(L,2), 'M_ema50': round(M,2), 'N_ema200': round(N,2),
            'Lp_ema21': round(Lp,2), 'Mp_ema50': round(Mp,2), 'Np_ema200': round(Np,2),
            'O_rsi': round(O_rsi,2),
            'stoch_k': round(stoch_k,2) if has_stoch else None,
            'stoch_d': round(stoch_d,2) if has_stoch else None,
            'stoch_cross_up': stoch_cross_up, 'stoch_cross_down': stoch_cross_down,
            'close_price': round(close_price,8), 'low_price': round(low_price,8), 'high_price': round(high_price,8),
            'ema21': round(ema21,8), 'ema50': round(ema50,8), 'ema200': round(ema200,8),
            'atr': round(atr,8), 'ATR_MULT': ATR_MULT, 'atr_mult_reason': atr_mult_reason,
            'atr_thresholds': {
                k: round(v,2) for k,v in {
                    'sweet_lo': atr_data['sweet_lo'],   'sweet_hi': atr_data['sweet_hi'],
                    't2_lo':    atr_data['t2_lo'],       't2_hi':    atr_data['t2_hi'],
                    't1_lo':    atr_data['t1_lo'],       't1_hi':    atr_data['t1_hi'],
                    'score_sweet_lo': atr_data['atr_score_sweet_lo'], 'score_sweet_hi': atr_data['atr_score_sweet_hi'],
                    'score_t2_lo':    atr_data['atr_score_t2_lo'],    'score_t2_hi':    atr_data['atr_score_t2_hi'],
                    'score_t1_lo':    atr_data['atr_score_t1_lo'],    'score_t1_hi':    atr_data['atr_score_t1_hi'],
                }.items()
            },
            'SESSION_MULT':         sess_data['SESSION_MULT'],
            'session':              sess_data['session_label'],
            'session_block':        sess_data['session_block'],
            'session_block_type':   sess_data['session_block_type'],
            'session_block_reason': sess_data['session_block_reason'],
            'session_override_reason': '',
            'macro_slope':        round(macro_data['macro_slope'],4) if macro_data['macro_slope'] is not None else None,
            'macro_trend':        macro_data['macro_trend'],
            'macro_trend_reason': macro_data['macro_trend_reason'],
            'atr_extreme': _atr_extreme,
            'atr_avg_20':  round(_atr_avg_20,4) if _atr_avg_20 is not None else None,
            'ml_signal':     ml_signal,
            'ml_confidence': round(ml_conf,4),
            'ml_size':       ml_size,
            'ml_proba':      ml_proba,
            'ml_error':      ml_error,
            'is_altcoin':          bool(not ('BTC' in symbol or 'ETH' in symbol)),
            'is_active_pos':       bool(is_active),
            'entry_price':         entry_val if is_active else None,
            'aging_status':        aging_status,
            'candles_since_entry': int(candles_since_entry),
            'pnl_pct':             pnl_pct,
            'bos_val': bos_val, 'funding_val': funding_val,
            'buy_liq_val': buy_liq_val, 'sell_liq_val': sell_liq_val,
            'dyn_buy_liq':      round(dyn_buy_liq,8)      if dyn_buy_liq      is not None else None,
            'swing_low_20':     round(swing_low_20,8)     if swing_low_20     is not None else None,
            'dist_to_liq':      round(dist_to_liq,4)      if dist_to_liq      is not None else None,
            'l2_zone': (
                'SKIP'       if dist_to_liq is not None and dist_to_liq < 1.0
                else 'SWEET_SPOT' if dist_to_liq is not None and dist_to_liq <= 5.0
                else 'WARNING'    if dist_to_liq is not None and dist_to_liq <= 10.0
                else 'GAGAL'      if dist_to_liq is not None
                else 'N/A'
            ),
            'dyn_sell_liq':     round(dyn_sell_liq,8)     if dyn_sell_liq     is not None else None,
            'swing_high_20':    round(swing_high_20,8)    if swing_high_20    is not None else None,
            'dist_to_sell_liq': round(dist_to_sell_liq,4) if dist_to_sell_liq is not None else None,
            's2_zone': (
                'SKIP'       if dist_to_sell_liq is not None and dist_to_sell_liq < 1.0
                else 'SWEET_SPOT' if dist_to_sell_liq is not None and dist_to_sell_liq <= 5.0
                else 'WARNING'    if dist_to_sell_liq is not None and dist_to_sell_liq <= 10.0
                else 'GAGAL'      if dist_to_sell_liq is not None
                else 'N/A'
            ),
            'time_limit_candles':       240 if macro_data['macro_trend'] == 'UPTREND' else 180,
            'time_limit_short_candles': 120,
            'time_limit_reason': (
                "UPTREND: LONG=240c SHORT=120c"
                if macro_data['macro_trend'] == 'UPTREND'
                else f"{macro_data['macro_trend']}: LONG=180c SHORT=120c"
            ),
        },
    }

    return json_safe(result)