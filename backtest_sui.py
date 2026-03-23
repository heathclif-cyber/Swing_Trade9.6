import pandas as pd
import numpy as np
import os

FILE_NAME = "DOGEUSDT_4H_4Years.csv"
FILE_PATH = FILE_NAME

if not os.path.exists(FILE_PATH):
    print("❌ File tidak ditemukan. Pastikan nama dan lokasi file benar.")
    exit(1)

df = pd.read_csv(FILE_PATH)
df.fillna(method='ffill', inplace=True)
df.dropna(subset=['Close', 'EMA_21', 'ATR_14'], inplace=True)
df.reset_index(drop=True, inplace=True)

capital = 1000.0
position_size_usd = 200.0

in_position = False
entry_price = 0.0
entry_time = None
active_sl = 0.0
tactical_tp1 = 0.0
tactical_tp2 = 0.0
tactical_tp3 = 0.0
coins_held = 0.0
phase_at_entry = ""
tp_phase = 0 # 0=none, 1=hit tp1, 2=hit tp2

trades = []

for i in range(5, len(df)):
    row = df.iloc[i]
    prev_row = df.iloc[i-1]
    
    current_price = row['Close']
    current_time = row['Timestamp'] if 'Timestamp' in df.columns else str(i)
    
    ema_21 = row.get('EMA_21', row.get('EMA_21_H4', 0))
    ema_50 = row.get('EMA_50', row.get('EMA_50_H4', 0))
    ema_200 = row.get('EMA_200', row.get('EMA_200_H4', 0))
    atr = row['ATR_14']
    
    cvd = row.get('CVD', 0)
    prev_cvd = prev_row.get('CVD', 0)
    oi = row.get('Open_Interest', 0)
    prev_oi = prev_row.get('Open_Interest', 0)
    
    pdh = row.get('PDH', 0)
    pdl = row.get('PDL', 0)
    pwh = row.get('PWH', current_price + (atr * 4))
    pwl = row.get('PWL', current_price - (atr * 4))
    fib_618 = row.get('Fib_0.618', 0)
    sfp_sweep = row.get('SFP_Sweep', False)
    
    # 1. Tentukan Fase Pasar
    if current_price > ema_21 and current_price > ema_200:
        market_phase = "Markup"
    elif current_price < ema_21 and current_price < ema_200:
        market_phase = "Markdown"
    else:
        market_phase = "Consolidation"
        
    price_up = current_price > prev_row['Close']
    volume_fakeout = price_up and (cvd < prev_cvd)
    oi_divergence = price_up and (oi < prev_oi)

    # --- LOGIKA ENTRY ---
    if not in_position:
        trigger_entry = False
        entry_reason = ""
        
        # Opsi A: Markup (Trend Riding, Pullback ke EMA 21 / 50)
        if market_phase == "Markup":
            if (row['Low'] <= ema_21 and current_price >= ema_21) or (row['Low'] <= ema_50 and current_price >= ema_50):
                if not volume_fakeout and not oi_divergence:
                    trigger_entry = True
                    entry_reason = "Markup: Pullback ke EMA 21/50"
                    
        # Opsi B: Markdown (Safety Net di area discount ekstrem)
        elif market_phase == "Markdown":
            # Beli hanya jika OTE divalidasi dengan volume positif atau terjadi Sweep
            is_ote = (current_price <= fib_618) if fib_618 > 0 else False
            if sfp_sweep or (is_ote and (cvd > prev_cvd)):
                trigger_entry = True
                entry_reason = "Markdown: Safety Net di OTE/SFP"

        if trigger_entry:
            in_position = True
            entry_price = current_price
            entry_time = current_time
            coins_held = position_size_usd / entry_price
            phase_at_entry = market_phase
            tp_phase = 0
            
            # SL: Swing Low dikurangi 1.5x ATR
            base_low = pwl if pwl > 0 else current_price - (atr * 2)
            active_sl = base_low - (atr * 1.5)
            # Batasi SL agar tidak terlalu lebar atau negatif
            if active_sl >= current_price or (current_price - active_sl) > (atr * 3.5):
                active_sl = current_price - (atr * 2.0)
                
            # TP1: Tembok Struktural EMA terdekat
            tactical_tp1 = ema_21 if phase_at_entry == "Markdown" else current_price + (atr * 1.5)
            if tactical_tp1 <= current_price:
                tactical_tp1 = current_price + atr
                
            tactical_tp2 = pdh if pdh > tactical_tp1 else tactical_tp1 + atr
            tactical_tp3 = pwh if pwh > tactical_tp2 else tactical_tp2 + atr

    # --- LOGIKA EXIT ---
    elif in_position:
        is_exit = False
        exit_reason = ""
        pnl_usd = 0.0
        
        # 1. Kill Switch Mutlak
        if volume_fakeout and phase_at_entry == "Markup":
            is_exit = True
            exit_reason = "KILL SWITCH: Volume Fakeout"
            
        elif oi_divergence and phase_at_entry == "Markup":
            is_exit = True
            exit_reason = "KILL SWITCH: OI Divergence"
            
        elif current_price < ema_21 and current_price < ema_200 and phase_at_entry == "Markup":
            is_exit = True
            exit_reason = "KILL SWITCH: Structural Breakdown"
            
        # 2. Stop Loss (Dipukul)
        elif current_price <= active_sl:
            is_exit = True
            exit_reason = "SL Hit"
            
        # 3. TP 1 (Jual 30%, SL to BE)
        elif tp_phase == 0 and current_price >= tactical_tp1:
            sold_coins = coins_held * 0.30
            pnl_usd += sold_coins * (current_price - entry_price)
            coins_held -= sold_coins
            tp_phase = 1
            # Geser SL ke Break Even
            active_sl = entry_price * 1.002
            
        # 4. TP 2 (PDH)
        elif tp_phase == 1 and current_price >= tactical_tp2:
            # Misal jual lagi 40%
            sold_coins = coins_held * 0.50
            pnl_usd += sold_coins * (current_price - entry_price)
            coins_held -= sold_coins
            tp_phase = 2
            
        # 5. TP 3 (PWH - Moonbag Selesai)
        elif tp_phase == 2 and current_price >= tactical_tp3:
            is_exit = True
            exit_reason = "TP3: Moonbag Hit"

        if is_exit:
            exit_price = current_price
            pnl_usd += coins_held * (exit_price - entry_price)
            capital += pnl_usd
            
            pnl_perc = (pnl_usd / position_size_usd) * 100
            
            trades.append({
                "Entry_Time": entry_time,
                "Exit_Time": current_time,
                "Entry_Reason": entry_reason,
                "Exit_Reason": exit_reason,
                "Entry_Price": entry_price,
                "Exit_Price": exit_price,
                "PnL_%": pnl_perc,
                "PnL_USD": pnl_usd
            })
            
            in_position = False
            coins_held = 0.0

