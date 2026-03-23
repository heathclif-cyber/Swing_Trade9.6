"""
BACKTEST — PROTOKOL 9.6: Institutional Flow Master (Wick Hunter Edition)
========================================================================
Version 3.0 — Vectorized pre-computation untuk performa cepat.
Data: DOGEUSDT H4 4 tahun.
"""

import pandas as pd
import numpy as np
import os
from datetime import datetime

# ──────────────────────────────────────────────
# KONFIGURASI
# ──────────────────────────────────────────────
FILE_PATH = "DOGEUSDT_4H_4Years.csv"
CAPITAL   = 1000.0   # Modal awal total
POS_USD   = 200.0    # Alokasi per setup ($200)
LB        = 20       # Lookback bars untuk swing

# ──────────────────────────────────────────────
# LOAD & PRE-COMPUTE
# ──────────────────────────────────────────────
if not os.path.exists(FILE_PATH):
    print(f"ERROR: File tidak ditemukan: {FILE_PATH}")
    raise SystemExit(1)

df = pd.read_csv(FILE_PATH)
df = df.ffill().dropna(subset=['Close', 'EMA_21', 'ATR_14']).reset_index(drop=True)

# Pre-compute rolling swing vectors
df['SW_LO'] = df['Low'].rolling(LB).min().shift(1)   # swing low from past LB bars
df['SW_HI'] = df['High'].rolling(LB).max().shift(1)  # swing high from past LB bars

# Fib 0.786 discount zone (vectorized)
fib_range     = df['SW_HI'] - df['SW_LO']
df['FIB_786'] = df['SW_HI'] - fib_range * 0.786

# Market Phase
def phase_vec(row):
    if row['Close'] > row['EMA_21'] and row['Close'] > row['EMA_200']:
        return 'MARKUP'
    if row['Close'] < row['EMA_21'] and row['Close'] < row['EMA_200']:
        return 'MARKDOWN'
    return 'CONSOLIDATION'

df['Phase'] = df.apply(phase_vec, axis=1)

# Signals (vectorized, shift 1 to avoid lookahead)
df['prev_Close'] = df['Close'].shift(1)
df['prev_CVD']   = df['CVD'].shift(1)
df['prev_OI']    = df['Open_Interest'].shift(1)

price_up        = df['Close'] > df['prev_Close']
df['VF']        = price_up & (df['CVD'] < df['prev_CVD'] * 0.8)     # Volume Fakeout
df['OID']       = price_up & (df['Open_Interest'] < df['prev_OI'] * 0.96)  # OI Divergence

# Death spiral: CVD drops >10% AND OI rising
cvd_chg         = (df['CVD'] - df['prev_CVD']) / df['prev_CVD'].abs().replace(0, np.nan)
df['DS']        = (cvd_chg < -0.10) & (df['Open_Interest'] > df['prev_OI'] * 1.02)

# EMA columns
df['EMA_50']  = df.get('EMA_50',  df['EMA_21'])
df['EMA_200'] = df.get('EMA_200', df['EMA_21'])

df = df.dropna(subset=['SW_LO', 'SW_HI', 'FIB_786']).reset_index(drop=True)

print(f"Loaded: {len(df):,} candles | {df['Timestamp'].iloc[0]} -> {df['Timestamp'].iloc[-1]}")

# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────
def nearest_ema_above(price, ema21, ema50, ema200, atr):
    cands = [('EMA_21', ema21), ('EMA_50', ema50), ('EMA_200', ema200)]
    above = [(n, v) for n, v in cands if v > price]
    if above:
        return min(above, key=lambda x: x[1])
    return ('EMA+ATR', price + atr * 1.5)

# ──────────────────────────────────────────────
# BACKTEST STATE
# ──────────────────────────────────────────────
capital      = CAPITAL
in_position  = False
entry_price  = 0.0
entry_cost   = 0.0
initial_sl   = 0.0
active_sl    = 0.0
tp1 = tp2 = tp3 = 0.0
coins_held   = 0.0
tp_phase     = 0
phase_entry  = ""
entry_reason = ""
entry_time   = ""
layer_done   = [False, False, False]

