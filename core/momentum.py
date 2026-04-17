import pandas as pd
from core.helpers import _last_val, safe_float

def check_momentum_hold(K, G, O_rsi, C_final, L):
    cvd_rising     = bool(K > 1.0)
    buy_dominant   = bool(G > 55.0)
    rsi_not_ob     = bool(O_rsi < 68.0)
    oi_net_rising  = bool(C_final > 3.0)
    not_extended   = bool(L < 3.0)

    momentum_factors = [cvd_rising, buy_dominant, rsi_not_ob, oi_net_rising, not_extended]
    momentum_score   = int(sum(momentum_factors))

    hold_tp_signal   = False
    hold_tp_strength = ""
    hold_tp_reasons = []

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

    return {
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

def evaluate_exit_signals(is_active, close_price, ema21, ema50, O_rsi, G, last, aging_status, candles_since_entry, tp1_val):
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
        if tp1_val is not None and close_price >= tp1_val * 0.99:
            exit_signals.append(("⚠️", "Mendekati TP1", round(close_price, 4), f"≥ {tp1_val*0.99:.4f} (99% TP1)"))

    exit_hard = sum(1 for e in exit_signals if e[0] == "❌")
    exit_warn = sum(1 for e in exit_signals if e[0] == "⚠️")
    
    if exit_hard >= 1:
        exit_reco = "PARTIAL EXIT atau FULL EXIT"
    elif exit_warn >= 1:
        exit_reco = "HOLD dengan monitoring ketat"
    else:
        exit_reco = "HOLD"
        
    return exit_signals, exit_reco, exit_hard, exit_warn

def calculate_trailing_sl_long(
        is_active, high_price, tp1_val, tp2_val, tp1_label,
        entry_val, sl_struct_L, sl_label_L, close_price,
        atr=0.0, tp3_val=None):  # [FIX P9.7] tambah atr + tp3_val untuk tahap 3
    """
    [FIX P9.7 - PERBAIKAN 4] Progressive 3-tahap trailing SL untuk LONG:
      Tahap 1 (TP1 hit): SL → entry + (ATR × 0.3)  — buffer slippage, bukan entry persis
      Tahap 2 (TP2 hit): SL → TP1 + (ATR × 0.2)   — lock profit level TP1
      Tahap 3 (TP3 hit / close > tp2+5%): trailing = close - (ATR × 1.5)  — ride momentum
    """
    trailing_sl = {
        'applicable': False, 'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False,
        'stage': 'NONE',  # 'NONE' | 'TP1_HIT' | 'TP2_HIT' | 'TP3_HIT' | 'TRAILING'
        'recommended_sl': None, 'recommended_sl_label': 'N/A',
        'action': 'N/A', 'note': 'Tidak aktif (tidak ada posisi terbuka atau TP belum tersentuh)'
    }
    if is_active:
        _tp1_L_hit = bool(high_price >= tp1_val)
        _tp2_L_hit = bool(high_price >= tp2_val)
        _tp3_L_hit = bool(tp3_val is not None and high_price >= tp3_val)
        # [FIX P9.7] Tahap 3 juga aktif jika close > tp2 + 5% (momentum riding)
        _tp3_momentum = bool(tp2_val > 0 and close_price > tp2_val * 1.05)

        trailing_sl['tp1_hit'] = _tp1_L_hit
        trailing_sl['tp2_hit'] = _tp2_L_hit
        trailing_sl['tp3_hit'] = _tp3_L_hit or _tp3_momentum

        if _tp3_L_hit or _tp3_momentum:
            # [FIX P9.7] Tahap 3: trailing dinamis = close - ATR×1.5
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP3_HIT' if _tp3_L_hit else 'TRAILING'
            _trail_val = round(close_price - (atr * 1.5), 8) if atr > 0 else round(tp2_val, 8)
            trailing_sl['recommended_sl']       = _trail_val
            trailing_sl['recommended_sl_label'] = 'Trailing SL @ Close − ATR×1.5 [FIX P9.7]'
            trailing_sl['action'] = (
                f'⭐⭐ TP3 tercapai → TRAILING SL aktif: SL = ${_trail_val:.4f} '
                f'(Close ${close_price:.4f} − ATR×1.5). Ride momentum penuh.'
            )
            trailing_sl['note'] = 'Tahap 3 [FIX P9.7]: SL trailing dinamis — lock profit sambil ride momentum'

        elif _tp2_L_hit:
            # [FIX P9.7] Tahap 2: SL → TP1 + ATR×0.2  (lock profit di level TP1)
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP2_HIT'
            _sl_tp2_stage = round(tp1_val + (atr * 0.2), 8) if atr > 0 else round(tp1_val, 8)
            trailing_sl['recommended_sl']       = _sl_tp2_stage
            trailing_sl['recommended_sl_label'] = f'Trailing SL @ TP1+ATR×0.2 [FIX P9.7] [{tp1_label}]'
            trailing_sl['action'] = (
                f'⭐ TP2 tercapai → GESER SL ke ${_sl_tp2_stage:.4f} '
                f'(TP1 ${tp1_val:.4f} + ATR×0.2). Lock profit TP1 level.'
            )
            trailing_sl['note'] = 'Tahap 2 [FIX P9.7]: SL di atas TP1 — profit TP1 terlindungi'

        elif _tp1_L_hit:
            # [FIX P9.7] Tahap 1: SL → entry + ATR×0.3  (bukan entry persis, ada buffer slippage)
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP1_HIT'
            _sl_be_buffer = round(entry_val + (atr * 0.3), 8) if atr > 0 else round(entry_val, 8)
            trailing_sl['recommended_sl']       = _sl_be_buffer
            trailing_sl['recommended_sl_label'] = 'Trailing SL @ BE+ATR×0.3 [FIX P9.7]'
            trailing_sl['action'] = (
                f'✅ TP1 tercapai → GESER SL ke ${_sl_be_buffer:.4f} '
                f'(Entry ${entry_val:.4f} + ATR×0.3 buffer slippage). Trade risk-free.'
            )
            trailing_sl['note'] = 'Tahap 1 [FIX P9.7]: SL di atas entry — trade risk-free, hindari slippage BE'

        else:
            trailing_sl['applicable'] = False
            trailing_sl['stage'] = 'NONE'
            trailing_sl['recommended_sl']       = round(sl_struct_L, 8)
            trailing_sl['recommended_sl_label'] = f'SL Struktural [{sl_label_L}]'
            trailing_sl['action'] = '⏳ TP1 belum tercapai. Pertahankan SL struktural awal.'
            trailing_sl['note'] = 'Trailing belum aktif — tunggu TP1 tercapai'
    return trailing_sl

def calculate_trailing_sl_short(
        is_active, low_price, tp1_val, tp2_val, tp1_label,
        entry_val, sl_struct_S, sl_label_S, close_price,
        atr=0.0, tp3_val=None):  # [FIX P9.7] tambah atr + tp3_val untuk tahap 3
    """
    [FIX P9.7 - PERBAIKAN 4] Progressive 3-tahap trailing SL untuk SHORT:
      Tahap 1 (TP1 hit): SL → entry - (ATR × 0.3)  — buffer slippage
      Tahap 2 (TP2 hit): SL → TP1 - (ATR × 0.2)   — lock profit TP1 level
      Tahap 3 (TP3 hit / close < tp2-5%): trailing = close + (ATR × 1.5) — ride momentum
    """
    trailing_sl = {
        'applicable': False, 'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False,
        'stage': 'NONE',  # 'NONE' | 'TP1_HIT' | 'TP2_HIT' | 'TP3_HIT' | 'TRAILING'
        'recommended_sl': None, 'recommended_sl_label': 'N/A',
        'action': 'N/A', 'note': 'Tidak aktif (tidak ada posisi terbuka atau TP belum tersentuh)'
    }
    if is_active:
        _tp1_S_hit = bool(low_price <= tp1_val)
        _tp2_S_hit = bool(low_price <= tp2_val)
        _tp3_S_hit = bool(tp3_val is not None and low_price <= tp3_val)
        # [FIX P9.7] Tahap 3 juga aktif jika close < tp2 - 5% (momentum riding SHORT)
        _tp3_momentum = bool(tp2_val > 0 and close_price < tp2_val * 0.95)

        trailing_sl['tp1_hit'] = _tp1_S_hit
        trailing_sl['tp2_hit'] = _tp2_S_hit
        trailing_sl['tp3_hit'] = _tp3_S_hit or _tp3_momentum

        if _tp3_S_hit or _tp3_momentum:
            # [FIX P9.7] Tahap 3: trailing dinamis = close + ATR×1.5
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP3_HIT' if _tp3_S_hit else 'TRAILING'
            _trail_val = round(close_price + (atr * 1.5), 8) if atr > 0 else round(tp2_val, 8)
            trailing_sl['recommended_sl']       = _trail_val
            trailing_sl['recommended_sl_label'] = 'Trailing SL @ Close + ATR×1.5 [FIX P9.7]'
            trailing_sl['action'] = (
                f'⭐⭐ TP3 tercapai → TRAILING SL aktif: SL = ${_trail_val:.4f} '
                f'(Close ${close_price:.4f} + ATR×1.5). Ride momentum penuh.'
            )
            trailing_sl['note'] = 'Tahap 3 [FIX P9.7]: SL trailing dinamis — lock profit sambil ride decline'

        elif _tp2_S_hit:
            # [FIX P9.7] Tahap 2: SL → TP1 - ATR×0.2
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP2_HIT'
            _sl_tp2_stage = round(tp1_val - (atr * 0.2), 8) if atr > 0 else round(tp1_val, 8)
            trailing_sl['recommended_sl']       = _sl_tp2_stage
            trailing_sl['recommended_sl_label'] = f'Trailing SL @ TP1−ATR×0.2 [FIX P9.7] [{tp1_label}]'
            trailing_sl['action'] = (
                f'⭐ TP2 tercapai → GESER SL ke ${_sl_tp2_stage:.4f} '
                f'(TP1 ${tp1_val:.4f} − ATR×0.2). Lock profit TP1 level.'
            )
            trailing_sl['note'] = 'Tahap 2 [FIX P9.7]: SL di bawah TP1 — profit TP1 terlindungi'

        elif _tp1_S_hit:
            # [FIX P9.7] Tahap 1: SL → entry - ATR×0.3
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP1_HIT'
            _sl_be_buffer = round(entry_val - (atr * 0.3), 8) if atr > 0 else round(entry_val, 8)
            trailing_sl['recommended_sl']       = _sl_be_buffer
            trailing_sl['recommended_sl_label'] = 'Trailing SL @ BE−ATR×0.3 [FIX P9.7]'
            trailing_sl['action'] = (
                f'✅ TP1 tercapai → GESER SL ke ${_sl_be_buffer:.4f} '
                f'(Entry ${entry_val:.4f} − ATR×0.3 buffer slippage). Trade risk-free.'
            )
            trailing_sl['note'] = 'Tahap 1 [FIX P9.7]: SL di bawah entry — trade risk-free SHORT'

        else:
            trailing_sl['applicable'] = False
            trailing_sl['stage'] = 'NONE'
            trailing_sl['recommended_sl']       = round(sl_struct_S, 8)
            trailing_sl['recommended_sl_label'] = f'SL Struktural [{sl_label_S}]'
            trailing_sl['action'] = '⏳ TP1 belum tercapai. Pertahankan SL struktural awal.'
            trailing_sl['note'] = 'Trailing belum aktif — tunggu TP1 tercapai'
    return trailing_sl

def detect_sl_wick_fakeout(is_active, close_price, low_price, last, sl_struct_L, K, D, E20):
    sl_wick_result = {
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
            sl_wick_result['verdict']        = 'BREAKDOWN NYATA'
            sl_wick_result['confidence_pct'] = 5
            sl_wick_result['action']         = '❌ EXIT SEGERA — Candle close di bawah SL. Ini bukan wick.'
            sl_wick_result['fakeout_count']  = 0
        else:
            sl_wick_result['verdict']        = 'SL AMAN'
            sl_wick_result['confidence_pct'] = 100
            sl_wick_result['action']         = '✅ Posisi aman. SL belum disentuh.'
            
    return sl_wick_result