print("\n📊 ==== HASIL BACKTEST OMNISCIENT (DATA-DRIVEN GUARDIAN) ====")
print(f"File Data        : {FILE_NAME}")
print(f"Total Trade      : {len(trades)}")

if trades:
    df_trades = pd.DataFrame(trades)
    win_trades = df_trades[df_trades['PnL_USD'] > 0]
    loss_trades = df_trades[df_trades['PnL_USD'] <= 0]
    
    win_rate = (len(win_trades) / len(trades)) * 100
    avg_win_usd = win_trades['PnL_USD'].mean() if not win_trades.empty else 0
    avg_loss_usd = loss_trades['PnL_USD'].mean() if not loss_trades.empty else 0
    
    print(f"Modal Akhir      : ${capital:.2f} (Awal: $1000.00)")
    print(f"Total PnL Net    : ${capital - 1000:.2f}")
    print(f"Total Return     : {((capital - 1000)/1000)*100:.2f}%")
    print(f"Win Rate         : {win_rate:.2f}% ({len(win_trades)} Win, {len(loss_trades)} Loss)")
    print(f"Net Profit/Win   : ${avg_win_usd:.2f} per trade")
    print(f"Net Loss/Loss    : ${avg_loss_usd:.2f} per trade")
    
    real_rr = abs(avg_win_usd / avg_loss_usd) if avg_loss_usd != 0 else 0
    print(f"Realized R:R     : 1 : {real_rr:.2f}")
    
    out_file = f"Backtest_Protocol96_DataDrivenGuardian.csv"
    df_trades.to_csv(out_file, index=False)
    print(f"\n📂 Laporan Data-Driven disave ke: {out_file}")
