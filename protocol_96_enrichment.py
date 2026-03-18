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
    df['FVG_Up'] = 0.0
    df['FVG_Down'] = 0.0
    
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

def enrich_dataset(df_m15: pd.DataFrame, df_h4: pd.DataFrame, df_d1: pd.DataFrame, df_w1: pd.DataFrame) -> pd.DataFrame:
    """
    Main enrichment function.
    """
    # 1. H4 EMAs Mapping
    df_h4 = df_h4.copy()
    df_h4['EMA_7_H4'] = ta.ema(df_h4['Close'], length=7)
    df_h4['EMA_21_H4'] = ta.ema(df_h4['Close'], length=21)
    df_h4['EMA_50_H4'] = ta.ema(df_h4['Close'], length=50)
    df_h4['EMA_200_H4'] = ta.ema(df_h4['Close'], length=200)
    df_h4['ATR_14_H4'] = ta.atr(df_h4['High'], df_h4['Low'], df_h4['Close'], length=14)
    
    # 2. M15 Enrichment
    df = df_m15.copy()
    df['ATR_14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    
    # 3. Merge H4 to M15
    # Ensure both have 'Open_Time'
    h4_slim = df_h4[['Open_Time', 'EMA_7_H4', 'EMA_21_H4', 'EMA_50_H4', 'EMA_200_H4', 'ATR_14_H4']]
    df = pd.merge_asof(
        df.sort_values('Open_Time'),
        h4_slim.sort_values('Open_Time'),
        on='Open_Time',
        direction='backward'
    )
    
    # 4. Liquidity & SMC
    df = calculate_liquidity_levels(df, df_d1, df_w1)
    df = calculate_smc_markers(df, df_h4)
    df = calculate_fib_ote(df)
    
    # 5. Temporal Alignment (UTC+8 & Sessions)
    df = apply_temporal_alignment(df, offset_hours=8)
    
    # Handle NaNs
    df = df.ffill().bfill()
    
    return df
