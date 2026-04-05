"""
Protocol 9.6 — 71-Point Quantitative Swing Trading Scoring Engine
Full spec-compliant implementation (Bagian 0–10).

Changes vs previous version:
  - Dual baseline MA20 + MA100 for OI and Volume
  - Dynamic ATR_MULT based on symbol + Altcoin_Index
  - SESSION_MULT from Market_Session column
  - Structure-based SL (Buy_Liq, FVG, SwingLow, Fib, VAL) with ATR fallback
  - Structure-based TP (Sell_Liq, FVG, OB, EMA, Fib, POC, VAH, PDH, PWH)
  - Aging system for active positions
  - Internal validation (Bagian 8)
  - Comprehensive exit signal monitoring (Bagian 7)
  - Rich narrative with actual numbers
"""
import pandas as pd
import numpy as np
from datetime import datetime
import re


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


def calculate_71point_score(df: pd.DataFrame, meta: dict) -> dict | None:
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

    last = df.iloc[-1]
    close_price = safe_float(last.get('Close', 0))
    low_price = safe_float(last.get('Low', close_price))
    high_price = safe_float(last.get('High', close_price))

    # ── Metadata ───────────────────────────────────────────────
    symbol = str(meta.get('Symbol', '')).upper()
    avg_entry = meta.get('AVG_ENTRY_PRICE')
    is_active = avg_entry is not None and float(avg_entry) > 0
    entry_val = float(avg_entry) if is_active else close_price
    entry_date_str = meta.get('ENTRY_DATE')

    # ── Slices ─────────────────────────────────────────────────
    s20 = df.iloc[-21:-1]   # 20 candles before last
    has100 = len(df) >= 101
    s100 = df.iloc[-101:-1] if has100 else s20

    candle_21_ago = df.iloc[-21] if len(df) >= 21 else df.iloc[0]

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 1 — KALKULASI UTAMA
    # ═══════════════════════════════════════════════════════════

    # ── OI ─────────────────────────────────────────────────────
    A = safe_float(last.get('Open_Interest', 0))
    B20 = s20['Open_Interest'].mean() if _has_col(df, 'Open_Interest') else (A or 1.0)
    B100 = s100['Open_Interest'].mean() if _has_col(df, 'Open_Interest') and has100 else B20
    C = ((A - B20) / B20 * 100) if B20 else 0.0
    C2 = ((A - B100) / B100 * 100) if B100 else 0.0
    C_final = (C + C2) / 2

    # ── Volume ─────────────────────────────────────────────────
    D = safe_float(last.get('Total_Volume', 0))
    E20 = s20['Total_Volume'].mean() if _has_col(df, 'Total_Volume') else (D or 1.0)
    E100 = s100['Total_Volume'].mean() if _has_col(df, 'Total_Volume') and has100 else E20
    F = ((D - E20) / E20 * 100) if E20 else 0.0
    F2 = ((D - E100) / E100 * 100) if E100 else 0.0
    F_final = (F + F2) / 2

    # ── Taker Buy ──────────────────────────────────────────────
    buy_vol = safe_float(last.get('Buy_Volume', 0))
    G = (buy_vol / D * 100) if D else 50.0

    # ── ATR ────────────────────────────────────────────────────
    atr = safe_float(last.get('ATR_14', 0))
    H = (atr / close_price * 100) if close_price else 0.0

    # ── CVD ────────────────────────────────────────────────────
    I_cvd = safe_float(last.get('CVD', 0))
    J_cvd = safe_float(candle_21_ago.get('CVD', 0))
    # K = CVD_norm% — WAJIB gunakan formula delta/abs, BUKAN nilai absolut CVD
    K = ((I_cvd - J_cvd) / abs(J_cvd) * 100) if J_cvd != 0 else 0.0
    close_21 = safe_float(candle_21_ago.get('Close', 0))
    # bool() wajib: pandas comparison → numpy.bool_ → tidak JSON-serializable
    cvd_div_bull = bool((I_cvd > J_cvd) and (close_price < close_21))
    cvd_div_bear = bool((I_cvd < J_cvd) and (close_price > close_21))

    # ── StochRSI ───────────────────────────────────────────────
    stoch_k   = _last_val(last, 'StochRSI_K')
    stoch_d   = _last_val(last, 'StochRSI_D')
    prev_row  = df.iloc[-2] if len(df) >= 2 else last
    stoch_k_prev = _last_val(prev_row, 'StochRSI_K')
    stoch_d_prev = _last_val(prev_row, 'StochRSI_D')
    has_stoch = stoch_k is not None and stoch_d is not None
    stoch_cross_up   = False
    stoch_cross_down = False
    if has_stoch and stoch_k_prev is not None and stoch_d_prev is not None:
        # bool() wajib: mencegah numpy.bool_ masuk ke return dict
        stoch_cross_up   = bool((stoch_k > stoch_d)   and (stoch_k_prev <= stoch_d_prev))
        stoch_cross_down = bool((stoch_k < stoch_d)   and (stoch_k_prev >= stoch_d_prev))

    # ── EMA ────────────────────────────────────────────────────
    ema21 = safe_float(last.get('EMA_21', close_price)) or close_price
    ema50 = safe_float(last.get('EMA_50', close_price)) or close_price
    ema200 = safe_float(last.get('EMA_200', close_price)) or close_price
    O_rsi = safe_float(last.get('RSI_6', 50))

    ref_long = close_price if is_active else low_price
    L = (ref_long - ema21) / ema21 * 100 if ema21 else 0.0
    M = (ref_long - ema50) / ema50 * 100 if ema50 else 0.0
    N = (ref_long - ema200) / ema200 * 100 if ema200 else 0.0
    Lp = (high_price - ema21) / ema21 * 100 if ema21 else 0.0
    Mp = (high_price - ema50) / ema50 * 100 if ema50 else 0.0
    Np = (high_price - ema200) / ema200 * 100 if ema200 else 0.0

    # ── ATR_MULT ───────────────────────────────────────────────
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

    sweet_lo, sweet_hi = 3.0 * ATR_MULT, 5.0 * ATR_MULT
    t2_lo, t2_hi = 2.0 * ATR_MULT, 7.0 * ATR_MULT
    t1_lo, t1_hi = 1.8 * ATR_MULT, 10.0 * ATR_MULT

    # ── SESSION_MULT ───────────────────────────────────────────
    sess_raw = str(last.get('Market_Session', '')) if 'Market_Session' in last.index else ''
    sess_upper = sess_raw.strip().upper()
    if sess_upper == 'ASIAN':
        SESSION_MULT = 0.85
    elif sess_upper == 'LONDON':
        SESSION_MULT = 1.00
    elif sess_upper == 'NEW YORK':
        SESSION_MULT = 1.00
    elif 'LONDON' in sess_upper and 'NEW YORK' in sess_upper:
        SESSION_MULT = 1.05
    else:
        SESSION_MULT = 0.90
    session_label = sess_raw.strip() if sess_raw.strip() else 'UNKNOWN'

    # ── AGING ──────────────────────────────────────────────────
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

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 2 — GATE CHECK (jalankan SEBELUM scoring)
    # ═══════════════════════════════════════════════════════════
    bos_val     = _last_val(last, 'BOS')
    funding_val = _last_val(last, 'Funding_Rate')
    buy_liq_val = _last_val(last, 'Buy_Liq')
    sell_liq_val= _last_val(last, 'Sell_Liq')
    has_bos     = bos_val is not None
    has_funding = funding_val is not None
    has_buy_liq = buy_liq_val is not None and buy_liq_val > 0
    has_sell_liq= sell_liq_val is not None and sell_liq_val > 0

    # ── Gate LONG ───────────────────────────────────────────────
    gate_L = {'status': 'CLEAR', 'gates': {}}

    # L1: Struktur tidak bearish
    if not has_bos:
        gate_L['gates']['L1'] = ('PASS', 'BOS tidak tersedia — skip')
    elif bos_val != -1:
        gate_L['gates']['L1'] = ('PASS', f'BOS={bos_val} — struktur netral/bullish')
    elif cvd_div_bull and O_rsi < 25 and has_funding and funding_val <= 0:
        gate_L['gates']['L1'] = ('PASS', 'BOS=-1 tapi exception: CVD div bull + RSI<25 + funding≤0')
    else:
        gate_L['gates']['L1'] = ('FAIL', '❌ GATE L1: Struktur bearish aktif (BOS=−1). Tunggu BOS flip atau konfirmasi reversal.')
        gate_L['status'] = 'BLOCKED'

    # L2: Likuiditas sudah/hampir diambil
    if not has_buy_liq:
        gate_L['gates']['L2'] = ('PASS', 'Buy_Liq tidak tersedia — skip')
    elif close_price <= buy_liq_val * 1.005:
        gate_L['gates']['L2'] = ('PASS', f'Harga ≤ Buy_Liq×1.005 — sweep sudah terjadi')
    elif close_price <= buy_liq_val * 1.020:
        gate_L['gates']['L2'] = ('WARN', f'⚠️ Harga dalam 2% di atas Buy_Liq ${buy_liq_val:.4f} — mendekati sweep')
        if gate_L['status'] == 'CLEAR': gate_L['status'] = 'WARNING'
    else:
        jarak_pct = (close_price / buy_liq_val - 1) * 100
        gate_L['gates']['L2'] = ('FAIL', f'❌ GATE L2: Likuiditas belum diambil. Harga +{jarak_pct:.2f}% di atas Buy_Liq ${buy_liq_val:.4f}.')
        gate_L['status'] = 'BLOCKED'

    # L3: Funding tidak positif berlebihan
    if not has_funding:
        gate_L['gates']['L3'] = ('PASS', 'Funding_Rate tidak tersedia — skip')
    elif funding_val <= 0.0003:
        gate_L['gates']['L3'] = ('PASS', f'Funding {funding_val:.5f} ≤ +0.0003 — netral/negatif')
    else:
        gate_L['gates']['L3'] = ('FAIL', f'❌ GATE L3: Funding positif tinggi ({funding_val:.5f}). Tunggu funding ≤ +0.0003.')
        gate_L['status'] = 'BLOCKED'

    # ── Gate SHORT ──────────────────────────────────────────────
    gate_S = {'status': 'CLEAR', 'gates': {}}

    # S1: Struktur tidak bullish
    if not has_bos:
        gate_S['gates']['S1'] = ('PASS', 'BOS tidak tersedia — skip')
    elif bos_val != 1:
        gate_S['gates']['S1'] = ('PASS', f'BOS={bos_val} — struktur netral/bearish')
    elif cvd_div_bear and O_rsi > 75 and has_funding and funding_val >= 0:
        gate_S['gates']['S1'] = ('PASS', 'BOS=+1 tapi exception: CVD div bear + RSI>75 + funding≥0')
    else:
        gate_S['gates']['S1'] = ('FAIL', '❌ GATE S1: Struktur bullish aktif (BOS=+1). Tunggu BOS flip ke 0/−1.')
        gate_S['status'] = 'BLOCKED'

    # S2: Sell liquidity sudah/hampir diambil
    if not has_sell_liq:
        gate_S['gates']['S2'] = ('PASS', 'Sell_Liq tidak tersedia — skip')
    elif close_price >= sell_liq_val * 0.995:
        gate_S['gates']['S2'] = ('PASS', f'Harga ≥ Sell_Liq×0.995 — sweep sudah terjadi')
    elif close_price >= sell_liq_val * 0.980:
        gate_S['gates']['S2'] = ('WARN', f'⚠️ Harga dalam 2% di bawah Sell_Liq ${sell_liq_val:.4f} — mendekati sweep')
        if gate_S['status'] == 'CLEAR': gate_S['status'] = 'WARNING'
    else:
        gate_S['gates']['S2'] = ('FAIL', f'❌ GATE S2: Sell liquidity belum diambil (${sell_liq_val:.4f}).')
        gate_S['status'] = 'BLOCKED'

    # S3: Funding tidak negatif berlebihan
    if not has_funding:
        gate_S['gates']['S3'] = ('PASS', 'Funding_Rate tidak tersedia — skip')
    elif funding_val >= -0.0003:
        gate_S['gates']['S3'] = ('PASS', f'Funding {funding_val:.5f} ≥ −0.0003 — normal')
    else:
        gate_S['gates']['S3'] = ('FAIL', f'❌ GATE S3: Funding sangat negatif ({funding_val:.5f}) — short squeeze risk.')
        gate_S['status'] = 'BLOCKED'

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 3 — SCORING LONG
    # ═══════════════════════════════════════════════════════════
    def score_oi(v):
        if v > 30: return 3
        if v >= 5: return 2
        if v >= -20: return 1
        return 0

    def score_vol(v):
        if v > 70: return 3
        if v >= 20: return 2
        if v >= -10: return 1
        return 0

    def score_atr(h):
        if sweet_lo <= h <= sweet_hi: return 3
        if (t2_lo <= h < sweet_lo) or (sweet_hi < h <= t2_hi): return 2
        if (t1_lo <= h < t2_lo) or (t2_hi < h <= t1_hi): return 1
        return 0

    s1 = score_oi(C_final)
    s2 = score_vol(F_final)
    # Spec: G<49→2, 49≤G≤51→1, G>52→0. Gap 51<G≤52 → diberi skor 1 (masih balanced)
    s3 = 2 if G < 49 else (1 if G <= 52 else 0)
    s4 = score_atr(H)
    s5 = 3 if cvd_div_bull else (2 if K > 1 else (1 if K >= 0 else 0))
    s6 = 3 if L < -3 else (2 if L < -1.5 else (1 if L < -0.5 else 0))
    s7 = 3 if M < -4 else (2 if M < -2 else (1 if M < 0 else 0))
    s8 = 3 if N < -7 else (2 if N < -3 else (1 if N < 0 else 0))
    s9 = 3 if O_rsi < 25 else (2 if O_rsi < 40 else (1 if O_rsi < 55 else 0))

    scores_L = {
        'OI':       (s1*5, 15, C_final, s1),
        'Vol':      (s2*4, 12, F_final, s2),
        'TakerBuy': (s3*4,  8, G, s3),
        'ATR':      (s4*3,  9, H, s4),
        'CVD':      (s5*3,  9, K, s5),
        'EMA21':    (s6*2,  6, L, s6),
        'EMA50':    (s7*2,  6, M, s7),
        'EMA200':   (s8*1,  3, N, s8),
        'RSI':      (s9*1,  3, O_rsi, s9),
    }
    RAW_L = sum(v[0] for v in scores_L.values())
    ADJ_L = round(RAW_L * SESSION_MULT, 1)

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 3 — SCORING SHORT
    # ═══════════════════════════════════════════════════════════
    s3s = 2 if G > 53 else (1 if G >= 51 else 0)
    s5s = 3 if cvd_div_bear else (2 if K < -1 else (1 if K <= 0 else 0))
    s6s = 3 if Lp > 5 else (2 if Lp >= 3 else (1 if Lp >= 1.5 else 0))
    s7s = 3 if Mp > 6 else (2 if Mp >= 4 else (1 if Mp >= 2 else 0))
    s8s = 3 if Np > 10 else (2 if Np >= 5 else (1 if Np >= 2 else 0))
    s9s = 3 if O_rsi > 75 else (2 if O_rsi >= 60 else (1 if O_rsi >= 45 else 0))

    scores_S = {
        'OI':       (s1*5,  15, C_final, s1),
        'Vol':      (s2*4,  12, F_final, s2),
        'TakerBuy': (s3s*4,  8, G, s3s),
        'ATR':      (s4*3,   9, H, s4),
        'CVD':      (s5s*3,  9, K, s5s),
        'EMA21':    (s6s*2,  6, Lp, s6s),
        'EMA50':    (s7s*2,  6, Mp, s7s),
        'EMA200':   (s8s*1,  3, Np, s8s),
        'RSI':      (s9s*1,  3, O_rsi, s9s),
    }
    RAW_S = sum(v[0] for v in scores_S.values())
    ADJ_S = round(RAW_S * SESSION_MULT, 1)

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 4 — KEPUTUSAN
    # ═══════════════════════════════════════════════════════════
    def get_tier(adj):
        if adj >= 53: return "FULL SIZE ENTRY", "FULL"
        if adj >= 36: return "HALF SIZE ENTRY", "HALF"
        if adj >= 21: return "WAIT & MONITOR", "WAIT"
        return "SKIP", "SKIP"

    dec_L, code_L = get_tier(ADJ_L)
    dec_S, code_S = get_tier(ADJ_S)

    if aging_status == "AGING":
        dec_L += " (⚠️ Posisi aging 8–14 hari)"
        dec_S += " (⚠️ Posisi aging 8–14 hari)"
    elif aging_status == "STALE":
        # downgrade one tier
        tier_order = ["FULL", "HALF", "WAIT", "SKIP"]
        for orig, nxt in zip(tier_order, tier_order[1:]):
            if code_L == orig:
                code_L = nxt
                dec_L, _ = get_tier(max(0, ADJ_L - 18))
                dec_L += " (❌ Posisi stale >14 hari)"
                break
        for orig, nxt in zip(tier_order, tier_order[1:]):
            if code_S == orig:
                code_S = nxt
                dec_S, _ = get_tier(max(0, ADJ_S - 18))
                dec_S += " (❌ Posisi stale >14 hari)"
                break

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 5 — STRUCTURE-BASED SL
    # ═══════════════════════════════════════════════════════════
    def dist_pct(target):
        return round((target - close_price) / close_price * 100, 4) if close_price else 0.0

    # ATR fallbacks
    sl_atr1_L = close_price - atr * 1.0
    sl_atr15_L = close_price - atr * 1.5
    sl_atr2_L = close_price - atr * 2.0

    sl_atr1_S = close_price + atr * 1.0
    sl_atr15_S = close_price + atr * 1.5
    sl_atr2_S = close_price + atr * 2.0

    # Collect SL candidates LONG (must be < close)
    sl_cands_L = []
    buy_liq = _last_val(last, 'Buy_Liq')
    if buy_liq and buy_liq > 0 and buy_liq * 0.997 < close_price:
        sl_cands_L.append((buy_liq * 0.997, "Likuiditas Buy"))
    fvg_db = _last_val(last, 'FVG_Down_Bottom')
    if fvg_db and fvg_db > 0 and fvg_db * 0.998 < close_price:
        sl_cands_L.append((fvg_db * 0.998, "FVG Bearish Bottom"))
    # swing low 3 candle
    if len(df) >= 3:
        sw3 = min(safe_float(df.iloc[-3].get('Low', 1e18)),
                  safe_float(df.iloc[-2].get('Low', 1e18)),
                  low_price) * 0.998
        if sw3 > 0 and sw3 < close_price:
            sl_cands_L.append((sw3, "Swing Low 3C"))
    fib786 = _last_val(last, 'Fib_0.786')
    if fib786 and fib786 > 0 and fib786 * 0.998 < close_price:
        sl_cands_L.append((fib786 * 0.998, "Fibonacci 0.786"))
    val_lev = _last_val(last, 'VAL')
    if val_lev and val_lev > 0 and val_lev * 0.998 < close_price:
        sl_cands_L.append((val_lev * 0.998, "Value Area Low"))
    pdl_lev = _last_val(last, 'PDL')
    if pdl_lev and pdl_lev > 0 and pdl_lev * 0.998 < close_price:
        sl_cands_L.append((pdl_lev * 0.998, "Prev Day Low"))
    # ATR fallbacks always added
    if sl_atr1_L < close_price:
        sl_cands_L.append((sl_atr1_L, "ATR ×1.0 (fallback)"))
    if sl_atr15_L < close_price:
        sl_cands_L.append((sl_atr15_L, "ATR ×1.5 (fallback)"))
    if sl_atr2_L < close_price:
        sl_cands_L.append((sl_atr2_L, "ATR ×2.0 (fallback)"))

    sl_cands_L.sort(key=lambda x: x[0], reverse=True)

    # Collect SL candidates SHORT (must be > close)
    sl_cands_S = []
    sell_liq = _last_val(last, 'Sell_Liq')
    if sell_liq and sell_liq > 0 and sell_liq * 1.003 > close_price:
        sl_cands_S.append((sell_liq * 1.003, "Likuiditas Sell"))
    fvg_ut = _last_val(last, 'FVG_Up_Top')
    if fvg_ut and fvg_ut > 0 and fvg_ut * 1.002 > close_price:
        sl_cands_S.append((fvg_ut * 1.002, "FVG Bullish Top"))
    if len(df) >= 3:
        sw3h = max(safe_float(df.iloc[-3].get('High', 0)),
                   safe_float(df.iloc[-2].get('High', 0)),
                   high_price) * 1.002
        if sw3h > close_price:
            sl_cands_S.append((sw3h, "Swing High 3C"))
    fib618 = _last_val(last, 'Fib_0.618')
    if fib618 and fib618 > 0 and fib618 * 1.002 > close_price:
        sl_cands_S.append((fib618 * 1.002, "Fibonacci 0.618"))
    vah_lev = _last_val(last, 'VAH')
    if vah_lev and vah_lev > 0 and vah_lev * 1.002 > close_price:
        sl_cands_S.append((vah_lev * 1.002, "Value Area High"))
    pdh_lev = _last_val(last, 'PDH')
    if pdh_lev and pdh_lev > 0 and pdh_lev * 1.002 > close_price:
        sl_cands_S.append((pdh_lev * 1.002, "Prev Day High"))
    if sl_atr1_S > close_price:
        sl_cands_S.append((sl_atr1_S, "ATR ×1.0 (fallback)"))
    if sl_atr15_S > close_price:
        sl_cands_S.append((sl_atr15_S, "ATR ×1.5 (fallback)"))
    if sl_atr2_S > close_price:
        sl_cands_S.append((sl_atr2_S, "ATR ×2.0 (fallback)"))

    sl_cands_S.sort(key=lambda x: x[0])

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 6 — STRUCTURE-BASED TP
    # ═══════════════════════════════════════════════════════════
    # TP LONG pool — spec 6A: semua level struktural, tanpa flat di pool
    tp_pool_L = []
    for col, lbl in [('Sell_Liq',       'Likuiditas Jual'),
                      ('FVG_Down_Top',   'FVG Bearish Top'),
                      ('FVG_Down_Bottom','FVG Bearish Bottom'),
                      ('FVG_Up_Top',     'FVG Bullish Top'),
                      ('FVG_Up_Bottom',  'FVG Bullish Bottom'),
                      ('OB_Price',       'Order Block'),
                      ('Fib_0.618',      'Fibonacci 0.618'),
                      ('Fib_0.786',      'Fibonacci 0.786'),
                      ('POC',            'Point of Control'),
                      ('VAH',            'Value Area High'),
                      ('PDH',            'Prev Day High'),
                      ('PWH',            'Prev Week High')]:
        v = _last_val(last, col)
        if v and v > 0 and v > close_price:
            tp_pool_L.append((v, lbl))
    for e_val, e_lbl in [(ema21, 'EMA 21'), (ema50, 'EMA 50'), (ema200, 'EMA 200')]:
        if e_val and e_val > close_price:
            tp_pool_L.append((e_val, e_lbl))

    # Sort terendah → tertinggi (resistance terdekat di atas Close = TP1 LONG)
    tp_pool_L = [(v, l) for v, l in tp_pool_L if v > close_price]
    tp_pool_L.sort(key=lambda x: x[0])
    seen = set()
    tp_dedup_L = []
    for v, l in tp_pool_L:
        if l not in seen:
            seen.add(l)
            tp_dedup_L.append((v, l))
    tp_pool_L = tp_dedup_L

    # Flat fallback HANYA jika slot struktural tidak tersedia
    _flat_L = [
        (entry_val * 1.025, "flat fallback — no structure (+2.5%)"),
        (entry_val * 1.046, "flat fallback — no structure (+4.6%)"),
        (entry_val * 1.070, "flat fallback — no structure (+7.0%)"),
    ]
    tp1_L = tp_pool_L[0] if len(tp_pool_L) >= 1 else _flat_L[0]
    tp2_L = tp_pool_L[1] if len(tp_pool_L) >= 2 else _flat_L[1]
    tp3_L = tp_pool_L[2] if len(tp_pool_L) >= 3 else _flat_L[2]

    # TP SHORT pool — spec 6B: 15 level struktural, bukan flat
    tp_pool_S = []
    for col, lbl in [('Buy_Liq',         'Likuiditas Beli'),
                      ('FVG_Up_Top',      'FVG Bullish Top'),
                      ('FVG_Up_Bottom',   'FVG Bullish Bottom'),
                      ('FVG_Down_Top',    'FVG Bearish Top'),
                      ('FVG_Down_Bottom', 'FVG Bearish Bottom'),
                      ('OB_Price',        'Order Block'),
                      ('Fib_0.786',       'Fibonacci 0.786'),
                      ('Fib_0.618',       'Fibonacci 0.618'),
                      ('POC',             'Point of Control'),
                      ('VAL',             'Value Area Low'),
                      ('PDL',             'Prev Day Low'),
                      ('PWL',             'Prev Week Low')]:
        v = _last_val(last, col)
        if v and v > 0 and v < close_price:
            tp_pool_S.append((v, lbl))
    for e_val, e_lbl in [(ema21, 'EMA 21'), (ema50, 'EMA 50'), (ema200, 'EMA 200')]:
        if e_val and e_val < close_price:
            tp_pool_S.append((e_val, e_lbl))

    # Sort tertinggi → terendah (support terdekat di bawah Close = TP1 SHORT)
    tp_pool_S = [(v, l) for v, l in tp_pool_S if v < close_price]
    tp_pool_S.sort(key=lambda x: x[0], reverse=True)
    seen = set()
    tp_dedup_S = []
    for v, l in tp_pool_S:
        if l not in seen:
            seen.add(l)
            tp_dedup_S.append((v, l))
    tp_pool_S = tp_dedup_S

    # Flat fallback HANYA jika slot struktural tidak tersedia
    _flat_S = [
        (entry_val * 0.975, "flat fallback — no structure (-2.5%)"),
        (entry_val * 0.954, "flat fallback — no structure (-4.6%)"),
        (entry_val * 0.930, "flat fallback — no structure (-7.0%)"),
    ]
    tp1_S = tp_pool_S[0] if len(tp_pool_S) >= 1 else _flat_S[0]
    tp2_S = tp_pool_S[1] if len(tp_pool_S) >= 2 else _flat_S[1]
    tp3_S = tp_pool_S[2] if len(tp_pool_S) >= 3 else _flat_S[2]

    # ── Select SL_STRUCTURE ────────────────────────────────────
    def select_sl_long(cands, tp1_val):
        for price, label in cands:
            denom = close_price - price
            if denom > 0:
                rr = (tp1_val - close_price) / denom
                if rr >= 2.0:
                    return price, label
        return sl_atr1_L, "ATR ×1.0 (fallback — no structure)"

    def select_sl_short(cands, tp1_val):
        for price, label in cands:
            denom = price - close_price
            if denom > 0:
                rr = (close_price - tp1_val) / denom
                if rr >= 2.0:
                    return price, label
        return sl_atr1_S, "ATR ×1.0 (fallback — no structure)"

    sl_struct_L, sl_label_L = select_sl_long(sl_cands_L, tp1_L[0])
    sl_struct_S, sl_label_S = select_sl_short(sl_cands_S, tp1_S[0])

    # ── R:R calculations ───────────────────────────────────────
    def rr_l(tp):
        d = close_price - sl_struct_L
        return round((tp - close_price) / d, 2) if d > 0 else 0.0

    def rr_s(tp):
        d = sl_struct_S - close_price
        return round((close_price - tp) / d, 2) if d > 0 else 0.0

    rr1_L, rr2_L, rr3_L = rr_l(tp1_L[0]), rr_l(tp2_L[0]), rr_l(tp3_L[0])
    rr1_S, rr2_S, rr3_S = rr_s(tp1_S[0]), rr_s(tp2_S[0]), rr_s(tp3_S[0])

    # ── R:R Matrix (backward compat with dashboard.js) ─────
    def rr_long_atr(tp, sl):
        d = close_price - sl
        return round((tp - close_price) / d, 2) if d > 0 else 0.0
    def rr_short_atr(tp, sl):
        d = sl - close_price
        return round((close_price - tp) / d, 2) if d > 0 else 0.0

    rr_matrix_L = [[rr_long_atr(tp, sl) for tp in [tp1_L[0], tp2_L[0], tp3_L[0]]] for sl in [sl_atr1_L, sl_atr15_L, sl_atr2_L]]
    rr_matrix_S = [[rr_short_atr(tp, sl) for tp in [tp1_S[0], tp2_S[0], tp3_S[0]]] for sl in [sl_atr1_S, sl_atr15_S, sl_atr2_S]]

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 7 — EXIT SIGNALS
    # ═══════════════════════════════════════════════════════════
    exit_signals = []
    if is_active:
        d21 = ((close_price / ema21) - 1) * 100 if ema21 else 0
        d50 = ((close_price / ema50) - 1) * 100 if ema50 else 0
        if O_rsi > 75:
            exit_signals.append(("❌", "RSI_6 overbought", O_rsi, "> 75"))
        if d21 > 3.6:
            exit_signals.append(("❌", "vs EMA21 extended", round(d21, 2), "> +3.6%"))
        if d50 > 4.6:
            exit_signals.append(("❌", "vs EMA50 extended", round(d50, 2), "> +4.6%"))
        if G > 53:
            exit_signals.append(("⚠️", "TakerBuy FOMO", round(G, 2), "> 53%"))
        bos = _last_val(last, 'BOS')
        if bos == -1:
            exit_signals.append(("⚠️", "BOS bearish", bos, "== -1"))
        fr = _last_val(last, 'Funding_Rate')
        if fr is not None and fr > 0.001:
            exit_signals.append(("⚠️", "Funding rate tinggi", fr, "> 0.001"))
        if aging_status == "AGING":
            exit_signals.append(("⚠️", "Posisi aging", candles_since_entry, "43-84 candles"))
        if aging_status == "STALE":
            exit_signals.append(("❌", "Posisi stale", candles_since_entry, "> 84 candles"))
        # Signal 10: mendekati TP1 (≥ 99% dari TP1) → siap partial exit
        if close_price >= tp1_L[0] * 0.99:
            exit_signals.append(("⚠️", "Mendekati TP1", round(close_price, 4), f"≥ {tp1_L[0]*0.99:.4f} (99% TP1)"))

    exit_hard = sum(1 for e in exit_signals if e[0] == "❌")
    exit_warn = sum(1 for e in exit_signals if e[0] == "⚠️")
    if exit_hard >= 1:
        exit_reco = "PARTIAL EXIT atau FULL EXIT"
    elif exit_warn >= 1:
        exit_reco = "HOLD dengan monitoring ketat"
    else:
        exit_reco = "HOLD"

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 8 — VALIDASI INTERNAL v12
    # ═══════════════════════════════════════════════════════════
    validations = []
    # V1–V2: Skor dalam range
    if not (0 <= RAW_L <= 71):
        validations.append("⚠️ V1: Skor long anomali")
    if not (0 <= RAW_S <= 71):
        validations.append("⚠️ V2: Skor short anomali")
    # V3: Tiap fitur tidak overflow maks
    for k, (pts, mx, _, _) in scores_L.items():
        if pts > mx:
            validations.append(f"⚠️ V3: Overflow {k} (L)")
    for k, (pts, mx, _, _) in scores_S.items():
        if pts > mx:
            validations.append(f"⚠️ V3: Overflow {k} (S)")
    # V4: TakerBuy poin maks 8
    if scores_L['TakerBuy'][0] > 8 or scores_S['TakerBuy'][0] > 8:
        validations.append("⚠️ V4: TakerBuy overflow (maks 8)")
    # V5–V6: SL posisi benar
    if sl_struct_L >= close_price:
        validations.append("⚠️ V5: SL long di atas harga")
    if sl_struct_S <= close_price:
        validations.append("⚠️ V6: SL short di bawah harga")
    # V7–V8: Urutan TP
    if not (tp1_L[0] <= tp2_L[0] <= tp3_L[0]):
        validations.append("⚠️ V7: Urutan TP long terbalik")
    if not (tp1_S[0] >= tp2_S[0] >= tp3_S[0]):
        validations.append("⚠️ V8: Urutan TP short terbalik")
    # V9–V10: TP sisi benar
    if any(t <= close_price for t in [tp1_L[0], tp2_L[0], tp3_L[0]]):
        validations.append("⚠️ V9: Ada TP long di bawah harga")
    if any(t >= close_price for t in [tp1_S[0], tp2_S[0], tp3_S[0]]):
        validations.append("⚠️ V10: Ada TP short di atas harga")
    # V11: RR positif
    if rr1_L <= 0:
        validations.append("⚠️ V11: RR long negatif")
    if rr1_S <= 0:
        validations.append("⚠️ V11: RR short negatif")
    # V12: Session mult diterapkan
    if round(ADJ_L, 1) != round(RAW_L * SESSION_MULT, 1):
        validations.append("⚠️ V12: Session mult tidak diterapkan (L)")
    # V13: ATR scoring pakai ATR_MULT
    if ATR_MULT == 1.0 and not is_major:
        validations.append("⚠️ V13: ATR_MULT=1.0 tapi bukan BTC/ETH")
    # V14: CVD scoring pakai CVD_norm% bukan absolut
    # K adalah CVD_norm% — validasi range masuk akal
    if abs(I_cvd) > 0 and abs(K) == abs(I_cvd):
        validations.append("⚠️ V14: CVD scoring mungkin pakai nilai absolut")
    valid_ok = len(validations) == 0

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 9 — MARKET CONTEXT
    # ═══════════════════════════════════════════════════════════
    ctx = {}
    ctx_cols = ['MSB','BOS','CHoCH','SFP_Sweep','FVG_Up_Top','FVG_Up_Bottom',
                'FVG_Down_Top','FVG_Down_Bottom','OB_Price','Fib_0.618','Fib_0.786',
                'POC','VAH','VAL','Buy_Liq','Sell_Liq','PDH','PDL','PWH','PWL',
                'EMA_7','EMA_7_H4','EMA_21_H4','EMA_50_H4','EMA_200_H4',
                'StochRSI_K','StochRSI_D','Funding_Rate','BTC_Price','BTC_Dominance','Altcoin_Index']
    for col in ctx_cols:
        v = _last_val(last, col)
        if v is not None:
            ctx[col] = v

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 10 — NARRATIVE (Bagian 12 spec)
    # ═══════════════════════════════════════════════════════════

    # Gate summary helpers
    def _gate_summary(gate: dict) -> str:
        parts = []
        for gk, (status, msg) in gate['gates'].items():
            if status == 'FAIL':  parts.append(f"{gk}:GAGAL")
            elif status == 'WARN': parts.append(f"{gk}:WARN")
        return ', '.join(parts) if parts else 'semua lolos'

    def _stoch_desc() -> str:
        if not has_stoch: return "StochRSI tidak tersedia"
        sk_r, sd_r = round(stoch_k, 1), round(stoch_d, 1)
        cross = " [CROSS UP ✅]" if stoch_cross_up else (" [CROSS DOWN ❌]" if stoch_cross_down else "")
        return f"StochRSI K={sk_r} D={sd_r}{cross}"

    def _top_blocker(scores: dict, is_long: bool) -> str:
        worst = sorted(scores.items(), key=lambda x: x[1][0])
        return ', '.join(f"{k}({v[0]}/{v[1]})" for k, v in worst[:2])

    def _top_driver(scores: dict) -> str:
        best = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
        return ', '.join(f"{k}({v[0]}/{v[1]})" for k, v in best[:2])

    vol_dir = "spike" if F_final > 20 else ("normal" if F_final >= -10 else "turun")
    vol_desc = f"{vol_dir} (MA20:{F:+.1f}% · MA100:{F2:+.1f}% · avg:{F_final:+.1f}%)"
    cvd_desc = "bullish divergence ✅" if cvd_div_bull else ("bearish divergence ❌" if cvd_div_bear else f"norm={K:+.1f}%")

    narrative_L = {
        'kondisi': (
            f"[GATE LONG: {gate_L['status']}] {_gate_summary(gate_L)}. "
            f"Sesi {session_label} (×{SESSION_MULT}). Vol {vol_desc}. "
            f"Ref={'Close' if is_active else 'Low'} ${(close_price if is_active else low_price):.4f} vs "
            f"EMA21 {L:+.2f}% (${ema21:.4f}), EMA50 {M:+.2f}% (${ema50:.4f}), EMA200 {N:+.2f}% (${ema200:.4f}). "
            f"CVD: {cvd_desc} (I={I_cvd:.0f}, J={J_cvd:.0f}). "
            f"RSI_6={O_rsi:.1f}. {_stoch_desc()}. "
            f"ATR={H:.2f}% | ATR_MULT={ATR_MULT} ({atr_mult_reason}) | sweet spot {sweet_lo:.1f}%–{sweet_hi:.1f}%."
        ),
        'keputusan': (
            f"RAW={RAW_L} → ADJ={ADJ_L} (×{SESSION_MULT}) → {dec_L}"
            + (f" [GATE BLOCKED: {_gate_summary(gate_L)}]" if gate_L['status'] == 'BLOCKED' else "")
            + (f" [AGING: {aging_status}]" if aging_status in ('AGING', 'STALE') else "")
            + f". Driver: {_top_driver(scores_L)}. Hambatan: {_top_blocker(scores_L, True)}. "
            f"SL [{sl_label_L}] ${sl_struct_L:.4f} ({dist_pct(sl_struct_L):+.2f}%). "
            f"TP1 [{tp1_L[1]}] ${tp1_L[0]:.4f} R:R {rr1_L}×. "
            f"TP2 [{tp2_L[1]}] ${tp2_L[0]:.4f} R:R {rr2_L}×. "
            f"TP3 [{tp3_L[1]}] ${tp3_L[0]:.4f} R:R {rr3_L}×."
        ),
        'skenario': (
            f"Tier 1 butuh: BOS→+1 (saat ini {bos_val}), CVD div bull (+RSI<25+funding≤0), Funding≤0. "
            f"Tier 2 butuh: {'sweep Buy_Liq $' + str(round(buy_liq_val,4)) if has_buy_liq else 'Buy_Liq N/A'}, "
            f"RSI<{'25 (saat ini '+str(round(O_rsi,1))+')' if s9<3 else 'OK'}, StochRSI cross up dari <20. "
            f"Level kunci: Close ${close_price:.4f}, EMA21 ${ema21:.4f}, EMA50 ${ema50:.4f}. "
            f"Sesi optimal: London ({session_label} saat ini). "
            + (f"Posisi {aging_status}: pertimbangkan exit dan re-entry setelah kondisi Tier 1 kembali positif." if aging_status in ('AGING','STALE') else "")
        ),
    }

    narrative_S = {
        'kondisi': (
            f"[GATE SHORT: {gate_S['status']}] {_gate_summary(gate_S)}. "
            f"Sesi {session_label} (×{SESSION_MULT}). Vol {vol_desc}. "
            f"High ${high_price:.4f} vs "
            f"EMA21 {Lp:+.2f}% (${ema21:.4f}), EMA50 {Mp:+.2f}% (${ema50:.4f}), EMA200 {Np:+.2f}% (${ema200:.4f}). "
            f"CVD: {cvd_desc} (I={I_cvd:.0f}, J={J_cvd:.0f}). "
            f"RSI_6={O_rsi:.1f}. {_stoch_desc()}. "
            f"ATR={H:.2f}% | ATR_MULT={ATR_MULT} ({atr_mult_reason}) | sweet spot {sweet_lo:.1f}%–{sweet_hi:.1f}%."
        ),
        'keputusan': (
            f"RAW={RAW_S} → ADJ={ADJ_S} (×{SESSION_MULT}) → {dec_S}"
            + (f" [GATE BLOCKED: {_gate_summary(gate_S)}]" if gate_S['status'] == 'BLOCKED' else "")
            + (f" [AGING: {aging_status}]" if aging_status in ('AGING', 'STALE') else "")
            + f". Driver: {_top_driver(scores_S)}. Hambatan: {_top_blocker(scores_S, False)}. "
            f"SL [{sl_label_S}] ${sl_struct_S:.4f} ({dist_pct(sl_struct_S):+.2f}%). "
            f"TP1 [{tp1_S[1]}] ${tp1_S[0]:.4f} R:R {rr1_S}×. "
            f"TP2 [{tp2_S[1]}] ${tp2_S[0]:.4f} R:R {rr2_S}×. "
            f"TP3 [{tp3_S[1]}] ${tp3_S[0]:.4f} R:R {rr3_S}×."
        ),
        'skenario': (
            f"Tier 1 butuh: BOS→-1 (saat ini {bos_val}), CVD div bear (+RSI>75+funding≥0), Funding≥0. "
            f"Tier 2 butuh: {'sweep Sell_Liq $' + str(round(sell_liq_val,4)) if has_sell_liq else 'Sell_Liq N/A'}, "
            f"RSI>{'75 (saat ini '+str(round(O_rsi,1))+')' if s9s<3 else 'OK'}, StochRSI cross down dari >80. "
            f"Level kunci: Close ${close_price:.4f}, EMA21 ${ema21:.4f}, EMA50 ${ema50:.4f}. "
            f"Sesi optimal: London/NY ({session_label} saat ini)."
        ),
    }

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 11 — MOMENTUM HOLD ANALYSIS
    # Apakah momentum masih kuat sehingga sebaiknya TAHAN TP?
    # ═══════════════════════════════════════════════════════════
    # 5 faktor penentu momentum masih kuat:
    # 1. CVD naik  (buyers mengakumulasi, tidak distribusi)
    # 2. Taker Buy dominan  (order buy lebih banyak dari sell)
    # 3. RSI belum overbought  (ruang naik masih ada)
    # 4. OI naik  (uang baru masuk ke pasar = bullish)
    # 5. Harga belum terlalu jauh dari EMA21  (belum extended)

    cvd_rising     = bool(K > 1.0)                  # CVD naik >1%
    buy_dominant   = bool(G > 55.0)                 # Taker Buy >55%
    rsi_not_ob     = bool(O_rsi < 68.0)             # RSI belum mendekati OB
    oi_net_rising  = bool(C_final > 3.0)            # OI di atas MA >3%
    not_extended   = bool(L < 3.0)                  # Jarak dari EMA21 < 3%

    momentum_factors = [cvd_rising, buy_dominant, rsi_not_ob, oi_net_rising, not_extended]
    momentum_score   = int(sum(momentum_factors))

    hold_tp_signal   = False
    hold_tp_strength = ""
    hold_tp_reasons: list[str] = []

    if momentum_score >= 4:
        hold_tp_signal   = True
        hold_tp_strength = "KUAT"
    elif momentum_score == 3:
        hold_tp_signal   = True
        hold_tp_strength = "SEDANG"

    if hold_tp_signal:
        if cvd_rising:    hold_tp_reasons.append(f"CVD naik ({K:+.1f}%)")
        if buy_dominant:  hold_tp_reasons.append(f"Buy Vol dominan ({G:.1f}%)")
        if rsi_not_ob:    hold_tp_reasons.append(f"RSI aman ({O_rsi:.1f})")
        if oi_net_rising: hold_tp_reasons.append(f"OI naik ({C_final:.1f}%)")
        if not_extended:  hold_tp_reasons.append(f"Jarak EMA21 aman ({L:.1f}%)")

    momentum_hold = {
        'signal':    bool(hold_tp_signal),
        'strength':  hold_tp_strength,
        'score':     momentum_score,
        'max_score': 5,
        'reasons':   hold_tp_reasons,
        'factors': {
            'cvd_rising':    cvd_rising,
            'buy_dominant':  buy_dominant,
            'rsi_not_ob':    rsi_not_ob,
            'oi_net_rising': oi_net_rising,
            'not_extended':  not_extended,
        },
    }

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 12 — SL WICK FAKEOUT DETECTION
    # Apakah sentuhan ke level SL hanya wick palsu atau breakdown nyata?
    # ═══════════════════════════════════════════════════════════
    # Logika:
    #   • Low menyentuh / melewati SL  → ada potensi analisis
    #   • Close candle masih di atas SL → body aman = kandidat fakeout
    #   • CVD tidak turun               → buyer masih hadir (defend)
    #   • Volume drop di bawah MA20     → tidak ada conviction bearish
    #   • Close > Open (body hijau)     → candle berbalik bullish
    #
    # Confidence scale:
    #   Semua sinyal fakeout terpenuhi  → FAKEOUT TINGGI (≥80%)
    #   2 dari 3                        → FAKEOUT SEDANG (~60%)
    #   1 atau tidak ada                → BREAKDOWN NYATA

    sl_wick_result: dict = {
        'applicable':       False,
        'sl_touched_wick':  False,
        'body_above_sl':    False,
        'cvd_defending':    False,
        'low_volume_drop':  False,
        'bullish_body':     False,
        'fakeout_count':    0,
        'verdict':          'N/A',
        'confidence_pct':   0,
        'action':           'N/A',
    }

    if is_active:
        sl_wick_result['applicable'] = True
        _sl_val = sl_struct_L

        sl_wick_result['sl_touched_wick'] = bool(low_price <= _sl_val)
        sl_wick_result['body_above_sl']   = bool(close_price > _sl_val)
        sl_wick_result['cvd_defending']   = bool(K >= 0.0)
        sl_wick_result['low_volume_drop'] = bool(D < E20)
        sl_wick_result['bullish_body']    = bool(close_price > safe_float(last.get('Open', close_price)))

        if sl_wick_result['sl_touched_wick'] and sl_wick_result['body_above_sl']:
            # Wick menyentuh SL tapi body selamat → evaluasi fakeout
            fakeout_signals = [
                sl_wick_result['cvd_defending'],
                sl_wick_result['low_volume_drop'],
                sl_wick_result['bullish_body'],
            ]
            fakeout_count = sum(fakeout_signals)
            sl_wick_result['fakeout_count'] = fakeout_count

            if fakeout_count >= 3:
                sl_wick_result['verdict']        = 'FAKEOUT TINGGI'
                sl_wick_result['confidence_pct'] = 85
                sl_wick_result['action']         = '⚡ TAHAN — Kemungkinan besar mantul. Pantau body candle berikutnya.'
            elif fakeout_count == 2:
                sl_wick_result['verdict']        = 'FAKEOUT SEDANG'
                sl_wick_result['confidence_pct'] = 60
                sl_wick_result['action']         = '⚠️ WASPADA — Ada sinyal fakeout. Tunggu konfirmasi candle berikutnya.'
            elif fakeout_count == 1:
                sl_wick_result['verdict']        = 'SINYAL LEMAH'
                sl_wick_result['confidence_pct'] = 35
                sl_wick_result['action']         = '🔶 SIAP EXIT — Momentum lemah. Pertimbangkan cut jika next candle red.'
            else:
                sl_wick_result['verdict']        = 'BREAKDOWN NYATA'
                sl_wick_result['confidence_pct'] = 10
                sl_wick_result['action']         = '❌ EXIT SEKARANG — Tidak ada sinyal fakeout. Breakdown konfirmasi.'

        elif sl_wick_result['sl_touched_wick'] and not sl_wick_result['body_above_sl']:
            # Body candle sudah di bawah SL → breakdown nyata
            sl_wick_result['verdict']        = 'BREAKDOWN NYATA'
            sl_wick_result['confidence_pct'] = 5
            sl_wick_result['action']         = '❌ EXIT SEGERA — Candle close di bawah SL. Ini bukan wick.'
            sl_wick_result['fakeout_count']  = 0

        else:
            # SL tidak disentuh sama sekali → aman
            sl_wick_result['verdict']        = 'SL AMAN'
            sl_wick_result['confidence_pct'] = 100
            sl_wick_result['action']         = '✅ Posisi aman. SL belum disentuh.'

    # ═══════════════════════════════════════════════════════════
    # BAGIAN 9 — PARAMETER PRIORITY INDICATOR (Tier 1/2/3)
    # ═══════════════════════════════════════════════════════════

    # --- LONG Priority ---
    def _ppi_bos_long():
        if not has_bos: return ('⚠️', 'N/A', 'Kolom tidak tersedia')
        if bos_val == 1:  return ('✅', f'BOS={bos_val}', 'Bullish')
        if bos_val == 0:  return ('⚠️', f'BOS={bos_val}', 'Netral')
        return ('❌', f'BOS={bos_val}', 'Bearish aktif')

    def _ppi_cvd_long():
        if cvd_div_bull: return ('✅', f'CVD div bull', f'K={K:+.2f}%')
        if K > 0:        return ('⚠️', f'K={K:+.2f}%', 'Naik tapi tanpa divergence')
        return ('❌', f'K={K:+.2f}%', 'CVD negatif')

    def _ppi_funding_long():
        if not has_funding: return ('⚠️', 'N/A', 'Tidak tersedia')
        if funding_val <= 0:      return ('✅', f'{funding_val:.5f}', 'Negatif/netral')
        if funding_val <= 0.0003: return ('⚠️', f'{funding_val:.5f}', 'Sedikit positif')
        return ('❌', f'{funding_val:.5f}', 'Positif tinggi — long trap')

    def _ppi_liqsweep_long():
        if not has_buy_liq: return ('⚠️', 'N/A', 'Tidak tersedia')
        j = (close_price / buy_liq_val - 1) * 100
        if close_price <= buy_liq_val * 1.005: return ('✅', f'${buy_liq_val:.4f}', 'Sweep selesai')
        if close_price <= buy_liq_val * 1.020: return ('⚠️', f'+{j:.2f}%', 'Mendekati')
        return ('❌', f'+{j:.2f}%', 'Belum diambil')

    def _ppi_rsi_long():
        if O_rsi < 25:  return ('✅', f'{O_rsi:.1f}', 'Oversold ekstrem')
        if O_rsi < 40:  return ('⚠️', f'{O_rsi:.1f}', 'Mendekati oversold')
        return ('❌', f'{O_rsi:.1f}', 'Tidak oversold')

    def _ppi_stoch_long():
        if not has_stoch: return ('⚠️', 'N/A', 'Tidak tersedia')
        sk, sd = round(stoch_k, 1), round(stoch_d, 1)
        if stoch_cross_up and stoch_k < 20: return ('✅', f'K={sk} D={sd}', 'Cross up dari <20')
        if stoch_k < stoch_d and stoch_k < 30: return ('⚠️', f'K={sk} D={sd}', 'K<D area rendah')
        return ('❌', f'K={sk} D={sd}', 'Tidak menguntungkan')

    ppi_long = {
        'tier1': [
            ('BOS/CHoCH',    *_ppi_bos_long()),
            ('CVD Diverg.',  *_ppi_cvd_long()),
            ('Funding Rate', *_ppi_funding_long()),
        ],
        'tier2': [
            ('Liq Sweep',  *_ppi_liqsweep_long()),
            ('RSI_6',      *_ppi_rsi_long()),
            ('StochRSI',   *_ppi_stoch_long()),
        ],
        'tier3': {
            'vol_above_ma':    bool(F_final > 0),
            'oi_positive':     bool(C_final > 5),
            'price_below_ema': bool(L < -1.5 or M < -2),
            'atr_ok':          bool(s4 >= 2),
            'positives':       int(sum([F_final > 0, C_final > 5, L < -1.5 or M < -2, s4 >= 2])),
        },
    }

    # --- SHORT Priority ---
    def _ppi_bos_short():
        if not has_bos: return ('⚠️', 'N/A', 'Kolom tidak tersedia')
        if bos_val == -1: return ('✅', f'BOS={bos_val}', 'Bearish')
        if bos_val == 0:  return ('⚠️', f'BOS={bos_val}', 'Netral')
        return ('❌', f'BOS={bos_val}', 'Bullish aktif')

    def _ppi_cvd_short():
        if cvd_div_bear: return ('✅', f'CVD div bear', f'K={K:+.2f}%')
        if K < 0:        return ('⚠️', f'K={K:+.2f}%', 'Turun tapi tanpa divergence')
        return ('❌', f'K={K:+.2f}%', 'CVD positif')

    def _ppi_funding_short():
        if not has_funding: return ('⚠️', 'N/A', 'Tidak tersedia')
        if funding_val >= 0:       return ('✅', f'{funding_val:.5f}', 'Positif/netral')
        if funding_val >= -0.0003: return ('⚠️', f'{funding_val:.5f}', 'Sedikit negatif')
        return ('❌', f'{funding_val:.5f}', 'Sangat negatif — squeeze risk')

    def _ppi_liqsweep_short():
        if not has_sell_liq: return ('⚠️', 'N/A', 'Tidak tersedia')
        j = (sell_liq_val / close_price - 1) * 100
        if close_price >= sell_liq_val * 0.995: return ('✅', f'${sell_liq_val:.4f}', 'Sweep selesai')
        if close_price >= sell_liq_val * 0.980: return ('⚠️', f'-{j:.2f}%', 'Mendekati')
        return ('❌', f'-{j:.2f}%', 'Belum diambil')

    def _ppi_rsi_short():
        if O_rsi > 75:  return ('✅', f'{O_rsi:.1f}', 'Overbought ekstrem')
        if O_rsi > 60:  return ('⚠️', f'{O_rsi:.1f}', 'Mendekati overbought')
        return ('❌', f'{O_rsi:.1f}', 'Tidak overbought')

    def _ppi_stoch_short():
        if not has_stoch: return ('⚠️', 'N/A', 'Tidak tersedia')
        sk, sd = round(stoch_k, 1), round(stoch_d, 1)
        if stoch_cross_down and stoch_k > 80: return ('✅', f'K={sk} D={sd}', 'Cross down dari >80')
        if stoch_k > stoch_d and stoch_k > 70: return ('⚠️', f'K={sk} D={sd}', 'K>D area tinggi')
        return ('❌', f'K={sk} D={sd}', 'Tidak menguntungkan')

    ppi_short = {
        'tier1': [
            ('BOS/CHoCH',    *_ppi_bos_short()),
            ('CVD Diverg.',  *_ppi_cvd_short()),
            ('Funding Rate', *_ppi_funding_short()),
        ],
        'tier2': [
            ('Liq Sweep',  *_ppi_liqsweep_short()),
            ('RSI_6',      *_ppi_rsi_short()),
            ('StochRSI',   *_ppi_stoch_short()),
        ],
        'tier3': {
            'vol_above_ma':    bool(F_final > 0),
            'oi_positive':     bool(C_final > 5),
            'price_above_ema': bool(Lp > 3 or Mp > 4),
            'atr_ok':          bool(s4 >= 2),
            'positives':       int(sum([F_final > 0, C_final > 5, Lp > 3 or Mp > 4, s4 >= 2])),
        },
    }

    # ── Gate overrides keputusan ─────────────────────────────
    if gate_L['status'] == 'BLOCKED':
        dec_L = 'SKIP'
        code_L = 'SKIP'
    if gate_S['status'] == 'BLOCKED':
        dec_S = 'SKIP'
        code_S = 'SKIP'

    # ═══════════════════════════════════════════════════════════
    # RETURN — json_safe() memastikan semua numpy type dikonversi
    # ═══════════════════════════════════════════════════════════
    pnl_pct = round((close_price / entry_val - 1) * 100, 4) if is_active and entry_val else None

    result = {
        'long': {
            'raw': RAW_L, 'total': ADJ_L,
            'pct': round(ADJ_L / 71 * 100, 2),
            'decision': dec_L, 'code': code_L,
            'gate': gate_L,
            'scores': scores_L, 'narrative': narrative_L,
            'ppi': ppi_long,
            'levels': {
                'sl_structure': round(sl_struct_L, 8), 'sl_label': sl_label_L,
                'sl_ketat': round(sl_atr1_L, 8), 'sl_normal': round(sl_atr15_L, 8), 'sl_lebar': round(sl_atr2_L, 8),
                'tp1': round(tp1_L[0], 8), 'tp1_label': tp1_L[1],
                'tp2': round(tp2_L[0], 8), 'tp2_label': tp2_L[1],
                'tp3': round(tp3_L[0], 8), 'tp3_label': tp3_L[1],
                'rr1': rr1_L, 'rr2': rr2_L, 'rr3': rr3_L,
                'rr_matrix': rr_matrix_L,
                'dist_sl': dist_pct(sl_struct_L),
                'dist_sl_ketat': dist_pct(sl_atr1_L), 'dist_sl_normal': dist_pct(sl_atr15_L), 'dist_sl_lebar': dist_pct(sl_atr2_L),
                'dist_tp1': dist_pct(tp1_L[0]), 'dist_tp2': dist_pct(tp2_L[0]), 'dist_tp3': dist_pct(tp3_L[0]),
            },
            'sl_candidates': [(round(p, 8), l) for p, l in sl_cands_L],
        },
        'short': {
            'raw': RAW_S, 'total': ADJ_S,
            'pct': round(ADJ_S / 71 * 100, 2),
            'decision': dec_S, 'code': code_S,
            'gate': gate_S,
            'scores': scores_S, 'narrative': narrative_S,
            'ppi': ppi_short,
            'levels': {
                'sl_structure': round(sl_struct_S, 8), 'sl_label': sl_label_S,
                'sl_ketat': round(sl_atr1_S, 8), 'sl_normal': round(sl_atr15_S, 8), 'sl_lebar': round(sl_atr2_S, 8),
                'tp1': round(tp1_S[0], 8), 'tp1_label': tp1_S[1],
                'tp2': round(tp2_S[0], 8), 'tp2_label': tp2_S[1],
                'tp3': round(tp3_S[0], 8), 'tp3_label': tp3_S[1],
                'rr1': rr1_S, 'rr2': rr2_S, 'rr3': rr3_S,
                'rr_matrix': rr_matrix_S,
                'dist_sl': dist_pct(sl_struct_S),
                'dist_sl_ketat': dist_pct(sl_atr1_S), 'dist_sl_normal': dist_pct(sl_atr15_S), 'dist_sl_lebar': dist_pct(sl_atr2_S),
                'dist_tp1': dist_pct(tp1_S[0]), 'dist_tp2': dist_pct(tp2_S[0]), 'dist_tp3': dist_pct(tp3_S[0]),
            },
            'sl_candidates': [(round(p, 8), l) for p, l in sl_cands_S],
        },
        'emergency': {
            'sl_touched': bool(is_active and (close_price < sl_struct_L)),
            'rsi_ob': bool(O_rsi > 75),
            'stale': bool(aging_status == "STALE"),
        },
        'exit': {
            'signals': exit_signals, 'recommendation': exit_reco,
            'hard_count': exit_hard, 'warn_count': exit_warn,
        },
        'momentum_hold': momentum_hold,
        'sl_wick':       sl_wick_result,
        'validation': {
            'ok': bool(valid_ok),
            'issues': validations,
            'badge': '✅ Kalkulasi v12 valid' if valid_ok else f'⚠️ {len(validations)} isu validasi'
        },
        'market_context': ctx,
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
                'sweet_lo': round(sweet_lo, 2), 'sweet_hi': round(sweet_hi, 2),
                't2_lo': round(t2_lo, 2), 't2_hi': round(t2_hi, 2),
                't1_lo': round(t1_lo, 2), 't1_hi': round(t1_hi, 2),
            },
            'SESSION_MULT': SESSION_MULT, 'session': session_label,
            'is_altcoin': bool(not is_major),
            'is_active_pos': bool(is_active), 'entry_price': entry_val if is_active else None,
            'aging_status': aging_status, 'candles_since_entry': int(candles_since_entry),
            'pnl_pct': pnl_pct,
            'bos_val': bos_val, 'funding_val': funding_val,
            'buy_liq_val': buy_liq_val, 'sell_liq_val': sell_liq_val,
        },
    }
    # Sanitize ALL numpy types sebelum dikembalikan ke Flask jsonify
    return json_safe(result)


