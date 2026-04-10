import pandas as pd
import algo_scoring
from tqdm import tqdm
import os
import argparse

def run_universal_backtest(csv_file, symbol, trade_direction, window_size=9999999, 
                           use_breakeven=True, sl_type='sl_lebar', 
                           dynamic_exit=None, max_hold_candles=None, tp_strategy='scaling'):
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
    
    start_idx = 101
    total_candles = len(df)
    
    for i in tqdm(range(start_idx, total_candles)):
        window_start = 0 if window_size > i else max(0, i - window_size)
        window_df = df.iloc[window_start : i+1].copy()
        current_candle = window_df.iloc[-1]
        
        timestamp_now = current_candle['Timestamp'] if 'Timestamp' in current_candle else i
        high_price = float(current_candle['High'])
        low_price = float(current_candle['Low'])
        close_price = float(current_candle['Close'])
        ema_50 = float(current_candle['EMA_50']) if 'EMA_50' in current_candle else close_price
        
        # Ekstraksi Indikator Dinamis
        rsi_6 = float(current_candle.get('RSI_6', 50))
        stoch_k = float(current_candle.get('StochRSI_K', 50))
        stoch_d = float(current_candle.get('StochRSI_D', 50))
        prev_k = float(window_df.iloc[-2].get('StochRSI_K', 50)) if len(window_df) >= 2 else stoch_k
        prev_d = float(window_df.iloc[-2].get('StochRSI_D', 50)) if len(window_df) >= 2 else stoch_d
        
        stoch_cross_down = (stoch_k < stoch_d) and (prev_k >= prev_d)
        stoch_cross_up = (stoch_k > stoch_d) and (prev_k <= prev_d)

        I_cvd = float(current_candle.get('CVD', 0))
        J_cvd = float(window_df.iloc[-21].get('CVD', 0)) if len(window_df) >= 21 else I_cvd
        close_21 = float(window_df.iloc[-21].get('Close', 0)) if len(window_df) >= 21 else close_price
        
        cvd_div_bear = (I_cvd < J_cvd) and (close_price > close_21)
        cvd_div_bull = (I_cvd > J_cvd) and (close_price < close_21)

        # ==========================================
        # 1. PENGURUSAN TRADE AKTIF
        # ==========================================
        if active_trade is not None:
            # A. Time-Based Exit
            if max_hold_candles and (i - active_trade['entry_index']) >= max_hold_candles:
                active_trade['status'] = 'CLOSED_TIME_LIMIT'
                active_trade['exit_price'] = close_price
                active_trade['exit_date'] = timestamp_now
                active_trade['pnl_pct'] = (close_price / active_trade['entry_price'] - 1) * 100 if active_trade['side'] == 'LONG' else (active_trade['entry_price'] / close_price - 1) * 100
                trade_history.append(active_trade)
                active_trade = None
                continue

            # B. Dynamic Exits
            if dynamic_exit == 'EMA21' and 'EMA_21' in current_candle:
                ema_21 = float(current_candle['EMA_21'])
                if (active_trade['side'] == 'LONG' and close_price < ema_21) or (active_trade['side'] == 'SHORT' and close_price > ema_21):
                    active_trade['status'] = 'CLOSED_DYNAMIC_EMA21'
                    active_trade['exit_price'] = close_price
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (close_price / active_trade['entry_price'] - 1) * 100 if active_trade['side'] == 'LONG' else (active_trade['entry_price'] / close_price - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue
            elif dynamic_exit == 'MOMENTUM':
                if active_trade['side'] == 'LONG' and rsi_6 > 75 and stoch_cross_down:
                    active_trade['status'] = 'CLOSED_DYNAMIC_MOMENTUM'
                    active_trade['exit_price'] = close_price
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (close_price / active_trade['entry_price'] - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue
                elif active_trade['side'] == 'SHORT' and rsi_6 < 25 and stoch_cross_up:
                    active_trade['status'] = 'CLOSED_DYNAMIC_MOMENTUM'
                    active_trade['exit_price'] = close_price
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (active_trade['entry_price'] / close_price - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue
            elif dynamic_exit == 'CVD':
                if active_trade['side'] == 'LONG' and cvd_div_bear:
                    active_trade['status'] = 'CLOSED_DYNAMIC_CVD_BEAR'
                    active_trade['exit_price'] = close_price
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (close_price / active_trade['entry_price'] - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue
                elif active_trade['side'] == 'SHORT' and cvd_div_bull:
                    active_trade['status'] = 'CLOSED_DYNAMIC_CVD_BULL'
                    active_trade['exit_price'] = close_price
                    active_trade['exit_date'] = timestamp_now
                    active_trade['pnl_pct'] = (active_trade['entry_price'] / close_price - 1) * 100
                    trade_history.append(active_trade)
                    active_trade = None
                    continue

            # C. Fixed Hit & Run Logic vs Scaling
            if active_trade['side'] == 'LONG':
                if tp_strategy.startswith('fixed_rr'):
                    if high_price >= active_trade['tp_fixed']:
                        active_trade['status'] = 'CLOSED_HIT_AND_RUN'
                        active_trade['exit_price'] = active_trade['tp_fixed']
                        active_trade['exit_date'] = timestamp_now
                        active_trade['pnl_pct'] = (active_trade['exit_price'] / active_trade['entry_price'] - 1) * 100
                        trade_history.append(active_trade)
                        active_trade = None
                        continue

                if active_trade is not None:
                    if low_price <= active_trade['sl']:
                        active_trade['status'] = 'CLOSED_SL'
                        active_trade['exit_price'] = active_trade['sl']
                        active_trade['exit_date'] = timestamp_now
                        active_trade['pnl_pct'] = (active_trade['exit_price'] / active_trade['entry_price'] - 1) * 100
                        trade_history.append(active_trade)
                        active_trade = None
                        continue

                    if tp_strategy == 'scaling':
                        if high_price >= active_trade['tp1'] and not active_trade.get('tp1_hit', False):
                            active_trade['tp1_hit'] = True
                            if use_breakeven: active_trade['sl'] = max(active_trade['sl'], active_trade['entry_price']) 
                        if high_price >= active_trade['tp2'] and not active_trade.get('tp2_hit', False):
                            active_trade['tp2_hit'] = True
                            if use_breakeven: active_trade['sl'] = max(active_trade['sl'], active_trade['tp1']) 
                        if high_price >= active_trade['tp3'] and not active_trade.get('tp3_hit', False):
                            active_trade['tp3_hit'] = True
                            if use_breakeven: active_trade['sl'] = max(active_trade['tp2'], ema_50) 

            elif active_trade['side'] == 'SHORT':
                if tp_strategy.startswith('fixed_rr'):
                    if low_price <= active_trade['tp_fixed']:
                        active_trade['status'] = 'CLOSED_HIT_AND_RUN'
                        active_trade['exit_price'] = active_trade['tp_fixed']
                        active_trade['exit_date'] = timestamp_now
                        active_trade['pnl_pct'] = (active_trade['entry_price'] / active_trade['exit_price'] - 1) * 100
                        trade_history.append(active_trade)
                        active_trade = None
                        continue

                if active_trade is not None:
                    if high_price >= active_trade['sl']:
                        active_trade['status'] = 'CLOSED_SL'
                        active_trade['exit_price'] = active_trade['sl']
                        active_trade['exit_date'] = timestamp_now
                        active_trade['pnl_pct'] = (active_trade['entry_price'] / active_trade['exit_price'] - 1) * 100
                        trade_history.append(active_trade)
                        active_trade = None
                        continue

                    if tp_strategy == 'scaling':
                        if low_price <= active_trade['tp1'] and not active_trade.get('tp1_hit', False):
                            active_trade['tp1_hit'] = True
                            if use_breakeven: active_trade['sl'] = min(active_trade['sl'], active_trade['entry_price'])
                        if low_price <= active_trade['tp2'] and not active_trade.get('tp2_hit', False):
                            active_trade['tp2_hit'] = True
                            if use_breakeven: active_trade['sl'] = min(active_trade['sl'], active_trade['tp1'])
                        if low_price <= active_trade['tp3'] and not active_trade.get('tp3_hit', False):
                            active_trade['tp3_hit'] = True
                            if use_breakeven: active_trade['sl'] = min(active_trade['tp2'], ema_50)

        # ==========================================
        # 2. MENCARI ENTRY BARU
        # ==========================================
        if active_trade is None:
            meta_new = {'Symbol': symbol, 'AVG_ENTRY_PRICE': None, 'ENTRY_DATE': None}
            res_new = algo_scoring.calculate_71point_score(window_df, meta_new)
            if res_new is None: continue
            
            code_L = res_new.get('long', {}).get('code', 'SKIP')
            levels_L = res_new.get('long', {}).get('levels', {})
            code_S = res_new.get('short', {}).get('code', 'SKIP')
            levels_S = res_new.get('short', {}).get('levels', {})
            
            if (trade_direction in ['LONG', 'BOTH']) and (code_L in ['FULL', 'HALF']):
                chosen_sl = levels_L.get(sl_type, close_price * 0.95)
                sl_struct = levels_L.get('sl_structure', chosen_sl)
                safe_sl = min(float(sl_struct), float(chosen_sl))
                risk = abs(close_price - safe_sl)
                
                # HIT AND RUN CALCULATION (LONG)
                if tp_strategy == 'fixed_rr_10': tp_fixed = close_price + (1.0 * risk)
                elif tp_strategy == 'fixed_rr_15': tp_fixed = close_price + (1.5 * risk)
                elif tp_strategy == 'fixed_rr_20': tp_fixed = close_price + (2.0 * risk)
                else: tp_fixed = close_price * 1.05
                
                if (levels_L.get('tp1', close_price * 1.05) / close_price - 1) * 100 >= 1.0:
                    active_trade = {
                        'side': 'LONG', 'entry_date': timestamp_now, 'entry_index': i, 'entry_price': close_price,
                        'sl': safe_sl, 'tp_fixed': tp_fixed,
                        'tp1': float(levels_L.get('tp1', close_price * 1.05)),
                        'tp2': float(levels_L.get('tp2', close_price * 1.10)),
                        'tp3': float(levels_L.get('tp3', close_price * 1.15)),
                        'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False, 'status': 'OPEN'
                    }
                    continue 
                    
            if (trade_direction in ['SHORT', 'BOTH']) and (code_S in ['FULL', 'HALF']) and (active_trade is None):
                chosen_sl = levels_S.get(sl_type, close_price * 1.05)
                sl_struct = levels_S.get('sl_structure', chosen_sl)
                safe_sl = max(float(sl_struct), float(chosen_sl))
                risk = abs(safe_sl - close_price)
                
                # HIT AND RUN CALCULATION (SHORT)
                if tp_strategy == 'fixed_rr_10': tp_fixed = close_price - (1.0 * risk)
                elif tp_strategy == 'fixed_rr_15': tp_fixed = close_price - (1.5 * risk)
                elif tp_strategy == 'fixed_rr_20': tp_fixed = close_price - (2.0 * risk)
                else: tp_fixed = close_price * 0.95
                
                if (1 - levels_S.get('tp1', close_price * 0.95) / close_price) * 100 >= 1.0:
                    active_trade = {
                        'side': 'SHORT', 'entry_date': timestamp_now, 'entry_index': i, 'entry_price': close_price,
                        'sl': safe_sl, 'tp_fixed': tp_fixed,
                        'tp1': float(levels_S.get('tp1', close_price * 0.95)),
                        'tp2': float(levels_S.get('tp2', close_price * 0.90)),
                        'tp3': float(levels_S.get('tp3', close_price * 0.85)),
                        'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False, 'status': 'OPEN'
                    }

    total_trades = len(trade_history)
    if active_trade is not None:
        last_close = float(current_candle['Close'])
        active_trade['exit_price'] = last_close
        active_trade['pnl_pct'] = (last_close / active_trade['entry_price'] - 1) * 100 if active_trade['side'] == 'LONG' else (active_trade['entry_price'] / last_close - 1) * 100
        active_trade['status'] = 'CLOSED_END_OF_DATA'
        active_trade['exit_date'] = timestamp_now
        trade_history.append(active_trade)

    return df, trade_history

if __name__ == "__main__":
    pass