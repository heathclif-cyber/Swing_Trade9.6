import sys
import warnings
from pathlib import Path
import numpy as np
import pandas as pd
_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.helpers import load_inference_config as _load_cfg

# ── Semua parameter dibaca dari inference_config.json (Single Source of Truth) ──
# Jika key tidak ada, fallback ke nilai default agar backward-compatible.
_cfg     = _load_cfg()
_fe_cfg  = _cfg.get("feature_engineering", {})

VP_WINDOW        = _fe_cfg.get("vp_window",        96)
VP_BINS          = _fe_cfg.get("vp_bins",           50)
FVG_MIN_GAP_ATR  = _fe_cfg.get("fvg_min_gap_atr",  0.5)
SWING_LOOKBACK   = _fe_cfg.get("swing_lookback",    5)
SEQ_LEN          = _cfg.get("inference", {}).get("seq_len", 32)



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
    # EMA H1 (ATR-normalized)
    'ema_7_h1', 'ema_21_h1', 'ema_50_h1', 'ema_200_h1',
    # EMA H4 (ATR-normalized)
    'ema_7_h4', 'ema_21_h4', 'ema_50_h4', 'ema_200_h4',
    # Momentum
    'rsi_6', 'stochrsi_k', 'stochrsi_d',
    # Volatility
    'atr_14_h1', 'atr_14_h4',
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
    'long_short_ratio',
    # Symbol encoding
    'symbol',
    # Structural features
    'dist_swing_high', 'dist_swing_low', 'price_in_range',
    'swing_momentum', 'h4_trend', 'trend_strength', 'vol_regime',
    # Smart money features v3 (9 fitur)
    'cvd_div_h4', 'cvd_slope_h4', 'vol_efficiency', 'absorption_z',
    'funding_price_div', 'rsi_h4', 'rsi_divergence',
    'wyckoff_phase', 'spring_upthrust',
    # Smart money features v4 (14 fitur baru)
    'ofi_raw', 'ofi_acceleration', 'ofi_z_score', 'ofi_h4_delta',
    'vwdp', 'vwdp_smooth',
    'hidden_divergence',
    'cvd_momentum_adv',
    'absorption_at_swing',
    'spread_to_volume',
    'ultra_high_vol',
    'no_demand', 'no_supply', 'effort_vs_result',
]

SYMBOL_MAP = {
    'SOLUSDT': 0, 'ETHUSDT': 1, 'BNBUSDT': 2, 'XRPUSDT': 3, 'DOGEUSDT': 4,
    'TONUSDT': 5, 'ADAUSDT': 6, 'TRXUSDT': 7, 'SHIBUSDT': 8, 'AVAXUSDT': 9,
    'LINKUSDT': 10, 'DOTUSDT': 11, 'SUIUSDT': 12, 'POLUSDT': 13, 'NEARUSDT': 14,
    'PEPEUSDT': 15, 'TAOUSDT': 16, 'APTOSUSDT': 17, 'ARBUSDT': 18, 'WLFIUSDT': 19,
    # alias untuk pair dengan prefix 1000
    '1000SHIBUSDT': 8, '1000PEPEUSDT': 15,
}


# --- Helper Functions ---
def calc_atr(high, low, close, period=14):
    """Wilder's RMA — identik dengan pandas_ta.atr(length=N)."""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    rma = tr.copy() * float('nan')
    rma.iloc[period - 1] = tr.iloc[:period].mean()
    for i in range(period, len(tr)):
        rma.iloc[i] = (rma.iloc[i - 1] * (period - 1) + tr.iloc[i]) / period
    return rma


def calc_ema(close, span):
    return close.ewm(span=span, adjust=False).mean()


