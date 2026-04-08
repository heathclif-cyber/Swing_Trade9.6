import pandas as pd
import algo_scoring
from tqdm import tqdm
import matplotlib.pyplot as plt
import os

# ==========================================
# ⚙️ KONFIGURASI BACKTEST (EMA-50 SURFER)
# ==========================================
# Tukar mengikut nama fail CSV anda
CSV_FILE = "DOGEUSDT_4H_4Years.csv" # Contoh fail yang anda ada
SYMBOL = "DOGEUSDT"
TRADE_DIRECTION = 'LONG'  # Pilihan: 'LONG', 'SHORT', atau 'BOTH'
WINDOW_SIZE = 9999999     
# ==========================================

def run_universal_backtest():
    print(f"🔄 Membaca data dari {CSV_FILE}...")
    if not os.path.exists(CSV_FILE):
        print(f"❌ Ralat: Fail {CSV_FILE} tidak dijumpai di direktori ini.")
        return

    try:
        df = pd.read_csv(CSV_FILE, comment='#')
    except Exception as e:
        print(f"❌ Gagal membaca fail: {e}")
        return

    if 'Timestamp' in df.columns:
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    
    trade_history = []
    active_trade = None
    
    start_idx = 101 # Perlukan buffer untuk pengiraan MA/ATR
    total_candles = len(df)
    
    print(f"🚀 Memulakan simulasi {TRADE_DIRECTION} pada {total_candles - start_idx} candle...")
    
    for i in tqdm(range(start_idx, total_candles)):
        window_start = 0 if WINDOW_SIZE > i else max(0, i - WINDOW_SIZE)
        window_df = df.iloc[window_start : i+1].copy()
        current_candle = window_df.iloc[-1]
        
        timestamp_now = current_candle['Timestamp'] if 'Timestamp' in current_candle else i
        high_price = float(current_candle['High'])
        low_price = float(current_candle['Low'])
        close_price = float(current_candle['Close'])
        
        # 💡 Tarik data EMA-50 untuk kawalan mod Runner
        ema_50 = float(current_candle['EMA_50']) if 'EMA_50' in current_candle else close_price
        
        # ==========================================
        # 1. PENGURUSAN TRADE AKTIF (Runner Mode)
        # ==========================================
        if active_trade is not None:
            if active_trade['side'] == 'LONG':
                # SL Dinamik naik mengikut EMA 50 jika TP3 sudah kena (Runner)
                if active_trade.get('tp3_hit', False):
                    active_trade['sl'] = max(active_trade['sl'], ema_50)

                # Cek SL Hit / Trailing SL
                if low_price <= active_trade['sl']:
                    active_trade['status'] = 'CLOSED_RUNNER_AT_EMA50' if active_trade.get('tp3_hit', False) else 'CLOSED_SL_OR_TRAILING'
                    active_trade['exit_price'] = active_trade['sl']
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (active_trade['exit_price'] / active_trade['entry_price'] - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue

                # Cek TP1 (Aman Modal)
                if high_price >= active_trade['tp1'] and not active_trade.get('tp1_hit', False):
                    active_trade['tp1_hit'] = True
                    active_trade['sl'] = max(active_trade['sl'], active_trade['entry_price']) 
                    
                # Cek TP2 (Kunci Profit)
                if high_price >= active_trade['tp2'] and not active_trade.get('tp2_hit', False):
                    active_trade['tp2_hit'] = True
                    active_trade['sl'] = max(active_trade['sl'], active_trade['tp1']) 
                    
                # Cek TP3 (Lepas jadi Runner!)
                if high_price >= active_trade['tp3'] and not active_trade.get('tp3_hit', False):
                    active_trade['tp3_hit'] = True
                    active_trade['sl'] = max(active_trade['tp2'], ema_50) 

            elif active_trade['side'] == 'SHORT':
                # SL Dinamik turun mengikut EMA 50
                if active_trade.get('tp3_hit', False):
                    active_trade['sl'] = min(active_trade['sl'], ema_50)

                # Cek SL Hit / Trailing SL
                if high_price >= active_trade['sl']:
                    active_trade['status'] = 'CLOSED_RUNNER_AT_EMA50' if active_trade.get('tp3_hit', False) else 'CLOSED_SL_OR_TRAILING'
                    active_trade['exit_price'] = active_trade['sl']
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (active_trade['entry_price'] / active_trade['exit_price'] - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue

                # Cek TP1
                if low_price <= active_trade['tp1'] and not active_trade.get('tp1_hit', False):
                    active_trade['tp1_hit'] = True
                    active_trade['sl'] = min(active_trade['sl'], active_trade['entry_price'])
                    
                # Cek TP2
                if low_price <= active_trade['tp2'] and not active_trade.get('tp2_hit', False):
                    active_trade['tp2_hit'] = True
                    active_trade['sl'] = min(active_trade['sl'], active_trade['tp1'])
                    
                # Cek TP3
                if low_price <= active_trade['tp3'] and not active_trade.get('tp3_hit', False):
                    active_trade['tp3_hit'] = True
                    active_trade['sl'] = min(active_trade['tp2'], ema_50)

        # ==========================================
        # 2. MENCARI ENTRY BARU
        # ==========================================
        if active_trade is None:
            # Memanggil Orchestrator algo_scoring
            meta_new = {'Symbol': SYMBOL, 'AVG_ENTRY_PRICE': None, 'ENTRY_DATE': None}
            res_new = algo_scoring.calculate_71point_score(window_df, meta_new)
            
            if res_new is None:
                continue
            
            long_data = res_new.get('long', {})
            short_data = res_new.get('short', {})
            
            code_L = long_data.get('code', 'SKIP')
            code_S = short_data.get('code', 'SKIP')
            
            levels_L = long_data.get('levels', {})
            levels_S = short_data.get('levels', {})
            
            # Eksekusi LONG
            if (TRADE_DIRECTION in ['LONG', 'BOTH']) and (code_L in ['FULL', 'HALF']):
                sl_struct = levels_L.get('sl_structure', close_price * 0.95)
                sl_lebar = levels_L.get('sl_lebar', close_price * 0.95)
                safe_sl = min(float(sl_struct), float(sl_lebar))
                
                tp1 = float(levels_L.get('tp1', close_price * 1.05))
                
                # Syarat wajib: TP1 mesti minimum 2.0%
                if (tp1 / close_price - 1) * 100 >= 2.0:
                    active_trade = {
                        'side': 'LONG',
                        'entry_date': timestamp_now,
                        'entry_price': close_price,
                        'sl': safe_sl,
                        'tp1': tp1,
                        'tp2': float(levels_L.get('tp2', close_price * 1.10)),
                        'tp3': float(levels_L.get('tp3', close_price * 1.15)),
                        'tp1_hit': False,
                        'tp2_hit': False,
                        'tp3_hit': False,
                        'status': 'OPEN'
                    }
                    continue 
                    
            # Eksekusi SHORT
            if (TRADE_DIRECTION in ['SHORT', 'BOTH']) and (code_S in ['FULL', 'HALF']) and (active_trade is None):
                sl_struct = levels_S.get('sl_structure', close_price * 1.05)
                sl_lebar = levels_S.get('sl_lebar', close_price * 1.05)
                safe_sl = max(float(sl_struct), float(sl_lebar))
                
                tp1 = float(levels_S.get('tp1', close_price * 0.95))
                
                if (1 - tp1 / close_price) * 100 >= 2.0:
                    active_trade = {
                        'side': 'SHORT',
                        'entry_date': timestamp_now,
                        'entry_price': close_price,
                        'sl': safe_sl,
                        'tp1': tp1,
                        'tp2': float(levels_S.get('tp2', close_price * 0.90)),
                        'tp3': float(levels_S.get('tp3', close_price * 0.85)),
                        'tp1_hit': False,
                        'tp2_hit': False,
                        'tp3_hit': False,
                        'status': 'OPEN'
                    }

    # ==========================================
    # 3. KIRAAN PRESTASI (HASIL)
    # ==========================================
    print("\n" + "="*60)
    print(f"📈 KEPUTUSAN BACKTEST MODULAR ({TRADE_DIRECTION})")
    print("="*60)
    
    total_trades = len(trade_history)
    
    # Tutup trade yang masih berjalan pada penghujung data
    if active_trade is not None:
        last_close = float(current_candle['Close'])
        active_trade['exit_price'] = last_close
        if active_trade['side'] == 'LONG':
            active_trade['pnl_pct'] = (last_close / active_trade['entry_price'] - 1) * 100
        else:
            active_trade['pnl_pct'] = (active_trade['entry_price'] / last_close - 1) * 100
        active_trade['status'] = 'CLOSED_END_OF_DATA'
        active_trade['exit_date'] = timestamp_now
        trade_history.append(active_trade)
        total_trades += 1

    if total_trades == 0:
        print("Tiada sebarang trade terhasil daripada setting ini.")
        return

    wins = [t for t in trade_history if t['pnl_pct'] > 0]
    losses = [t for t in trade_history if t['pnl_pct'] <= 0]
    
    win_rate = (len(wins) / total_trades) * 100
    total_pnl = sum(t['pnl_pct'] for t in trade_history)
    
    print(f"Total Trade              : {total_trades}")
    print(f"Trade Menang (Win)       : {len(wins)}")
    print(f"Trade Kalah (Loss/BE)    : {len(losses)}")
    print(f"Kadar Kemenangan (Win%)  : {win_rate:.2f}%")
    print(f"Total PnL Terkumpul      : {total_pnl:+.2f}%\n")

    print("\n20 Trade Terakhir:")
    for t in trade_history[-20:]:
        print(f" 🔹 {t['entry_date']} | {t['side']} | Entry: {t['entry_price']:.4f} | Exit: {t['exit_price']:.4f} | PnL: {t['pnl_pct']:+.2f}% | Status: {t['status']}")
    print("="*60)

    # ==========================================
    # 4. VISUALISASI GRAFIK
    # ==========================================
    print("\n📊 Membina grafik Analisis Trade...")
    df_trades = pd.DataFrame(trade_history)
    df_trades['entry_date'] = pd.to_datetime(df_trades['entry_date'])
    df_trades['exit_date'] = pd.to_datetime(df_trades['exit_date'])
    df_trades = df_trades.sort_values('exit_date')
    df_trades['cum_pnl'] = df_trades['pnl_pct'].cumsum()
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12), gridspec_kw={'height_ratios': [2, 1]}, sharex=False)
    
    ax1.plot(df['Timestamp'], df['Close'], label=f'Harga {SYMBOL}', color='gray', alpha=0.4, linewidth=1.5)
    
    for index, t in df_trades.iterrows():
        trade_color = 'green' if t['pnl_pct'] > 0 else 'red'
        marker_entry = '^' if t['side'] == 'LONG' else 'v'
        
        ax1.scatter(t['entry_date'], t['entry_price'], color='blue', marker=marker_entry, s=100, zorder=5, alpha=0.8)
        ax1.scatter(t['exit_date'], t['exit_price'], color=trade_color, marker='x', s=100, zorder=5, linewidths=2)
        ax1.plot([t['entry_date'], t['exit_date']], [t['entry_price'], t['exit_price']], color=trade_color, linestyle='--', alpha=0.6, linewidth=1.5)
    
    ax1.scatter([], [], color='blue', marker='^', label='Entry (LONG)')
    ax1.scatter([], [], color='blue', marker='v', label='Entry (SHORT)')
    ax1.scatter([], [], color='green', marker='x', label='Exit (Win)')
    ax1.scatter([], [], color='red', marker='x', label='Exit (Loss/BE)')
    
    ax1.set_title(f"Aksi Harga & Posisi Trade - {SYMBOL} ({TRADE_DIRECTION})", fontsize=16, fontweight='bold')
    ax1.set_ylabel("Harga (USDT)", fontsize=12)
    ax1.grid(True, linestyle='--', alpha=0.5)
    ax1.legend(loc='upper left', fontsize=10, framealpha=0.9)
    
    ax2.plot(df_trades['exit_date'], df_trades['cum_pnl'], label='Cumulative PnL (%)', color='#2ca02c', linewidth=2.5)
    win_pts = df_trades[df_trades['pnl_pct'] > 0]
    loss_pts = df_trades[df_trades['pnl_pct'] <= 0]
    ax2.scatter(win_pts['exit_date'], win_pts['cum_pnl'], color='green', marker='^', s=80, zorder=5)
    ax2.scatter(loss_pts['exit_date'], loss_pts['cum_pnl'], color='red', marker='v', s=80, zorder=5)
    
    ax2.set_title("Pertumbuhan Modal / Equity Curve", fontsize=14, fontweight='bold')
    ax2.set_xlabel("Tarikh Trade Ditutup", fontsize=12)
    ax2.set_ylabel("PnL Terkumpul (%)", fontsize=12)
    ax2.grid(True, linestyle='--', alpha=0.5)
    ax2.axhline(0, color='black', linewidth=1.5, linestyle='-')
    
    info_text = (f"Arah: {TRADE_DIRECTION}\nJumlah Trade: {total_trades}\nWin Rate: {win_rate:.2f}%\nTotal PnL: {total_pnl:+.2f}%")
    ax2.text(0.015, 0.95, info_text, transform=ax2.transAxes, fontsize=11, verticalalignment='top', bbox=dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.9, edgecolor='gray'))
    
    plt.tight_layout()
    filename = f"Backtest_Visual_{SYMBOL}_{TRADE_DIRECTION}_Modular.png"
    plt.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"✅ Gambar carta disimpan: {filename}")
    plt.show()

if __name__ == "__main__":
    run_universal_backtest()