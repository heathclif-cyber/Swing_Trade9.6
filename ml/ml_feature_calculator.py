import numpy as np
import pandas as pd
import warnings

# --- PARAMETER & KONSTANTA ---
VP_WINDOW        = 96      # Volume profile rolling window (24 jam M15)
VP_BINS          = 50      # Price bins untuk volume profile
OB_LOOKBACK      = 30      # Order block lookback
FVG_MIN_GAP_ATR  = 0.5     # Minimum FVG gap dalam ATR
SWING_LOOKBACK   = 5       # Swing high/low lookback
SEQ_LEN          = 20      # LSTM sequence length — FIX: diturunkan dari 32 → 20 agar kongruen dengan training

FEATURE_COLS = [
    # OHLCV
    'open', 'high', 'low', 'close', 'volume',
    # Volume flow
    'volume_delta', 'cvd', 'buy_volume', 'sell_volume',
    # Market structure
    'MSB_BOS', 'CHoCH', 'bars_since_BOS',
    'FVG_up', 'FVG_down',
    'Buy_Liq', 'Sell_Liq', 'SFP_sweep',
    # Derivatives
    'open_interest', 'funding_rate',
    # EMA M15 (ATR-normalized)
    'ema_7_m15', 'ema_21_m15', 'ema_50_m15', 'ema_200_m15',
    # EMA H4 (ATR-normalized)
    'ema_7_h4', 'ema_21_h4', 'ema_50_h4', 'ema_200_h4',
    # Momentum
    'rsi_6', 'stochrsi_k', 'stochrsi_d',
    # Volatility
    'atr_14_m15', 'atr_14_h4',
    # Key levels (ATR-normalized)
    'PDH', 'PDL', 'PWH', 'PWL',
    'Fib_618', 'Fib_786',
    # Volume profile (ATR-normalized)
    'POC', 'VAH', 'VAL',
    # Macro
    'btc_dominance', 'fear_greed', 'market_session',
    # Derived
    'log_ret_1', 'log_ret_5', 'log_ret_20',
    'vol_ratio_20',
    'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos',
    'time_to_funding_norm',
    # Partial NaN (ok)
    'long_short_ratio', 'long_account_pct', 'short_account_pct',
    'taker_buy_sell_ratio',
    # Symbol encoding
    'symbol',
]

SYMBOL_MAP = {
    'SOLUSDT': 0, 'ETHUSDT': 1, 'BNBUSDT': 2, 'XRPUSDT': 3, 'DOGEUSDT': 4,
    'TONUSDT': 5, 'ADAUSDT': 6, 'TRXUSDT': 7, 'SHIBUSDT': 8, 'AVAXUSDT': 9,
    'LINKUSDT': 10, 'DOTUSDT': 11, 'SUIUSDT': 12, 'POLUSDT': 13, 'NEARUSDT': 14,
    'PEPEUSDT': 15, 'TAOUSDT': 16, 'APTOSUSDT': 17, 'ARBUSDT': 18, 'WLFIUSDT': 19,
}

# --- Helper Functions ---
def calc_atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, min_periods=period, adjust=False).mean()

def calc_ema(close, span):
    return close.ewm(span=span, adjust=False).mean()