def calc_rsi(close, period=6):
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_features_realtime(symbol, df_h1, funding_rate=None, btc_dominance=None, fear_greed=None):
    """
    Hitung 85 fitur v4 dari DataFrame H1.
    df_h1 harus memiliki DatetimeIndex UTC dan kolom lowercase:
      open, high, low, close, volume, taker_buy_volume (opsional)

    Fitur mencakup:
      - 71 fitur Smart Money v3 (OHLCV, volume flow, market structure, EMA, dll.)
      - 14 fitur Smart Money v4 baru (OFI, VWDP, hidden divergence, VSA, dll.)
    """
    # ── Guard: pastikan DatetimeIndex UTC ──
    if not isinstance(df_h1.index, pd.DatetimeIndex):
        df_h1 = df_h1.copy()
        for ts_col in ('open_time', 'Open_Time'):
            if ts_col in df_h1.columns:
                df_h1[ts_col] = pd.to_datetime(df_h1[ts_col], unit='ms', utc=True)
                df_h1 = df_h1.set_index(ts_col)
                df_h1.index.name = 'timestamp'
                break

    # ── Normalize kolom names ──
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
    df = df_h1.copy()
    df.columns = [col_map.get(c, c.lower()) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated(keep='first')]

    if len(df) < 200:
        warnings.warn(f"[{symbol}] DataFrame H1 hanya {len(df)} bar, beberapa fitur mungkin NaN (warm-up).")

    out = pd.DataFrame(index=df.index)

    # 1. OHLCV
    out['open']   = df['open']
    out['high']   = df['high']
    out['low']    = df['low']
    out['close']  = df['close']
    out['volume'] = df['volume']

    # 2. ATR H1  (nama v3: atr_14_h1)
    out['atr_14_h1'] = calc_atr(df['high'], df['low'], df['close'], 14)
    atr_safe = out['atr_14_h1'].replace(0, 1e-8)

    # 3. Volume flow
    _bv = df.get('taker_buy_volume',
          df.get('taker_buy_base_asset_volume'))
    if isinstance(_bv, pd.DataFrame):
        _bv = _bv.iloc[:, 0]
    if _bv is not None:
        out['buy_volume']   = _bv
        out['sell_volume']  = df['volume'] - _bv
        out['volume_delta'] = out['buy_volume'] - out['sell_volume']
    else:
        sign = np.sign(df['close'].diff()).fillna(0)
        out['volume_delta'] = sign * df['volume']
        out['buy_volume']   = np.where(out['volume_delta'] > 0, df['volume'], 0)
        out['sell_volume']  = np.where(out['volume_delta'] < 0, df['volume'], 0)
    out['cvd'] = out['volume_delta'].cumsum()

    # 4. EMA H1 (ATR-normalized, nama v3: ema_*_h1)
    for span in [7, 21, 50, 200]:
        ema = calc_ema(df['close'], span)
        out[f'ema_{span}_h1'] = (ema - df['close']) / atr_safe

    # 5. Momentum
    out['rsi_6'] = calc_rsi(df['close'], 6)
    rsi_14   = calc_rsi(df['close'], 14)
    min_rsi  = rsi_14.rolling(14).min()
    max_rsi  = rsi_14.rolling(14).max()
    stochrsi = (rsi_14 - min_rsi) / (max_rsi - min_rsi + 1e-8)
    out['stochrsi_k'] = stochrsi.rolling(3).mean() * 100
    out['stochrsi_d'] = out['stochrsi_k'].rolling(3).mean()

    # 6. Swing high/low
    is_swing_high = (df['high'] == df['high'].rolling(SWING_LOOKBACK * 2 + 1, center=True).max())
    is_swing_low  = (df['low']  == df['low'].rolling(SWING_LOOKBACK * 2 + 1, center=True).min())
    swing_hi = df['high'].where(is_swing_high).ffill()
    swing_lo = df['low'].where(is_swing_low).ffill()

    # 7. Market structure (BOS/CHoCH)
    bos    = pd.Series(0, index=df.index)
    bos_up = df['close'] > swing_hi.shift(1)
    bos_dn = df['close'] < swing_lo.shift(1)
    bos.loc[bos_up] = 1
    bos.loc[bos_dn] = -1
    current_trend = bos.replace(0, np.nan).ffill().fillna(0)
    out['MSB_BOS'] = bos
    prev_trend     = current_trend.shift(1)
    out['CHoCH']   = ((current_trend != prev_trend) & (current_trend != 0) & (prev_trend != 0)).astype(int)

    bars_since_bos = np.zeros(len(df))
    count = 0
    for i in range(len(df)):
        if bos.iloc[i] != 0:
            count = 0
        else:
            count += 1
        bars_since_bos[i] = count
    out['bars_since_BOS'] = bars_since_bos

    # 8. FVG
    fvg_up = df['low'] - df['high'].shift(2)
    fvg_dn = df['low'].shift(2) - df['high']
    idx_up = fvg_up > (FVG_MIN_GAP_ATR * out['atr_14_h1'].shift(1))
    idx_dn = fvg_dn > (FVG_MIN_GAP_ATR * out['atr_14_h1'].shift(1))
    out['FVG_up']   = np.where(idx_up, fvg_up / atr_safe, 0)
    out['FVG_down'] = np.where(idx_dn, fvg_dn / atr_safe, 0)

    # 9. Liquidity & SFP
    out['Buy_Liq']  = (df['close'] - swing_lo) / atr_safe
    out['Sell_Liq'] = (swing_hi - df['close']) / atr_safe
    sfp_bear = (df['high'] > swing_hi.shift(1)) & (df['close'] < swing_hi.shift(1))
    sfp_bull = (df['low']  < swing_lo.shift(1)) & (df['close'] > swing_lo.shift(1))
    out['SFP_sweep'] = (sfp_bear | sfp_bull).astype(int)

    # 10. Derivatives
    out['open_interest'] = df.get('open_interest', np.nan)
    out['funding_rate']  = funding_rate if funding_rate is not None else np.nan
    out['long_short_ratio'] = df.get('long_short_ratio', np.nan)

    # 11. PDH/PDL/PWH/PWL
    df_daily  = df.resample('D').agg({'high': 'max', 'low': 'min'})
    df_weekly = df.resample('W-MON').agg({'high': 'max', 'low': 'min'})
    pdh = df_daily['high'].shift(1).reindex(df.index).ffill()
    pdl = df_daily['low'].shift(1).reindex(df.index).ffill()
    pwh = df_weekly['high'].shift(1).reindex(df.index).ffill()
    pwl = df_weekly['low'].shift(1).reindex(df.index).ffill()
    out['PDH'] = (pdh - df['close']) / atr_safe
    out['PDL'] = (pdl - df['close']) / atr_safe
    out['PWH'] = (pwh - df['close']) / atr_safe
    out['PWL'] = (pwl - df['close']) / atr_safe

    # 12. Fibonacci (rolling 96 bar H1 = 4 hari)
    roll_high = df['high'].rolling(96).max()
    roll_low  = df['low'].rolling(96).min()
    fib_618   = roll_high - 0.618 * (roll_high - roll_low)
    fib_786   = roll_high - 0.786 * (roll_high - roll_low)
    out['Fib_618'] = (fib_618 - df['close']) / atr_safe
    out['Fib_786'] = (fib_786 - df['close']) / atr_safe

    # 13. Volume profile (POC/VAH/VAL)
    poc = pd.Series(np.nan, index=df.index)
    vah = pd.Series(np.nan, index=df.index)
    val = pd.Series(np.nan, index=df.index)
    for i in range(VP_WINDOW, len(df)):
        window    = df.iloc[i - VP_WINDOW:i]
        hist, bins = np.histogram(window['close'], bins=VP_BINS, weights=window['volume'])
        poc_idx   = np.argmax(hist)
        poc.iloc[i] = (bins[poc_idx] + bins[poc_idx + 1]) / 2
        total_vol = hist.sum()
        target_vol = total_vol * 0.7
        lower_idx = upper_idx = poc_idx
        current_vol = hist[poc_idx]
        while current_vol < target_vol and (lower_idx > 0 or upper_idx < VP_BINS - 1):
            left_vol  = hist[lower_idx - 1] if lower_idx > 0 else 0
            right_vol = hist[upper_idx + 1] if upper_idx < VP_BINS - 1 else 0
            if left_vol > right_vol:
                lower_idx   -= 1
                current_vol += left_vol
            elif right_vol > 0:
                upper_idx   += 1
                current_vol += right_vol
            elif left_vol > 0:
                lower_idx   -= 1
                current_vol += left_vol
            else:
                break
        vah.iloc[i] = bins[upper_idx + 1]
        val.iloc[i] = bins[lower_idx]
    out['POC'] = (poc.ffill() - df['close']) / atr_safe
    out['VAH'] = (vah.ffill() - df['close']) / atr_safe
    out['VAL'] = (val.ffill() - df['close']) / atr_safe

    # 14. Macro
    out['btc_dominance'] = btc_dominance if btc_dominance is not None else np.nan
    if fear_greed is None:
        _fg_col = next((c for c in ('fear_greed', 'Fear_Greed') if c in df.columns), None)
        fear_greed = float(df[_fg_col].iloc[-1]) if _fg_col else None
    out['fear_greed'] = fear_greed if fear_greed is not None else np.nan

    # 15. Market session (berdasarkan UTC hour)
    hrs  = df.index.hour
    sess = np.zeros(len(df))
    sess[(hrs >= 0)  & (hrs < 8)]  = 1   # Asia
    sess[(hrs >= 7)  & (hrs < 15)] = 2   # London
    sess[(hrs >= 13) & (hrs < 21)] = 3   # NY
    out['market_session'] = sess

    # 16. Log returns & vol ratio
    out['log_ret_1']   = np.log(df['close'] / df['close'].shift(1))
    out['log_ret_5']   = np.log(df['close'] / df['close'].shift(5))
    out['log_ret_20']  = np.log(df['close'] / df['close'].shift(20))
    out['vol_ratio_20'] = df['volume'] / df['volume'].rolling(20).mean()

    # 17. Cyclic encoding
    out['hour_sin'] = np.sin(2 * np.pi * df.index.hour / 24)
    out['hour_cos'] = np.cos(2 * np.pi * df.index.hour / 24)
    out['dow_sin']  = np.sin(2 * np.pi * df.index.dayofweek / 7)
    out['dow_cos']  = np.cos(2 * np.pi * df.index.dayofweek / 7)

    # 18. Time to funding (UTC: 00:00, 08:00, 16:00)
    minutes = df.index.hour * 60 + df.index.minute
    funding_points = [0, 8 * 60, 16 * 60, 24 * 60]
    time_to = [min([p - m for p in funding_points if p > m], default=0) for m in minutes]
    out['time_to_funding_norm'] = np.array(time_to) / 480.0

    # 19. H4 EMA & ATR (resample dari H1)
    df_h4 = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    h4_atr = calc_atr(df_h4['high'], df_h4['low'], df_h4['close'], 14)
    out['atr_14_h4'] = h4_atr.reindex(df.index).ffill()
    for span in [7, 21, 50, 200]:
        ema_h4     = calc_ema(df_h4['close'], span)
        reindexed  = ema_h4.reindex(df.index).ffill()
        out[f'ema_{span}_h4'] = (reindexed - df['close']) / atr_safe

    # 20. Symbol encoding
    out['symbol'] = SYMBOL_MAP.get(symbol.upper(), -1)

    # 21. Structural features
    out['dist_swing_high'] = (swing_hi - df['close']) / atr_safe
    out['dist_swing_low']  = (df['close'] - swing_lo) / atr_safe
    out['price_in_range']  = (df['close'] - swing_lo) / (swing_hi - swing_lo + 1e-8)
    swing_hi_diff   = swing_hi - swing_hi.shift(1)
    swing_lo_diff   = swing_lo - swing_lo.shift(1)
    out['swing_momentum']  = ((swing_hi_diff + swing_lo_diff) / 2) / atr_safe
    out['h4_trend']        = np.sign(out['ema_7_h4'] - out['ema_21_h4'])
    out['trend_strength']  = np.abs(out['ema_7_h4'] - out['ema_50_h4'])
    out['vol_regime']      = out['atr_14_h1'] / (out['atr_14_h1'].rolling(96).mean() + 1e-8)

    # ── 22. SMART MONEY FEATURES (v3 baru — 9 fitur) ──────────────────────

    # 22a. CVD divergence H4: apakah CVD H4 berlawanan arah dengan harga H4?
    cvd_h4   = out['cvd'].resample('4h').last().reindex(df.index).ffill()
    price_h4 = df['close'].resample('4h').last().reindex(df.index).ffill()
    cvd_chg  = cvd_h4.diff(4)    # perubahan CVD dalam 4 bar H1 (= 1 bar H4)
    price_chg = price_h4.diff(4)
    # +1 = divergence bullish (CVD naik, harga turun), -1 = bearish, 0 = tidak ada divergence
    out['cvd_div_h4'] = np.where(
        (cvd_chg > 0) & (price_chg < 0), 1,
        np.where((cvd_chg < 0) & (price_chg > 0), -1, 0)
    ).astype(float)

    # 22b. CVD slope H4: kemiringan CVD selama 4 bar H1 (ATR-normalized)
    out['cvd_slope_h4'] = cvd_chg / atr_safe

    # 22c. Volume efficiency: seberapa efisien pergerakan harga per unit volume
    # = |price_change| / volume. Tinggi = smart money, rendah = chop.
    price_move = df['close'].diff().abs()
    vol_safe   = df['volume'].replace(0, 1e-8)
    out['vol_efficiency'] = (price_move / vol_safe).rolling(14).mean()

    # 22d. Absorption z-score: volume tinggi tapi harga tidak bergerak (absorption)
    # Skor positif = volume besar dengan range kecil (possible absorption)
    candle_range = (df['high'] - df['low']).replace(0, 1e-8)
    vol_per_range = df['volume'] / candle_range
    vpr_mean = vol_per_range.rolling(20).mean()
    vpr_std  = vol_per_range.rolling(20).std().replace(0, 1e-8)
    out['absorption_z'] = (vol_per_range - vpr_mean) / vpr_std

    # 22e. Funding-price divergence: funding positif tapi harga turun (short squeeze setup)
    # = funding_rate × sign(price_change). Positif = alignment, negatif = divergence
    _fr_series = pd.Series(
        funding_rate if funding_rate is not None else np.nan,
        index=df.index
    )
    price_sign        = np.sign(df['close'].diff())
    out['funding_price_div'] = _fr_series * price_sign

    # 22f. RSI H4: RSI-6 dihitung dari OHLCV H4
    rsi_h4_raw        = calc_rsi(df_h4['close'], 6)
    out['rsi_h4']     = rsi_h4_raw.reindex(df.index).ffill()

    # 22g. RSI divergence: RSI H1 vs harga (bullish/bearish divergence sederhana)
    # Lookback 14 bar: harga baru high tapi RSI lebih rendah = bearish div (-1)
    #                   harga baru low  tapi RSI lebih tinggi = bullish div (+1)
    _lk = 14
    price_new_high = df['close'] > df['close'].rolling(_lk).max().shift(1)
    price_new_low  = df['close'] < df['close'].rolling(_lk).min().shift(1)
    rsi_lower      = out['rsi_6'] < out['rsi_6'].rolling(_lk).max().shift(1)
    rsi_higher     = out['rsi_6'] > out['rsi_6'].rolling(_lk).min().shift(1)
    out['rsi_divergence'] = np.where(
        price_new_high & rsi_lower, -1,
        np.where(price_new_low & rsi_higher, 1, 0)
    ).astype(float)

    # 22h. Wyckoff phase (simplified): deteksi akumulasi/distribusi dari volume + range
    # Phase encoding: 2=Markup, 1=Accumulation, 0=Neutral, -1=Distribution, -2=Markdown
    _ema_close = calc_ema(df['close'], 20)
    _trend_up  = df['close'] > _ema_close
    _trend_dn  = df['close'] < _ema_close
    _high_vol  = df['volume'] > df['volume'].rolling(20).mean()
    _low_range = candle_range < candle_range.rolling(20).mean()
    _high_range = candle_range > candle_range.rolling(20).mean()
    wyckoff = pd.Series(0.0, index=df.index)
    # Markup: uptrend + high volume + expanding range
    wyckoff[_trend_up & _high_vol & _high_range] = 2
    # Accumulation: downtrend bottom + high volume + narrow range (absorption)
    wyckoff[_trend_dn & _high_vol & _low_range]  = 1
    # Distribution: uptrend top + high volume + narrow range
    wyckoff[_trend_up & _high_vol & _low_range]  = -1
    # Markdown: downtrend + high volume + expanding range
    wyckoff[_trend_dn & _high_vol & _high_range] = -2
    out['wyckoff_phase'] = wyckoff

    # 22i. Spring & Upthrust detection
    # Spring  (+1): harga sweep di bawah swing_lo lalu close di atas swing_lo (bullish trap)
    # Upthrust (-1): harga sweep di atas swing_hi lalu close di bawah swing_hi (bearish trap)
    spring   = (df['low'] < swing_lo.shift(1)) & (df['close'] > swing_lo.shift(1))
    upthrust = (df['high'] > swing_hi.shift(1)) & (df['close'] < swing_hi.shift(1))
    out['spring_upthrust'] = np.where(spring, 1, np.where(upthrust, -1, 0)).astype(float)

    # ── End smart money v3 features ───────────────────────────────────────

    # ══════════════════════════════════════════════════════════════════════
    # 23. SMART MONEY FEATURES V4 (14 fitur baru)
    # ══════════════════════════════════════════════════════════════════════

    # 23a. Order Flow Imbalance (OFI) — menggunakan taker buy/sell volume
    # OFI = buy_volume jika harga naik, -sell_volume jika harga turun
    # Proxy smart money agression per bar
    price_up   = df['close'] >= df['close'].shift(1)
    price_dn   = df['close'] < df['close'].shift(1)
    ofi        = pd.Series(0.0, index=df.index)
    ofi[price_up] = out['buy_volume'][price_up]
    ofi[price_dn] = -out['sell_volume'][price_dn]
    out['ofi_raw'] = ofi / (out['atr_14_h1'].replace(0, 1e-8) * 1e6 + 1e-8)  # normalize scale

    # 23b. OFI Acceleration — perubahan OFI antar bar (percepatan order flow)
    out['ofi_acceleration'] = out['ofi_raw'].diff()

    # 23c. OFI Z-Score — posisi OFI relatif terhadap distribusi rolling 20 bar
    ofi_mean = out['ofi_raw'].rolling(20).mean()
    ofi_std  = out['ofi_raw'].rolling(20).std().replace(0, 1e-8)
    out['ofi_z_score'] = (out['ofi_raw'] - ofi_mean) / ofi_std

    # 23d. OFI H4 Delta — rata-rata OFI per 4 bar H1 (ekuivalen H4)
    # Positif = net buy pressure pada H4, negatif = net sell
    ofi_h4_sum     = out['ofi_raw'].rolling(4).sum()
    ofi_h4_prev    = ofi_h4_sum.shift(4)
    out['ofi_h4_delta'] = ofi_h4_sum - ofi_h4_prev

    # 23e. Volume-Weighted Direction Pressure (VWDP)
    # VWDP = sum(sign(close - open) * volume) / rolling_volume
    # Mengukur arah volume-weighted. Mendekati +1 = semua bar bullish bervolume tinggi
    bar_direction = np.sign(df['close'] - df['open'])
    directed_vol  = bar_direction * df['volume']
    rolling_vol   = df['volume'].rolling(14).sum().replace(0, 1e-8)
    out['vwdp']   = directed_vol.rolling(14).sum() / rolling_vol

    # 23f. VWDP Smooth — EMA(14) dari VWDP untuk sinyal lebih bersih
    out['vwdp_smooth'] = calc_ema(out['vwdp'], 14)

    # 23g. Hidden Divergence — berlawanan arah dengan regular divergence
    # Hidden bullish  (+1): price higher low, RSI lower low   → trend continuation up
    # Hidden bearish  (-1): price lower high, RSI higher high → trend continuation dn
    _lk2       = 14
    ph_high    = df['close'].rolling(_lk2).max().shift(1)
    pl_low     = df['close'].rolling(_lk2).min().shift(1)
    rsi_ph     = out['rsi_6'].rolling(_lk2).max().shift(1)
    rsi_pl     = out['rsi_6'].rolling(_lk2).min().shift(1)
    hid_bull   = (df['close'] > pl_low) & (out['rsi_6'] < rsi_pl)   # higher low price + lower RSI
    hid_bear   = (df['close'] < ph_high) & (out['rsi_6'] > rsi_ph)  # lower high price + higher RSI
    out['hidden_divergence'] = np.where(hid_bull, 1, np.where(hid_bear, -1, 0)).astype(float)

    # 23h. CVD Momentum Advanced — percepatan CVD rolling vs MA
    # Mengukur apakah CVD sedang mempercepat atau melambat
    cvd_ma14        = out['cvd'].rolling(14).mean()
    cvd_ma5         = out['cvd'].rolling(5).mean()
    out['cvd_momentum_adv'] = (cvd_ma5 - cvd_ma14) / atr_safe

    # 23i. Absorption At Swing — volume anomali tinggi tepat di dekat swing level
    # Volume besar di dekat swing level → kemungkinan absorption atau reversal
    near_swing_hi    = (df['high'] - swing_hi).abs() < out['atr_14_h1']
    near_swing_lo    = (df['low']  - swing_lo).abs() < out['atr_14_h1']
    near_swing       = near_swing_hi | near_swing_lo
    vol_z            = (df['volume'] - df['volume'].rolling(20).mean()) / \
                       (df['volume'].rolling(20).std().replace(0, 1e-8))
    out['absorption_at_swing'] = np.where(near_swing, vol_z, 0.0).astype(float)

    # 23j. Spread to Volume — candle range relatif terhadap volume (VSA style)
    # Rendah = churn/absorption, Tinggi = effisien / trending
    candle_range2  = (df['high'] - df['low']).replace(0, 1e-8)
    spread_raw     = candle_range2 / (df['volume'].replace(0, 1e-8))
    spr_mean       = spread_raw.rolling(20).mean()
    spr_std        = spread_raw.rolling(20).std().replace(0, 1e-8)
    out['spread_to_volume'] = (spread_raw - spr_mean) / spr_std

    # 23k. Ultra High Volume — flag boolean (sebagai float) jika volume > mean + 3σ
    vol_mean  = df['volume'].rolling(20).mean()
    vol_std   = df['volume'].rolling(20).std().replace(0, 1e-8)
    out['ultra_high_vol'] = ((df['volume'] - vol_mean) / vol_std > 3.0).astype(float)

    # 23l. No Demand Bar (VSA)
    # No Demand: bar range sempit, volume rendah (<50% MA), close di 1/3 bawah
    narrow_range = candle_range2 < candle_range2.rolling(20).mean() * 0.7
    low_vol      = df['volume'] < df['volume'].rolling(20).mean() * 0.5
    close_low    = (df['close'] - df['low']) < (df['high'] - df['low']) * 0.4
    out['no_demand'] = (narrow_range & low_vol & close_low).astype(float)

    # 23m. No Supply Bar (VSA)
    # No Supply: bar range sempit, volume rendah (<50% MA), close di 1/3 atas
    close_high   = (df['close'] - df['low']) > (df['high'] - df['low']) * 0.6
    out['no_supply'] = (narrow_range & low_vol & close_high).astype(float)

    # 23n. Effort vs Result (VSA)
    # = volume tinggi tapi harga tidak bergerak jauh (effort tanpa result)
    # Positif = effort > result (absorption), negatif = result > effort (breakout)
    norm_vol      = (df['volume'] - vol_mean) / (vol_std + 1e-8)
    norm_range    = (candle_range2 - candle_range2.rolling(20).mean()) / \
                    (candle_range2.rolling(20).std().replace(0, 1e-8) + 1e-8)
    out['effort_vs_result'] = norm_vol - norm_range

    # ── End smart money v4 features ───────────────────────────────────────

    return out[FEATURE_COLS]


def get_lgbm_input(features_df: pd.DataFrame) -> pd.DataFrame:
    """Ambil 1 row terakhir dari features_df."""
    return features_df.iloc[[-1]].copy()


def get_lstm_input(features_df: pd.DataFrame, seq_len: int = SEQ_LEN) -> np.ndarray:
    """Ambil seq_len row terakhir dari features_df."""
    return features_df.tail(seq_len).values