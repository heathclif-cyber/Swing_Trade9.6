"""
Protocol 9.6 — 71-Point Quantitative Swing Trading Scoring Engine
Spec-compliant implementation for BOTH live Binance data and CSV data.

Features:
  - 9 weighted features, max 71 points
  - TakerBuy capped at score 2 (max 8 pts)
  - ATR bounds ×2 for altcoins
  - Full SL/TP/R:R matrix
  - Exit signal monitoring for active positions
  - Rich narrative generation with actual computed values
"""
import pandas as pd
import numpy as np
from datetime import datetime


def safe_float(val, default=0.0):
    try:
        if pd.isna(val):
            return default
        return float(val)
    except Exception:
        return default


def get_market_session(dt=None):
    """Determine UTC market session from datetime."""
    if dt is None:
        dt = datetime.utcnow()
    hour = dt.hour
    if 0 <= hour < 8:
        return "ASIAN"
    elif 8 <= hour < 13:
        return "LONDON"
    elif 13 <= hour < 22:
        return "NEW YORK"
    else:
        return "ASIAN (Late)"


def calculate_71point_score(df: pd.DataFrame, symbol_metadata: dict) -> dict | None:
    """
    Full 71-point quantitative scoring engine.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame with at minimum:
        Open_Interest, Total_Volume, Buy_Volume, Close, High, Low,
        EMA_21, EMA_50, EMA_200, RSI_6, ATR_14, CVD
        Must have ≥ 22 rows (last candle + 20 candles for MA20 + 1 overlap).
    symbol_metadata : dict
        Keys used:
          'Symbol'          – ticker string (e.g. 'SUIUSDT')
          'AVG_ENTRY_PRICE' – float or None (None = new entry mode)
          'TOTAL_QTY'       – float or None
          'TOTAL_COST'      – float or None

    Returns
    -------
    dict  with keys 'long', 'short', 'emergency', 'exit', 'variables'
    None  if insufficient data
    """
    if len(df) < 22:
        return None

    # ── Ensure CVD exists ──────────────────────────────────────────────────────
    if 'CVD' not in df.columns:
        if 'Buy_Volume' in df.columns and 'Total_Volume' in df.columns:
            sell_vol = df['Total_Volume'] - df['Buy_Volume']
            df = df.copy()
            df['CVD'] = (df['Buy_Volume'] - sell_vol).cumsum()
        else:
            df = df.copy()
            df['CVD'] = 0.0

    # ── Slice windows ──────────────────────────────────────────────────────────
    recent_20   = df.iloc[-21:-1]   # 20 candles BEFORE the last candle
    last_candle = df.iloc[-1]
    candle_21_ago = df.iloc[-21]

    # ── Symbol classification ──────────────────────────────────────────────────
    symbol_name = str(symbol_metadata.get('Symbol', '')).upper()
    close_price = safe_float(last_candle.get('Close', 0))
    is_altcoin  = not ('BTC' in symbol_name or 'ETH' in symbol_name or close_price > 3000)

    # ── ATR bounds (altcoin ×2 of BTC reference) ──────────────────────────────
    if is_altcoin:
        b1, b2, b3, b4, b5, b6 = 3.0, 5.0, 2.0, 7.0, 1.8, 10.0
    else:
        b1, b2, b3, b4, b5, b6 = 1.5, 2.5, 1.0, 3.5, 0.9, 5.0

    # ═══════════════════════════════════════════════════════════════════════════
    # BAGIAN 1 — VARIABLE CALCULATION
    # ═══════════════════════════════════════════════════════════════════════════

    # OI
    A = safe_float(last_candle.get('Open_Interest', 0))
    if 'Open_Interest' in df.columns and recent_20['Open_Interest'].notna().any():
        B = recent_20['Open_Interest'].mean()
    else:
        B = A if A else 1.0
    C_oi_norm = ((A - B) / B * 100) if B != 0 else 0.0

    # Volume
    D = safe_float(last_candle.get('Total_Volume', 0))
    if 'Total_Volume' in df.columns and recent_20['Total_Volume'].notna().any():
        E = recent_20['Total_Volume'].mean()
    else:
        E = D if D else 1.0
    F_vol_norm = ((D - E) / E * 100) if E != 0 else 0.0

    # TakerBuy %
    buy_vol     = safe_float(last_candle.get('Buy_Volume', 0))
    G_taker_buy = (buy_vol / D * 100) if D != 0 else 50.0

    # ATR %
    atr       = safe_float(last_candle.get('ATR_14', 0))
    H_atr_pct = (atr / close_price * 100) if close_price != 0 else 0.0

    # CVD delta
    I_cvd      = safe_float(last_candle.get('CVD', 0))
    J_cvd_prev = safe_float(candle_21_ago.get('CVD', 0))
    K_cvd_norm = ((I_cvd - J_cvd_prev) / abs(J_cvd_prev) * 100) if J_cvd_prev != 0 else 0.0

    close_21_ago = safe_float(candle_21_ago.get('Close', 0))
    cvd_div_bull = (I_cvd > J_cvd_prev) and (close_price < close_21_ago)
    cvd_div_bear = (I_cvd < J_cvd_prev) and (close_price > close_21_ago)

    # EMAs
    ema21  = safe_float(last_candle.get('EMA_21',  close_price))
    ema50  = safe_float(last_candle.get('EMA_50',  close_price))
    ema200 = safe_float(last_candle.get('EMA_200', close_price))
    if ema21  == 0: ema21  = close_price
    if ema50  == 0: ema50  = close_price
    if ema200 == 0: ema200 = close_price

    # RSI
    O_rsi = safe_float(last_candle.get('RSI_6', 50))

    # Reference prices
    avg_entry = symbol_metadata.get('AVG_ENTRY_PRICE')
    is_active_pos = avg_entry is not None and float(avg_entry) > 0

    # LONG uses Low if new entry, Close if active position
    low_price  = safe_float(last_candle.get('Low',  close_price))
    high_price = safe_float(last_candle.get('High', close_price))
    ref_long   = close_price if is_active_pos else low_price

    # EMA distances — LONG
    L_ema21  = (ref_long - ema21)  / ema21  * 100 if ema21  else 0.0
    M_ema50  = (ref_long - ema50)  / ema50  * 100 if ema50  else 0.0
    N_ema200 = (ref_long - ema200) / ema200 * 100 if ema200 else 0.0

    # EMA distances — SHORT (always High)
    Lp_ema21  = (high_price - ema21)  / ema21  * 100 if ema21  else 0.0
    Mp_ema50  = (high_price - ema50)  / ema50  * 100 if ema50  else 0.0
    Np_ema200 = (high_price - ema200) / ema200 * 100 if ema200 else 0.0

    # ═══════════════════════════════════════════════════════════════════════════
    # BAGIAN 2 — SCORING LONG
    # ═══════════════════════════════════════════════════════════════════════════

    # ① OI_norm (×5, max 15)
    if   C_oi_norm > 30:               s1 = 3
    elif 5  <= C_oi_norm <= 30:         s1 = 2
    elif -20 <= C_oi_norm <  5:         s1 = 1
    else:                               s1 = 0

    # ② Vol_norm (×4, max 12)
    if   F_vol_norm > 70:              s2 = 3
    elif 20 <= F_vol_norm <= 70:        s2 = 2
    elif -10 <= F_vol_norm < 20:        s2 = 1
    else:                               s2 = 0

    # ③ TakerBuy LONG (×4, max 8) — skor maks = 2
    if   G_taker_buy < 49:             s3 = 2
    elif 49 <= G_taker_buy <= 51:       s3 = 1
    else:                               s3 = 0   # >51% = bearish pressure, bad for long

    # ④ ATR% (×3, max 9)
    if   b1 <= H_atr_pct <= b2:        s4 = 3
    elif (b3 <= H_atr_pct < b1) or (b2 < H_atr_pct <= b4): s4 = 2
    elif (b5 <= H_atr_pct < b3) or (b4 < H_atr_pct <= b6): s4 = 1
    else:                               s4 = 0

    # ⑤ CVD LONG (×3, max 9)
    if   cvd_div_bull:                 s5 = 3
    elif K_cvd_norm > 1:               s5 = 2
    elif 0 <= K_cvd_norm <= 1:         s5 = 1
    else:                               s5 = 0

    # ⑥ vs EMA21 LONG (×2, max 6)
    if   L_ema21 < -3:                 s6 = 3
    elif -3  <= L_ema21 < -1.5:        s6 = 2
    elif -1.5 <= L_ema21 < -0.5:       s6 = 1
    else:                               s6 = 0

    # ⑦ vs EMA50 LONG (×2, max 6)
    if   M_ema50 < -4:                 s7 = 3
    elif -4  <= M_ema50 < -2:          s7 = 2
    elif -2  <= M_ema50 <  0:          s7 = 1
    else:                               s7 = 0

    # ⑧ vs EMA200 LONG (×1, max 3)
    if   N_ema200 < -7:                s8 = 3
    elif -7  <= N_ema200 < -3:         s8 = 2
    elif -3  <= N_ema200 <  0:         s8 = 1
    else:                               s8 = 0

    # ⑨ RSI_6 LONG (×1, max 3)
    if   O_rsi < 25:                   s9 = 3
    elif 25 <= O_rsi < 40:             s9 = 2
    elif 40 <= O_rsi < 55:             s9 = 1
    else:                               s9 = 0

    # scores_L: key → (actual_points, max_points, raw_value, star_level)
    scores_L = {
        'OI':        (s1 * 5,  15, C_oi_norm,    s1),
        'Vol':       (s2 * 4,  12, F_vol_norm,   s2),
        'TakerBuy':  (s3 * 4,   8, G_taker_buy,  s3),
        'ATR':       (s4 * 3,   9, H_atr_pct,    s4),
        'CVD':       (s5 * 3,   9, K_cvd_norm,   s5),
        'EMA21':     (s6 * 2,   6, L_ema21,      s6),
        'EMA50':     (s7 * 2,   6, M_ema50,      s7),
        'EMA200':    (s8 * 1,   3, N_ema200,     s8),
        'RSI':       (s9 * 1,   3, O_rsi,        s9),
    }
    total_L = sum(v[0] for v in scores_L.values())

    # ═══════════════════════════════════════════════════════════════════════════
    # BAGIAN 3 — SCORING SHORT
    # ═══════════════════════════════════════════════════════════════════════════

    # ① OI — same as long
    # ② Vol — same as long
    # ④ ATR — same as long

    # ③ TakerBuy SHORT (×4, max 8) — skor maks = 2
    if   G_taker_buy > 53:             s3s = 2
    elif 51 <= G_taker_buy <= 53:       s3s = 1
    else:                               s3s = 0

    # ⑤ CVD SHORT (×3, max 9)
    if   cvd_div_bear:                 s5s = 3
    elif K_cvd_norm < -1:              s5s = 2
    elif K_cvd_norm <= 0:              s5s = 1
    else:                               s5s = 0

    # ⑥ vs EMA21 SHORT (×2, max 6)
    if   Lp_ema21 > 5:                 s6s = 3
    elif 3  <= Lp_ema21 <= 5:          s6s = 2
    elif 1.5 <= Lp_ema21 < 3:          s6s = 1
    else:                               s6s = 0

    # ⑦ vs EMA50 SHORT (×2, max 6)
    if   Mp_ema50 > 6:                 s7s = 3
    elif 4  <= Mp_ema50 <= 6:          s7s = 2
    elif 2  <= Mp_ema50 < 4:           s7s = 1
    else:                               s7s = 0

    # ⑧ vs EMA200 SHORT (×1, max 3)
    if   Np_ema200 > 10:               s8s = 3
    elif 5  <= Np_ema200 <= 10:        s8s = 2
    elif 2  <= Np_ema200 < 5:          s8s = 1
    else:                               s8s = 0

    # ⑨ RSI_6 SHORT (×1, max 3)
    if   O_rsi > 75:                   s9s = 3
    elif 60 <= O_rsi <= 75:            s9s = 2
    elif 45 <= O_rsi < 60:             s9s = 1
    else:                               s9s = 0

    scores_S = {
        'OI':        (s1  * 5, 15, C_oi_norm,    s1),
        'Vol':       (s2  * 4, 12, F_vol_norm,   s2),
        'TakerBuy':  (s3s * 4,  8, G_taker_buy,  s3s),
        'ATR':       (s4  * 3,  9, H_atr_pct,    s4),
        'CVD':       (s5s * 3,  9, K_cvd_norm,   s5s),
        'EMA21':     (s6s * 2,  6, Lp_ema21,     s6s),
        'EMA50':     (s7s * 2,  6, Mp_ema50,     s7s),
        'EMA200':    (s8s * 1,  3, Np_ema200,    s8s),
        'RSI':       (s9s * 1,  3, O_rsi,        s9s),
    }
    total_S = sum(v[0] for v in scores_S.values())

    # ═══════════════════════════════════════════════════════════════════════════
    # BAGIAN 4 — KEPUTUSAN
    # ═══════════════════════════════════════════════════════════════════════════

    def get_tier(score):
        if   score >= 53: return "FULL SIZE ENTRY",  "FULL",  1.0
        elif score >= 36: return "HALF SIZE ENTRY",  "HALF",  1.5
        elif score >= 21: return "WAIT & MONITOR",   "WAIT",  2.0
        else:             return "SKIP",             "SKIP",  0.0

    dec_L, code_L, sl_mul_L = get_tier(total_L)
    dec_S, code_S, sl_mul_S = get_tier(total_S)

    # ═══════════════════════════════════════════════════════════════════════════
    # BAGIAN 5 — SL & TP LEVELS
    # ═══════════════════════════════════════════════════════════════════════════

    entry_val = float(avg_entry) if is_active_pos and avg_entry else close_price

    # LONG SL (from Close)
    sl_ketat_L  = close_price - (atr * 1.0)
    sl_normal_L = close_price - (atr * 1.5)
    sl_lebar_L  = close_price - (atr * 2.0)

    # LONG TP (from entry)
    tp1_L = entry_val * 1.025
    tp2_L = entry_val * 1.046
    tp3_L = entry_val * 1.070

    # SHORT SL (from Close)
    sl_ketat_S  = close_price + (atr * 1.0)
    sl_normal_S = close_price + (atr * 1.5)
    sl_lebar_S  = close_price + (atr * 2.0)

    # SHORT TP (from entry)
    tp1_S = entry_val * 0.975
    tp2_S = entry_val * 0.954
    tp3_S = entry_val * 0.930

    # R:R helpers
    def rr_long(tp, sl):
        denom = close_price - sl
        return round((tp - close_price) / denom, 2) if denom > 0 else 0.0

    def rr_short(tp, sl):
        denom = sl - close_price
        return round((close_price - tp) / denom, 2) if denom > 0 else 0.0

    # Full R:R matrix — every SL vs every TP
    def build_rr_matrix_long():
        sls = [sl_ketat_L, sl_normal_L, sl_lebar_L]
        tps = [tp1_L, tp2_L, tp3_L]
        return [[rr_long(tp, sl) for tp in tps] for sl in sls]

    def build_rr_matrix_short():
        sls = [sl_ketat_S, sl_normal_S, sl_lebar_S]
        tps = [tp1_S, tp2_S, tp3_S]
        return [[rr_short(tp, sl) for tp in tps] for sl in sls]

    rr_matrix_L = build_rr_matrix_long()
    rr_matrix_S = build_rr_matrix_short()

    # Distance % from close to each SL/TP
    def dist_pct(target):
        return round((target - close_price) / close_price * 100, 4) if close_price else 0.0

    # ═══════════════════════════════════════════════════════════════════════════
    # BAGIAN 6 — EXIT SIGNALS (only meaningful if active position)
    # ═══════════════════════════════════════════════════════════════════════════

    exit_signals = []
    if is_active_pos:
        dist_ema21_pct = ((close_price / ema21) - 1) * 100 if ema21 else 0
        dist_ema50_pct = ((close_price / ema50) - 1) * 100 if ema50 else 0

        if O_rsi > 75:
            exit_signals.append(("❌", "RSI_6 overbought",     O_rsi,           "> 75",    "EXIT"))
        if dist_ema21_pct > 3.6:
            exit_signals.append(("❌", "vs EMA21 extended",    dist_ema21_pct,  "> +3.6%", "EXIT"))
        if dist_ema50_pct > 4.6:
            exit_signals.append(("❌", "vs EMA50 extended",    dist_ema50_pct,  "> +4.6%", "EXIT"))
        if G_taker_buy > 53:
            exit_signals.append(("⚠️", "TakerBuy FOMO",        G_taker_buy,     "> 53%",   "WATCH"))
        bos_val = safe_float(last_candle.get('BOS', 0))
        if bos_val == -1:
            exit_signals.append(("⚠️", "BOS bearish",          bos_val,         "== -1",   "WATCH"))
        fr_val = safe_float(last_candle.get('Funding_Rate', 0))
        if fr_val > 0.001:
            exit_signals.append(("⚠️", "Funding rate tinggi",  fr_val,          "> 0.001", "WATCH"))
        # ATR compression check (vs initial position ATR)
        # Note: ATR_entry not tracked server-side yet; skip for now

    exit_hard = sum(1 for e in exit_signals if e[0] == "❌")
    exit_warn = sum(1 for e in exit_signals if e[0] == "⚠️")
    if exit_hard >= 1:
        exit_reco = "PARTIAL EXIT atau FULL EXIT"
    elif exit_warn >= 1:
        exit_reco = "HOLD dengan monitoring ketat"
    else:
        exit_reco = "HOLD"

    # Position-specific distances
    position_metrics = {}
    if is_active_pos:
        position_metrics = {
            'pnl_pct':     round((close_price / entry_val - 1) * 100, 4),
            'dist_sl_pct': dist_pct(sl_ketat_L),
            'dist_tp1_pct': dist_pct(tp1_L),
            'dist_tp2_pct': dist_pct(tp2_L),
            'dist_tp3_pct': dist_pct(tp3_L),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # BAGIAN 8 — NARRATIVE
    # ═══════════════════════════════════════════════════════════════════════════

    sess     = get_market_session(datetime.utcnow())
    vol_desc = f"di atas MA20 (+{F_vol_norm:.1f}%)" if F_vol_norm > 0 else f"di bawah MA20 ({F_vol_norm:.1f}%)"
    if cvd_div_bull:
        cvd_desc = "bullish divergence (CVD naik, harga turun)"
    elif cvd_div_bear:
        cvd_desc = "bearish divergence (CVD turun, harga naik)"
    else:
        cvd_desc = f"perubahan {K_cvd_norm:+.1f}% dari 20 candle lalu"

    pos_label = f"Close (${close_price:.4f})" if is_active_pos else f"Low (${low_price:.4f})"
    session_note = " ⚠️ Signal muncul di sesi ASIAN — konfirmasi di sesi London/NY disarankan." if 'ASIAN' in sess else ""

    # Find top 2 contributing features for narrative
    def top_features(scores_dict):
        sorted_feats = sorted(scores_dict.items(), key=lambda x: x[1][0], reverse=True)
        return [f"{k} ({v[0]}/{v[1]})" for k, v in sorted_feats[:2]]

    top_L = top_features(scores_L)
    top_S = top_features(scores_S)

    narrative_L = {
        'kondisi': (
            f"Sesi {sess}. Volume {vol_desc}. "
            f"{pos_label} berada di {'atas' if L_ema21 > 0 else 'bawah'} EMA21 berjarak {L_ema21:+.2f}%, "
            f"EMA50 {M_ema50:+.2f}%, EMA200 {N_ema200:+.2f}%. "
            f"CVD: {cvd_desc}. RSI_6 = {O_rsi:.1f}. ATR = {H_atr_pct:.2f}%.{session_note}"
        ),
        'keputusan': (
            f"Skor setup mencapai {total_L}/71 ({total_L/71*100:.1f}%) → {dec_L}. "
            f"Fitur terkuat: {', '.join(top_L)}. "
            f"OI {C_oi_norm:+.1f}%, Vol {F_vol_norm:+.1f}%, TakerBuy {G_taker_buy:.1f}%. "
            f"R:R vs SL Normal: TP1 {rr_matrix_L[1][0]:.1f}x, TP2 {rr_matrix_L[1][1]:.1f}x, TP3 {rr_matrix_L[1][2]:.1f}x."
        ),
        'skenario': (
            f"SL ketat ${sl_ketat_L:.4f} ({dist_pct(sl_ketat_L):+.2f}%) | "
            f"SL normal ${sl_normal_L:.4f} ({dist_pct(sl_normal_L):+.2f}%). "
            f"TP1 ${tp1_L:.4f} ({dist_pct(tp1_L):+.2f}%), "
            f"TP2 ${tp2_L:.4f} ({dist_pct(tp2_L):+.2f}%), "
            f"TP3 ${tp3_L:.4f} ({dist_pct(tp3_L):+.2f}%). "
            f"Monitor: RSI < 30 untuk konfirmasi oversold, EMA21 sebagai resistance."
        ),
    }

    narrative_S = {
        'kondisi': (
            f"Sesi {sess}. Volume {vol_desc}. "
            f"High (${high_price:.4f}) berjarak EMA21 {Lp_ema21:+.2f}%, "
            f"EMA50 {Mp_ema50:+.2f}%, EMA200 {Np_ema200:+.2f}%. "
            f"CVD: {cvd_desc}. RSI_6 = {O_rsi:.1f}. ATR = {H_atr_pct:.2f}%.{session_note}"
        ),
        'keputusan': (
            f"Skor setup mencapai {total_S}/71 ({total_S/71*100:.1f}%) → {dec_S}. "
            f"Fitur terkuat: {', '.join(top_S)}. "
            f"OI {C_oi_norm:+.1f}%, Vol {F_vol_norm:+.1f}%, TakerBuy {G_taker_buy:.1f}%. "
            f"R:R vs SL Normal: TP1 {rr_matrix_S[1][0]:.1f}x, TP2 {rr_matrix_S[1][1]:.1f}x, TP3 {rr_matrix_S[1][2]:.1f}x."
        ),
        'skenario': (
            f"SL ketat ${sl_ketat_S:.4f} ({dist_pct(sl_ketat_S):+.2f}%) | "
            f"SL normal ${sl_normal_S:.4f} ({dist_pct(sl_normal_S):+.2f}%). "
            f"TP1 ${tp1_S:.4f} ({dist_pct(tp1_S):+.2f}%), "
            f"TP2 ${tp2_S:.4f} ({dist_pct(tp2_S):+.2f}%), "
            f"TP3 ${tp3_S:.4f} ({dist_pct(tp3_S):+.2f}%). "
            f"Monitor: RSI > 70 untuk konfirmasi overbought, EMA21 sebagai support."
        ),
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # RETURN STRUCTURE
    # ═══════════════════════════════════════════════════════════════════════════

    return {
        'long': {
            'total':    total_L,
            'pct':      round(total_L / 71 * 100, 2),
            'decision': dec_L,
            'code':     code_L,
            'scores':   scores_L,
            'narrative': narrative_L,
            'levels': {
                'sl_ketat':  round(sl_ketat_L,  8),
                'sl_normal': round(sl_normal_L, 8),
                'sl_lebar':  round(sl_lebar_L,  8),
                'tp1':       round(tp1_L,        8),
                'tp2':       round(tp2_L,        8),
                'tp3':       round(tp3_L,        8),
                'rr1':       rr_long(tp1_L, sl_normal_L),
                'rr2':       rr_long(tp2_L, sl_normal_L),
                'rr3':       rr_long(tp3_L, sl_normal_L),
                'rr_matrix': rr_matrix_L,
                'dist_sl_ketat':  dist_pct(sl_ketat_L),
                'dist_sl_normal': dist_pct(sl_normal_L),
                'dist_sl_lebar':  dist_pct(sl_lebar_L),
                'dist_tp1':       dist_pct(tp1_L),
                'dist_tp2':       dist_pct(tp2_L),
                'dist_tp3':       dist_pct(tp3_L),
            },
        },
        'short': {
            'total':    total_S,
            'pct':      round(total_S / 71 * 100, 2),
            'decision': dec_S,
            'code':     code_S,
            'scores':   scores_S,
            'narrative': narrative_S,
            'levels': {
                'sl_ketat':  round(sl_ketat_S,  8),
                'sl_normal': round(sl_normal_S, 8),
                'sl_lebar':  round(sl_lebar_S,  8),
                'tp1':       round(tp1_S,        8),
                'tp2':       round(tp2_S,        8),
                'tp3':       round(tp3_S,        8),
                'rr1':       rr_short(tp1_S, sl_normal_S),
                'rr2':       rr_short(tp2_S, sl_normal_S),
                'rr3':       rr_short(tp3_S, sl_normal_S),
                'rr_matrix': rr_matrix_S,
                'dist_sl_ketat':  dist_pct(sl_ketat_S),
                'dist_sl_normal': dist_pct(sl_normal_S),
                'dist_sl_lebar':  dist_pct(sl_lebar_S),
                'dist_tp1':       dist_pct(tp1_S),
                'dist_tp2':       dist_pct(tp2_S),
                'dist_tp3':       dist_pct(tp3_S),
            },
        },
        'emergency': {
            'sl_touched': is_active_pos and (close_price < sl_ketat_L),
            'rsi_ob':     O_rsi > 75,
        },
        'exit': {
            'signals':        exit_signals,
            'recommendation': exit_reco,
            'hard_count':     exit_hard,
            'warn_count':     exit_warn,
        },
        'variables': {
            'C_oi_norm':    round(C_oi_norm,    2),
            'F_vol_norm':   round(F_vol_norm,   2),
            'G_taker_buy':  round(G_taker_buy,  2),
            'H_atr_pct':    round(H_atr_pct,    2),
            'K_cvd_norm':   round(K_cvd_norm,   2),
            'L_ema21':      round(L_ema21,       2),
            'M_ema50':      round(M_ema50,       2),
            'N_ema200':     round(N_ema200,      2),
            'O_rsi':        round(O_rsi,         2),
            'Lp_ema21':     round(Lp_ema21,      2),
            'Mp_ema50':     round(Mp_ema50,      2),
            'Np_ema200':    round(Np_ema200,     2),
            'cvd_div_bull': cvd_div_bull,
            'cvd_div_bear': cvd_div_bear,
            'close_price':  round(close_price,   8),
            'low_price':    round(low_price,      8),
            'high_price':   round(high_price,     8),
            'ema21':        round(ema21,          8),
            'ema50':        round(ema50,          8),
            'ema200':       round(ema200,         8),
            'atr':          round(atr,            8),
            'is_altcoin':   is_altcoin,
            'is_active_pos': is_active_pos,
            'entry_price':   entry_val if is_active_pos else None,
            'session':       sess,
        },
        'position_metrics': position_metrics,
    }
