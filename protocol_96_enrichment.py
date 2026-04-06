import pandas as pd
import pandas_ta as ta
import numpy as np
from datetime import datetime, timedelta

def apply_temporal_alignment(df: pd.DataFrame, offset_hours: int = 8) -> pd.DataFrame:
    """
    Synchronize dataset time to UTC + offset_hours and add Market Session mapping.
    """
    df = df.copy()
    
    # ── Identify time column ──
    time_col = None
    if 'Timestamp' in df.columns: time_col = 'Timestamp'
    elif 'Open_Time' in df.columns: time_col = 'Open_Time'
    
    if time_col:
        # Convert to datetime and add offset
        df[time_col] = pd.to_datetime(df[time_col]) + pd.to_timedelta(offset_hours, unit='h')
        
        # ── Market Session Mapping ──
        def get_session(dt):
            h = dt.hour
            # ASIAN: 07:00 - 15:00
            # LONDON: 15:00 - 23:00
            # NEW YORK: 20:00 - 04:00 (H-1) -> 20:00 to 04:00
            sessions = []
            if 7 <= h < 15: sessions.append("ASIAN")
            if 15 <= h < 23: sessions.append("LONDON")
            if h >= 20 or h < 4: sessions.append("NEW YORK")
            return " / ".join(sessions) if sessions else "OFF-MARKET"
        
        df['Market_Session'] = df[time_col].apply(get_session)
        
        # Sort chronologically (just in case)
        df = df.sort_values(time_col).reset_index(drop=True)
        
    return df


