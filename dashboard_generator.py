import os
import sys
import pandas as pd
import numpy as np
import json
from datetime import datetime
import re

CSV_FILE = 'enriched_export.csv'

def parse_csv_and_metadata(filepath):
    """Parses `#` comments for metadata and loads the CSV."""
    metadata = {
        'Symbol': 'UNKNOWN',
        'Timeframe': '4H', 
        'AVG_ENTRY_PRICE': None,
        'TOTAL_QTY': None,
        'TOTAL_COST': None,
        'Export_Time': None
    }
    
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found.")
        return metadata, pd.DataFrame()
        
    data_lines = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line_str = line.strip()
            if line_str.startswith('#'):
                # Extract metadata
                if 'Symbol' in line_str:
                    parts = line_str.split('Symbol')
                    if len(parts) > 1: metadata['Symbol'] = parts[1].replace(':', '').replace('=', '').strip()
                elif 'Timeframe' in line_str:
                    parts = line_str.split('Timeframe')
                    if len(parts) > 1: metadata['Timeframe'] = parts[1].replace(':', '').replace('=', '').strip()
                elif 'AVG ENTRY PRICE' in line_str or 'Entry #1: Price=' in line_str:
                    # Try to extract the number
                    match = re.search(r'[\d\.]+', line_str.split('PRICE')[-1] if 'PRICE' in line_str else line_str.split('Price=')[-1])
                    if match: metadata['AVG_ENTRY_PRICE'] = float(match.group())
                elif 'TOTAL QTY' in line_str:
                    match = re.search(r'[\d\.]+', line_str.split('QTY')[-1])
                    if match: metadata['TOTAL_QTY'] = float(match.group())
                elif 'TOTAL COST' in line_str:
                    match = re.search(r'[\d\.]+', line_str.split('COST')[-1])
                    if match: metadata['TOTAL_COST'] = float(match.group())
                elif 'Export Time' in line_str:
                    parts = line_str.split('Export Time')
                    if len(parts) > 1: metadata['Export_Time'] = parts[1].replace(':', '').replace('=', '').strip()
            else:
                data_lines.append(line_str)
                
    if not data_lines:
        return metadata, pd.DataFrame()
        
    from io import StringIO
    df = pd.read_csv(StringIO('\n'.join(data_lines)))
    return metadata, df

def get_market_session(timestamp_str):
    try:
        # Expected format: 2026-03-18 04:14:59
        dt = pd.to_datetime(timestamp_str)
        hour = dt.hour
        # Simplified UTC sessions
        if 0 <= hour < 8: return "ASIAN"
        elif 8 <= hour < 13: return "LONDON"
        elif 13 <= hour < 22: return "NEW YORK"
        else: return "ASIAN (Late)"
    except:
        return "UNKNOWN"

def safe_float(val, default=0.0):
    try:
        if pd.isna(val): return default
        return float(val)
    except:
        return default

