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

def calculate_trailing_sl_long(is_active, high_price, tp1_val, tp2_val, tp1_label, entry_val, sl_struct_L, sl_label_L, close_price):
    trailing_sl = {
        'applicable': False, 'tp1_hit': False, 'tp2_hit': False,
        'stage': 'NONE',  # 'NONE' | 'TP1_HIT' | 'TP2_HIT'
        'recommended_sl': None, 'recommended_sl_label': 'N/A',
        'action': 'N/A', 'note': 'Tidak aktif (tidak ada posisi terbuka atau TP belum tersentuh)'
    }
    if is_active:
        _tp1_L_hit = bool(high_price >= tp1_val)
        _tp2_L_hit = bool(high_price >= tp2_val)
        trailing_sl['tp1_hit'] = _tp1_L_hit
        trailing_sl['tp2_hit'] = _tp2_L_hit

        if _tp2_L_hit:
            # [FIX] applicable hanya True saat TP benar-benar tercapai
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP2_HIT'
            trailing_sl['recommended_sl']       = round(tp1_val, 8)
            trailing_sl['recommended_sl_label'] = f'Trailing SL @ TP1 [{tp1_label}]'
            trailing_sl['action'] = '⭐ TP2 tercapai → GESER SL ke TP1. Lock profit partial.'
            trailing_sl['note'] = 'Trailing aktif: SL di TP1 — risiko closed di profit TP1 level'
        elif _tp1_L_hit:
            # [FIX] applicable hanya True saat TP benar-benar tercapai
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP1_HIT'
            trailing_sl['recommended_sl']       = round(entry_val, 8)
            trailing_sl['recommended_sl_label'] = f'Trailing SL @ Breakeven (Entry)'
            trailing_sl['action'] = '✅ TP1 tercapai → GESER SL ke Breakeven. Trade sudah risk-free.'
            trailing_sl['note'] = 'Trailing aktif: SL di entry — trade risk-free, tunggu TP2'
        else:
            # [FIX] TP belum tercapai = trailing tidak aktif, TIDAK kirim alert
            trailing_sl['applicable'] = False
            trailing_sl['stage'] = 'NONE'
            trailing_sl['recommended_sl']       = round(sl_struct_L, 8)
            trailing_sl['recommended_sl_label'] = f'SL Struktural [{sl_label_L}]'
            trailing_sl['action'] = '⏳ TP1 belum tercapai. Pertahankan SL struktural awal.'
            trailing_sl['note'] = 'Trailing belum aktif — tunggu TP1 tercapai'
    return trailing_sl

def calculate_trailing_sl_short(is_active, low_price, tp1_val, tp2_val, tp1_label, entry_val, sl_struct_S, sl_label_S, close_price):
    trailing_sl = {
        'applicable': False, 'tp1_hit': False, 'tp2_hit': False,
        'stage': 'NONE',  # 'NONE' | 'TP1_HIT' | 'TP2_HIT'
        'recommended_sl': None, 'recommended_sl_label': 'N/A',
        'action': 'N/A', 'note': 'Tidak aktif (tidak ada posisi terbuka atau TP belum tersentuh)'
    }
    if is_active:
        _tp1_S_hit = bool(low_price <= tp1_val)
        _tp2_S_hit = bool(low_price <= tp2_val)
        trailing_sl['tp1_hit'] = _tp1_S_hit
        trailing_sl['tp2_hit'] = _tp2_S_hit

        if _tp2_S_hit:
            # [FIX] applicable hanya True saat TP benar-benar tercapai
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP2_HIT'
            trailing_sl['recommended_sl']       = round(tp1_val, 8)
            trailing_sl['recommended_sl_label'] = f'Trailing SL @ TP1 [{tp1_label}]'
            trailing_sl['action'] = '⭐ TP2 tercapai → GESER SL ke TP1. Lock profit partial.'
            trailing_sl['note'] = 'Trailing aktif: SL di TP1 — risiko closed di profit TP1 level'
        elif _tp1_S_hit:
            # [FIX] applicable hanya True saat TP benar-benar tercapai
            trailing_sl['applicable'] = True
            trailing_sl['stage'] = 'TP1_HIT'
            trailing_sl['recommended_sl']       = round(entry_val, 8)
            trailing_sl['recommended_sl_label'] = f'Trailing SL @ Breakeven (Entry)'
            trailing_sl['action'] = '✅ TP1 tercapai → GESER SL ke Breakeven. Trade sudah risk-free.'
            trailing_sl['note'] = 'Trailing aktif: SL di entry — trade risk-free, tunggu TP2'
        else:
            # [FIX] TP belum tercapai = trailing tidak aktif, TIDAK kirim alert
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