def calculate_smc_markers(df_m15: pd.DataFrame, df_h4: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate FVG, Order Blocks, and Liquidity Sweeps (SFP).
    """
    df = df_m15.copy()
    
    # ── FVG (Fair Value Gap) ──
    # Bullish FVG: Low[i] > High[i-2]
    # Bearish FVG: High[i] < Low[i-2]

    
    for i in range(2, len(df)):
        # Bullish FVG
        if df['Low'].iloc[i] > df['High'].iloc[i-2]:
            df.at[df.index[i-1], 'FVG_Up_Top'] = df['Low'].iloc[i]
            df.at[df.index[i-1], 'FVG_Up_Bottom'] = df['High'].iloc[i-2]
        # Bearish FVG
        if df['High'].iloc[i] < df['Low'].iloc[i-2]:
            df.at[df.index[i-1], 'FVG_Down_Top'] = df['Low'].iloc[i-2]
            df.at[df.index[i-1], 'FVG_Down_Bottom'] = df['High'].iloc[i]
            
    # ── Order Block (OB) ──
    # Simple OB: The last opposite candle before an FVG-creating impulse
    df['OB_Price'] = np.nan
    for i in range(2, len(df)):
        # If bullish FVG created at i, OB is candle i-2 if it was bearish
        if df['Low'].iloc[i] > df['High'].iloc[i-2]:
            if df['Close'].iloc[i-2] < df['Open'].iloc[i-2]:
                df.at[df.index[i], 'OB_Price'] = df['High'].iloc[i-2]
        # If bearish FVG created at i, OB is candle i-2 if it was bullish
        if df['High'].iloc[i] < df['Low'].iloc[i-2]:
            if df['Close'].iloc[i-2] > df['Open'].iloc[i-2]:
                df.at[df.index[i], 'OB_Price'] = df['Low'].iloc[i-2]

    return df

def calculate_liquidity_levels(df_m15: pd.DataFrame, df_d1: pd.DataFrame, df_w1: pd.DataFrame) -> pd.DataFrame:
    """
    Add PDH/PDL and PWH/PWL columns.
    """
    df = df_m15.copy()
    
    pdh = df_d1.iloc[-2]['High'] if len(df_d1) >= 2 else 0.0
    pdl = df_d1.iloc[-2]['Low'] if len(df_d1) >= 2 else 0.0
    pwh = df_w1.iloc[-2]['High'] if len(df_w1) >= 2 else 0.0
    pwl = df_w1.iloc[-2]['Low'] if len(df_w1) >= 2 else 0.0
    
    df['PDH'] = pdh
    df['PDL'] = pdl
    df['PWH'] = pwh
    df['PWL'] = pwl
    
    # ── Liquidity Sweep (SFP) ──
    df['SFP_Sweep'] = False
    for i in range(len(df)):
        high = df['High'].iloc[i]
        low = df['Low'].iloc[i]
        close = df['Close'].iloc[i]
        
        # High sweep
        if high > pdh > close: df.at[df.index[i], 'SFP_Sweep'] = True
        elif high > pwh > close: df.at[df.index[i], 'SFP_Sweep'] = True
        # Low sweep
        if low < pdl < close: df.at[df.index[i], 'SFP_Sweep'] = True
        elif low < pwl < close: df.at[df.index[i], 'SFP_Sweep'] = True
        
    return df

def calculate_fib_ote(df: pd.DataFrame, lookback: int = 40) -> pd.DataFrame:
    """
    Calculate Fibonacci OTE (0.618, 0.786) based on macro swings.
    """
    df = df.copy()
    df['Fib_0.618'] = np.nan
    df['Fib_0.786'] = np.nan
    
    for i in range(lookback, len(df)):
        window = df.iloc[i-lookback:i]
        swing_high = window['High'].max()
        swing_low = window['Low'].min()
        diff = swing_high - swing_low
        
        # Assuming we are looking for discount zone in an uptrend (retracement from high)
        df.at[df.index[i], 'Fib_0.618'] = swing_high - (0.618 * diff)
        df.at[df.index[i], 'Fib_0.786'] = swing_high - (0.786 * diff)
        
    return df

def calculate_market_structure(df_macro: pd.DataFrame, df_base: pd.DataFrame) -> pd.DataFrame:
    """
    Kalkulasi Market Structure (MSB, BOS, CHoCH) pada timeframe Makro (D1/W1) 
    dan proyeksikan ke base timeframe (H4/1D).
    """
    df = df_base.copy()
    
    # Simple pivot isolation for macro structure
    df_macro = df_macro.copy()
    window = 3
    df_macro['Pivot_High'] = df_macro['High'] == df_macro['High'].rolling(window=window*2+1, center=True).max()
    df_macro['Pivot_Low'] = df_macro['Low'] == df_macro['Low'].rolling(window=window*2+1, center=True).min()
    
    last_ph = np.nan
    last_pl = np.nan
    trend = 0 # 1 untuk uptrend, -1 untuk downtrend
    
    df_macro['MSB'] = 0
    df_macro['BOS'] = 0
    df_macro['CHoCH'] = 0
    
    for i in range(len(df_macro)):
        current_close = df_macro['Close'].iloc[i]
        
        if df_macro['Pivot_High'].iloc[i]:
            last_ph = df_macro['High'].iloc[i]
        if df_macro['Pivot_Low'].iloc[i]:
            last_pl = df_macro['Low'].iloc[i]
            
        # Break of Resistance
        if pd.notna(last_ph) and current_close > last_ph:
            if trend == -1:
                df_macro.at[df_macro.index[i], 'CHoCH'] = 1
                trend = 1
            elif trend == 1:
                df_macro.at[df_macro.index[i], 'BOS'] = 1
            else:
                df_macro.at[df_macro.index[i], 'MSB'] = 1
                trend = 1
            last_ph = np.nan
            
        # Break of Support
        elif pd.notna(last_pl) and current_close < last_pl:
            if trend == 1:
                df_macro.at[df_macro.index[i], 'CHoCH'] = -1
                trend = -1
            elif trend == -1:
                df_macro.at[df_macro.index[i], 'BOS'] = -1
            else:
                df_macro.at[df_macro.index[i], 'MSB'] = -1
                trend = -1
            last_pl = np.nan

    # Merge project macro markers ke base timeframe via forward fill
    macro_slim = df_macro[['Open_Time', 'MSB', 'BOS', 'CHoCH']].copy()
    df = pd.merge_asof(
        df.sort_values('Open_Time'),
        macro_slim.sort_values('Open_Time'),
        on='Open_Time',
        direction='backward'
    )
    
    # Fill NaN and rolling them
    df['MSB'] = df['MSB'].fillna(0)
    df['BOS'] = df['BOS'].fillna(0)
    df['CHoCH'] = df['CHoCH'].fillna(0)
    return df

def calculate_volume_profile(df: pd.DataFrame, bins: int = 24) -> pd.DataFrame:
    """
    Hitung Historical Volume Profile (POC, VAH, VAL) pada Rolling Window.
    Digunakan untuk melihat institusional value area dalam 30 hari terakhir.
    """
    df = df.copy()
    df['POC'] = np.nan
    df['VAH'] = np.nan
    df['VAL'] = np.nan
    
    # Kita butuh minimal 30 candle H4 (sekitar 5 hari) atau D1 (1 bln) untuk window
    lookback = min(len(df), 30)
    if lookback < 5:
        return df
        
    for i in range(lookback, len(df)):
        window = df.iloc[i-lookback:i]
        min_price = window['Low'].min()
        max_price = window['High'].max()
        
        if max_price == min_price:
            continue
            
        vol_profile = np.zeros(bins)
        price_step = (max_price - min_price) / bins
        
        # Distribusikan volume ke bins harga
        for _, row in window.iterrows():
            low = row['Low']
            high = row['High']
            vol = row['Total_Volume']
            
            # Simple average distribution
            bin_idx = int((((high + low) / 2) - min_price) / price_step)
            bin_idx = min(bin_idx, bins - 1)
            vol_profile[bin_idx] += vol
            
        # Tentukan POC
        poc_idx = np.argmax(vol_profile)
        poc_price = min_price + (poc_idx * price_step)
        
        # Kalkulasi Value Area (VA) - 70% dari volume
        total_vol = np.sum(vol_profile)
        va_vol_target = total_vol * 0.7
        va_vol_current = vol_profile[poc_idx]
        
        upper_idx = poc_idx
        lower_idx = poc_idx
        
        while va_vol_current < va_vol_target and (upper_idx < bins - 1 or lower_idx > 0):
            vol_up = vol_profile[upper_idx + 1] if upper_idx < bins - 1 else 0
            vol_down = vol_profile[lower_idx - 1] if lower_idx > 0 else 0
            
            if vol_up == 0 and vol_down == 0:
                if upper_idx < bins - 1:
                    upper_idx += 1
                elif lower_idx > 0:
                    lower_idx -= 1
                else: break
            elif vol_up > vol_down:
                upper_idx += 1
                va_vol_current += vol_up
            else:
                lower_idx -= 1
                va_vol_current += vol_down
                
        vah_price = min_price + (upper_idx * price_step)
        val_price = min_price + (lower_idx * price_step)
        
        df.at[df.index[i], 'POC'] = poc_price
        df.at[df.index[i], 'VAH'] = vah_price
        df.at[df.index[i], 'VAL'] = val_price

    return df

def enrich_dataset(df_base: pd.DataFrame, df_macro_h4: pd.DataFrame, df_macro_d1: pd.DataFrame, df_macro_w1: pd.DataFrame) -> pd.DataFrame:
    """
    Main enrichment function, disesuaikan untuk Swing Trading (Base H4 / D1).
    Parameter df_base merupakan timeframe operasional kita (bisa H4).
    """
    df = df_base.copy()
    
    # 1. Base Indicators & SMC Components
    df['ATR_14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    df = calculate_liquidity_levels(df, df_macro_d1, df_macro_w1)
    df = calculate_smc_markers(df, df_macro_h4)
    df = calculate_fib_ote(df, lookback=30)
    
    # 2. H4/Macro EMAs Mapping
    df_h4 = df_macro_h4.copy()
    df_h4['EMA_7_H4'] = ta.ema(df_h4['Close'], length=7)
    df_h4['EMA_21_H4'] = ta.ema(df_h4['Close'], length=21)
    df_h4['EMA_50_H4'] = ta.ema(df_h4['Close'], length=50)
    df_h4['EMA_200_H4'] = ta.ema(df_h4['Close'], length=200)
    df_h4['ATR_14_H4'] = ta.atr(df_h4['High'], df_h4['Low'], df_h4['Close'], length=14)
    
    h4_slim = df_h4[['Open_Time', 'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4', 'ATR_14_H4']]
    if 'Open_Time' in df.columns:
        df = pd.merge_asof(
            df.sort_values('Open_Time'),
            h4_slim.sort_values('Open_Time'),
            on='Open_Time',
            direction='backward'
        )
    
    # 3. Swing Trading Enhancements (Market Structure & Volume Profile)
    df = calculate_market_structure(df_macro_d1, df)
    df = calculate_volume_profile(df, bins=30)
    
    # 4. Temporal Alignment (UTC+8 & Sessions)
    df = apply_temporal_alignment(df, offset_hours=8)
    
    # Handle NaNs
    df = df.ffill().bfill()
    
    return df
