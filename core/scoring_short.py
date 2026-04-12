import pandas as pd
from core.helpers import _last_val, safe_float
from core.levels import get_atr_projections_short

def calculate_short_score(df: pd.DataFrame, ctx: dict) -> dict:
    last           = ctx['last']
    close_price    = ctx['close_price']
    low_price      = ctx['low_price']
    high_price     = ctx['high_price']
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
    cvd_div_bear   = ctx['cvd_div_bear']
    Lp             = ctx['Lp']
    Mp             = ctx['Mp']
    Np             = ctx['Np']
    O_rsi          = ctx['O_rsi']
    has_bos        = ctx['has_bos']
    bos_val        = ctx['bos_val']
    has_funding    = ctx['has_funding']
    funding_val    = ctx['funding_val']
    has_sell_liq   = ctx['has_sell_liq']
    sell_liq_val   = ctx['sell_liq_val']
    dyn_sell_liq   = ctx['dyn_sell_liq']
    has_dyn_sell_liq = ctx['has_dyn_sell_liq']
    dist_to_sell_liq = ctx['dist_to_sell_liq']
    swing_high_20  = ctx['swing_high_20']
    has_stoch      = ctx['has_stoch']
    stoch_k        = ctx['stoch_k']
    stoch_d        = ctx['stoch_d']
    stoch_cross_down = ctx['stoch_cross_down']
    atr            = ctx['atr']
    ATR_MULT       = ctx['ATR_MULT']
    atr_mult_reason= ctx['atr_mult_reason']
    sl_atr1_S      = ctx['sl_atr1_S']
    sl_atr15_S     = ctx['sl_atr15_S']
    sl_atr2_S      = ctx['sl_atr2_S']
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

    # ── Gate SHORT ──────────────────────────────────────────────
    gate_S = {'status': 'CLEAR', 'gates': {}}

    if not has_bos:
        gate_S['gates']['S1'] = ('PASS', 'BOS tidak tersedia — skip')
    elif bos_val != 1:
        gate_S['gates']['S1'] = ('PASS', f'BOS={bos_val} — struktur netral/bearish')
    elif cvd_div_bear and O_rsi > 75 and has_funding and funding_val >= 0:
        gate_S['gates']['S1'] = ('PASS', 'BOS=+1 tapi exception: CVD div bear + RSI>75 + funding≥0')
    else:
        gate_S['gates']['S1'] = ('FAIL', '❌ GATE S1: Struktur bullish aktif (BOS=+1). Tunggu BOS flip ke 0/−1.')
        gate_S['status'] = 'BLOCKED'

    if not has_dyn_sell_liq:
        if not has_sell_liq:
            gate_S['gates']['S2'] = ('PASS', 'Dynamic Sell_Liq tidak dapat dihitung (data High kurang) — skip')
        elif close_price >= sell_liq_val * 0.995:
            gate_S['gates']['S2'] = ('PASS', f'[Statis] Harga ≥ Sell_Liq×0.995 — sweep sudah terjadi')
        elif close_price >= sell_liq_val * 0.980:
            gate_S['gates']['S2'] = ('WARN', f'⚠️ [Statis] Harga dalam 2% di bawah Sell_Liq ${sell_liq_val:.4f} — mendekati sweep')
            if gate_S['status'] == 'CLEAR': gate_S['status'] = 'WARNING'
        else:
            gate_S['gates']['S2'] = ('FAIL', f'❌ GATE S2 [Statis]: Sell liquidity belum diambil (${sell_liq_val:.4f}).')
            gate_S['status'] = 'BLOCKED'
    else:
        _ds = dist_to_sell_liq
        if _ds < 1.0:
            gate_S['gates']['S2'] = (
                'SKIP',
                f'⚡ GATE S2: Harga terlalu dekat dyn_Sell_Liq (dist={_ds:.2f}%). '
                f'Level: ${dyn_sell_liq:.4f} | SwingHigh(20): ${swing_high_20:.4f}. '
                f'Kemungkinan sedang tersapu — TUNGGU konfirmasi reversal.'
            )
            if gate_S['status'] == 'CLEAR': gate_S['status'] = 'WARNING'
        elif _ds <= 5.0:
            gate_S['gates']['S2'] = (
                'PASS',
                f'✅ GATE S2: Sweet Spot (dist={_ds:.2f}%). '
                f'Harga cukup dekat dengan dyn_Sell_Liq ${dyn_sell_liq:.4f} — '
                f'likuiditas hampir/sudah diambil. SwingHigh(20): ${swing_high_20:.4f}.'
            )
        elif _ds <= 10.0:
            gate_S['gates']['S2'] = (
                'WARN',
                f'⚠️ GATE S2: Warning Zone (dist={_ds:.2f}%). '
                f'Harga cukup jauh dari dyn_Sell_Liq ${dyn_sell_liq:.4f}. '
                f'Tunggu harga lebih dekat ke SwingHigh(20) ${swing_high_20:.4f}.'
            )
            if gate_S['status'] == 'CLEAR': gate_S['status'] = 'WARNING'
        else:
            gate_S['gates']['S2'] = (
                'FAIL',
                f'❌ GATE S2: Likuiditas jual belum diambil (dist={_ds:.2f}%). '
                f'Harga {_ds:.2f}% di bawah dyn_Sell_Liq ${dyn_sell_liq:.4f} '
                f'(SwingHigh(20): ${swing_high_20:.4f}). Tunggu price action menuju zona liq.'
            )
            gate_S['status'] = 'BLOCKED'

    if not has_funding:
        gate_S['gates']['S3'] = ('PASS', 'Funding_Rate tidak tersedia — skip')
    elif funding_val >= -0.0003:
        gate_S['gates']['S3'] = ('PASS', f'Funding {funding_val:.5f} ≥ −0.0003 — normal')
    else:
        gate_S['gates']['S3'] = ('FAIL', f'❌ GATE S3: Funding sangat negatif ({funding_val:.5f}) — short squeeze risk.')
        gate_S['status'] = 'BLOCKED'

    # ── [IMPR. 2] RSI V-Shape Memory: 2-candle lookback ──────
    rsi_vshaped_short = False
    rsi_vshaped_note_s = ""
    if len(df) >= 3:
        rsi_1ago = df.iloc[-2].get('RSI_6', None)
        rsi_2ago = df.iloc[-3].get('RSI_6', None)
        if rsi_1ago is not None and rsi_2ago is not None:
            if float(rsi_1ago) > 80 or float(rsi_2ago) > 80:
                rsi_vshaped_short = True
                rsi_vshaped_note_s = f"V-Shape RSI SHORT: candle-1={float(rsi_1ago):.1f}, candle-2={float(rsi_2ago):.1f} (salah satu >80)"

    # ── Scoring ───────────────────────────────────────────────
    # [IMPR. 1] Liquidation Hunter: OI turun drastis + volume meledak = poin maksimal
    def score_oi(v):
        if C_final < -10 and F_final > 50:
            return 3  # Liquidation Hunter: max score — panic selling + volume spike
        if v > 30: return 3
        if v >= 5: return 2
        if v >= -20: return 1
        return 0

    _liq_hunter_triggered_s = bool(C_final < -10 and F_final > 50)

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

    s1 = score_oi(C_final)
    s2 = score_vol(F_final)
    s3s = 2 if G > 53 else (1 if G >= 51 else 0)
    s4 = score_atr_scoring(H)
    s5s = 3 if cvd_div_bear else (2 if K < -1 else (1 if K <= 0 else 0))
    s6s = 3 if Lp > 5 else (2 if Lp >= 3 else (1 if Lp >= 1.5 else 0))
    s7s = 3 if Mp > 6 else (2 if Mp >= 4 else (1 if Mp >= 2 else 0))
    s8s = 3 if Np > 10 else (2 if Np >= 5 else (1 if Np >= 2 else 0))

    # [IMPR. 2] RSI V-Shape Memory: izinkan entry jika RSI sekarang >= 65 DAN ada lookback >80 di 2 candle sebelumnya
    if rsi_vshaped_short and O_rsi >= 65:
        s9s = 3  # V-Shape konfirmasi — beri skor penuh
    else:
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

    # [IMPR. 3] Elastisitas "Karet Gelang" — Mean Reversion Bonus SHORT
    # Jika harga sudah sangat jauh di atas EMA21 (> +6%), beri +5 poin darurat
    _karet_gelang_triggered_s = bool(Lp > 6.0)
    _karet_gelang_bonus_s = 0
    _karet_gelang_note_s = ""
    if _karet_gelang_triggered_s:
        _karet_gelang_bonus_s = 5
        _karet_gelang_note_s = f"⚡ KARET GELANG SHORT: Harga {Lp:.2f}% di atas EMA21 (>+6%). +5 bonus darurat mean reversion."
        ADJ_S = round(ADJ_S + _karet_gelang_bonus_s, 1)

    # ── KEPUTUSAN
    def get_tier(adj):
        if adj >= _thr_full: return "FULL SIZE ENTRY", "FULL"
        if adj >= _thr_half: return "HALF SIZE ENTRY", "HALF"
        if adj >= _thr_wait: return "WAIT & MONITOR", "WAIT"
        return "SKIP", "SKIP"

    dec_S, code_S = get_tier(ADJ_S)

    if aging_status == "AGING":
        dec_S += " (⚠️ Posisi aging 8–14 hari)"
    elif aging_status == "STALE":
        tier_order = ["FULL", "HALF", "WAIT", "SKIP"]
        for orig, nxt in zip(tier_order, tier_order[1:]):
            if code_S == orig:
                code_S = nxt
                dec_S, _ = get_tier(max(0, ADJ_S - 18))
                dec_S += " (❌ Posisi stale >14 hari)"
                break

    # ── Gate overrides
    if gate_S['status'] == 'BLOCKED':
        dec_S, code_S = 'SKIP', 'SKIP'
    if session_block:
        dec_S, code_S = 'SKIP', 'SKIP'
    elif session_block_type == 'CONDITIONAL_NY' and ADJ_S < 40:
        dec_S, code_S = 'SKIP', 'SKIP'
    elif session_block_type == 'CONDITIONAL_OTHER' and ADJ_S < 45:
        dec_S, code_S = 'SKIP', 'SKIP'

    _req_score_full_S = _thr_full + 5
    _m_slope = macro_slope if macro_slope is not None else 0.0

    if macro_trend == 'UPTREND':
        if ADJ_S >= _req_score_full_S:
            gate_S['gates']['S4'] = ('PASS', f'✅ GATE S4: Skor short sangat kuat ({ADJ_S:.1f} ≥ {_req_score_full_S}) — izinkan short melawan UPTREND secara penuh.')
        elif ADJ_S >= _thr_half:
            gate_S['gates']['S4'] = ('WARN', f'⚠️ GATE S4: Skor cukup untuk entry HALF SIZE melawan UPTREND ({ADJ_S:.1f} ≥ {_thr_half}, slope={_m_slope:.2f}%). Maksimal HALF SIZE ENTRY.')
            if gate_S['status'] == 'CLEAR': gate_S['status'] = 'WARNING'
            if code_S not in ('SKIP',): dec_S, code_S = 'HALF SIZE ENTRY', 'HALF'
        else:
            gate_S['gates']['S4'] = ('FAIL', f'❌ GATE S4: Skor short terlalu lemah ({ADJ_S:.1f} < {_thr_half}) untuk short melawan UPTREND (slope={_m_slope:.2f}%). SKIP.')
            gate_S['status'] = 'BLOCKED'
            dec_S, code_S = 'SKIP', 'SKIP'
    elif macro_trend == 'SIDEWAYS':
        gate_S['gates']['S4'] = ('WARN', f'⚠️ Tren dominan SIDEWAYS (slope={_m_slope:.2f}%)')
        if gate_S['status'] == 'CLEAR': gate_S['status'] = 'WARNING'
    elif macro_trend == 'DOWNTREND':
        gate_S['gates']['S4'] = ('PASS', f'Tren dominan DOWNTREND (slope={_m_slope:.2f}%) — mendukung short')
    else:
        gate_S['gates']['S4'] = ('PASS', 'Tren dominan tidak diketahui — skip S4')


    # SL CANDIDATES
    sl_cands_S = []
    sell_liq = _last_val(last, 'Sell_Liq')
    if sell_liq and sell_liq > 0 and sell_liq * 1.003 > close_price: sl_cands_S.append((sell_liq * 1.003, "Likuiditas Sell"))
    fvg_ut = _last_val(last, 'FVG_Up_Top')
    if fvg_ut and fvg_ut > 0 and fvg_ut * 1.002 > close_price: sl_cands_S.append((fvg_ut * 1.002, "FVG Bullish Top"))
    if len(df) >= 3:
        sw3h = max(safe_float(df.iloc[-3].get('High', 0)), safe_float(df.iloc[-2].get('High', 0)), high_price) * 1.002
        if sw3h > close_price: sl_cands_S.append((sw3h, "Swing High 3C"))
    fib618 = _last_val(last, 'Fib_0.618')
    if fib618 and fib618 > 0 and fib618 * 1.002 > close_price: sl_cands_S.append((fib618 * 1.002, "Fibonacci 0.618"))
    vah_lev = _last_val(last, 'VAH')
    if vah_lev and vah_lev > 0 and vah_lev * 1.002 > close_price: sl_cands_S.append((vah_lev * 1.002, "Value Area High"))
    pdh_lev = _last_val(last, 'PDH')
    if pdh_lev and pdh_lev > 0 and pdh_lev * 1.002 > close_price: sl_cands_S.append((pdh_lev * 1.002, "Prev Day High"))
    
    if sl_atr1_S > close_price: sl_cands_S.append((sl_atr1_S, "ATR ×1.0 (fallback)"))
    if sl_atr15_S > close_price: sl_cands_S.append((sl_atr15_S, "ATR ×1.5 (fallback)"))
    if sl_atr2_S > close_price: sl_cands_S.append((sl_atr2_S, "ATR ×2.0 (fallback)"))
    sl_cands_S.sort(key=lambda x: x[0])

    # TP CANDIDATES
    min_tp_dist = atr * (1.0 * ATR_MULT)
    tp_pool_S = []
    for col, lbl in [('Buy_Liq','Likuiditas Beli'), ('FVG_Up_Top','FVG Bullish Top'),
                     ('FVG_Up_Bottom','FVG Bullish Bottom'), ('FVG_Down_Top','FVG Bearish Top'),
                     ('FVG_Down_Bottom','FVG Bearish Bottom'), ('OB_Price','Order Block'),
                     ('Fib_0.786','Fibonacci 0.786'), ('Fib_0.618','Fibonacci 0.618'),
                     ('POC','Point of Control'), ('VAL','Value Area Low'),
                     ('PDL','Prev Day Low'), ('PWL','Prev Week Low')]:
        v = _last_val(last, col)
        if v and v > 0 and v < (close_price - min_tp_dist): tp_pool_S.append((v, lbl))
    for e_val, e_lbl in [(ema21, 'EMA 21'), (ema50, 'EMA 50'), (ema200, 'EMA 200')]:
        if e_val and e_val < (close_price - min_tp_dist): tp_pool_S.append((e_val, e_lbl))
    
    tp_pool_S = [(v, l) for v, l in tp_pool_S if v < (close_price - min_tp_dist)]
    tp_pool_S.sort(key=lambda x: x[0], reverse=True)
    seen = set(); tp_dedup_S = []
    for v, l in tp_pool_S:
        if l not in seen:
            seen.add(l)
            tp_dedup_S.append((v, l))
    tp_pool_S = tp_dedup_S

    _flat_S = get_atr_projections_short(entry_val, atr, ATR_MULT)
    tp1_S = tp_pool_S[0] if len(tp_pool_S) >= 1 else _flat_S[0]
    tp2_S = tp_pool_S[1] if len(tp_pool_S) >= 2 else _flat_S[1]
    tp3_S = tp_pool_S[2] if len(tp_pool_S) >= 3 else _flat_S[2]

    def select_sl_short(cands, tp1_val):
        for price, label in cands:
            denom = price - close_price
            if denom > 0:
                rr = (close_price - tp1_val) / denom
                if rr >= 2.0:
                    return price, label
        return sl_atr1_S, "ATR ×1.0 (fallback — no structure)"

    sl_struct_S, sl_label_S = select_sl_short(sl_cands_S, tp1_S[0])
    if sl_struct_S < sl_atr2_S:
        sl_struct_S = sl_atr2_S
        sl_label_S  = f"{sl_label_S} → SAFE SL (ATR×2)"

    def dist_pct(target):
        return round((target - close_price) / close_price * 100, 4) if close_price else 0.0

    def rr_s(tp):
        d = sl_struct_S - close_price
        return round((close_price - tp) / d, 2) if d > 0 else 0.0

    rr1_S, rr2_S, rr3_S = rr_s(tp1_S[0]), rr_s(tp2_S[0]), rr_s(tp3_S[0])
    _dist_tp1_S = (close_price - tp1_S[0]) / close_price * 100 if close_price else 0.0

    if code_S not in ('SKIP',) and _dist_tp1_S < 2.0:
        dec_S, code_S = 'SKIP', 'SKIP'
        gate_S['gates']['S5_TP1'] = ('FAIL', f'❌ HYBRID Gate TP1: Jarak TP1 SHORT terlalu dekat ({_dist_tp1_S:.2f}% < 2.0%) — potensi profit tidak layak.')
        gate_S['status'] = 'BLOCKED'

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
        cross = " [CROSS UP ✅]" if ctx['stoch_cross_up'] else (" [CROSS DOWN ❌]" if stoch_cross_down else "")
        return f"StochRSI K={sk_r} D={sd_r}{cross}"

    vol_dir = "spike" if F_final > 20 else ("normal" if F_final >= -10 else "turun")
    vol_desc = f"{vol_dir} (MA20:{ctx['F']:+.1f}% · MA100:{ctx['F2']:+.1f}% · avg:{F_final:+.1f}%)"
    cvd_desc = "bullish divergence ✅" if ctx['cvd_div_bull'] else ("bearish divergence ❌" if cvd_div_bear else f"norm={K:+.1f}%")

    narrative_S = {
        'kondisi': (
            f"[GATE SHORT: {gate_S['status']}] {_gate_summary(gate_S)}. "
            f"Sesi {session_label} (×{SESSION_MULT}). Vol {vol_desc}. "
            f"High ${high_price:.4f} vs "
            f"EMA21 {Lp:+.2f}% (${ema21:.4f}), EMA50 {Mp:+.2f}% (${ema50:.4f}), EMA200 {Np:+.2f}% (${ema200:.4f}). "
            f"CVD: {cvd_desc} (I={I_cvd:.0f}, J={J_cvd:.0f}). "
            f"RSI_6={O_rsi:.1f}. {_stoch_desc()}. "
            f"ATR={H:.2f}% | ATR_MULT={ATR_MULT} ({atr_mult_reason}) | sweet spot {sweet_lo:.1f}%–{sweet_hi:.1f}%."
            + (f" | 🎯 {rsi_vshaped_note_s}" if rsi_vshaped_short else "")
            + (f" | {_karet_gelang_note_s}" if _karet_gelang_triggered_s else "")
            + (f" | 🩸 LIQUIDATION HUNTER: OI={C_final:.1f}% + Vol={F_final:.1f}% → OI MAX SCORE" if _liq_hunter_triggered_s else "")
        ),
        'keputusan': (
            f"RAW={RAW_S} → ADJ={ADJ_S} (×{SESSION_MULT}"
            + (f"+{_karet_gelang_bonus_s}pts KaretGelang" if _karet_gelang_triggered_s else "")
            + f") → {dec_S}"
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
            f"RSI>{'75 (saat ini '+str(round(O_rsi,1))+')' if s9s<3 else 'OK'}"
            + (f" [V-Shape RSI aktif ✅]" if rsi_vshaped_short else "")
            + f", StochRSI cross down dari >80. "
            f"Level kunci: Close ${close_price:.4f}, EMA21 ${ema21:.4f}, EMA50 ${ema50:.4f}. "
            f"Sesi optimal: London/NY ({session_label} saat ini)."
        ),
    }

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

    return {
        'raw_score': RAW_S, 'adj_score': ADJ_S, 'dec': dec_S, 'code': code_S,
        'gate': gate_S, 'scores': scores_S, 'narrative': narrative_S, 'ppi': ppi_short,
        'sl_cands': sl_cands_S, 'tp_candidates': tp_pool_S,
        'sl_struct': sl_struct_S, 'sl_label': sl_label_S,
        'tp1': tp1_S, 'tp2': tp2_S, 'tp3': tp3_S,
        'rr1': rr1_S, 'rr2': rr2_S, 'rr3': rr3_S,
        'liq_hunter_triggered': _liq_hunter_triggered_s,
        'rsi_vshaped_short': rsi_vshaped_short, 'rsi_vshaped_note': rsi_vshaped_note_s,
        'karet_gelang_triggered': _karet_gelang_triggered_s, 'karet_gelang_bonus': _karet_gelang_bonus_s,
        'karet_gelang_note': _karet_gelang_note_s,
    }