trades = []

# ──────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────
for i in range(len(df)):
    r = df.iloc[i]

    ts      = str(r['Timestamp'])
    price   = float(r['Close'])
    low     = float(r['Low'])
    high    = float(r['High'])
    ema21   = float(r['EMA_21'])
    ema50   = float(r['EMA_50'])
    ema200  = float(r['EMA_200'])
    atr     = float(r['ATR_14'])
    pdh     = float(r.get('PDH', 0) or 0)
    pdl     = float(r.get('PDL', 0) or 0)
    pwh     = float(r.get('PWH', 0) or 0)
    pwl     = float(r.get('PWL', 0) or 0)
    sw_lo   = float(r['SW_LO'])
    sw_hi   = float(r['SW_HI'])
    f786    = float(r['FIB_786'])
    phase   = str(r['Phase'])
    vf      = bool(r['VF'])
    oid     = bool(r['OID'])
    ds      = bool(r['DS'])

    # ─────────────────────────────
    # KILL SWITCH + EXIT
    # ─────────────────────────────
    if in_position:
        kill = False; kill_r = ""
        pnl = 0.0; is_exit = False; exit_str = ""; exit_px = price

        if phase_entry == "MARKUP":
            if price < ema21 and price < ema200:
                kill = True; kill_r = "KILL: Structural Breakdown"
            elif vf and tp_phase == 0:
                kill = True; kill_r = "KILL: Volume Fakeout"
            elif oid and tp_phase == 0:
                kill = True; kill_r = "KILL: OI Divergence"

        if phase_entry == "MARKDOWN" and ds and price < entry_price and tp_phase == 0:
            kill = True; kill_r = "KILL: Death Spiral"

        if kill:
            is_exit = True; exit_str = kill_r

        elif low <= active_sl:
            is_exit = True; exit_str = "SL Hit"; exit_px = max(active_sl, low)

        elif tp_phase == 0 and high >= tp1:
            sold = coins_held * 0.30
            pnl += sold * (tp1 - entry_price)
            coins_held -= sold
            tp_phase = 1
            active_sl = max(active_sl, entry_price * 1.001)

        elif tp_phase == 1 and high >= tp2:
            sold = coins_held * 0.571
            pnl += sold * (tp2 - entry_price)
            coins_held -= sold
            tp_phase = 2
            active_sl = max(active_sl, ema21 * 0.995)

        elif tp_phase == 2 and high >= tp3:
            is_exit = True; exit_str = "TP3: PWH Moonbag"; exit_px = tp3

        # Trailing SL (only up, only after TP1)
        if not is_exit and tp_phase > 0:
            trail = ema21 * 0.99
            if trail > active_sl:
                active_sl = trail

        if is_exit:
            pnl += coins_held * (exit_px - entry_price)
            pnl_pct = (pnl / entry_cost) * 100 if entry_cost > 0 else 0
            capital += entry_cost + pnl

            trades.append({
                "Entry_Time"   : entry_time,
                "Exit_Time"    : ts,
                "Phase"        : phase_entry,
                "Entry_Price"  : round(entry_price, 6),
                "Exit_Price"   : round(exit_px, 6),
                "Initial_SL"   : round(initial_sl, 6),
                "Entry_Reason" : entry_reason,
                "Exit_Reason"  : exit_str,
                "Cost_USD"     : round(entry_cost, 2),
                "PnL_USD"      : round(pnl, 2),
                "PnL_pct"      : round(pnl_pct, 2),
                "Capital_After": round(capital, 2),
            })
            in_position = False; coins_held = 0.0; entry_cost = 0.0
            layer_done = [False, False, False]; tp_phase = 0
        continue

    # ─────────────────────────────
    # ENTRY LOGIC
    # ─────────────────────────────
    if capital < POS_USD * 0.15:
        continue

    if phase == "MARKUP":
        t21 = (low <= ema21 * 1.005) and (price >= ema21 * 0.995)
        t50 = (low <= ema50 * 1.005) and (price >= ema50 * 0.995)
        if (t21 or t50) and not vf and not oid:
            ep = ema21 if t21 else ema50
            capital -= POS_USD
            in_position = True; entry_price = ep; entry_cost = POS_USD
            entry_time = ts; coins_held = POS_USD / ep; tp_phase = 0
            phase_entry = "MARKUP"; layer_done = [False]*3
            entry_reason = f"MARKUP Pullback {'EMA21' if t21 else 'EMA50'}"

            sl_raw = sw_lo - (2.0 * atr)
            if sl_raw >= ep or (ep - sl_raw) > atr * 6:
                sl_raw = ep - (2.0 * atr)
            active_sl = sl_raw; initial_sl = sl_raw

            n, tv = nearest_ema_above(ep, ema21, ema50, ema200, atr)
            tp1 = tv if tv > ep else ep + atr
            tp2 = pdh if pdh > tp1 else tp1 + atr * 2
            tp3 = pwh if pwh > tp2 else tp2 + atr * 3

    elif phase == "MARKDOWN":
        # Layer 1 — Fib 0.786 (20%)
        if not layer_done[0] and low <= f786 and f786 > 0 and not ds:
            alloc = min(POS_USD * 0.20, capital)
            qty = alloc / f786
            capital -= alloc
            in_position = True; entry_price = f786; entry_cost = alloc
            coins_held = qty; tp_phase = 0; layer_done[0] = True
            phase_entry = "MARKDOWN"; entry_time = ts
            entry_reason = f"MARKDOWN L1 Fib0.786@{f786:.5f}"

            sl_raw = sw_lo - (2.0 * atr)
            if sl_raw >= f786: sl_raw = f786 - (2.0 * atr)
            active_sl = sl_raw; initial_sl = sl_raw

            n, tv = nearest_ema_above(f786, ema21, ema50, ema200, atr)
            tp1 = tv if tv > f786 else f786 + atr
            tp2 = pdh if pdh > tp1 else tp1 + atr * 2
            tp3 = pwh if pwh > tp2 else tp2 + atr * 3

        # Layer 2 — PDL (30%)
        elif in_position and layer_done[0] and not layer_done[1] and pdl > 0 and low <= pdl and not ds:
            alloc = min(POS_USD * 0.30, capital)
            if alloc > 0:
                qty = alloc / pdl
                capital -= alloc; coins_held += qty; entry_cost += alloc
                entry_price = entry_cost / coins_held
                layer_done[1] = True
                entry_reason += f"+L2 PDL@{pdl:.5f}"
                active_sl = min(active_sl, sw_lo - 2.0 * atr)

        # Layer 3 — PWL (50%)
        elif in_position and layer_done[1] and not layer_done[2] and pwl > 0 and low <= pwl and not ds:
            alloc = min(POS_USD * 0.50, capital)
            if alloc > 0:
                qty = alloc / pwl
                capital -= alloc; coins_held += qty; entry_cost += alloc
                entry_price = entry_cost / coins_held
                layer_done[2] = True
                entry_reason += f"+L3 PWL@{pwl:.5f}"
                active_sl = min(active_sl, sw_lo - 2.5 * atr)

