import pandas as pd
import numpy as np
from datetime import datetime
import timezone

def safe_float(val, default=0.0):
    try:
        if pd.isna(val): return default
        return float(val)
    except:
        return default

def get_market_session(dt):
    """Simplified UTC sessions."""
    hour = dt.hour
    if 0 <= hour < 8: return "ASIAN"
    elif 8 <= hour < 13: return "LONDON"
    elif 13 <= hour < 22: return "NEW YORK"
    else: return "ASIAN (Late)"

def calculate_71point_score(df, symbol_metadata):
    """
    Executes the massive 71-point scoring logic based on the last candle of the provided DataFrame.
    DataFrame must have columns: Open_Interest, Total_Volume, Buy_Volume, Close, High, Low, EMA_21, EMA_50, EMA_200, RSI_6, ATR_14, CVD.
    """
    if len(df) < 22:
        return None

    # Ensure CVD exists or calculate it
    if 'CVD' not in df.columns:
        if 'Buy_Volume' in df.columns and 'Total_Volume' in df.columns:
            sell_vol = df['Total_Volume'] - df['Buy_Volume']
            df['CVD'] = (df['Buy_Volume'] - sell_vol).cumsum()
        else:
            df['CVD'] = 0.0

    # Variables for calculation (Last vs MA20)
    recent_20 = df.iloc[-21:-1]
    last_candle = df.iloc[-1]
    candle_21_ago = df.iloc[-21]
    
    # Core variables
    A = safe_float(last_candle.get('Open_Interest', 0))
    B = recent_20['Open_Interest'].mean() if 'Open_Interest' in df.columns else (A or 1.0)
    C_oi_norm = ((A - B) / B * 100) if B != 0 else 0
    
    D = safe_float(last_candle.get('Total_Volume', 0))
    E = recent_20['Total_Volume'].mean() if 'Total_Volume' in df.columns else (D or 1.0)
    F_vol_norm = ((D - E) / E * 100) if E != 0 else 0
    
    buy_vol = safe_float(last_candle.get('Buy_Volume', 0))
    G_taker_buy = (buy_vol / D * 100) if D != 0 else 50.0
    
    close_price = safe_float(last_candle.get('Close', 0))
    atr = safe_float(last_candle.get('ATR_14', 0))
    H_atr_pct = (atr / close_price * 100) if close_price != 0 else 0
    
    I_cvd = safe_float(last_candle.get('CVD', 0))
    J_cvd_prev = safe_float(candle_21_ago.get('CVD', 0))
    K_cvd_norm = ((I_cvd - J_cvd_prev) / abs(J_cvd_prev) * 100) if J_cvd_prev != 0 else 0
    
    close_21_ago = safe_float(candle_21_ago.get('Close', 0))
    cvd_div_bull = (I_cvd > J_cvd_prev) and (close_price < close_21_ago)
    cvd_div_bear = (I_cvd < J_cvd_prev) and (close_price > close_21_ago)
    
    ema21 = safe_float(last_candle.get('EMA_21', close_price))
    ema50 = safe_float(last_candle.get('EMA_50', close_price))
    ema200 = safe_float(last_candle.get('EMA_200', close_price))
    
    is_active_pos = symbol_metadata.get('AVG_ENTRY_PRICE') is not None
    ref_price_long = close_price if is_active_pos else safe_float(last_candle.get('Low', close_price))
    ref_price_short = safe_float(last_candle.get('High', close_price))
    
    L_ema21_dist = (ref_price_long - ema21) / ema21 * 100 if ema21 else 0
    M_ema50_dist = (ref_price_long - ema50) / ema50 * 100 if ema50 else 0
    N_ema200_dist = (ref_price_long - ema200) / ema200 * 100 if ema200 else 0
    
    Lp_ema21_dist = (ref_price_short - ema21) / ema21 * 100 if ema21 else 0
    Mp_ema50_dist = (ref_price_short - ema50) / ema50 * 100 if ema50 else 0
    Np_ema200_dist = (ref_price_short - ema200) / ema200 * 100 if ema200 else 0
    
    rsi6 = safe_float(last_candle.get('RSI_6', 50))
    
    is_altcoin = True
    symbol_name = symbol_metadata.get('Symbol', '').upper()
    if 'BTC' in symbol_name or 'ETH' in symbol_name or close_price > 3000:
        is_altcoin = False
    
    # ATR Bounds Scaling
    if is_altcoin:
        b1, b2, b3, b4, b5, b6 = 3.0, 5.0, 2.0, 7.0, 1.8, 10.0
    else:
        b1, b2, b3, b4, b5, b6 = 1.5, 2.5, 1.0, 3.5, 0.9, 5.0

    # ==========================
    # SCORING ENGINE (LONG)
    # ==========================
    scores_L = {}
    
    # 1. OI
    if C_oi_norm > 30: s1 = 3
    elif 5 <= C_oi_norm <= 30: s1 = 2
    elif -20 <= C_oi_norm < 5: s1 = 1
    else: s1 = 0
    scores_L['OI'] = (s1 * 5, 15, C_oi_norm, s1)
    
    # 2. Vol
    if F_vol_norm > 70: s2 = 3
    elif 20 <= F_vol_norm <= 70: s2 = 2
    elif -10 <= F_vol_norm < 20: s2 = 1
    else: s2 = 0
    scores_L['Vol'] = (s2 * 4, 12, F_vol_norm, s2)
    
    # 3. Taker
    if G_taker_buy < 49: s3 = 2
    elif 49 <= G_taker_buy <= 51: s3 = 1
    else: s3 = 0
    scores_L['TakerBuy'] = (s3 * 4, 8, G_taker_buy, s3)
    
    # 4. ATR
    if b1 <= H_atr_pct <= b2: s4 = 3
    elif (b3 <= H_atr_pct < b1) or (b2 < H_atr_pct <= b4): s4 = 2
    elif (b5 <= H_atr_pct < b3) or (b4 < H_atr_pct <= b6): s4 = 1
    else: s4 = 0
    scores_L['ATR'] = (s4 * 3, 9, H_atr_pct, s4)
    
    # 5. CVD
    if cvd_div_bull: s5 = 3
    elif K_cvd_norm > 1: s5 = 2
    elif 0 <= K_cvd_norm <= 1: s5 = 1
    else: s5 = 0
    scores_L['CVD'] = (s5 * 3, 9, K_cvd_norm, s5)
    
    # 6. EMA21
    if L_ema21_dist < -3: s6 = 3
    elif -3 <= L_ema21_dist < -1.5: s6 = 2
    elif -1.5 <= L_ema21_dist < -0.5: s6 = 1
    else: s6 = 0
    scores_L['EMA21'] = (s6 * 2, 6, L_ema21_dist, s6)
    
    # 7. EMA50
    if M_ema50_dist < -4: s7 = 3
    elif -4 <= M_ema50_dist < -2: s7 = 2
    elif -2 <= M_ema50_dist < 0: s7 = 1
    else: s7 = 0
    scores_L['EMA50'] = (s7 * 2, 6, M_ema50_dist, s7)
    
    # 8. EMA200
    if N_ema200_dist < -7: s8 = 3
    elif -7 <= N_ema200_dist < -3: s8 = 2
    elif -3 <= N_ema200_dist < 0: s8 = 1
    else: s8 = 0
    scores_L['EMA200'] = (s8 * 1, 3, N_ema200_dist, s8)
    
    # 9. RSI
    if rsi6 < 25: s9 = 3
    elif 25 <= rsi6 < 40: s9 = 2
    elif 40 <= rsi6 < 55: s9 = 1
    else: s9 = 0
    scores_L['RSI'] = (s9 * 1, 3, rsi6, s9)
    
    total_L = sum(v[0] for v in scores_L.values())
    
    # ==========================
    # SCORING ENGINE (SHORT)
    # ==========================
    scores_S = {}
    
    scores_S['OI'] = (s1 * 5, 15, C_oi_norm, s1)
    scores_S['Vol'] = (s2 * 4, 12, F_vol_norm, s2)
    
    if G_taker_buy > 53: s3s = 2
    elif 51 <= G_taker_buy <= 53: s3s = 1
    else: s3s = 0
    scores_S['TakerBuy'] = (s3s * 4, 8, G_taker_buy, s3s)
    
    scores_S['ATR'] = (s4 * 3, 9, H_atr_pct, s4)
    
    if cvd_div_bear: s5s = 3
    elif K_cvd_norm < -1: s5s = 2
    elif K_cvd_norm <= 0: s5s = 1
    else: s5s = 0
    scores_S['CVD'] = (s5s * 3, 9, K_cvd_norm, s5s)
    
    if Lp_ema21_dist > 5: s6s = 3
    elif 3 <= Lp_ema21_dist <= 5: s6s = 2
    elif 1.5 <= Lp_ema21_dist < 3: s6s = 1
    else: s6s = 0
    scores_S['EMA21'] = (s6s * 2, 6, Lp_ema21_dist, s6s)
    
    if Mp_ema50_dist > 6: s7s = 3
    elif 4 <= Mp_ema50_dist <= 6: s7s = 2
    elif 2 <= Mp_ema50_dist < 4: s7s = 1
    else: s7s = 0
    scores_S['EMA50'] = (s7s * 2, 6, Mp_ema50_dist, s7s)
    
    if Np_ema200_dist > 10: s8s = 3
    elif 5 <= Np_ema200_dist <= 10: s8s = 2
    elif 2 <= Np_ema200_dist < 5: s8s = 1
    else: s8s = 0
    scores_S['EMA200'] = (s8s * 1, 3, Np_ema200_dist, s8s)
    
    if rsi6 > 75: s9s = 3
    elif 60 <= rsi6 <= 75: s9s = 2
    elif 45 <= rsi6 < 60: s9s = 1
    else: s9s = 0
    scores_S['RSI'] = (s9s * 1, 3, rsi6, s9s)
    
    total_S = sum(v[0] for v in scores_S.values())
    
    def get_tier(score):
        if score >= 53: return "FULL SIZE ENTRY", "FULL"
        elif score >= 36: return "HALF SIZE ENTRY", "HALF"
        elif score >= 21: return "WAIT & MONITOR", "WAIT"
        else: return "SKIP", "SKIP"
        
    dec_L, code_L = get_tier(total_L)
    dec_S, code_S = get_tier(total_S)
    
    # Narrative
    sess = get_market_session(datetime.now()) # Approximated
    vol_desc = f"di atas MA20 (+{F_vol_norm:.1f}%)" if F_vol_norm > 0 else f"di bawah MA20 ({F_vol_norm:.1f}%)"
    cvd_desc = "bullish divergence" if cvd_div_bull else ("bearish divergence" if cvd_div_bear else f"perubahan {K_cvd_norm:.1f}%")
    
    narrative_L = {
        'kondisi': f"Sesi {sess}. Vol {vol_desc}. RSI {rsi6:.1f}. Harga {'atas' if L_ema21_dist>0 else 'bawah'} EMA21 ({L_ema21_dist:.1f}%). CVD {cvd_desc}.",
        'keputusan': f"Skor {total_L}/71 ({total_L/71*100:.1f}%). Mandat: {dec_L}.",
        'skenario': f"Monitor level SL di {close_price - (atr*1.5):.5f}."
    }
    
    narrative_S = {
        'kondisi': f"Sesi {sess}. Vol {vol_desc}. RSI {rsi6:.1f}. Distansi EMA21 (+{Lp_ema21_dist:.1f}%). CVD {cvd_desc}.",
        'keputusan': f"Skor {total_S}/71 ({total_S/71*100:.1f}%). Mandat: {dec_S}.",
        'skenario': f"Monitor level SL di {close_price + (atr*1.5):.5f}."
    }

    entry_ref = float(symbol_metadata.get('AVG_ENTRY_PRICE', close_price)) or close_price

    return {
        'long': {
            'total': total_L,
            'pct': round(total_L / 71 * 100, 2),
            'decision': dec_L,
            'code': code_L,
            'scores': scores_L,
            'narrative': narrative_L,
            'levels': {
                'sl_ketat': close_price - (atr * 1.0),
                'sl_normal': close_price - (atr * 1.5),
                'sl_lebar': close_price - (atr * 2.0),
                'tp1': entry_ref * 1.025,
                'tp2': entry_ref * 1.046,
                'tp3': entry_ref * 1.070
            }
        },
        'short': {
            'total': total_S,
            'pct': round(total_S / 71 * 100, 2),
            'decision': dec_S,
            'code': code_S,
            'scores': scores_S,
            'narrative': narrative_S,
            'levels': {
                'sl_ketat': close_price + (atr * 1.0),
                'sl_normal': close_price + (atr * 1.5),
                'sl_lebar': close_price + (atr * 2.0),
                'tp1': entry_ref * 0.975,
                'tp2': entry_ref * 0.954,
                'tp3': entry_ref * 0.930
            }
        },
        'emergency': {
            'sl_touched': is_active_pos and close_price < (close_price - (atr * 1.0)), # Placeholder logic
            'rsi_ob': rsi6 > 75
        }
    }