def calculate_scoring(df, metadata):
    if len(df) < 22:
        print("Not enough data. Need at least 22 candles.")
        return None
        
    # Ensure CVD exists
    if 'CVD' not in df.columns:
        if 'Buy_Volume' in df.columns and 'Total_Volume' in df.columns and 'Sell_Volume' not in df.columns:
            df['Sell_Volume'] = df['Total_Volume'] - df['Buy_Volume']
        if 'Buy_Volume' in df.columns and 'Sell_Volume' in df.columns:
            df['CVD'] = (df['Buy_Volume'] - df['Sell_Volume']).cumsum()
        else:
            df['CVD'] = 0.0

    # Basic Variables
    last_idx = -1
    prev20_start = -21
    prev20_end = -1
    
    # Slicing
    recent_20 = df.iloc[prev20_start:prev20_end]
    last_candle = df.iloc[-1]
    candle_21_ago = df.iloc[-21]
    
    # 1. Open Interest
    A = safe_float(last_candle.get('Open_Interest', 0))
    B = recent_20['Open_Interest'].mean() if 'Open_Interest' in df.columns else 0
    C = ((A - B) / B * 100) if B != 0 else 0
    
    # 2. Volume
    D = safe_float(last_candle.get('Total_Volume', 0))
    E = recent_20['Total_Volume'].mean() if 'Total_Volume' in df.columns else 0
    F = ((D - E) / E * 100) if E != 0 else 0
    
    # 3. TakerBuy
    buy_vol = safe_float(last_candle.get('Buy_Volume', 0))
    G = (buy_vol / D * 100) if D != 0 else 50.0
    
    # 4. ATR%
    close_price = safe_float(last_candle.get('Close', 0))
    atr = safe_float(last_candle.get('ATR_14', 0))
    H = (atr / close_price * 100) if close_price != 0 else 0
    
    is_altcoin = True
    if metadata['Symbol'] in ['BTC', 'BTCUSDT', 'ETHUSDT'] or close_price > 3000:
        is_altcoin = False
        
    atr_multiplier = 2.0 if is_altcoin else 1.0

    # 5. CVD
    I = safe_float(last_candle.get('CVD', 0))
    J = safe_float(candle_21_ago.get('CVD', 0))
    K = ((I - J) / abs(J) * 100) if J != 0 else 0
    
    close_21_ago = safe_float(candle_21_ago.get('Close', 0))
    cvd_div_bull = (I > J) and (close_price < close_21_ago)
    cvd_div_bear = (I < J) and (close_price > close_21_ago)
    
    # EMA Distances
    ema21 = safe_float(last_candle.get('EMA_21', close_price))
    ema50 = safe_float(last_candle.get('EMA_50', close_price))
    ema200 = safe_float(last_candle.get('EMA_200', close_price))
    
    is_active_pos = metadata.get('AVG_ENTRY_PRICE') is not None
    ref_price_long = close_price if is_active_pos else safe_float(last_candle.get('Low', close_price))
    ref_price_short = safe_float(last_candle.get('High', close_price))
    
    L = (ref_price_long - ema21) / ema21 * 100 if ema21 else 0
    M = (ref_price_long - ema50) / ema50 * 100 if ema50 else 0
    N = (ref_price_long - ema200) / ema200 * 100 if ema200 else 0
    
    Lp = (ref_price_short - ema21) / ema21 * 100 if ema21 else 0
    Mp = (ref_price_short - ema50) / ema50 * 100 if ema50 else 0
    Np = (ref_price_short - ema200) / ema200 * 100 if ema200 else 0
    
    O_rsi = safe_float(last_candle.get('RSI_6', 50))
    
    # ==========================
    # SCORING LONG
    # ==========================
    scores_long = {}
    details_long = {}
    
    # 1. OI
    if C > 30: s1 = 3
    elif 5 <= C <= 30: s1 = 2
    elif -20 <= C < 5: s1 = 1
    else: s1 = 0
    scores_long['OI'] = s1 * 5; details_long['OI'] = (s1, C)

    # 2. Vol
    if F > 70: s2 = 3
    elif 20 <= F <= 70: s2 = 2
    elif -10 <= F < 20: s2 = 1
    else: s2 = 0
    scores_long['Vol'] = s2 * 4; details_long['Vol'] = (s2, F)

    # 3. TakerBuy (max 2)
    if G < 49: s3 = 2
    elif 49 <= G <= 51: s3 = 1
    else: s3 = 0
    scores_long['TakerBuy'] = s3 * 4; details_long['TakerBuy'] = (s3, G)

    # 4. ATR
    # For Altcoin bounds are multiplied by 2:
    # Orig: 3-5 (3), 2-3|5-7 (2), 1.8-2|7-10 (1) -> Wait, sweet spot altcoin is 3-5% (noted in prompt). So if NOT altcoin, it is 1.5-2.5%.
    # Prompt says: "batas ATR% dikalikan ×2 untuk altcoin (sweet spot altcoin = 3–5%, bukan 1.5–2.5%)"
    # Oh, the rule given in prompt is ALREADY the altcoin scaled version? Wait.
    # Prompt:
    # 3.0% ≤ H ≤ 5.0%                        -> skor 3
    # (2.0% ≤ H < 3.0%) ATAU (5.0% < H ≤ 7.0%) -> skor 2
    # (1.8% ≤ H < 2.0%) ATAU (7.0% < H ≤ 10%)  -> skor 1
    # "CATATAN ATR ALTCOIN: semua batas ATR% dikalikan ×2 untuk altcoin"
    # That implies the numbers given in the prompt ARE the BTC ones, or THEY ARE the altcoin ones. 
    # Usually BTC ATR is 1.5-2.5%. So 3-5% is clearly the ALTCOIN sweet spot. Let's assume the limits provided in the prompt are the ALTCOIN limits. If it's BTC, divide by 2.
    if is_altcoin:
        b1, b2, b3, b4, b5, b6 = 3.0, 5.0, 2.0, 7.0, 1.8, 10.0
    else:
        # BTC limits
        b1, b2, b3, b4, b5, b6 = 1.5, 2.5, 1.0, 3.5, 0.9, 5.0

    if b1 <= H <= b2: s4 = 3
    elif (b3 <= H < b1) or (b2 < H <= b4): s4 = 2
    elif (b5 <= H < b3) or (b4 < H <= b6): s4 = 1
    else: s4 = 0
    scores_long['ATR'] = s4 * 3; details_long['ATR'] = (s4, H)

    # 5. CVD
    if cvd_div_bull: s5 = 3
    elif K > 1: s5 = 2
    elif 0 <= K <= 1: s5 = 1
    else: s5 = 0
    scores_long['CVD'] = s5 * 3; details_long['CVD'] = (s5, K)

    # 6. vs EMA21
    if L < -3: s6 = 3
    elif -3 <= L < -1.5: s6 = 2
    elif -1.5 <= L < -0.5: s6 = 1
    else: s6 = 0
    scores_long['EMA21'] = s6 * 2; details_long['EMA21'] = (s6, L)

    # 7. vs EMA50
    if M < -4: s7 = 3
    elif -4 <= M < -2: s7 = 2
    elif -2 <= M < 0: s7 = 1
    else: s7 = 0
    scores_long['EMA50'] = s7 * 2; details_long['EMA50'] = (s7, M)

    # 8. vs EMA200
    if N < -7: s8 = 3
    elif -7 <= N < -3: s8 = 2
    elif -3 <= N < 0: s8 = 1
    else: s8 = 0
    scores_long['EMA200'] = s8 * 1; details_long['EMA200'] = (s8, N)

    # 9. RSI
    if O_rsi < 25: s9 = 3
    elif 25 <= O_rsi < 40: s9 = 2
    elif 40 <= O_rsi < 55: s9 = 1
    else: s9 = 0
    scores_long['RSI'] = s9 * 1; details_long['RSI'] = (s9, O_rsi)

    total_long = sum(scores_long.values())
    pct_long = total_long / 71 * 100

    # ==========================
    # SCORING SHORT
    # ==========================
    scores_short = {}
    details_short = {}
    
    # 1 & 2 & 4 exact same as long
    scores_short['OI'] = s1 * 5; details_short['OI'] = (s1, C)
    scores_short['Vol'] = s2 * 4; details_short['Vol'] = (s2, F)
    scores_short['ATR'] = s4 * 3; details_short['ATR'] = (s4, H)

    # 3. TakerBuy SHORT
    if G > 53: s3_s = 2
    elif 51 <= G <= 53: s3_s = 1
    else: s3_s = 0
    scores_short['TakerBuy'] = s3_s * 4; details_short['TakerBuy'] = (s3_s, G)

    # 5. CVD Bear
    if cvd_div_bear: s5_s = 3
    elif K < -1: s5_s = 2
    elif K <= 0: s5_s = 1
    else: s5_s = 0
    scores_short['CVD'] = s5_s * 3; details_short['CVD'] = (s5_s, K)

    # 6. vs EMA21 SHORT
    if Lp > 5: s6_s = 3
    elif 3 <= Lp <= 5: s6_s = 2
    elif 1.5 <= Lp < 3: s6_s = 1
    else: s6_s = 0
    scores_short['EMA21'] = s6_s * 2; details_short['EMA21'] = (s6_s, Lp)

    # 7. vs EMA50 SHORT
    if Mp > 6: s7_s = 3
    elif 4 <= Mp <= 6: s7_s = 2
    elif 2 <= Mp < 4: s7_s = 1
    else: s7_s = 0
    scores_short['EMA50'] = s7_s * 2; details_short['EMA50'] = (s7_s, Mp)

    # 8. vs EMA200 SHORT
    if Np > 10: s8_s = 3
    elif 5 <= Np <= 10: s8_s = 2
    elif 2 <= Np < 5: s8_s = 1
    else: s8_s = 0
    scores_short['EMA200'] = s8_s * 1; details_short['EMA200'] = (s8_s, Np)

    # 9. RSI SHORT
    if O_rsi > 75: s9_s = 3
    elif 60 <= O_rsi <= 75: s9_s = 2
    elif 45 <= O_rsi < 60: s9_s = 1
    else: s9_s = 0
    scores_short['RSI'] = s9_s * 1; details_short['RSI'] = (s9_s, O_rsi)

    total_short = sum(scores_short.values())
    pct_short = total_short / 71 * 100

    def get_decision(scores_total):
        if scores_total >= 53: return "FULL SIZE ENTRY", 1.0, "FULL"
        elif scores_total >= 36: return "HALF SIZE ENTRY", 1.5, "HALF"
        elif scores_total >= 21: return "WAIT & MONITOR", 2.0, "WAIT"
        else: return "SKIP", 0.0, "SKIP"

    dec_long_str, sl_mul_long, dec_long_code = get_decision(total_long)
    dec_short_str, sl_mul_short, dec_short_code = get_decision(total_short)

    # SL and TP Levels
    entry_val = float(metadata['AVG_ENTRY_PRICE']) if metadata['AVG_ENTRY_PRICE'] else close_price

    # Long Levels
    sl_ketat_L = close_price - (atr * 1.0)
    sl_normal_L = close_price - (atr * 1.5)
    sl_lebar_L = close_price - (atr * 2.0)

    tp1_L = entry_val * 1.025
    tp2_L = entry_val * 1.046
    tp3_L = entry_val * 1.070

    # Short Levels
    sl_ketat_S = close_price + (atr * 1.0)
    sl_normal_S = close_price + (atr * 1.5)
    sl_lebar_S = close_price + (atr * 2.0)

    tp1_S = entry_val * 0.975
    tp2_S = entry_val * 0.954
    tp3_S = entry_val * 0.930

    # Risk Reward
    def calc_rr_L(tp, sl): return (tp - close_price) / (close_price - sl) if sl < close_price else 0
    def calc_rr_S(tp, sl): return (close_price - tp) / (sl - close_price) if sl > close_price else 0

    # Exit signals logic (Posisi aktif)
    exit_signals = []
    if is_active_pos:
        # PnL
        pnl_pct = (close_price / entry_val - 1) * 100

        # Signals
        if O_rsi > 75: exit_signals.append(("❌", "RSI_6 overbought", O_rsi, "> 75"))
        if ((close_price/ema21 - 1)*100) > 3.6: exit_signals.append(("❌", "vs EMA21 extended", (close_price/ema21 - 1)*100, "> +3.6%"))
        if ((close_price/ema50 - 1)*100) > 4.6: exit_signals.append(("❌", "vs EMA50 extended", (close_price/ema50 - 1)*100, "> +4.6%"))
        
        if G > 53: exit_signals.append(("⚠️", "TakerBuy FOMO", G, "> 53%"))
        bos_val = safe_float(last_candle.get('BOS', 0))
        if bos_val == -1: exit_signals.append(("⚠️", "BOS bearish", bos_val, "== -1"))
        fr_val = safe_float(last_candle.get('Funding_Rate', 0))
        if fr_val > 0.001: exit_signals.append(("⚠️", "Funding rate tinggi", fr_val, "> +0.001"))
        
        atr_entry = atr # Approximation: we don't have historical ATR from entry
        if atr < atr_entry * 0.75: exit_signals.append(("⚠️", "ATR mengempis", atr, f"< {atr_entry*0.75:.4f}"))

    exit_x = sum(1 for e in exit_signals if e[0] == "❌")
    exit_w = sum(1 for e in exit_signals if e[0] == "⚠️")
    exit_reco = "HOLD"
    if exit_x >= 1: exit_reco = "PARTIAL EXIT atau FULL EXIT"
    elif exit_w >= 1: exit_reco = "HOLD dengan monitoring ketat"

    # Narrative Generation
    sess = get_market_session(str(last_candle.get('Timestamp', datetime.now(datetime.timezone.utc))))
    
    vol_desc = f"di atas MA20 (+{F:.1f}%)" if F > 0 else f"di bawah MA20 ({F:.1f}%)"
    cvd_desc = "bullish divergence" if cvd_div_bull else ("bearish divergence" if cvd_div_bear else f"perubahan {K:.1f}% dari 20 candle lalu")
    
    narrative_long = {
        'kondisi': f"Sesi pasar {sess}. Volume {vol_desc}. Posisi harga (${close_price:.4f}) berada di {'atas' if L>0 else 'bawah'} EMA21 berjarak {L:.1f}%. CVD menunjukkan {cvd_desc}. Momentum RSI di {O_rsi:.1f}.",
        'keputusan': f"Skor setup mencapai {total_long}/71 ({pct_long:.1f}%) menghasilkan keputusan {dec_long_str}. " + ("Faktor dominan mendukung: RSI oversold." if O_rsi < 40 else ""),
        'skenario': f"Skenario validasi: pantau pada sesi berikutnya. Jika harga > {tp1_L:.4f}, tier akan membaik."
    }

    narrative_short = {
        'kondisi': f"Sesi pasar {sess}. Volume {vol_desc}. Harga berjarak {Lp:.1f}% terhadap EMA21 (short focus). CVD {cvd_desc}. RSI di {O_rsi:.1f}.",
        'keputusan': f"Skor setup mencapai {total_short}/71 ({pct_short:.1f}%) menghasilkan keputusan {dec_short_str}. ",
        'skenario': f"Monitor level SL ketat di {sl_ketat_S:.4f}. Break bawah {tp1_S:.4f} mengkonfirmasi setup."
    }

    # Market context
    market_context = {}
    for col in ['MSB', 'BOS', 'CHoCH', 'SFP_Sweep', 'FVG_Up_Top', 'FVG_Up_Bottom', 'FVG_Down_Top', 'FVG_Down_Bottom', 'OB_Price', 'Fib_0.618', 'Fib_0.786', 'POC', 'VAH', 'VAL', 'Buy_Liq', 'Sell_Liq', 'PDH', 'PDL', 'PWH', 'PWL', 'EMA_7', 'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4', 'StochRSI_K', 'StochRSI_D', 'Funding_Rate', 'BTC_Price', 'BTC_Dominance', 'Altcoin_Index']:
        if col in df.columns:
            val = last_candle.get(col)
            if pd.notna(val) and val != "":
                market_context[col] = val

    return {
        'metadata': metadata,
        'current_price': close_price,
        'timestamp': last_candle.get('Timestamp', ''),
        'session': sess,
        'is_active': is_active_pos,
        'long': {
            'total': total_long,
            'pct': pct_long,
            'decision': dec_long_str,
            'code': dec_long_code,
            'scores': scores_long,
            'details': details_long,
            'levels': {
                'sl_ketat': sl_ketat_L, 'sl_normal': sl_normal_L, 'sl_lebar': sl_lebar_L, 'tp1': tp1_L, 'tp2': tp2_L, 'tp3': tp3_L,
                'rr1': calc_rr_L(tp1_L, sl_normal_L), 'rr2': calc_rr_L(tp2_L, sl_normal_L), 'rr3': calc_rr_L(tp3_L, sl_normal_L)
            },
            'narrative': narrative_long
        },
        'short': {
            'total': total_short,
            'pct': pct_short,
            'decision': dec_short_str,
            'code': dec_short_code,
            'scores': scores_short,
            'details': details_short,
            'levels': {
                'sl_ketat': sl_ketat_S, 'sl_normal': sl_normal_S, 'sl_lebar': sl_lebar_S, 'tp1': tp1_S, 'tp2': tp2_S, 'tp3': tp3_S,
                'rr1': calc_rr_S(tp1_S, sl_normal_S), 'rr2': calc_rr_S(tp2_S, sl_normal_S), 'rr3': calc_rr_S(tp3_S, sl_normal_S)
            },
            'narrative': narrative_short
        },
        'exit': {
            'active': is_active_pos,
            'pnl_pct': pnl_pct if is_active_pos else 0,
            'signals': exit_signals,
            'recommendation': exit_reco
        },
        'context': market_context,
        'emergency': {
            'sl_hit': is_active_pos and close_price < sl_ketat_L,
            'rsi_overbought': is_active_pos and O_rsi > 75
        }
    }