# Close open position at end
if in_position and coins_held > 0:
    lp  = float(df['Close'].iloc[-1])
    pnl = coins_held * (lp - entry_price)
    capital += entry_cost + pnl
    trades.append({
        "Entry_Time"   : entry_time,
        "Exit_Time"    : df['Timestamp'].iloc[-1],
        "Phase"        : phase_entry,
        "Entry_Price"  : round(entry_price, 6),
        "Exit_Price"   : round(lp, 6),
        "Initial_SL"   : round(initial_sl, 6),
        "Entry_Reason" : entry_reason,
        "Exit_Reason"  : "OPEN @ End (MTM)",
        "Cost_USD"     : round(entry_cost, 2),
        "PnL_USD"      : round(pnl, 2),
        "PnL_pct"      : round(pnl / entry_cost * 100 if entry_cost else 0, 2),
        "Capital_After": round(capital, 2),
    })

# ──────────────────────────────────────────────
# RESULTS
# ──────────────────────────────────────────────
SEP = "=" * 65
print(f"\n{SEP}")
print("  ⚡ BACKTEST — PROTOKOL 9.6 WICK HUNTER EDITION v3.0")
print(SEP)
print(f"  Data        : {FILE_PATH}  ({len(df):,} candles H4)")
print(f"  Periode     : {df['Timestamp'].iloc[0]}  →  {df['Timestamp'].iloc[-1]}")
print(f"  Modal Awal  : ${CAPITAL:,.2f}  |  Alokasi/Setup: ${POS_USD:,.2f}")
print("-" * 65)
print(f"  Total Trade : {len(trades)}")

