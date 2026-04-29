"""
core/normalize.py — Single Source of Truth (SSOT) for shared constants & utilities.

This module centralizes:
  1. BINANCE_KLINE_URLS  — multi-endpoint kline URL list (ISP resilience)
  2. SYMBOL_MAP          — symbol → integer encoding for ML models
  3. normalize_columns() — unified column normalization for any timeframe

All other modules MUST import from here instead of defining their own copies.
"""

import pandas as pd

# ── Binance Kline Endpoints (ISP-resilient, ordered by accessibility) ─────────
# fapi.binance.com is NOT blocked by Internet Positif (Indonesia ISP filter)
# api.binance.com IS typically blocked → put it last
BINANCE_KLINE_URLS: list[str] = [
    "https://fapi.binance.com/fapi/v1/klines",
    "https://data-api.binance.vision/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api3.binance.com/api/v3/klines",
    "https://api4.binance.com/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]

# ── Symbol Encoding Map (SSOT) ───────────────────────────────────────────────
# Used by ml_feature_calculator and ml_signal for categorical encoding.
SYMBOL_MAP: dict[str, int] = {
    'SOLUSDT': 0, 'ETHUSDT': 1, 'BNBUSDT': 2, 'XRPUSDT': 3, 'DOGEUSDT': 4,
    'TONUSDT': 5, 'ADAUSDT': 6, 'TRXUSDT': 7, 'SHIBUSDT': 8, 'AVAXUSDT': 9,
    'LINKUSDT': 10, 'DOTUSDT': 11, 'SUIUSDT': 12, 'POLUSDT': 13, 'NEARUSDT': 14,
    'PEPEUSDT': 15, 'TAOUSDT': 16, 'APTOSUSDT': 17, 'ARBUSDT': 18, 'WLFIUSDT': 19,
    # aliases for pairs with 1000 prefix
    '1000SHIBUSDT': 8, '1000PEPEUSDT': 15,
}


def normalize_columns(df: pd.DataFrame, timeframe: str = "1h") -> pd.DataFrame:
    """
    Normalize OHLCV column names to lowercase standard and set DatetimeIndex.

    Handles both REST API output (capitalised columns, Open_Time in ms) and
    already-normalised DataFrames.  Duplicate columns are dropped (first kept).

    Parameters
    ----------
    df : pd.DataFrame
        Raw DataFrame from Binance klines endpoint.
    timeframe : str, optional
        Human-readable label for logging (default '1h'); does not affect logic.

    Returns
    -------
    pd.DataFrame
        Normalised DataFrame with lowercase columns and ``DatetimeIndex`` named
        ``'timestamp'``.
    """
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
    df = df.copy()
    df.columns = [col_map.get(c, c.lower()) for c in df.columns]
    # Drop duplicate columns — keep the first
    df = df.loc[:, ~df.columns.duplicated(keep='first')]
    # Set DatetimeIndex from open_time (Unix ms → UTC datetime)
    if 'open_time' in df.columns:
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
        df = df.set_index('open_time')
        df.index.name = 'timestamp'
    elif not isinstance(df.index, pd.DatetimeIndex):
        # Fallback: try converting the existing index
        try:
            df.index = pd.to_datetime(df.index, unit='ms', utc=True)
            df.index.name = 'timestamp'
        except Exception:
            pass
    return df
