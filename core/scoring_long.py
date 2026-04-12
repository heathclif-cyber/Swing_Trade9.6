import pandas as pd
from core.helpers import _last_val, safe_float
from core.levels import get_atr_projections_long

def calculate_long_score(df: pd.DataFrame, ctx: dict) -> dict:
    # Extracted variables
    last           = ctx['last']
    close_price    = ctx['close_price']
    low_price      = ctx['low_price']
    entry_val      = ctx['entry_val']
    is_active      = ctx['is_active']
    aging_status   = ctx['aging_status']
    SESSION_MULT   = ctx['SESSION_MULT']
    session_label  = ctx['session_label']
    session_block  = ctx['session_block']
    session_block_type   = ctx['session_block_type']
    session_block_reason = ctx['session_block_reason']
    macro_slope    = ctx['macro_slope']
    macro_trend    = ctx['macro_trend']
    C_final        = ctx['C_final']
    F_final        = ctx['F_final']
    G              = ctx['G']
    H              = ctx['H']
    K              = ctx['K']
    cvd_div_bull   = ctx['cvd_div_bull']
    L              = ctx['L']
    M              = ctx['M']
    N              = ctx['N']
    O_rsi          = ctx['O_rsi']
    has_bos        = ctx['has_bos']
    bos_val        = ctx['bos_val']
    has_funding    = ctx['has_funding']
    funding_val    = ctx['funding_val']
    has_buy_liq    = ctx['has_buy_liq']
    buy_liq_val    = ctx['buy_liq_val']
    dyn_buy_liq    = ctx['dyn_buy_liq']
    has_dyn_liq    = ctx['has_dyn_liq']
    dist_to_liq    = ctx['dist_to_liq']
    swing_low_20   = ctx['swing_low_20']
    has_stoch      = ctx['has_stoch']
    stoch_k        = ctx['stoch_k']
    stoch_d        = ctx['stoch_d']
    stoch_k_prev   = ctx['stoch_k_prev']
    stoch_d_prev   = ctx['stoch_d_prev']
    stoch_cross_up = ctx['stoch_cross_up']
    atr            = ctx['atr']
    ATR_MULT       = ctx['ATR_MULT']
    atr_mult_reason= ctx['atr_mult_reason']
    sl_atr1_L      = ctx['sl_atr1_L']
    sl_atr15_L     = ctx['sl_atr15_L']
    sl_atr2_L      = ctx['sl_atr2_L']
    _thr_full      = ctx['_thr_full']
    _thr_half      = ctx['_thr_half']
    _thr_wait      = ctx['_thr_wait']
    I_cvd          = ctx['I_cvd']
    J_cvd          = ctx['J_cvd']
    ema21          = ctx['ema21']
    ema50          = ctx['ema50']
    ema200         = ctx['ema200']
    sweet_lo       = ctx['sweet_lo']
    sweet_hi       = ctx['sweet_hi']

    atr_score_sweet_lo = ctx['atr_score_sweet_lo']
    atr_score_sweet_hi = ctx['atr_score_sweet_hi']
    atr_score_t2_lo    = ctx['atr_score_t2_lo']
    atr_score_t2_hi    = ctx['atr_score_t2_hi']
    atr_score_t1_lo    = ctx['atr_score_t1_lo']
    atr_score_t1_hi    = ctx['atr_score_t1_hi']

    # ── Variabel baru (3 improvisasi) ──────────────────────────────
    oi_change        = ctx['oi_change']
    O_rsi_1          = ctx['O_rsi_1']
    O_rsi_2          = ctx['O_rsi_2']
    dist_ema21_close = ctx['dist_ema21_close']
    F                = ctx['F']  # Rel Volume vs MA20

    # ── [IMPR. 3] Karet Gelang: hitung bonus SEBELUM gate agar bisa bypass L1
    _karet_gelang_triggered = bool(dist_ema21_close < -6.0)
    _karet_gelang_bonus     = 5 if _karet_gelang_triggered else 0
    _karet_gelang_note      = (
        f"⚡ KARET GELANG LONG: Close {dist_ema21_close:.2f}% di bawah EMA21 (<-6%). +5 bonus darurat mean reversion."
        if _karet_gelang_triggered else ""
    )

    # ── [IMPR. 2] RSI V-Shape Memory (precomputed dari ctx) ───
    rsi_vshaped_long = (O_rsi <= 35) and (O_rsi_1 < 20 or O_rsi_2 < 20)
    rsi_vshaped_note = (
        f"V-Shape RSI: candle-1={O_rsi_1:.1f}, candle-2={O_rsi_2:.1f} (salah satu <20)"
        if rsi_vshaped_long else ""
    )

    # ── Gate LONG ───────────────────────────────────────────────
    gate_L = {'status': 'CLEAR', 'gates': {}}

    # [IMPR. 3] Karet Gelang ekstrem → bypass Gate L1 (BOS diabaikan)
    if _karet_gelang_triggered:
        gate_L['gates']['L1'] = ('PASS', f'BOS diabaikan — Karet Gelang Ekstrem (Close {dist_ema21_close:.2f}% vs EMA21 <-6%)')
    elif not has_bos:
        gate_L['gates']['L1'] = ('PASS', 'BOS tidak tersedia — skip')
    elif bos_val != -1:
        gate_L['gates']['L1'] = ('PASS', f'BOS={bos_val} — struktur netral/bullish')
    elif cvd_div_bull and (O_rsi < 25 or rsi_vshaped_long) and has_funding and funding_val <= 0:
        gate_L['gates']['L1'] = ('PASS', 'BOS=-1 exception: CVD div bull + RSI V-Shape/Oversold + funding≤0')
    else:
        gate_L['gates']['L1'] = ('FAIL', '❌ GATE L1: Struktur bearish aktif (BOS=−1). Tunggu BOS flip atau konfirmasi reversal.')
        gate_L['status'] = 'BLOCKED'

    if not has_dyn_liq:
        if not has_buy_liq:
            gate_L['gates']['L2'] = ('PASS', 'Dynamic Buy_Liq tidak dapat dihitung (data Low kurang) — skip')
        elif close_price <= buy_liq_val * 1.005:
            gate_L['gates']['L2'] = ('PASS', f'[Statis] Harga ≤ Buy_Liq×1.005 — sweep sudah terjadi')
        elif close_price <= buy_liq_val * 1.020:
            gate_L['gates']['L2'] = ('WARN', f'⚠️ [Statis] Harga +{(close_price/buy_liq_val-1)*100:.2f}% di atas Buy_Liq ${buy_liq_val:.4f}')
            if gate_L['status'] == 'CLEAR': gate_L['status'] = 'WARNING'
        else:
            jarak_pct = (close_price / buy_liq_val - 1) * 100
            gate_L['gates']['L2'] = ('FAIL', f'❌ GATE L2 [Statis]: Harga +{jarak_pct:.2f}% di atas Buy_Liq ${buy_liq_val:.4f}.')
            gate_L['status'] = 'BLOCKED'
    else:
        _d = dist_to_liq
        if _d < 1.0:
            gate_L['gates']['L2'] = (
                'SKIP',
                f'⚡ GATE L2: Harga terlalu dekat dyn_Buy_Liq (dist={_d:.2f}%). '
                f'Level: ${dyn_buy_liq:.4f} | SwingLow(20): ${swing_low_20:.4f}. '
                f'Kemungkinan sedang tersapu — TUNGGU konfirmasi reversal.'
            )
            if gate_L['status'] == 'CLEAR': gate_L['status'] = 'WARNING'
        elif _d <= 8.0:  # [FIX 2] Perlebar sweet spot: 5% → 8%
            gate_L['gates']['L2'] = (
                'PASS',
                f'✅ GATE L2: Sweet Spot (dist={_d:.2f}%). '
                f'Harga cukup dekat dengan dyn_Buy_Liq ${dyn_buy_liq:.4f} — '
                f'likuiditas hampir/sudah diambil. SwingLow(20): ${swing_low_20:.4f}.'
            )
        elif _d <= 15.0:  # [FIX 2] Perlebar warning zone: 10% → 15%
            gate_L['gates']['L2'] = (
                'WARN',
                f'⚠️ GATE L2: Warning Zone (dist={_d:.2f}%). '
                f'Harga cukup jauh dari dyn_Buy_Liq ${dyn_buy_liq:.4f}. '
                f'Tunggu harga lebih dekat ke SwingLow(20) ${swing_low_20:.4f}.'
            )
            if gate_L['status'] == 'CLEAR': gate_L['status'] = 'WARNING'
        else:  # [FIX 2] BLOCK threshold: 10% → 15%
            gate_L['gates']['L2'] = (
                'FAIL',
                f'❌ GATE L2: Likuiditas belum diambil (dist={_d:.2f}%). '
                f'Harga +{_d:.2f}% di atas dyn_Buy_Liq ${dyn_buy_liq:.4f} '
                f'(SwingLow(20): ${swing_low_20:.4f}). Tunggu price action menuju zona liq.'
            )
            gate_L['status'] = 'BLOCKED'

    if not has_funding:
        gate_L['gates']['L3'] = ('PASS', 'Funding_Rate tidak tersedia — skip')
    elif funding_val <= 0.0003:
        gate_L['gates']['L3'] = ('PASS', f'Funding {funding_val:.5f} ≤ +0.0003 — netral/negatif')
    else:
        gate_L['gates']['L3'] = ('FAIL', f'❌ GATE L3: Funding positif tinggi ({funding_val:.5f}). Tunggu funding ≤ +0.0003.')
        gate_L['status'] = 'BLOCKED'

    # ── [IMPR. 1] OI LONG — Liquidation Hunter ────────────────
    def score_oi(v, oi_chg, rel_vol):
        # Skenario 1: Serok Bawah (Ritel Rekt, Institusi Beli)
        if oi_chg < -10 and rel_vol > 50: return 3   # 15 Poin
        # Skenario 2: Tren Naik Kuat
        if v > 30: return 3                           # 15 Poin
        if v >= 5: return 2                           # 10 Poin
        if v >= -20: return 1                         #  5 Poin
        return 0

    _liq_hunter_triggered = bool(oi_change < -10 and F > 50)

    def score_vol(v):
        if v > 70: return 3
        if v >= 20: return 2
        if v >= -10: return 1
        return 0

    def score_atr_scoring(h):
        if atr_score_sweet_lo <= h <= atr_score_sweet_hi: return 3
        if (atr_score_t2_lo <= h < atr_score_sweet_lo) or (atr_score_sweet_hi < h <= atr_score_t2_hi): return 2
        if (atr_score_t1_lo <= h < atr_score_t2_lo) or (atr_score_t2_hi < h <= atr_score_t1_hi): return 1
        return 0

    s1 = score_oi(C_final, oi_change, F)
    s2 = score_vol(F_final)
    s3 = 2 if G < 49 else (1 if G <= 52 else 0)
    s4 = score_atr_scoring(H)
    s5 = 3 if cvd_div_bull else (2 if K > 1 else (1 if K >= 0 else 0))
    s6 = 3 if L < -3 else (2 if L < -1.5 else (1 if L < -0.5 else 0))
    s7 = 3 if M < -4 else (2 if M < -2 else (1 if M < 0 else 0))
    s8 = 3 if N < -7 else (2 if N < -3 else (1 if N < 0 else 0))

    # [FIX 3] RSI V-Shape Memory + threshold diturunkan agar tidak terlalu ketat
    if rsi_vshaped_long and O_rsi <= 35:
        s9 = 3  # V-Shape konfirmasi — beri skor penuh
    else:
        s9 = 3 if O_rsi < 35 else (2 if O_rsi < 50 else (1 if O_rsi < 60 else 0))  # [FIX 3] was <25/<40/<55

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

    # [IMPR. 3] Karet Gelang bonus diterapkan ke ADJ_L
    ADJ_L = round(ADJ_L + _karet_gelang_bonus, 1)

    # ── [P4] StochRSI Gatekeeper
    stoch_gatekeeper_ok   = False
    stoch_gatekeeper_skip = False
    stoch_gatekeeper_reason = ""
    stoch_bonus_points    = 0

    if not cvd_div_bull and has_stoch:
        _stoch_rising = (stoch_k_prev is not None and stoch_k > stoch_k_prev)
        _stoch_ok = bool((stoch_k < 20) and _stoch_rising and (stoch_k < stoch_d))
        if _stoch_ok:
            stoch_gatekeeper_ok = True
            stoch_gatekeeper_reason = f"StochRSI OK: K={stoch_k:.1f}<20, naik dari {stoch_k_prev:.1f}, K<D({stoch_d:.1f})"
            if stoch_cross_up and stoch_k < 20:
                stoch_bonus_points = 2
                stoch_gatekeeper_reason += " ✅ CROSS UP di <20 (+2 bonus)"
        else:
            stoch_gatekeeper_skip = True
            _reasons = []
            if stoch_k >= 20:    _reasons.append(f"K={stoch_k:.1f}≥20")
            if not _stoch_rising: _reasons.append(f"K tidak naik ({stoch_k:.1f}≤prev {f'{stoch_k_prev:.1f}' if stoch_k_prev is not None else 'N/A'})")
            if stoch_k >= stoch_d: _reasons.append(f"K({stoch_k:.1f})≥D({stoch_d:.1f})")
            stoch_gatekeeper_reason = f"❌ StochRSI GAGAL (tanpa CVD div): {', '.join(_reasons)}"
    elif cvd_div_bull:
        stoch_gatekeeper_ok = True
        stoch_gatekeeper_reason = "StochRSI bypass: CVD divergence bull terdeteksi"
        if has_stoch and stoch_cross_up and stoch_k < 20:
            stoch_bonus_points = 2
            stoch_gatekeeper_reason += " ✅ CROSS UP di <20 (+2 bonus)"
    else:
        stoch_gatekeeper_ok = True
        stoch_gatekeeper_reason = "StochRSI data tidak tersedia — gatekeeper di-skip"

    ADJ_L = round(ADJ_L + stoch_bonus_points, 1)

    # ── KEPUTUSAN 
    def get_tier(adj):
        if adj >= _thr_full: return "FULL SIZE ENTRY", "FULL"
        if adj >= _thr_half: return "HALF SIZE ENTRY", "HALF"
        if adj >= _thr_wait: return "WAIT & MONITOR", "WAIT"
        return "SKIP", "SKIP"

    dec_L, code_L = get_tier(ADJ_L)

    if aging_status == "AGING":
        dec_L += " (⚠️ Posisi aging 8–14 hari)"
    elif aging_status == "STALE":
        tier_order = ["FULL", "HALF", "WAIT", "SKIP"]
        for orig, nxt in zip(tier_order, tier_order[1:]):
            if code_L == orig:
                code_L = nxt
                dec_L, _ = get_tier(max(0, ADJ_L - 18))
                dec_L += " (❌ Posisi stale >14 hari)"
                break

    # ── Gate overrides
    if gate_L['status'] == 'BLOCKED':
        dec_L, code_L = 'SKIP', 'SKIP'
    if session_block:
        dec_L, code_L = 'SKIP', 'SKIP'
    elif session_block_type == 'CONDITIONAL_NY' and ADJ_L < 40:
        dec_L, code_L = 'SKIP', 'SKIP'
    elif session_block_type == 'CONDITIONAL_OTHER' and ADJ_L < 45:
        dec_L, code_L = 'SKIP', 'SKIP'
    
    stoch_gate_override  = ""
    stoch_penalty_applied = False
    stoch_penalty_pts     = 0
    STOCH_PENALTY = 8  # [FIX 4] poin penalti jika StochRSI tidak konfirmasi
    if stoch_gatekeeper_skip:
        ADJ_L = round(ADJ_L - STOCH_PENALTY, 1)
        stoch_penalty_applied = True
        stoch_penalty_pts     = STOCH_PENALTY
        stoch_gate_override   = f"⚠️ StochRSI penalty -{STOCH_PENALTY}pts: {stoch_gatekeeper_reason}"
        # [FIX 4] Re-evaluate tier setelah penalti (bukan hard SKIP)
        dec_L, code_L = get_tier(ADJ_L)
        if gate_L['status'] == 'BLOCKED':
            dec_L, code_L = 'SKIP', 'SKIP'
        elif session_block:
            dec_L, code_L = 'SKIP', 'SKIP'
        elif session_block_type == 'CONDITIONAL_NY' and ADJ_L < 40:
            dec_L, code_L = 'SKIP', 'SKIP'
        elif session_block_type == 'CONDITIONAL_OTHER' and ADJ_L < 45:
            dec_L, code_L = 'SKIP', 'SKIP'

    _req_score_full_L = _thr_full + 5
    _m_slope = macro_slope if macro_slope is not None else 0.0

    if macro_trend == 'DOWNTREND':
        if ADJ_L >= _req_score_full_L:
            gate_L['gates']['L4'] = ('PASS', f'✅ GATE L4: Skor long sangat kuat ({ADJ_L:.1f} ≥ {_req_score_full_L}) — izinkan long melawan DOWNTREND secara penuh.')
        elif ADJ_L >= _thr_half:
            gate_L['gates']['L4'] = ('WARN', f'⚠️ GATE L4: Skor cukup untuk entry HALF SIZE melawan DOWNTREND ({ADJ_L:.1f} ≥ {_thr_half}, slope={_m_slope:.2f}%). Maksimal HALF SIZE ENTRY.')
            if gate_L['status'] == 'CLEAR': gate_L['status'] = 'WARNING'
            if code_L not in ('SKIP',): dec_L, code_L = 'HALF SIZE ENTRY', 'HALF'
        else:
            gate_L['gates']['L4'] = ('FAIL', f'❌ GATE L4: Skor long terlalu lemah ({ADJ_L:.1f} < {_thr_half}) untuk long melawan DOWNTREND (slope={_m_slope:.2f}%). SKIP.')
            gate_L['status'] = 'BLOCKED'
            dec_L, code_L = 'SKIP', 'SKIP'
    elif macro_trend == 'SIDEWAYS':
        gate_L['gates']['L4'] = ('WARN', f'⚠️ Tren dominan SIDEWAYS (slope={_m_slope:.2f}%)')
        if gate_L['status'] == 'CLEAR': gate_L['status'] = 'WARNING'
    elif macro_trend == 'UPTREND':
        gate_L['gates']['L4'] = ('PASS', f'Tren dominan UPTREND (slope={_m_slope:.2f}%) — mendukung long')
    else:
        gate_L['gates']['L4'] = ('PASS', 'Tren dominan tidak diketahui — skip L4')

    # SL CANDIDATES
    sl_cands_L = []
    buy_liq = _last_val(last, 'Buy_Liq')
    if buy_liq and buy_liq > 0 and buy_liq * 0.997 < close_price: sl_cands_L.append((buy_liq * 0.997, "Likuiditas Buy"))
    fvg_db = _last_val(last, 'FVG_Down_Bottom')
    if fvg_db and fvg_db > 0 and fvg_db * 0.998 < close_price: sl_cands_L.append((fvg_db * 0.998, "FVG Bearish Bottom"))
    if len(df) >= 3:
        sw3 = min(safe_float(df.iloc[-3].get('Low', 1e18)), safe_float(df.iloc[-2].get('Low', 1e18)), low_price) * 0.998
        if sw3 > 0 and sw3 < close_price: sl_cands_L.append((sw3, "Swing Low 3C"))
    fib786 = _last_val(last, 'Fib_0.786')
    if fib786 and fib786 > 0 and fib786 * 0.998 < close_price: sl_cands_L.append((fib786 * 0.998, "Fibonacci 0.786"))
    val_lev = _last_val(last, 'VAL')
    if val_lev and val_lev > 0 and val_lev * 0.998 < close_price: sl_cands_L.append((val_lev * 0.998, "Value Area Low"))
    pdl_lev = _last_val(last, 'PDL')
    if pdl_lev and pdl_lev > 0 and pdl_lev * 0.998 < close_price: sl_cands_L.append((pdl_lev * 0.998, "Prev Day Low"))

    if sl_atr1_L < close_price: sl_cands_L.append((sl_atr1_L, "ATR ×1.0 (fallback)"))
    if sl_atr15_L < close_price: sl_cands_L.append((sl_atr15_L, "ATR ×1.5 (fallback)"))
    if sl_atr2_L < close_price: sl_cands_L.append((sl_atr2_L, "ATR ×2.0 (fallback)"))
    sl_cands_L.sort(key=lambda x: x[0], reverse=True)

    # TP CANDIDATES
    min_tp_dist = atr * (1.0 * ATR_MULT)
    tp_pool_L = []
    for col, lbl in [('Sell_Liq','Likuiditas Jual'), ('FVG_Down_Top','FVG Bearish Top'),
                     ('FVG_Down_Bottom','FVG Bearish Bottom'), ('FVG_Up_Top','FVG Bullish Top'),
                     ('FVG_Up_Bottom','FVG Bullish Bottom'), ('OB_Price','Order Block'),
                     ('Fib_0.618','Fibonacci 0.618'), ('Fib_0.786','Fibonacci 0.786'),
                     ('POC','Point of Control'), ('VAH','Value Area High'),
                     ('PDH','Prev Day High'), ('PWH','Prev Week High')]:
        v = _last_val(last, col)
        if v and v > 0 and v > (close_price + min_tp_dist): tp_pool_L.append((v, lbl))
    for e_val, e_lbl in [(ema21, 'EMA 21'), (ema50, 'EMA 50'), (ema200, 'EMA 200')]:
        if e_val and e_val > (close_price + min_tp_dist): tp_pool_L.append((e_val, e_lbl))
    
    tp_pool_L = [(v, l) for v, l in tp_pool_L if v > (close_price + min_tp_dist)]
    tp_pool_L.sort(key=lambda x: x[0])
    seen = set(); tp_dedup_L = []
    for v, l in tp_pool_L:
        if l not in seen:
            seen.add(l)
            tp_dedup_L.append((v, l))
    tp_pool_L = tp_dedup_L

    _flat_L = get_atr_projections_long(entry_val, atr, ATR_MULT)
    tp1_L = tp_pool_L[0] if len(tp_pool_L) >= 1 else _flat_L[0]
    tp2_L = tp_pool_L[1] if len(tp_pool_L) >= 2 else _flat_L[1]
    tp3_L = tp_pool_L[2] if len(tp_pool_L) >= 3 else _flat_L[2]

    def select_sl_long(cands, tp1_val):
        for price, label in cands:
            denom = close_price - price
            if denom > 0:
                rr = (tp1_val - close_price) / denom
                if rr >= 2.0:
                    return price, label
        return sl_atr1_L, "ATR ×1.0 (fallback — no structure)"

    sl_struct_L, sl_label_L = select_sl_long(sl_cands_L, tp1_L[0])
    if sl_struct_L > sl_atr2_L:
        sl_struct_L = sl_atr2_L
        sl_label_L  = f"{sl_label_L} → SAFE SL (ATR×2)"

    def dist_pct(target):
        return round((target - close_price) / close_price * 100, 4) if close_price else 0.0

    def rr_l(tp):
        d = close_price - sl_struct_L
        return round((tp - close_price) / d, 2) if d > 0 else 0.0

    rr1_L, rr2_L, rr3_L = rr_l(tp1_L[0]), rr_l(tp2_L[0]), rr_l(tp3_L[0])
    _dist_tp1_L = (tp1_L[0] - close_price) / close_price * 100 if close_price else 0.0

    if code_L not in ('SKIP',) and _dist_tp1_L < 2.0:
        dec_L, code_L = 'SKIP', 'SKIP'
        gate_L['gates']['L5_TP1'] = ('FAIL', f'❌ HYBRID Gate TP1: Jarak TP1 LONG terlalu dekat ({_dist_tp1_L:.2f}% < 2.0%) — potensi profit tidak layak.')
        gate_L['status'] = 'BLOCKED'

    def _gate_summary(gate: dict) -> str:
        parts = []
        for gk, (status, msg) in gate['gates'].items():
            if status == 'FAIL':  parts.append(f"{gk}:GAGAL")
            elif status == 'WARN': parts.append(f"{gk}:WARN")
        return ', '.join(parts) if parts else 'semua lolos'

    def _top_driver(scores: dict) -> str:
        best = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)
        return ', '.join(f"{k}({v[0]}/{v[1]})" for k, v in best[:2])
        
    def _top_blocker(scores: dict, is_long: bool) -> str:
        worst = sorted(scores.items(), key=lambda x: x[1][0])
        return ', '.join(f"{k}({v[0]}/{v[1]})" for k, v in worst[:2])

    def _stoch_desc() -> str:
        if not has_stoch: return "StochRSI tidak tersedia"
        sk_r, sd_r = round(stoch_k, 1), round(stoch_d, 1)
        cross = " [CROSS UP ✅]" if stoch_cross_up else (" [CROSS DOWN ❌]" if ctx['stoch_cross_down'] else "")
        return f"StochRSI K={sk_r} D={sd_r}{cross}"

    vol_dir = "spike" if F_final > 20 else ("normal" if F_final >= -10 else "turun")
    vol_desc = f"{vol_dir} (MA20:{ctx['F']:+.1f}% · MA100:{ctx['F2']:+.1f}% · avg:{F_final:+.1f}%)"
    cvd_desc = "bullish divergence ✅" if cvd_div_bull else ("bearish divergence ❌" if ctx['cvd_div_bear'] else f"norm={K:+.1f}%")

    narrative_L = {
        'kondisi': (
            f"[GATE LONG: {gate_L['status']}] {_gate_summary(gate_L)}. "
            f"Sesi {session_label} (×{SESSION_MULT}). Vol {vol_desc}. "
            f"Ref={'Close' if is_active else 'Low'} ${(close_price if is_active else low_price):.4f} vs "
            f"EMA21 {L:+.2f}% (${ema21:.4f}), EMA50 {M:+.2f}% (${ema50:.4f}), EMA200 {N:+.2f}% (${ema200:.4f}). "
            f"CVD: {cvd_desc} (I={I_cvd:.0f}, J={J_cvd:.0f}). "
            f"RSI_6={O_rsi:.1f}. {_stoch_desc()}. "
            f"ATR={H:.2f}% | ATR_MULT={ATR_MULT} ({atr_mult_reason}) | sweet spot {sweet_lo:.1f}%–{sweet_hi:.1f}%."
            + (f" | 🎯 {rsi_vshaped_note}" if rsi_vshaped_long else "")
            + (f" | {_karet_gelang_note}" if _karet_gelang_triggered else "")
            + (f" | 🩸 LIQUIDATION HUNTER: OI={C_final:.1f}% + Vol={F_final:.1f}% → OI MAX SCORE" if _liq_hunter_triggered else "")
        ),
        'keputusan': (
            f"RAW={RAW_L} → ADJ={ADJ_L} (×{SESSION_MULT}"
            + (f"+{_karet_gelang_bonus}pts KaretGelang" if _karet_gelang_triggered else "")
            + f") → {dec_L}"
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
            f"RSI<{'25 (saat ini '+str(round(O_rsi,1))+')' if s9<3 else 'OK'}"
            + (f" [V-Shape RSI aktif ✅]" if rsi_vshaped_long else "")
            + f", StochRSI cross up dari <20. "
            f"Level kunci: Close ${close_price:.4f}, EMA21 ${ema21:.4f}, EMA50 ${ema50:.4f}. "
            f"Sesi optimal: London ({session_label} saat ini). "
            + (f"Posisi {aging_status}: pertimbangkan exit dan re-entry setelah kondisi Tier 1 kembali positif." if aging_status in ('AGING','STALE') else "")
        ),
    }

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
        if O_rsi < 35:  return ('✅', f'{O_rsi:.1f}', 'Oversold')                 # [FIX 3] was <25
        if O_rsi < 50:  return ('⚠️', f'{O_rsi:.1f}', 'Mendekati oversold')      # [FIX 3] was <40
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

    return {
        'raw_score': RAW_L, 'adj_score': ADJ_L, 'dec': dec_L, 'code': code_L,
        'gate': gate_L, 'scores': scores_L, 'narrative': narrative_L, 'ppi': ppi_long,
        'sl_cands': sl_cands_L, 'tp_candidates': tp_pool_L,
        'sl_struct': sl_struct_L, 'sl_label': sl_label_L,
        'tp1': tp1_L, 'tp2': tp2_L, 'tp3': tp3_L,
        'rr1': rr1_L, 'rr2': rr2_L, 'rr3': rr3_L,
        'stoch_gatekeeper_ok': stoch_gatekeeper_ok, 'stoch_gatekeeper_skip': stoch_gatekeeper_skip,
        'stoch_gatekeeper_reason': stoch_gatekeeper_reason, 'stoch_bonus_points': stoch_bonus_points,
        'stoch_gate_override': stoch_gate_override,
        'stoch_penalty_applied': stoch_penalty_applied,  # [FIX 4]
        'stoch_penalty_pts': stoch_penalty_pts,           # [FIX 4]
        'liq_hunter_triggered': _liq_hunter_triggered,
        'rsi_vshaped_long': rsi_vshaped_long, 'rsi_vshaped_note': rsi_vshaped_note,
        'karet_gelang_triggered': _karet_gelang_triggered, 'karet_gelang_bonus': _karet_gelang_bonus,
        'karet_gelang_note': _karet_gelang_note,
    }