if trades:
    dt        = pd.DataFrame(trades)
    wins      = dt[dt['PnL_USD'] > 0]
    losses    = dt[dt['PnL_USD'] <= 0]
    wr        = len(wins) / max(len(trades), 1) * 100
    avg_win   = wins['PnL_USD'].mean()   if not wins.empty   else 0
    avg_loss  = losses['PnL_USD'].mean() if not losses.empty else 0
    best_p    = dt['PnL_pct'].max()
    worst_p   = dt['PnL_pct'].min()
    final_c   = dt['Capital_After'].iloc[-1]
    total_r   = (final_c - CAPITAL) / CAPITAL * 100
    rr        = abs(avg_win / avg_loss) if avg_loss != 0 else 9.99

    # Max Drawdown
    peak = CAPITAL; max_dd = 0.0
    for c in dt['Capital_After']:
        if c > peak: peak = c
        dd = (peak - c) / peak * 100
        if dd > max_dd: max_dd = dd

    print(f"  Win Rate    : {wr:.1f}%  ({len(wins)} Win / {len(losses)} Loss)")
    print(f"  Modal Akhir : ${final_c:,.2f}")
    print(f"  Total Return: {total_r:+.2f}%  (${final_c - CAPITAL:+,.2f})")
    print(f"  Max Drawdown: -{max_dd:.1f}%")
    print(f"  Avg Win     : ${avg_win:+.2f}  |  Avg Loss: ${avg_loss:.2f}")
    print(f"  Best Trade  : +{best_p:.1f}%   |  Worst  : {worst_p:.1f}%")
    print(f"  Realized R:R: 1 : {rr:.2f}")
    print("-" * 65)

    for ph in ["MARKUP", "MARKDOWN"]:
        sub = dt[dt['Phase'] == ph]
        if not sub.empty:
            sw = len(sub[sub['PnL_USD'] > 0])
            print(f"  {ph:12}: {len(sub):3} trades | WR {sw/len(sub)*100:.1f}% | "
                  f"Avg PnL ${sub['PnL_USD'].mean():+.2f} | "
                  f"Avg $deployed ${sub['Cost_USD'].mean():.0f}")

    print("\n  🔍 Exit Reason Breakdown:")
    for reason, cnt in dt['Exit_Reason'].value_counts().items():
        avg = dt[dt['Exit_Reason'] == reason]['PnL_USD'].mean()
        tag = "✅" if avg > 0 else "❌"
        print(f"     {tag} {cnt:3}×  {str(reason)[:52]:<52}  avg ${avg:+.2f}")

    ts0      = datetime.now().strftime("%Y%m%d_%H%M")
    out_file = f"Backtest_WickHunter_v3_DOGEUSDT_4H_{ts0}.csv"
    dt.to_csv(out_file, index=False)
    print(f"\n  📂 Saved: {out_file}")
else:
    print("  Tidak ada trade. Cek kondisi entry.")

print(SEP + "\n")
