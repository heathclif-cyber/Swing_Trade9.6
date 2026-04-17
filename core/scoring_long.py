import pandas as pd
from core.helpers import _last_val, safe_float
from core.levels import get_atr_projections_long

# ── [FIX P9.7 - PERBAIKAN 3] Leverage Mode & SL Cap ─────────────────────────
# Set LEVERAGE_MODE = True saat trading futures dengan leverage 3-5×
# MAX_SL_PCT: maksimal jarak SL dari entry (3.5% = 17.5% account loss pada leverage 5×)
LEVERAGE_MODE = True   # [FIX P9.7] Toggle proteksi modal untuk futures leverage
MAX_SL_PCT    = 0.035  # [FIX P9.7] Hard cap SL: 3.5% dari close_price

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
    # [FIX] Baca threshold LONG dinamis (dari MODE AGRESIF UPTREND), fallback ke default
    _thr_full      = ctx.get('_thr_full_L', _thr_full)
    _thr_half      = ctx.get('_thr_half_L', _thr_half)
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

    # ── [FIX LIKUIDASI] Confirmation Candle: Bullish + lower wick dominan
    # Confirmation candle = bukti bahwa perburuan likuiditas beli SUDAH SELESAI dan harga ditolak naik
    # Paralel dengan _rejection_candle_liq di SHORT (bearish + upper wick dominan)
    _last_open  = safe_float(df.iloc[-1].get('Open', close_price))
    _last_high  = safe_float(df.iloc[-1].get('High', close_price))
    _last_low   = safe_float(df.iloc[-1].get('Low', low_price))
    _prev_close = safe_float(df.iloc[-2].get('Close', close_price)) if len(df) >= 2 else close_price
    _body       = abs(close_price - _last_open)
    _lower_wick = min(close_price, _last_open) - _last_low
    _total_rng  = _last_high - _last_low if _last_high > _last_low else 0.001
    _bullish_candle    = close_price > _last_open           # candle naik
    _lower_wick_dom    = (_lower_wick / _total_rng) > 0.4  # lower wick > 40% dari range
    _close_above_prev  = close_price > _prev_close           # close di atas low candle sebelumnya
    _confirmation_candle_liq = bool(_bullish_candle and _lower_wick_dom and _close_above_prev)

    # ── [TAMBAHAN C] Confirmation candle check untuk Gate L2 ──────────────
    # Paralel dengan _rejection_candle (Gate S2 SHORT)
    _open_price  = safe_float(last.get('Open', close_price))
    _prev_low    = safe_float(df.iloc[-2].get('Low', low_price)) if len(df) >= 2 else low_price
    _confirmation_candle = bool(
        close_price > _open_price                      # candle terakhir bullish (close > open)
        and low_price > swing_low_20 * 1.005           # low tidak menyentuh swing low baru
        and close_price > _prev_low * 1.002            # close di atas low candle sebelumnya
    ) if swing_low_20 is not None else False

    # ── [FIX 5] Karet Gelang LONG: tambah session filter + volume minimum
    # Paralel dengan FIX 5 SHORT: hanya aktif di prime session + volume cukup
    _is_prime_session = session_label.upper() in (
        'LONDON', 'NEW YORK', 'LONDON+NEW YORK', 'LONDON NEW YORK'
    )
    _karet_gelang_triggered = bool(
        dist_ema21_close < -6.0
        and _is_prime_session    # [FIX 5] hanya aktif di sesi prime
        and F > -10              # [FIX 5] volume tidak terlalu rendah
    )
    _karet_gelang_bonus     = 5 if _karet_gelang_triggered else 0
    if _karet_gelang_triggered:
        _karet_gelang_note = (
            f"⚡ KARET GELANG LONG: Close {dist_ema21_close:.2f}% di bawah EMA21 (<-6%). "
            f"+5 bonus darurat mean reversion. Sesi={session_label}."
        )
    elif dist_ema21_close < -6.0 and not _is_prime_session:
        _karet_gelang_note = (
            f"⚡ KARET GELANG LONG tidak aktif: Close {dist_ema21_close:.2f}% di bawah EMA21 (<-6%) "
            f"tapi sesi bukan prime (sesi={session_label}). Bonus diabaikan."
        )
    elif dist_ema21_close < -6.0 and F <= -10:
        _karet_gelang_note = (
            f"⚡ KARET GELANG LONG tidak aktif: Close {dist_ema21_close:.2f}% di bawah EMA21 (<-6%) "
            f"tapi volume terlalu rendah (F={F:.1f}% ≤ -10). Bonus diabaikan."
        )
    else:
        _karet_gelang_note = ""

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

    # ── [TAMBAHAN C + FIX LIKUIDASI] Gate L2: wajib confirmation candle di sweet spot
    # Paralel dengan Gate S2 SHORT yang wajib rejection candle sebelum PASS
    if not has_dyn_liq:
        if not has_buy_liq:
            gate_L['gates']['L2'] = ('PASS', 'Dynamic Buy_Liq tidak dapat dihitung (data Low kurang) — skip')
        elif close_price <= buy_liq_val * 1.005:
            # [TAMBAHAN C] Syarat confirmation candle juga untuk jalur statis
            if _confirmation_candle:
                gate_L['gates']['L2'] = ('PASS',
                    f'[Statis] Harga ≤ Buy_Liq×1.005 + confirmation candle terkonfirmasi — sweep sudah terjadi.')
            else:
                gate_L['gates']['L2'] = ('WARN',
                    f'⚠️ [Statis] Harga ≤ Buy_Liq×1.005 tapi BELUM ada confirmation candle. '
                    f'Tunggu 1 candle bullish menutup di atas open (saat ini close={close_price:.4f} vs open={_open_price:.4f}).')
                if gate_L['status'] == 'CLEAR': gate_L['status'] = 'WARNING'
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
            # [TAMBAHAN C] Wajib ada confirmation candle sebelum PASS di sweet spot
            if _confirmation_candle:
                gate_L['gates']['L2'] = (
                    'PASS',
                    f'✅ GATE L2: Sweet Spot (dist={_d:.2f}%) + confirmation candle terkonfirmasi. '
                    f'Harga cukup dekat dyn_Buy_Liq ${dyn_buy_liq:.4f} dan ada konfirmasi bullish. '
                    f'SwingLow(20): ${swing_low_20:.4f}.'
                )
            else:
                gate_L['gates']['L2'] = (
                    'WARN',
                    f'⚠️ GATE L2: Sweet Spot (dist={_d:.2f}%) tapi BELUM ada confirmation candle. '
                    f'Harga dekat dyn_Buy_Liq ${dyn_buy_liq:.4f} namun belum ada konfirmasi candle bullish. '
                    f'Tunggu 1 candle bullish menutup di atas open dengan lower wick dominan. SwingLow(20): ${swing_low_20:.4f}.'
                )
                if gate_L['status'] == 'CLEAR': gate_L['status'] = 'WARNING'
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

    # ── [FIX v2.0] OI LONG — dua sub-fungsi terpisah (Fix 1 + Fix 2) ────
    def _score_oi_trend(v):
        """Membaca momentum OI jangka menengah via C_final."""
        if v > 30: return 3    # OI bengkak kuat → tren naik meyakinkan
        if v >= 5: return 2
        if v >= -20: return 1
        return 0               # OI kolaps ekstrem tanpa pemulihan

    def _score_oi_event(oi_chg, rel_vol):
        """
        [FIX LIKUIDASI] Membaca event liquidasi sweep via oi_change + volume + CVD konfirmasi.
        OI turun + volume spike hanya diberi skor tinggi jika CVD sudah mengkonfirmasi
        (K >= 0 = flow beli mulai dominan) — sweep selesai, bukan sedang berlangsung.
        """
        if oi_chg < -10 and rel_vol > 50 and K >= 0: return 3   # [FIX v2.0] Serok bawah terkonfirmasi CVD
        if oi_chg < -10 and rel_vol > 50: return 2               # [FIX v2.0] Serok bawah belum terkonfirmasi CVD
        if oi_chg < -10: return 1                                 # [FIX v2.0] OI turun tapi volume normal
        return 0

    _oi_trend_score = _score_oi_trend(C_final)
    _oi_event_score = _score_oi_event(oi_change, F)
    s1 = max(_oi_trend_score, _oi_event_score)  # [FIX v2.0] ambil sinyal terkuat
    _liq_hunter_triggered = bool(oi_change < -10 and F > 50)

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
    s3 = 2 if G < 49 else (1 if G <= 52 else 0)
    s4 = score_atr_scoring(H)
    s5 = 3 if cvd_div_bull else (2 if K > 1 else (1 if K >= 0 else 0))
    s6 = 3 if L < -3 else (2 if L < -1.5 else (1 if L < -0.5 else 0))
    s7 = 3 if M < -4 else (2 if M < -2 else (1 if M < 0 else 0))
    s8 = 3 if N < -7 else (2 if N < -3 else (1 if N < 0 else 0))

    # [FIX v2.0] RSI threshold: V-Shape tetap skor penuh; normal threshold <35/<50/<62
    if rsi_vshaped_long and O_rsi <= 35:
        s9 = 3  # V-Shape konfirmasi — skor penuh (tidak berubah)
    else:
        s9 = 3 if O_rsi < 35 else (2 if O_rsi < 50 else (1 if O_rsi < 62 else 0))  # [FIX v2.0] was <35/<50/<60

    scores_L = {
        'OI':       (s1*4,  12, C_final, s1),  # [FIX v2.0] 5×→12 max → 4× 12 max
        'Vol':      (s2*3,   9, F_final, s2),  # [FIX v2.0] 4× → 3×
        'TakerBuy': (s3*3,   6, G, s3),        # [FIX v2.0] 4× → 3× (s3 max=2 → max=6)
        'ATR':      (s4*3,   9, H, s4),        # tetap
        'CVD':      (s5*4,  12, K, s5),        # [FIX v2.0] 3× → 4× (price action lebih dominan)
        'EMA21':    (s6*3,   9, L, s6),        # [FIX v2.0] 2× → 3×
        'EMA50':    (s7*3,   9, M, s7),        # [FIX v2.0] 2× → 3×
        'EMA200':   (s8*2,   6, N, s8),        # [FIX v2.0] 1× → 2×
        'RSI':      (s9*2,   6, O_rsi, s9),    # [FIX v2.0] 1× → 2×
    }  # [FIX v2.0] Total max: 12+9+6+9+12+9+9+6+6 = 78 poin (was 71)
    RAW_L = sum(v[0] for v in scores_L.values())

    # ── [FIX v2.0] Conflict Penalty (LONG) ───────────────────────────
    _conflict_penalties = []
    # Konflik 1: OI naik kuat tapi CVD negatif (bullish OI vs bearish flow)
    if C_final > 20 and K < -1:
        _conflict_penalties.append(('OI_vs_CVD', -5,
            f'OI={C_final:.1f}% naik tapi CVD={K:.1f}% negatif'))
    # Konflik 2: RSI oversold tapi funding positif tinggi
    if O_rsi < 35 and has_funding and funding_val > 0.0005:
        _conflict_penalties.append(('RSI_vs_Funding', -4,
            f'RSI={O_rsi:.1f} oversold tapi funding={funding_val:.5f} positif tinggi'))
    # Konflik 3: Harga di bawah EMA21 tapi BOS bearish aktif
    if L < -2 and has_bos and bos_val == -1 and not _karet_gelang_triggered:
        _conflict_penalties.append(('EMA_vs_BOS', -3,
            f'Harga {L:.1f}% di bawah EMA21 tapi BOS=-1 bearish'))
    # [FIX LIKUIDASI] Konflik 4: HOLLOW DUMP PATTERN
    # OI turun + Volume spike + CVD naik (sesaat) + tanpa confirmation candle
    # = kemungkinan short squeeze palsu sebelum dump dilanjutkan
    # Paralel dengan HOLLOW_PUMP_PATTERN di SHORT
    if (C_final < -10 and F_final > 50 and K > 1
            and not _confirmation_candle_liq
            and oi_change < -3):
        _conflict_penalties.append((
            'HOLLOW_DUMP_PATTERN', -10,
            f'OI={C_final:.1f}%↓ + Vol={F_final:.1f}%↑ + CVD={K:.1f}%↑ + '
            f'OI_change={oi_change:.1f}%↓ tanpa confirmation candle. '
            f'Pola Hollow Dump terdeteksi — kemungkinan short squeeze palsu sebelum dump lanjut.'
        ))
    _total_conflict_penalty = sum(p[1] for p in _conflict_penalties)
    RAW_L = RAW_L + _total_conflict_penalty  # [FIX v2.0] penalti ke RAW sebelum session mult
    # ── End Conflict Penalty ────────────────────────────────────────

    ADJ_L = round(RAW_L * SESSION_MULT, 1)  # [FIX v2.0] threshold ctx perlu ×1.098

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

    # ── [FIX 6] CVD bypass StochRSI — cek syarat tren bearish
    # Paralel dengan SHORT: bypass ditolak jika tren masih terlalu bullish (RSI>72, distEMA>3%)
    # LONG: bypass ditolak jika tren masih terlalu bearish (RSI<20, distEMA<-6%)
    _trend_still_bearish = bool(O_rsi < 20 and dist_ema21_close < -6.0)
    # Jika bypass CVD aktif tapi tren masih sangat bearish, batalkan bypass
    if cvd_div_bull and _trend_still_bearish and stoch_gatekeeper_skip is False and not has_stoch:
        # Hanya berlaku saat bypass karena "data tidak tersedia" + tren sangat bearish
        stoch_gatekeeper_ok = False
        stoch_gatekeeper_skip = True
        stoch_gatekeeper_reason = (
            f"❌ StochRSI bypass DITOLAK: Tren masih sangat bearish "
            f"(RSI={O_rsi:.1f}<20, distEMA21={dist_ema21_close:.1f}%<-6%) — "
            f"CVD div bull mungkin false signal di tengah downtrend berat. "
            f"Tunggu konfirmasi tambahan."
        )

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
        if cvd_div_bull and _trend_still_bearish:
            stoch_gate_override += f" [Bypass DITOLAK: tren masih bearish (RSI={O_rsi:.1f}, distEMA={dist_ema21_close:.1f}%)]"
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

    # [FIX P9.7] Teruskan close_price dan macro_trend ke fallback ATR projection
    # Sehingga TP3 berbasis harga SEKARANG (bukan entry_val statis)
    _flat_L = get_atr_projections_long(
        entry_val, atr, ATR_MULT,
        close_price=close_price,    # [FIX P9.7] anchor TP3 ke harga terkini
        macro_trend=macro_trend,    # [FIX P9.7] untuk override UPTREND 10x validation
    )

    # [FIX P9.7 - P1] Prioritaskan level struktural dulu, fallback ke ATR hanya jika tidak ada
    # Struktural yang diprioritaskan: Sell_Liq, PWH, PDH (sudah ada di tp_pool_L yang difilter)
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

    # ── [FIX P9.7 - PERBAIKAN 3] Hard Cap SL untuk Leverage Mode ────────────
    _sl_capped = False  # [FIX P9.7] tracking apakah SL sudah dicap
    if LEVERAGE_MODE:
        _sl_cap_price = close_price * (1 - MAX_SL_PCT)  # [FIX P9.7] 3.5% cap
        if sl_struct_L < _sl_cap_price:
            import logging as _log
            _log.getLogger(__name__).warning(
                f"[FIX P9.7] SL CAP AKTIF (LEVERAGE MODE): SL struktural ${sl_struct_L:.4f} "
                f"({((close_price - sl_struct_L) / close_price * 100):.2f}% dari close) "
                f"melebihi cap {MAX_SL_PCT*100:.1f}%. Dicap ke ${_sl_cap_price:.4f}."
            )
            sl_struct_L = _sl_cap_price
            sl_label_L  = f"{sl_label_L} [SL-CAP {MAX_SL_PCT*100:.1f}%]"  # [FIX P9.7]
            _sl_capped = True

            # [FIX P9.7] Recalculate RR TP1 dengan SL baru
            _rr_tp1_after_cap = (
                (tp1_L[0] - close_price) / (close_price - sl_struct_L)
                if (close_price - sl_struct_L) > 0 else 0.0
            )
            # [FIX P9.7] Downgrade ke WAIT atau SKIP jika RR TP1 < 1.5 setelah cap
            if _rr_tp1_after_cap < 1.5 and code_L not in ('SKIP',):
                _log.getLogger(__name__).warning(
                    f"[FIX P9.7] RR TP1={_rr_tp1_after_cap:.2f}x < 1.5 setelah SL cap — "
                    f"downgrade ke WAIT/SKIP."
                )
                if code_L == 'FULL':
                    dec_L, code_L = 'WAIT & MONITOR', 'WAIT'
                elif code_L == 'HALF':
                    dec_L, code_L = 'SKIP', 'SKIP'
    # ── End SL Cap ───────────────────────────────────────────────────────────

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

    # ── [FIX LIKUIDASI] Gate L5_LIQ: Hard block jika Buy Liquidity Hunt sedang berlangsung
    # Paralel dengan Gate S5_LIQ di SHORT
    # Kondisi: OI turun + sweeping Buy_Liq + CVD belum naik (K < 0) + tanpa confirmation candle
    # = sweep masih berlangsung, belum reversal
    _buy_liq_sweep_active = bool(
        C_final < -5          # OI turun (jangka menengah)
        and oi_change < -5    # OI masih turun per candle (momentum sweep aktif)
        and F_final > 50      # Volume spike = banyak stop loss sedang dieksekusi
        and K < 0             # CVD belum naik = flow beli belum dominan
    )

    if _buy_liq_sweep_active and not _confirmation_candle_liq:
        gate_L['gates']['L5_LIQ'] = (
            'FAIL',
            f'❌ GATE L5 [BUY LIQ HUNT ACTIVE]: OI={C_final:.1f}% turun + '
            f'OI_change={oi_change:.1f}% + Vol={F_final:.1f}%↑ + CVD={K:.1f}%↓ — '
            f'Buy Liquidity Hunt SEDANG berlangsung (stop loss masih dieksekusi). '
            f'CVD belum konfirmasi reversal (K<0). '
            f'Wajib tunggu confirmation candle bullish sebelum LONG. '
            f'Entry sekarang = catch falling knife.'
        )
        gate_L['status'] = 'BLOCKED'
        dec_L, code_L = 'SKIP', 'SKIP'
    elif _buy_liq_sweep_active and _confirmation_candle_liq:
        gate_L['gates']['L5_LIQ'] = (
            'PASS',
            f'✅ GATE L5 [BUY LIQ HUNT]: Sweep selesai + confirmation candle bullish terkonfirmasi. '
            f'Entry LONG valid (OI={C_final:.1f}%, OI_chg={oi_change:.1f}%, CVD={K:.1f}%, Vol={F_final:.1f}%).'
        )
    else:
        gate_L['gates']['L5_LIQ'] = ('PASS', f'Kondisi Buy Liq Hunt tidak aktif (OI={C_final:.1f}%, OI_chg={oi_change:.1f}%, CVD={K:.1f}%) — skip')

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

    # Deteksi konflik hollow dump untuk narasi
    _hollow_dump_detected = any(p[0] == 'HOLLOW_DUMP_PATTERN' for p in _conflict_penalties)

    narrative_L = {
        'kondisi': (
            f"[GATE LONG: {gate_L['status']}] {_gate_summary(gate_L)}. "
            f"Sesi {session_label} (×{SESSION_MULT}). Vol {vol_desc}. "
            f"Ref={'Close' if is_active else 'Low'} ${(close_price if is_active else low_price):.4f} vs "
            f"EMA21 {L:+.2f}% (${ema21:.4f}), EMA50 {M:+.2f}% (${ema50:.4f}), EMA200 {N:+.2f}% (${ema200:.4f}). "
            f"CVD: {cvd_desc} (I={I_cvd:.0f}, J={J_cvd:.0f}). "
            f"RSI_6={O_rsi:.1f} (prev={O_rsi_1:.1f}). {_stoch_desc()}. "
            f"ATR={H:.2f}% | ATR_MULT={ATR_MULT} ({atr_mult_reason}) | sweet spot {sweet_lo:.1f}%–{sweet_hi:.1f}%."
            + (f" | 🎯 {rsi_vshaped_note}" if rsi_vshaped_long else "")
            + (f" | {_karet_gelang_note}" if _karet_gelang_note else "")
            + (f" | 🩸 LIQUIDATION HUNTER: OI={C_final:.1f}% + Vol={F_final:.1f}% → OI MAX SCORE" if _liq_hunter_triggered else "")
            + (f" | [TAMBAHAN C] Confirmation candle: {'OK ✅' if _confirmation_candle else 'BELUM ⚠️'}")
            + (f" | ⚠️ HOLLOW DUMP PATTERN: OI↓ + Vol↑ + CVD↑ tanpa konfirmasi candle" if _hollow_dump_detected else "")
            + (f" | {stoch_gate_override}" if stoch_gate_override else "")
        ),
        'keputusan': (
            f"RAW={RAW_L} → ADJ={ADJ_L} (×{SESSION_MULT}"
            + (f"+{_karet_gelang_bonus}pts KaretGelang" if _karet_gelang_triggered else "")
            + (f"-{STOCH_PENALTY}pts StochGK" if stoch_penalty_applied else "")
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
            + f", StochRSI cross up dari <20 (saat ini: {'OK' if stoch_gatekeeper_ok else 'BELUM'}). "
            f"Confirmation candle {'OK ✅' if _confirmation_candle else 'diperlukan ⚠️'}. "
            f"Level kunci: Close ${close_price:.4f}, EMA21 ${ema21:.4f}, EMA50 ${ema50:.4f}. "
            f"Sesi optimal: London/NY ({session_label} saat ini)."
            + (f" Posisi {aging_status}: pertimbangkan exit dan re-entry setelah kondisi Tier 1 kembali positif." if aging_status in ('AGING','STALE') else "")
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
        if O_rsi < 35:  return ('✅', f'{O_rsi:.1f}', 'Oversold')            # [FIX v2.0]
        if O_rsi < 50:  return ('⚠️', f'{O_rsi:.1f}', 'Mendekati oversold') # [FIX v2.0]
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
        'stoch_penalty_applied': stoch_penalty_applied,
        'stoch_penalty_pts': stoch_penalty_pts,
        'liq_hunter_triggered': _liq_hunter_triggered,
        'rsi_vshaped_long': rsi_vshaped_long, 'rsi_vshaped_note': rsi_vshaped_note,
        'karet_gelang_triggered': _karet_gelang_triggered, 'karet_gelang_bonus': _karet_gelang_bonus,
        'karet_gelang_note': _karet_gelang_note,
        'oi_trend_score': _oi_trend_score,                       # [FIX v2.0]
        'oi_event_score': _oi_event_score,                       # [FIX v2.0]
        'conflict_penalties': _conflict_penalties,               # [FIX v2.0]
        'total_conflict_penalty': _total_conflict_penalty,       # [FIX v2.0]
        # [FIX LIKUIDASI] Confirmation candle
        'confirmation_candle':     _confirmation_candle,
        'confirmation_candle_liq': _confirmation_candle_liq,
        'buy_liq_sweep_active':    _buy_liq_sweep_active,
        # [FIX 6] CVD bypass tren
        'cvd_bypass_valid':        bool(cvd_div_bull and not _trend_still_bearish),
        'trend_still_bearish':     _trend_still_bearish,
        # [FIX P9.7 - PERBAIKAN 3] SL Cap monitoring
        'sl_capped':               _sl_capped,           # [FIX P9.7] True jika SL dicap oleh leverage mode
        'leverage_mode':           LEVERAGE_MODE,        # [FIX P9.7]
        'max_sl_pct':              MAX_SL_PCT,           # [FIX P9.7]
    }