def calc_rsi(close, period=6):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=period-1, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(com=period-1, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_features_realtime(symbol, df_m15, funding_rate=None, btc_dominance=None, fear_greed=None):
    # ── Pastikan index adalah DatetimeIndex UTC ──
    import pandas as pd
    if not isinstance(df_m15.index, pd.DatetimeIndex):
        df_m15 = df_m15.copy()
        if 'open_time' in df_m15.columns:
            df_m15['open_time'] = pd.to_datetime(df_m15['open_time'], unit='ms', utc=True)
            df_m15 = df_m15.set_index('open_time')
            df_m15.index.name = 'timestamp'
        elif 'Open_Time' in df_m15.columns:
            df_m15['Open_Time'] = pd.to_datetime(df_m15['Open_Time'], unit='ms', utc=True)
            df_m15 = df_m15.set_index('Open_Time')
            df_m15.index.name = 'timestamp'
    df = df_m15
    # ── End guard ──
    
    # ── Normalize kolom names dari data_engine (kapital → lowercase) ──
    col_map = {
        'Open':           'open',
        'High':           'high',
        'Low':            'low',
        'Close':          'close',
        'Total_Volume':   'volume',
        'Taker_Buy_Base': 'taker_buy_volume',
        'Sell_Volume':    'taker_sell_volume',
        'Open_Time':      'open_time',
    }
    df = df_m15.copy()
    df.columns = [col_map.get(c, c.lower()) for c in df.columns]
    # ── End normalize ──
    
    # ... sisa kode tetap sama ...
    
    if len(df) < 200:
        warnings.warn(f"Panjang dataframe kurang dari 200 bar ({len(df)}), beberapa fitur mungkin NaN karena warm-up.")

    # Inisialisasi output
    out = pd.DataFrame(index=df.index)
    
    # 1. OHLCV
    out['open'] = df['open']
    out['high'] = df['high']
    out['low'] = df['low']
    out['close'] = df['close']
    out['volume'] = df['volume']
    
    # 2. ATR M15
    out['atr_14_m15'] = calc_atr(df['high'], df['low'], df['close'], 14)
    atr_safe = out['atr_14_m15'].replace(0, 1e-8)
    
    # Volume delta & CVD
    _bv = df.get('taker_buy_volume', df.get('Taker_Buy_Base', df.get('taker_buy_base_asset_volume')))
    buy_vol = _bv.iloc[:, 0] if isinstance(_bv, pd.DataFrame) else _bv
    if buy_vol is not None:
        out['buy_volume'] = buy_vol
        out['sell_volume'] = df['volume'] - buy_vol
        out['volume_delta'] = out['buy_volume'] - out['sell_volume']
    else:
        sign = np.sign(df['close'].diff()).fillna(0)
        out['volume_delta'] = sign * df['volume']
        out['buy_volume'] = np.where(out['volume_delta'] > 0, df['volume'], 0)
        out['sell_volume'] = np.where(out['volume_delta'] < 0, df['volume'], 0)
        
    out['cvd'] = out['volume_delta'].cumsum()

    # EMA M15 (ATR-normalized)
    for span in [7, 21, 50, 200]:
        ema = calc_ema(df['close'], span)
        out[f'ema_{span}_m15'] = (ema - df['close']) / atr_safe

    # Momentum
    out['rsi_6'] = calc_rsi(df['close'], 6)
    
    # StochRSI
    rsi_14 = calc_rsi(df['close'], 14)
    min_rsi = rsi_14.rolling(14).min()
    max_rsi = rsi_14.rolling(14).max()
    stochrsi = (rsi_14 - min_rsi) / (max_rsi - min_rsi + 1e-8)
    out['stochrsi_k'] = stochrsi.rolling(3).mean() * 100
    out['stochrsi_d'] = out['stochrsi_k'].rolling(3).mean()

    # Market Structure (BOS/CHoCH/bars_since_BOS)
    is_swing_high = (df['high'] == df['high'].rolling(SWING_LOOKBACK*2+1, center=True).max())
    is_swing_low = (df['low'] == df['low'].rolling(SWING_LOOKBACK*2+1, center=True).min())
    
    swing_hi = df['high'].where(is_swing_high).ffill()
    swing_lo = df['low'].where(is_swing_low).ffill()
    
    bos = pd.Series(0, index=df.index)
    bos_up = df['close'] > swing_hi.shift(1)
    bos_dn = df['close'] < swing_lo.shift(1)
    bos.loc[bos_up] = 1
    bos.loc[bos_dn] = -1
    
    current_trend = bos.replace(0, np.nan).ffill().fillna(0)
    out['MSB_BOS'] = bos
    prev_trend = current_trend.shift(1)
    out['CHoCH'] = ((current_trend != prev_trend) & (current_trend != 0) & (prev_trend != 0)).astype(int)
    
    bars_since_bos = np.zeros(len(df))
    count = 0
    for i in range(len(df)):
        if bos.iloc[i] != 0:
            count = 0
        else:
            count += 1
        bars_since_bos[i] = count
    out['bars_since_BOS'] = bars_since_bos

    # FVG
    fvg_up = df['low'] - df['high'].shift(2)
    fvg_dn = df['low'].shift(2) - df['high']
    
    idx_up = fvg_up > (FVG_MIN_GAP_ATR * out['atr_14_m15'].shift(1))
    out['FVG_up'] = np.where(idx_up, fvg_up / out['atr_14_m15'], 0)
    
    idx_dn = fvg_dn > (FVG_MIN_GAP_ATR * out['atr_14_m15'].shift(1))
    out['FVG_down'] = np.where(idx_dn, fvg_dn / out['atr_14_m15'], 0)

    # Liquidity & SFP
    out['Buy_Liq'] = (df['close'] - swing_lo) / atr_safe
    out['Sell_Liq'] = (swing_hi - df['close']) / atr_safe
    
    sfp_bear = (df['high'] > swing_hi.shift(1)) & (df['close'] < swing_hi.shift(1))
    sfp_bull = (df['low'] < swing_lo.shift(1)) & (df['close'] > swing_lo.shift(1))
    out['SFP_sweep'] = (sfp_bear | sfp_bull).astype(int)

    # Derivatives
    out['open_interest'] = df.get('open_interest', np.nan)
    out['funding_rate'] = funding_rate if funding_rate is not None else np.nan

    for col in ['long_short_ratio', 'long_account_pct', 'short_account_pct', 'taker_buy_sell_ratio']:
        out[col] = df.get(col, np.nan)

    # PDH/PDL/PWH/PWL
    df_daily = df.resample('D').agg({'high':'max', 'low':'min'})
    df_weekly = df.resample('W-MON').agg({'high':'max', 'low':'min'})
    
    pdh = df_daily['high'].shift(1).reindex(df.index).ffill()
    pdl = df_daily['low'].shift(1).reindex(df.index).ffill()
    pwh = df_weekly['high'].shift(1).reindex(df.index).ffill()
    pwl = df_weekly['low'].shift(1).reindex(df.index).ffill()
    
    out['PDH'] = (pdh - df['close']) / atr_safe
    out['PDL'] = (pdl - df['close']) / atr_safe
    out['PWH'] = (pwh - df['close']) / atr_safe
    out['PWL'] = (pwl - df['close']) / atr_safe

    # Fibonacci
    roll_high = df['high'].rolling(96).max()
    roll_low = df['low'].rolling(96).min()
    fib_618 = roll_high - 0.618 * (roll_high - roll_low)
    fib_786 = roll_high - 0.786 * (roll_high - roll_low)
    
    out['Fib_618'] = (fib_618 - df['close']) / atr_safe
    out['Fib_786'] = (fib_786 - df['close']) / atr_safe

    # Volume profile (POC/VAH/VAL)
    poc = pd.Series(np.nan, index=df.index)
    vah = pd.Series(np.nan, index=df.index)
    val = pd.Series(np.nan, index=df.index)
    
    for i in range(VP_WINDOW, len(df)):
        window = df.iloc[i-VP_WINDOW:i]
        hist, bins = np.histogram(window['close'], bins=VP_BINS, weights=window['volume'])
        poc_idx = np.argmax(hist)
        poc.iloc[i] = (bins[poc_idx] + bins[poc_idx+1]) / 2
        
        total_vol = hist.sum()
        target_vol = total_vol * 0.7
        
        lower_idx = poc_idx
        upper_idx = poc_idx
        current_vol = hist[poc_idx]
        
        while current_vol < target_vol and (lower_idx > 0 or upper_idx < VP_BINS - 1):
            left_vol = hist[lower_idx - 1] if lower_idx > 0 else 0
            right_vol = hist[upper_idx + 1] if upper_idx < VP_BINS - 1 else 0
            
            if left_vol > right_vol:
                lower_idx -= 1
                current_vol += left_vol
            else:
                if right_vol > 0:
                    upper_idx += 1
                    current_vol += right_vol
                elif left_vol > 0:
                    lower_idx -= 1
                    current_vol += left_vol
                else:
                    break
                    
        vah.iloc[i] = bins[upper_idx+1]
        val.iloc[i] = bins[lower_idx]

    out['POC'] = (poc.ffill() - df['close']) / atr_safe
    out['VAH'] = (vah.ffill() - df['close']) / atr_safe
    out['VAL'] = (val.ffill() - df['close']) / atr_safe

    # Macro
    out['btc_dominance'] = btc_dominance if btc_dominance is not None else np.nan

    # FIX: Jika fear_greed tidak diteruskan sebagai argumen,
    # coba baca dari kolom df (jika enrichment sudah fetch dari Alternative.me)
    if fear_greed is None:
        _fg_col = 'fear_greed' if 'fear_greed' in df.columns else (
                  'Fear_Greed' if 'Fear_Greed' in df.columns else None)
        fear_greed = float(df[_fg_col].iloc[-1]) if _fg_col else None
    out['fear_greed'] = fear_greed if fear_greed is not None else np.nan
    
    # Market session
    hrs = df.index.hour
    sess = np.zeros(len(df))
    sess[(hrs >= 0) & (hrs < 8)] = 1   # Asia
    sess[(hrs >= 7) & (hrs < 15)] = 2  # London
    sess[(hrs >= 13) & (hrs < 21)] = 3 # NY
    out['market_session'] = sess

    # Derived
    out['log_ret_1'] = np.log(df['close'] / df['close'].shift(1))
    out['log_ret_5'] = np.log(df['close'] / df['close'].shift(5))
    out['log_ret_20'] = np.log(df['close'] / df['close'].shift(20))
    
    out['vol_ratio_20'] = df['volume'] / df['volume'].rolling(20).mean()

    # Cyclic Encoding
    out['hour_sin'] = np.sin(2 * np.pi * df.index.hour / 24)
    out['hour_cos'] = np.cos(2 * np.pi * df.index.hour / 24)
    out['dow_sin'] = np.sin(2 * np.pi * df.index.dayofweek / 7)
    out['dow_cos'] = np.cos(2 * np.pi * df.index.dayofweek / 7)

    # Time to funding
    minutes = df.index.hour * 60 + df.index.minute
    funding_points = [0, 8*60, 16*60, 24*60]
    time_to = [min([p - m for p in funding_points if p > m]) for m in minutes]
    out['time_to_funding_norm'] = np.array(time_to) / 480.0

    # H4 EMA & ATR
    df_h4 = df.resample('4h').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last'})
    h4_atr = calc_atr(df_h4['high'], df_h4['low'], df_h4['close'], 14)
    out['atr_14_h4'] = h4_atr.reindex(df.index).ffill()
    
    for span in [7, 21, 50, 200]:
        ema_h4 = calc_ema(df_h4['close'], span)
        reindexed_ema = ema_h4.reindex(df.index).ffill()
        out[f'ema_{span}_h4'] = (reindexed_ema - df['close']) / atr_safe

    # Symbol encoding
    out['symbol'] = SYMBOL_MAP.get(symbol.upper(), -1)
    
    return out[FEATURE_COLS]

def get_lgbm_input(features_df: pd.DataFrame) -> pd.DataFrame:
    """
    Ambil 1 row terakhir dari features_df.
    FIX: Tidak lagi inject OB_price=0.0 secara manual.
    Gunakan lgbm_model.feature_name_ sebagai source of truth di ml_signal.py.
    """
    last_row = features_df.iloc[[-1]].copy()
    return last_row

def get_lstm_input(features_df: pd.DataFrame, seq_len: int = SEQ_LEN) -> np.ndarray:
    """
    Ambil seq_len row terakhir dari features_df (tanpa OB_price).
    Default seq_len mengikuti konstanta SEQ_LEN = 20.
    """
    tail = features_df.tail(seq_len)
    return tail.values
