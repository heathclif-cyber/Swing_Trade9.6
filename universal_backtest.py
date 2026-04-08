import pandas as pd
import algo_scoring
from tqdm import tqdm
import os
import argparse

def run_universal_backtest(csv_file, symbol, trade_direction, window_size=9999999):
    print(f"🔄 Membaca data dari {csv_file}...")
    if not os.path.exists(csv_file):
        print(f"❌ Ralat: Fail {csv_file} tidak dijumpai di direktori ini.")
        return None, None

    try:
        df = pd.read_csv(csv_file, comment='#')
    except Exception as e:
        print(f"❌ Gagal membaca fail: {e}")
        return None, None

    if 'Timestamp' in df.columns:
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    
    trade_history = []
    active_trade = None
    
    start_idx = 101 # Perlukan buffer untuk pengiraan MA/ATR
    total_candles = len(df)
    
    print(f"🚀 Memulakan simulasi {trade_direction} pada {total_candles - start_idx} candle untuk {symbol}...")
    
    for i in tqdm(range(start_idx, total_candles)):
        window_start = 0 if window_size > i else max(0, i - window_size)
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
                if active_trade.get('tp3_hit', False):
                    active_trade['sl'] = max(active_trade['sl'], ema_50)

                if low_price <= active_trade['sl']:
                    active_trade['status'] = 'CLOSED_RUNNER_AT_EMA50' if active_trade.get('tp3_hit', False) else 'CLOSED_SL_OR_TRAILING'
                    active_trade['exit_price'] = active_trade['sl']
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (active_trade['exit_price'] / active_trade['entry_price'] - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue

                if high_price >= active_trade['tp1'] and not active_trade.get('tp1_hit', False):
                    active_trade['tp1_hit'] = True
                    active_trade['sl'] = max(active_trade['sl'], active_trade['entry_price']) 
                    
                if high_price >= active_trade['tp2'] and not active_trade.get('tp2_hit', False):
                    active_trade['tp2_hit'] = True
                    active_trade['sl'] = max(active_trade['sl'], active_trade['tp1']) 
                    
                if high_price >= active_trade['tp3'] and not active_trade.get('tp3_hit', False):
                    active_trade['tp3_hit'] = True
                    active_trade['sl'] = max(active_trade['tp2'], ema_50) 

            elif active_trade['side'] == 'SHORT':
                if active_trade.get('tp3_hit', False):
                    active_trade['sl'] = min(active_trade['sl'], ema_50)

                if high_price >= active_trade['sl']:
                    active_trade['status'] = 'CLOSED_RUNNER_AT_EMA50' if active_trade.get('tp3_hit', False) else 'CLOSED_SL_OR_TRAILING'
                    active_trade['exit_price'] = active_trade['sl']
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (active_trade['entry_price'] / active_trade['exit_price'] - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue

                if low_price <= active_trade['tp1'] and not active_trade.get('tp1_hit', False):
                    active_trade['tp1_hit'] = True
                    active_trade['sl'] = min(active_trade['sl'], active_trade['entry_price'])
                    
                if low_price <= active_trade['tp2'] and not active_trade.get('tp2_hit', False):
                    active_trade['tp2_hit'] = True
                    active_trade['sl'] = min(active_trade['sl'], active_trade['tp1'])
                    
                if low_price <= active_trade['tp3'] and not active_trade.get('tp3_hit', False):
                    active_trade['tp3_hit'] = True
                    active_trade['sl'] = min(active_trade['tp2'], ema_50)

        # ==========================================
        # 2. MENCARI ENTRY BARU
        # ==========================================
        if active_trade is None:
            meta_new = {'Symbol': symbol, 'AVG_ENTRY_PRICE': None, 'ENTRY_DATE': None}
            res_new = algo_scoring.calculate_71point_score(window_df, meta_new)
            
            if res_new is None:
                continue
            
            long_data = res_new.get('long', {})
            short_data = res_new.get('short', {})
            
            code_L = long_data.get('code', 'SKIP')
            code_S = short_data.get('code', 'SKIP')
            
            levels_L = long_data.get('levels', {})
            levels_S = short_data.get('levels', {})
            
            if (trade_direction in ['LONG', 'BOTH']) and (code_L in ['FULL', 'HALF']):
                sl_struct = levels_L.get('sl_structure', close_price * 0.95)
                sl_lebar = levels_L.get('sl_lebar', close_price * 0.95)
                safe_sl = min(float(sl_struct), float(sl_lebar))
                
                tp1 = float(levels_L.get('tp1', close_price * 1.05))
                
                if (tp1 / close_price - 1) * 100 >= 2.0:
                    active_trade = {
                        'side': 'LONG',
                        'entry_date': timestamp_now,
                        'entry_price': close_price,
                        'sl': safe_sl,
                        'tp1': tp1,
                        'tp2': float(levels_L.get('tp2', close_price * 1.10)),
                        'tp3': float(levels_L.get('tp3', close_price * 1.15)),
                        'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False, 'status': 'OPEN'
                    }
                    continue 
                    
            if (trade_direction in ['SHORT', 'BOTH']) and (code_S in ['FULL', 'HALF']) and (active_trade is None):
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
                        'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False, 'status': 'OPEN'
                    }

    # ==========================================
    # 3. KIRAAN PRESTASI (HASIL)
    # ==========================================
    print("\n" + "="*60)
    print(f"📈 KEPUTUSAN BACKTEST MODULAR ({trade_direction})")
    print("="*60)
    
    total_trades = len(trade_history)
    
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
        return None, None

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

    # KEMBALIKAN DATA KE COLAB
    return df, trade_history

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Universal Backtester")
    parser.add_argument("--csv", type=str, required=True, help="Nama fail CSV")
    parser.add_argument("--symbol", type=str, required=True, help="Symbol Koin (cth: DOGEUSDT)")
    parser.add_argument("--direction", type=str, default="LONG", help="Arah trade: LONG, SHORT, atau BOTH")
    args = parser.parse_args()
    
    run_universal_backtest(args.csv, args.symbol, args.direction)