def generate_html(data):
    # Determine color logic
    def get_color(code):
        return {'FULL': '#1D9E75', 'HALF': '#10b981', 'WAIT': '#BA7517', 'SKIP': '#E24B4A'}.get(code, '#555')
        
    def get_dot(score, max_score):
        # normalize to 0-3
        ratio = score / (max_score+0.0001)
        if ratio >= 0.99: return "#1D9E75"
        elif ratio >= 0.66: return "#BA7517"
        elif ratio >= 0.33: return "#D85A30"
        else: return "#E24B4A"

    def render_scores(scores, details, maxes):
        html = "<div class='scoring-grid'>"
        for k in ['OI', 'Vol', 'TakerBuy', 'ATR', 'CVD', 'EMA21', 'EMA50', 'EMA200', 'RSI']:
            sc = scores.get(k, 0)
            mx = maxes.get(k, 0)
            val = details.get(k, (0, 0))[1]
            dot = get_dot(details.get(k, (0,0))[0], maxes.get(k, 3) / maxes.get(k, 1)) if maxes.get(k, 1) else "#E24B4A"
            if k == 'RSI': dot = get_dot(details.get(k, (0,0))[0], 3)
            if k == 'EMA200': dot = get_dot(details.get(k, (0,0))[0], 3)
            # just direct score to dot lookup
            score_val = details.get(k, (0,0))[0]
            if score_val == 3: dot = "#1D9E75"
            elif score_val == 2: dot = "#BA7517"
            elif score_val == 1: dot = "#D85A30"
            else: dot = "#E24B4A"
            
            html += f"""
            <div class='score-item'>
                <div class='score-hdr'>
                    <span class='dot' style='background:{dot}'></span> {k}
                </div>
                <div class='score-val'>{val:+.2f}</div>
                <div class='score-bar'><div class='fill' style='width:{(sc/(mx+0.001))*100}%; background:{dot}'></div></div>
                <div class='score-pts'>{sc}/{mx} pts</div>
            </div>
            """
        html += "</div>"
        return html

    maxes = {'OI': 15, 'Vol': 12, 'TakerBuy': 8, 'ATR': 9, 'CVD': 9, 'EMA21': 6, 'EMA50': 6, 'EMA200': 3, 'RSI': 3}

    html = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <title>Quantitative Swing Dashboard</title>
        <style>
            :root {{
                --bg-main: #0a0a0b;
                --bg-card: #151518;
                --bg-card-hover: #1c1c1f;
                --text-main: #f0f0f5;
                --text-muted: #8b8b99;
                --border: #2a2a35;
                --brand: #2563eb;
                --green: #1D9E75;
                --red: #E24B4A;
                --yellow: #BA7517;
            }}
            * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', system-ui, sans-serif; }}
            body {{ background: var(--bg-main); color: var(--text-main); font-size: 14px; padding: 20px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            
            /* Header */
            .header {{ background: var(--bg-card); padding: 20px; border-radius: 12px; border: 1px solid var(--border); margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; }}
            .header-info h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
            .header-info p {{ color: var(--text-muted); }}
            .active-pos-badge {{ background: rgba(29, 158, 117, 0.1); border: 1px solid var(--green); color: var(--green); padding: 12px 16px; border-radius: 8px; font-weight: 500; text-align: right; }}
            
            /* Emergency */
            .emergency-banner {{ background: rgba(226, 75, 74, 0.15); border: 2px solid var(--red); color: #ff6b6b; padding: 16px; border-radius: 8px; margin-bottom: 24px; font-weight: bold; text-align: center; }}
            
            /* Tabs */
            .tabs {{ display: flex; gap: 12px; margin-bottom: 24px; }}
            .tab-btn {{ background: var(--bg-card); border: 1px solid var(--border); color: var(--text-muted); padding: 12px 24px; border-radius: 8px; cursor: pointer; font-weight: 600; transition: all 0.2s; }}
            .tab-btn.active {{ background: var(--border); color: var(--text-main); }}
            
            /* Tab Content */
            .tab-content {{ display: none; margin-bottom: 24px; }}
            .tab-content.active {{ display: block; }}
            
            /* Decision Banner */
            .decision {{ padding: 24px; border-radius: 12px; margin-bottom: 24px; text-align: center; border-left: 6px solid; }}
            .decision.FULL {{ background: rgba(29, 158, 117, 0.1); border-color: var(--green); }}
            .decision.HALF {{ background: rgba(16, 185, 129, 0.1); border-color: #10b981; }}
            .decision.WAIT {{ background: rgba(186, 117, 23, 0.1); border-color: var(--yellow); }}
            .decision.SKIP {{ background: rgba(226, 75, 74, 0.1); border-color: var(--red); }}
            .decision h2 {{ font-size: 28px; margin-bottom: 8px; }}
            .decision p {{ font-size: 16px; opacity: 0.9; }}
            
            /* Grid Modules */
            .section {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 24px; }}
            .section h3 {{ margin-bottom: 20px; font-size: 16px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; }}
            
            /* Scoring */
            .scoring-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }}
            .score-item {{ background: var(--bg-main); padding: 16px; border-radius: 8px; border: 1px solid var(--border); }}
            .score-hdr {{ display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; color: var(--text-muted); margin-bottom: 12px; }}
            .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
            .score-val {{ font-size: 20px; font-weight: 700; margin-bottom: 12px; }}
            .score-bar {{ height: 4px; background: var(--bg-card); border-radius: 2px; overflow: hidden; margin-bottom: 8px; }}
            .score-bar .fill {{ height: 100%; }}
            .score-pts {{ font-size: 12px; color: var(--text-muted); text-align: right; }}
            
            /* Levels */
            .levels-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }}
            .level-card {{ background: var(--bg-main); border: 1px solid var(--border); padding: 16px; border-radius: 8px; }}
            .level-card .lbl {{ font-size: 12px; color: var(--text-muted); margin-bottom: 8px; }}
            .level-card .val {{ font-size: 18px; font-weight: 600; margin-bottom: 8px; }}
            .rr-badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }}
            .rr-good {{ background: rgba(29, 158, 117, 0.2); color: var(--green); }}
            .rr-bad {{ background: rgba(226, 75, 74, 0.2); color: var(--red); }}
            
            /* Exits */
            .exit-row {{ display: flex; justify-content: space-between; align-items: center; padding: 12px; border-bottom: 1px solid var(--border); }}
            .exit-row:last-child {{ border: none; }}
            
            /* Narrative */
            .narrative p {{ line-height: 1.6; margin-bottom: 12px; font-size: 15px; }}
            .nar-lbl {{ font-weight: 600; color: var(--text-muted); display: block; margin-top: 20px; margin-bottom: 4px; font-size: 12px; text-transform: uppercase; }}
            .pos {{ color: #10b981; font-weight: 500; }}
            .neg {{ color: #ef4444; font-weight: 500; }}
            .neu {{ font-weight: 600; color: #fff; }}
            
            @media (max-width: 768px) {{
                .levels-grid {{ grid-template-columns: 1fr 1fr; }}
                .header {{ flex-direction: column; align-items: flex-start; gap: 16px; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- HEADER -->
            <div class="header">
                <div class="header-info">
                    <h1>{data['metadata'].get('Symbol', 'UNKNOWN')} &middot; {data['metadata'].get('Timeframe', '4H')} &middot; {data['session']}</h1>
                    <p>Current Close: ${data['current_price']:.5f} &nbsp;|&nbsp; Updated: {data['timestamp']}</p>
                </div>
                """
    if data['is_active']:
        html += f"""
                <div class="active-pos-badge">
                    <div style="font-size:12px; opacity:0.8;">POSISI AKTIF</div>
                    <div>Entry: ${data['metadata']['AVG_ENTRY_PRICE']} &middot; P&L: {data['exit']['pnl_pct']:+.2f}%</div>
                </div>
        """
    html += """
            </div>
    """

    if data['emergency']['sl_hit'] or data['emergency']['rsi_overbought']:
        msg = "⚠️ SL SUDAH TERSENTUH — EVALUASI EXIT SEGERA" if data['emergency']['sl_hit'] else "⚠️ RSI OVERBOUGHT — CEK EXIT SIGNAL"
        html += f"<div class='emergency-banner'>{msg}</div>"

    # Navigation
    html += """
            <div class="tabs">
                <button class="tab-btn active" onclick="openTab('LONG')">LONG Setup</button>
                <button class="tab-btn" onclick="openTab('SHORT')">SHORT Setup</button>
            </div>
    """

    for setup, key in [('LONG', 'long'), ('SHORT', 'short')]:
        act = "active" if setup == 'LONG' else ""
        d = data[key]
        html += f"""
            <div id="{setup}" class="tab-content {act}">
                <div class="decision {d['code']}">
                    <h2 style="color:var(--{'green' if d['code'] in ['FULL','HALF'] else ('yellow' if d['code']=='WAIT' else 'red')})">
                        {d['decision']} <span style="font-weight:400; opacity:0.7">|</span> {d['total']}/71
                    </h2>
                    <p>Persentase Akurasi Syarat: {d['pct']:.1f}%</p>
                </div>
                
                <div class="section">
                    <h3>Technical Scoring Breakdown</h3>
                    {render_scores(d['scores'], d['details'], maxes)}
                </div>
                
                <div class="section">
                    <h3>Risk Management & Targets</h3>
                    <div class="levels-grid">
                        <div class="level-card">
                            <div class="lbl">🛑 SL Ketat (1.0 ATR)</div>
                            <div class="val">${d['levels']['sl_ketat']:.5f}</div>
                        </div>
                        <div class="level-card">
                            <div class="lbl">🛑 SL Normal (1.5 ATR)</div>
                            <div class="val">${d['levels']['sl_normal']:.5f}</div>
                            <div class="rr-badge {'rr-good' if d['levels']['rr1'] >= 2 else ('' if d['levels']['rr1']==0 else 'rr-bad')}">
                                Min R:R: {d['levels']['rr1']:.2f}
                            </div>
                        </div>
                        <div class="level-card">
                            <div class="lbl">🛑 SL Lebar (2.0 ATR)</div>
                            <div class="val">${d['levels']['sl_lebar']:.5f}</div>
                        </div>
                        <div class="level-card" style="border-color: var(--brand);">
                            <div class="lbl">🎯 Target TP</div>
                            <div class="val" style="color: var(--text-main);">
                                TP1: ${d['levels']['tp1']:.5f}<br>
                                TP2: ${d['levels']['tp2']:.5f}<br>
                                TP3: ${d['levels']['tp3']:.5f}
                            </div>
                        </div>
                    </div>
                </div>
        """
        
        if data['is_active']:
            html += f"""
                <div class="section">
                    <h3>Exit Signals Monitor</h3>
                    <div style="background: var(--bg-main); border: 1px solid var(--border); border-radius: 8px;">
            """
            for icon, name, val, th in data['exit']['signals']:
                html += f"""<div class="exit-row">
                    <div><span>{icon}</span> <span style="margin-left:8px; font-weight:500">{name}</span></div>
                    <div style="color:var(--text-muted)">{val:.2f} ({th})</div>
                </div>"""
            if not data['exit']['signals']:
                html += "<div style='padding:16px; text-align:center; color:var(--text-muted)'>Semua indikator masih dalam batas aman.</div>"
            
            reco_color = '#E24B4A' if 'EXIT' in data['exit']['recommendation'] else ('#BA7517' if 'ketat' in data['exit']['recommendation'] else '#1D9E75')
            html += f"""
                    </div>
                    <div style="margin-top: 16px; padding: 12px; border-radius: 8px; background: {reco_color}22; border: 1px solid {reco_color}; text-align: center; font-weight: 600; color: {reco_color}">
                        MANDATE: {data['exit']['recommendation']}
                    </div>
                </div>
            """

        nar = d['narrative']
        hd_color = "var(--green)" if setup=="LONG" else "var(--red)"
        html += f"""
                <div class="section narrative">
                    <h3 style="color:{hd_color}">Narasi Analis Utama</h3>
                    <span class="nar-lbl">Kondisi Pasar</span>
                    <p>{nar['kondisi'].replace('-', '—')}</p>
                    <span class="nar-lbl">Keputusan Rasional</span>
                    <p>{nar['keputusan']}</p>
                    <span class="nar-lbl">Skenario Lanjutan</span>
                    <p>{nar['skenario']}</p>
                </div>
            </div>
        """

    html += """
        </div>
        <script>
            function openTab(tabId) {
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                event.currentTarget.classList.add('active');
            }
        </script>
    </body>
    </html>
    """
    
    with open('analysis_dashboard.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("✅ Successfully generated analysis_dashboard.html")

if __name__ == "__main__":
    filepath = CSV_FILE
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        
    print(f"Loading {filepath}...")
    metadata, df = parse_csv_and_metadata(filepath)
    print("Computing metrics...")
    data = calculate_scoring(df, metadata)
    if data:
        generate_html(data)
