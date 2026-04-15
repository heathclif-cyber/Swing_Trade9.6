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
    # [FIX 4] Threshold SHORT terpisah — lebih ketat saat UPTREND
    _thr_full_S    = ctx.get('_thr_full_S', _thr_full)
    _thr_half_S    = ctx.get('_thr_half_S', _thr_half)
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

    # ── [IMPROVEMENT 3] SMT Divergence ──────────────────────────
    _smt_bear_valid   = ctx.get('smt_bear_valid',   False)
    _smt_bear_caution = ctx.get('smt_bear_caution', False)
    _smt_note         = ctx.get('smt_note', '')

    # ── [IMPROVEMENT 4] Market Leader Trap ──────────────────────
    _is_market_leader  = ctx.get('is_market_leader', False)
    _rs_extreme_count  = ctx.get('rs_extreme_count', 0)
    _rs_note           = ctx.get('rs_note', '')

    # ── [FIX LIKUIDASI] Rejection Candle: Bearish engulfing / upper wick dominan
    # Rejection candle = bukti bahwa perburuan likuiditas SUDAH SELESAI dan harga ditolak
    _last_open  = safe_float(df.iloc[-1].get('Open', close_price))
    _last_high  = safe_float(df.iloc[-1].get('High', high_price))
    _last_low   = safe_float(df.iloc[-1].get('Low', low_price))
    _body       = abs(close_price - _last_open)
    _upper_wick = _last_high - max(close_price, _last_open)
    _total_rng  = _last_high - _last_low if _last_high > _last_low else 0.001
    _bearish_candle    = close_price < _last_open
    _upper_wick_dom    = (_upper_wick / _total_rng) > 0.4  # wick > 40% dari range
    _rejection_candle_liq  = bool(_bearish_candle and _upper_wick_dom)

    # ── [IMPR. 3 + FIX 5] Karet Gelang: hitung bonus SEBELUM gate agar bisa bypass S1
    _is_prime_session = session_label.upper() in (
        'LONDON', 'NEW YORK', 'LONDON+NEW YORK', 'LONDON NEW YORK'
    )
    _karet_gelang_triggered_s = bool(
        dist_ema21_close > 6.0
        and _is_prime_session      # [FIX 5] hanya aktif di sesi prime
        and F > -10                # [FIX 5] volume tidak terlalu rendah
    )
    _karet_gelang_bonus_s     = 5 if _karet_gelang_triggered_s else 0
    if _karet_gelang_triggered_s:
        _karet_gelang_note_s = (
            f"⚡ KARET GELANG SHORT: Close {dist_ema21_close:.2f}% di atas EMA21 (>+6%). "
            f"+5 bonus darurat mean reversion. Sesi={session_label}."
        )
    elif dist_ema21_close > 6.0 and not _is_prime_session:
        _karet_gelang_note_s = (
            f"⚡ KARET GELANG SHORT tidak aktif: Close {dist_ema21_close:.2f}% di atas EMA21 (>+6%) "
            f"tapi sesi bukan prime (sesi={session_label}). Bonus diabaikan."
        )
    elif dist_ema21_close > 6.0 and F <= -10:
        _karet_gelang_note_s = (
            f"⚡ KARET GELANG SHORT tidak aktif: Close {dist_ema21_close:.2f}% di atas EMA21 (>+6%) "
            f"tapi volume terlalu rendah (F={F:.1f}% ≤ -10). Bonus diabaikan."
        )
    else:
        _karet_gelang_note_s = ""

    # ── [IMPR. 2] RSI V-Shape Memory (precomputed dari ctx) ───
    rsi_vshaped_short  = (O_rsi >= 65) and (O_rsi_1 > 80 or O_rsi_2 > 80)
    rsi_vshaped_note_s = (
        f"V-Shape RSI SHORT: candle-1={O_rsi_1:.1f}, candle-2={O_rsi_2:.1f} (salah satu >80)"
        if rsi_vshaped_short else ""
    )

    # ── [TAMBAHAN C] Rejection candle check untuk Gate S2 ──────────────
    _open_price  = safe_float(last.get('Open', close_price))
    _prev_high   = safe_float(df.iloc[-2].get('High', high_price)) if len(df) >= 2 else high_price
    _rejection_candle = bool(
        close_price < _open_price                    # candle terakhir bearish (close < open)
        and high_price < swing_high_20 * 0.995       # high tidak menyentuh swing high baru
        and close_price < _prev_high * 0.998         # close di bawah high candle sebelumnya
    ) if swing_high_20 is not None else False

    # ── Gate SHORT ──────────────────────────────────────────────
    gate_S = {'status': 'CLEAR', 'gates': {}}

    # [IMPR. 3] Karet Gelang ekstrem → bypass Gate S1 (BOS diabaikan)
    if _karet_gelang_triggered_s:
        gate_S['gates']['S1'] = ('PASS', f'BOS diabaikan — Karet Gelang Ekstrem (Close {dist_ema21_close:.2f}% vs EMA21 >+6%)')
    elif not has_bos:
        gate_S['gates']['S1'] = ('PASS', 'BOS tidak tersedia — skip')
    elif bos_val != 1:
        gate_S['gates']['S1'] = ('PASS', f'BOS={bos_val} — struktur netral/bearish')
    elif cvd_div_bear and (O_rsi > 75 or rsi_vshaped_short) and has_funding and funding_val >= 0:
        gate_S['gates']['S1'] = ('PASS', 'BOS=+1 exception: CVD div bear + RSI V-Shape/Overbought + funding≥0')
    else:
        gate_S['gates']['S1'] = ('FAIL', '❌ GATE S1: Struktur bullish aktif (BOS=+1). Tunggu BOS flip ke 0/−1.')
        gate_S['status'] = 'BLOCKED'

    if not has_dyn_sell_liq:
        if not has_sell_liq:
            gate_S['gates']['S2'] = ('PASS', 'Dynamic Sell_Liq tidak dapat dihitung (data High kurang) — skip')
        elif close_price >= sell_liq_val * 0.995:
            # [TAMBAHAN C] Syarat rejection candle juga untuk jalur statis
            if _rejection_candle:
                gate_S['gates']['S2'] = ('PASS',
                    f'[Statis] Harga ≥ Sell_Liq×0.995 + rejection candle terkonfirmasi — sweep sudah terjadi.')
            else:
                # [IMPROVEMENT 1] Ubah dari WARNING menjadi hard BLOCKED
                gate_S['gates']['S2'] = ('FAIL',
                    f'❌ GATE S2: Sweet Spot tapi BELUM ada rejection candle. '
                    f'Tunggu 1 candle konfirmasi penutupan merah.')
                gate_S['status'] = 'BLOCKED'
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
            # [TAMBAHAN C] Wajib ada rejection candle sebelum PASS di sweet spot
            if _rejection_candle:
                gate_S['gates']['S2'] = (
                    'PASS',
                    f'✅ GATE S2: Sweet Spot (dist={_ds:.2f}%) + rejection candle terkonfirmasi. '
                    f'Harga cukup dekat dyn_Sell_Liq ${dyn_sell_liq:.4f} dan ada penolakan bearish. '
                    f'SwingHigh(20): ${swing_high_20:.4f}.'
                )
            else:
                # [IMPROVEMENT 1] Ubah dari WARN menjadi hard BLOCKED
                gate_S['gates']['S2'] = (
                    'FAIL',
                    f'❌ GATE S2: Sweet Spot tapi BELUM ada rejection candle. '
                    f'Tunggu 1 candle konfirmasi penutupan merah.'
                )
                gate_S['status'] = 'BLOCKED'
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

    # ── [IMPROVEMENT 2] OI SHORT — Exhaustion vs Expansion (Fix Strength Trap) ───
    def _score_oi_trend_s(v, rsi_val, oi_chg, taker_buy_ratio):
        """
        [IMPROVEMENT 2] Pisahkan OI Exhaustion vs OI Expansion.
        Short Squeeze Risk terdeteksi bila OI naik deras + RSI OB + Taker Buy dominan.
        Skor 0 diberikan agar conflict penalty yang menghukum.
        """
        # Deteksi Short Squeeze Risk (Expansion)
        _squeeze_risk = bool(v > 15 and oi_chg > 3 and rsi_val > 70 and taker_buy_ratio > 55)
        if _squeeze_risk:
            return 0  # Skor 0, biarkan conflict penalty yang menghukum

        # OI Exhaustion
        if v > 20 and -2 <= oi_chg <= 2:   return 3
        if v > 10 and oi_chg < 0:           return 3
        if v > 5  and oi_chg < 2:           return 2
        if -5 <= v <= 5 and oi_chg < 0:    return 1
        if v < -5:                          return 0
        return 0

    def _score_oi_event_s(oi_chg):
        """Membaca event exhaustion buyer via oi_change."""
        if -5 <= oi_chg <= 0: return 3    # [FIX v2.0] Kelelahan beli: OI stagnan/turun tipis
        if oi_chg < -5: return 1          # [FIX v2.0] OI turun — hati-hati short squeeze
        return 0

    _oi_trend_score_s = _score_oi_trend_s(C_final, O_rsi, oi_change, G)
    _oi_event_score_s = _score_oi_event_s(oi_change)
    s1 = max(_oi_trend_score_s, _oi_event_score_s)  # [FIX v2.0] ambil sinyal terkuat

    # Flag squeeze risk untuk conflict penalty
    _squeeze_risk_active = bool(
        C_final > 15 and oi_change > 3 and O_rsi > 70 and G > 55
    )
    _liq_hunter_triggered_s = bool((C_final > 30 and O_rsi > 75) or (-5 <= oi_change <= 0))

    # ── Helper scoring functions (dikembalikan setelah refactor OI)
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

    s2 = score_vol(F_final)
    s3s = 2 if G > 53 else (1 if G >= 51 else 0)
    s4 = score_atr_scoring(H)
    s5s = 3 if cvd_div_bear else (2 if K < -1 else (1 if K <= 0 else 0))
    s6s = 3 if Lp > 5 else (2 if Lp >= 3 else (1 if Lp >= 1.5 else 0))
    s7s = 3 if Mp > 6 else (2 if Mp >= 4 else (1 if Mp >= 2 else 0))
    s8s = 3 if Np > 10 else (2 if Np >= 5 else (1 if Np >= 2 else 0))

    # [IMPR. 2] RSI V-Shape Memory: izinkan entry jika RSI ≥ 65 DAN lookback >80
    if rsi_vshaped_short and O_rsi >= 65:
        s9s = 3  # V-Shape konfirmasi — beri skor penuh
    else:
        s9s = 3 if O_rsi > 75 else (2 if O_rsi >= 60 else (1 if O_rsi >= 45 else 0))

    scores_S = {
        'OI':       (s1*4,  12, C_final, s1),  # [FIX v2.0] 5× → 4×
        'Vol':      (s2*3,   9, F_final, s2),  # [FIX v2.0] 4× → 3×
        'TakerBuy': (s3s*3,  6, G, s3s),       # [FIX v2.0] 4× → 3× (s3s max=2 → max=6)
        'ATR':      (s4*3,   9, H, s4),        # tetap
        'CVD':      (s5s*4, 12, K, s5s),       # [FIX v2.0] 3× → 4×
        'EMA21':    (s6s*3,  9, Lp, s6s),      # [FIX v2.0] 2× → 3×
        'EMA50':    (s7s*3,  9, Mp, s7s),      # [FIX v2.0] 2× → 3×
        'EMA200':   (s8s*2,  6, Np, s8s),      # [FIX v2.0] 1× → 2×
        'RSI':      (s9s*2,  6, O_rsi, s9s),   # [FIX v2.0] 1× → 2×
    }  # [FIX v2.0] Total max: 12+9+6+9+12+9+9+6+6 = 78 poin (was 71)
    RAW_S = sum(v[0] for v in scores_S.values())

    # ── [FIX v2.0] Conflict Penalty (SHORT) ───────────────────────────
    _conflict_penalties_s = []
    # Konflik 1: OI turun tapi CVD positif (bearish OI tapi flow masih beli)
    if C_final < -10 and K > 1:
        _conflict_penalties_s.append(('OI_vs_CVD', -5,
            f'OI={C_final:.1f}% turun tapi CVD={K:.1f}% positif'))
    # Konflik 2: RSI overbought tapi funding sangat negatif (short squeeze risk)
    if O_rsi > 70 and has_funding and funding_val < -0.0005:
        _conflict_penalties_s.append(('RSI_vs_Funding', -4,
            f'RSI={O_rsi:.1f} overbought tapi funding={funding_val:.5f} sangat negatif'))
    # Konflik 3: Harga di atas EMA21 tapi BOS bullish aktif
    if Lp > 2 and has_bos and bos_val == 1 and not _karet_gelang_triggered_s:
        _conflict_penalties_s.append(('EMA_vs_BOS', -3,
            f'Harga {Lp:.1f}% di atas EMA21 tapi BOS=+1 bullish'))
    # [TAMBAHAN B] Konflik 4: CVD masih naik tapi RSI baru saja turun dari overbought (false reversal risk)
    if K > 2 and O_rsi < 70 and O_rsi_1 > 75:
        _conflict_penalties_s.append(('CVD_vs_RSI_Drop', -5,
            f'CVD masih naik ({K:.1f}%) tapi RSI baru turun dari OB '
            f'({O_rsi_1:.1f}→{O_rsi:.1f}) — kemungkinan false reversal, bukan top'))
    # [FIX LIKUIDASI] Konflik 5: Hollow Pump Pattern
    # OI naik + Volume spike + CVD turun + harga belum berbalik = tanda manipulasi Market Maker
    if (C_final > 15 and F_final > 50 and K < -1
            and not _rejection_candle_liq
            and oi_change > 3):
        _conflict_penalties_s.append((
            'HOLLOW_PUMP_PATTERN', -10,
            f'OI={C_final:.1f}%↑ + Vol={F_final:.1f}%↑ + CVD={K:.1f}%↓ + '
            f'OI_change={oi_change:.1f}%↑ tanpa rejection candle. '
            f'Pola Hollow Pump terdeteksi — kemungkinan manipulasi Market Maker.'
        ))
    # [IMPROVEMENT 2] Konflik 6: Short Squeeze Risk (OI Expansion aktif)
    if _squeeze_risk_active:
        _conflict_penalties_s.append((
            'Short_Squeeze_Risk', -8,
            f'SQUEEZE RISK aktif (OI ekspansi + RSI OB + Taker Buy dominan): '
            f'OI={C_final:.1f}%↑ OI_chg={oi_change:.1f}% RSI={O_rsi:.1f} TakerBuy={G:.1f}%.'
        ))
    # [IMPROVEMENT 4] Konflik 7: Market Leader Trap
    if _is_market_leader:
        _conflict_penalties_s.append((
            'Market_Leader_Trap', -10,
            f'MARKET LEADER: Koin terkuat di sesi ini, sangat berbahaya untuk di-short. '
            f'({_rs_extreme_count}/3 extreme — {_rs_note})'
        ))
    _total_conflict_penalty_s = sum(p[1] for p in _conflict_penalties_s)
    RAW_S = RAW_S + _total_conflict_penalty_s  # [FIX v2.0] penalti ke RAW sebelum session mult
    # ── End Conflict Penalty ────────────────────────────────

    ADJ_S = round(RAW_S * SESSION_MULT, 1)  # [FIX v2.0] threshold ctx perlu ×1.098

    # [IMPR. 3] Karet Gelang bonus diterapkan ke ADJ_S
    ADJ_S = round(ADJ_S + _karet_gelang_bonus_s, 1)

    # [FIX 4] Gunakan threshold SHORT yang lebih ketat (_thr_full_S, _thr_half_S)
    def get_tier(adj):
        if adj >= _thr_full_S: return "FULL SIZE ENTRY", "FULL"
        if adj >= _thr_half_S: return "HALF SIZE ENTRY", "HALF"
        if adj >= _thr_wait:   return "WAIT & MONITOR", "WAIT"
        return "SKIP", "SKIP"

    dec_S, code_S = get_tier(ADJ_S)

    # ── [TAMBAHAN A + FIX LIKUIDASI] StochRSI Gatekeeper SHORT ─────────────────────────
    STOCH_PENALTY_S   = 6  # lebih ringan dari LONG (-8) karena short tidak punya bonus StochRSI
    
    _trend_still_bullish  = bool(O_rsi > 72 and dist_ema21_close > 3.0)
    _cvd_bypass_valid     = bool(cvd_div_bear and not _trend_still_bullish)
    _stoch_ok_short   = bool(has_stoch and stoch_k is not None and stoch_k > 70 and stoch_cross_down)
    _stoch_skip_short = bool(has_stoch and not _stoch_ok_short and not _cvd_bypass_valid)
    stoch_gate_override_s = ""

    if _stoch_skip_short:
        ADJ_S = round(ADJ_S - STOCH_PENALTY_S, 1)
        _stoch_k_str = f"{stoch_k:.1f}" if has_stoch and stoch_k is not None else "N/A"
        stoch_gate_override_s = (
            f"⚠️ StochRSI penalty -{STOCH_PENALTY_S}pts: "
            f"K={_stoch_k_str} tidak konfirmasi bearish "
            f"(butuh K>70 dan cross-down, tanpa valid CVD div bear)"
        )
        if cvd_div_bear and _trend_still_bullish:
            stoch_gate_override_s += f" [Bypass DITOLAK: tren masih bullish (RSI={O_rsi:.1f}, distEMA={dist_ema21_close:.1f}%)]"
        # Re-evaluate tier setelah penalty
        dec_S, code_S = get_tier(ADJ_S)
        # Terapkan ulang gate overrides
        if gate_S['status'] == 'BLOCKED':
            dec_S, code_S = 'SKIP', 'SKIP'
        if session_block:
            dec_S, code_S = 'SKIP', 'SKIP'
        elif session_block_type == 'CONDITIONAL_NY' and ADJ_S < 40:
            dec_S, code_S = 'SKIP', 'SKIP'
        elif session_block_type == 'CONDITIONAL_OTHER' and ADJ_S < 45:
            dec_S, code_S = 'SKIP', 'SKIP'
    elif _stoch_ok_short:
        stoch_gate_override_s = (
            f"✅ StochRSI konfirmasi SHORT: K={stoch_k:.1f} cross-down dari >70"
        )
    elif _cvd_bypass_valid:
        stoch_gate_override_s = "StochRSI bypass: CVD divergence bear valid (tren tidak terlalu bullish)"
    else:
        stoch_gate_override_s = "StochRSI tidak tersedia — gatekeeper di-skip"

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
        elif ADJ_S >= _thr_half_S:
            gate_S['gates']['S4'] = ('WARN', f'⚠️ GATE S4: Skor cukup untuk entry HALF SIZE melawan UPTREND ({ADJ_S:.1f} ≥ {_thr_half_S}, slope={_m_slope:.2f}%). Maksimal HALF SIZE ENTRY.')
            if gate_S['status'] == 'CLEAR': gate_S['status'] = 'WARNING'
            if code_S not in ('SKIP',): dec_S, code_S = 'HALF SIZE ENTRY', 'HALF'
        else:
            gate_S['gates']['S4'] = ('FAIL', f'❌ GATE S4: Skor short terlalu lemah ({ADJ_S:.1f} < {_thr_half_S}) untuk short melawan UPTREND (slope={_m_slope:.2f}%). SKIP.')
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

    # ── [FIX LIKUIDASI] Gate S5_LIQ: Hard block jika Liquidation Hunt sedang berlangsung ──
    _liq_hunt_active_now = bool(C_final > 20 and O_rsi > 70 and oi_change > 5)

    if _liq_hunt_active_now and not _rejection_candle_liq:
        gate_S['gates']['S5_LIQ'] = (
            'FAIL',
            f'❌ GATE S5 [LIQ HUNT ACTIVE]: OI={C_final:.1f}% naik + RSI={O_rsi:.1f} + '
            f'OI_change={oi_change:.1f}% — Liquidation Hunt SEDANG berlangsung. '
            f'Wajib tunggu rejection candle sebelum SHORT. '
            f'Entry sekarang = masuk ke mulut Market Maker.'
        )
        gate_S['status'] = 'BLOCKED'
        dec_S, code_S = 'SKIP', 'SKIP'
    elif _liq_hunt_active_now and _rejection_candle_liq:
        gate_S['gates']['S5_LIQ'] = (
            'PASS',
            f'✅ GATE S5 [LIQ HUNT]: Liquidation Hunt SELESAI + rejection candle terkonfirmasi. '
            f'Entry SHORT valid (OI={C_final:.1f}%, RSI={O_rsi:.1f}, OI_chg={oi_change:.1f}%).'
        )
    else:
        gate_S['gates']['S5_LIQ'] = ('PASS', f'Kondisi Liq Hunt tidak aktif (OI={C_final:.1f}%, RSI={O_rsi:.1f}, OI_chg={oi_change:.1f}%) — skip')

    # ── [IMPROVEMENT 3] Gate S5_SMT: SMT Divergence Filter ──────────────
    if _smt_bear_caution:
        gate_S['gates']['S5_SMT'] = (
            'FAIL',
            f'❌ GATE S5_SMT: BTC juga naik kuat, bukan speculative pump. '
            f'{_smt_note}. Short berisiko tinggi di tengah broad market rally.'
        )
        gate_S['status'] = 'BLOCKED'
        dec_S, code_S = 'SKIP', 'SKIP'
    elif _smt_bear_valid:
        gate_S['gates']['S5_SMT'] = (
            'PASS',
            f'✅ GATE S5_SMT: SMT Divergence valid — koin naik tapi BTC flat/lemah. '
            f'{_smt_note}. Sinyal speculative pump terkonfirmasi.'
        )
    else:
        gate_S['gates']['S5_SMT'] = (
            'PASS',
            f'Gate S5_SMT tidak aktif — {_smt_note}'
        )

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
        cross = " [CROSS UP ❌]" if ctx['stoch_cross_up'] else (" [CROSS DOWN ✅]" if stoch_cross_down else "")
        return f"StochRSI K={sk_r} D={sd_r}{cross}"

    vol_dir = "spike" if F_final > 20 else ("normal" if F_final >= -10 else "turun")
    vol_desc = f"{vol_dir} (MA20:{ctx['F']:+.1f}% · MA100:{ctx['F2']:+.1f}% · avg:{F_final:+.1f}%)"
    cvd_desc = "bullish divergence ❌" if ctx['cvd_div_bull'] else ("bearish divergence ✅" if cvd_div_bear else f"norm={K:+.1f}%")

    narrative_S = {
        'kondisi': (
            f"[GATE SHORT: {gate_S['status']}] {_gate_summary(gate_S)}. "
            f"Sesi {session_label} (×{SESSION_MULT}). Vol {vol_desc}. "
            f"High ${high_price:.4f} vs "
            f"EMA21 {Lp:+.2f}% (${ema21:.4f}), EMA50 {Mp:+.2f}% (${ema50:.4f}), EMA200 {Np:+.2f}% (${ema200:.4f}). "
            f"CVD: {cvd_desc} (I={I_cvd:.0f}, J={J_cvd:.0f}). "
            f"RSI_6={O_rsi:.1f} (prev={O_rsi_1:.1f}). {_stoch_desc()}. "
            f"ATR={H:.2f}% | ATR_MULT={ATR_MULT} ({atr_mult_reason}) | sweet spot {sweet_lo:.1f}%–{sweet_hi:.1f}%."
            + (f" | 🎯 {rsi_vshaped_note_s}" if rsi_vshaped_short else "")
            + (f" | {_karet_gelang_note_s}" if _karet_gelang_note_s else "")
            + (f" | 🩸 LIQUIDATION HUNTER: OI={C_final:.1f}% + Vol={F_final:.1f}% → OI MAX SCORE" if _liq_hunter_triggered_s else "")
            + (f" | [TAMBAHAN A] {stoch_gate_override_s}" if stoch_gate_override_s else "")
            + (f" | [TAMBAHAN C] Rejection candle: {'OK ✅' if _rejection_candle else 'BELUM ⚠️'}")
            + (f" | [SMT] {_smt_note}" + (' ⚠️ CAUTION' if _smt_bear_caution else (' ✅ VALID' if _smt_bear_valid else '')))
            + (f" | [RS] {'⚠️ MARKET LEADER ({_rs_extreme_count}/3)' if _is_market_leader else _rs_note}")
            + (f" | [SQUEEZE RISK] OI Ekspansi aktif — penalti -8pts" if _squeeze_risk_active else "")
        ),
        'keputusan': (
            f"RAW={RAW_S} → ADJ={ADJ_S} (×{SESSION_MULT}"
            + (f"+{_karet_gelang_bonus_s}pts KaretGelang" if _karet_gelang_triggered_s else "")
            + (f"-{STOCH_PENALTY_S}pts StochGK" if _stoch_skip_short else "")
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
            + f", StochRSI K>70 + cross-down (saat ini: {'OK' if _stoch_ok_short else 'BELUM'}). "
            f"Rejection candle {'OK ✅' if _rejection_candle else 'diperlukan ⚠️'}. "
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
        'oi_trend_score': _oi_trend_score_s,                     # [FIX v2.0]
        'oi_event_score': _oi_event_score_s,                     # [FIX v2.0]
        'conflict_penalties': _conflict_penalties_s,             # [FIX v2.0]
        'total_conflict_penalty': _total_conflict_penalty_s,     # [FIX v2.0]
        # [TAMBAHAN A] StochRSI Gatekeeper SHORT
        'stoch_gatekeeper_ok_s':     _stoch_ok_short,
        'stoch_gatekeeper_skip_s':   _stoch_skip_short,
        'stoch_gate_override_s':     stoch_gate_override_s,
        'stoch_penalty_applied_s':   _stoch_skip_short,
        'stoch_penalty_pts_s':       STOCH_PENALTY_S if _stoch_skip_short else 0,
        # [TAMBAHAN C] Rejection candle
        'rejection_candle':          _rejection_candle,
        # [FIX LIKUIDASI]
        'rejection_candle_liq':      _rejection_candle_liq,
        'liq_hunt_active':           _liq_hunt_active_now,
        'cvd_bypass_valid':          _cvd_bypass_valid,
        'trend_still_bullish':       _trend_still_bullish,
        # [IMPROVEMENT 2] OI Squeeze Risk
        'squeeze_risk_active':       _squeeze_risk_active,
        # [IMPROVEMENT 3] SMT Divergence
        'smt_bear_valid':            _smt_bear_valid,
        'smt_bear_caution':          _smt_bear_caution,
        'smt_note':                  _smt_note,
        # [IMPROVEMENT 4] Market Leader
        'is_market_leader':          _is_market_leader,
        'rs_extreme_count':          _rs_extreme_count,
        'rs_note':                   _rs_note,
    }
