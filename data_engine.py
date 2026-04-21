import time
import requests
import logging
import pandas as pd
import pandas_ta as ta
import protocol_96_enrichment as enrichment
from urllib3.exceptions import InsecureRequestWarning

# Suppress insecure request warnings
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

logger = logging.getLogger("DataEngine")

# Cache for 1D and 1W data (Optimasi API Calls)
_macro_cache = {}
# Format: { "symbol": { "1d": (df, timestamp), "1w": (df, timestamp) } }
CACHE_DURATION = 4 * 3600  # 4 hours

BINANCE_KLINE_URLS = [
    "https://fapi.binance.com/fapi/v1/klines",
    "https://data-api.binance.vision/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
_last_working_url = None

def get_klines_rest(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    global _last_working_url
    urls = list(BINANCE_KLINE_URLS)
    if _last_working_url and _last_working_url in urls:
        urls.remove(_last_working_url)
        urls.insert(0, _last_working_url)

    for url in urls:
        try:
            total_klines = []
            end_time = None
            while len(total_klines) < limit:
                req_limit = min(1000, limit - len(total_klines))
                params = {"symbol": symbol, "interval": interval, "limit": req_limit}
                if end_time:
                    params['endTime'] = end_time
                resp = requests.get(url, params=params, timeout=10, verify=False)
                if resp.status_code == 200:
                    chunk = resp.json()
                    if not chunk or not isinstance(chunk, list):
                        break
                    total_klines = chunk + total_klines
                    if len(chunk) < req_limit:
                        break
                    end_time = chunk[0][0] - 1
                else:
                    break
            if total_klines:
                _last_working_url = url
                df = pd.DataFrame(total_klines, columns=[
                    'Open_Time', 'Open', 'High', 'Low', 'Close', 'Total_Volume',
                    'Close_Time', 'Quote_Asset_Volume', 'Trades', 'Taker_Buy_Base', 'Taker_Buy_Quote', 'Ignore'
                ])
                df['Open_Time'] = pd.to_datetime(df['Open_Time'], unit='ms')
                df['Close_Time'] = pd.to_datetime(df['Close_Time'], unit='ms')
                for col in ['Open', 'High', 'Low', 'Close', 'Total_Volume', 'Taker_Buy_Base']:
                    df[col] = df[col].astype(float)
                df['Buy_Volume'] = df['Taker_Buy_Base']
                df['Sell_Volume'] = df['Total_Volume'] - df['Buy_Volume']
                df['Volume_Delta'] = df['Buy_Volume'] - df['Sell_Volume']
                return df
        except Exception as e:
            continue
    return pd.DataFrame()

def apply_base_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    df = df.copy()
    df["EMA_7"]   = ta.ema(df["Close"], length=7)
    df["EMA_21"]  = ta.ema(df["Close"], length=21)
    df["EMA_50"]  = ta.ema(df["Close"], length=50)
    df["EMA_200"] = ta.ema(df["Close"], length=200)
    df["RSI_6"]   = ta.rsi(df["Close"], length=6)
    
    atr_result = ta.atr(df["High"], df["Low"], df["Close"], length=14)
    df["ATR_14"] = atr_result if atr_result is not None else None
    
    try:
        stoch = ta.stochrsi(df["Close"], length=14, rsi_length=14, k=3, d=3)
        if stoch is not None and not stoch.empty:
            df["StochRSI_K"] = stoch.iloc[:, 0]
            df["StochRSI_D"] = stoch.iloc[:, 1]
        else:
            df["StochRSI_K"] = None
            df["StochRSI_D"] = None
    except Exception:
        df["StochRSI_K"] = None
        df["StochRSI_D"] = None
    
    if "Buy_Volume" in df.columns and "Sell_Volume" in df.columns:
        df["Volume_Delta"] = df["Buy_Volume"] - df["Sell_Volume"]
        df["CVD"]          = df["Volume_Delta"].cumsum()
    return df

def fetch_oi(symbol: str, limit: int = 500) -> pd.DataFrame:
    try:
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        params = {"symbol": symbol, "period": "4h", "limit": limit}
        resp = requests.get(url, params=params, timeout=10, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                oi_df = pd.DataFrame(data)
                oi_df["Open_Time"]     = pd.to_datetime(oi_df["timestamp"], unit="ms")
                oi_df["Open_Interest"] = oi_df["sumOpenInterest"].astype(float)
                return oi_df[["Open_Time", "Open_Interest"]]
    except Exception as e:
        logger.debug(f"OI fetch failed for {symbol}: {e}")
    return pd.DataFrame()

def fetch_funding_rate(symbol: str, limit: int = 200) -> pd.DataFrame:
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        resp = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=10, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                fr_df = pd.DataFrame(data)
                fr_df["Open_Time"]    = pd.to_datetime(fr_df["fundingTime"], unit="ms")
                fr_df["Funding_Rate"] = fr_df["fundingRate"].astype(float)
                return fr_df[["Open_Time", "Funding_Rate"]]
    except Exception as e:
        logger.debug(f"Funding Rate fetch failed for {symbol}: {e}")
    return pd.DataFrame()

def get_macro_data(symbol: str, interval: str) -> pd.DataFrame:
    """Fetch 1d or 1w data with 4-hour caching to avoid rate limit."""
    now_ts = time.time()
    if symbol not in _macro_cache:
        _macro_cache[symbol] = {}
        
    cached = _macro_cache[symbol].get(interval)
    if cached and (now_ts - cached[1] < CACHE_DURATION):
        return cached[0]
        
    df = get_klines_rest(symbol, interval, limit=100)
    _macro_cache[symbol][interval] = (df, now_ts)
    return df

def get_data_engine_enriched(symbol: str, interval: str = "4h", limit: int = 250) -> pd.DataFrame:
    """Master logic for getting full enriched ready data."""
    df = get_klines_rest(symbol, interval, limit)
    if df.empty: return df
    
    df = apply_base_indicators(df)
    
    # Enrich with OI & FR
    oi_df = fetch_oi(symbol)
    if not oi_df.empty:
        try:
            df = pd.merge_asof(df.sort_values("Open_Time"), oi_df.sort_values("Open_Time"), on="Open_Time", direction="backward")
        except Exception:
            df["Open_Interest"] = 0.0
    else:
        df["Open_Interest"] = 0.0

    fr_df = fetch_funding_rate(symbol)
    if not fr_df.empty:
        try:
            df = pd.merge_asof(df.sort_values("Open_Time"), fr_df.sort_values("Open_Time"), on="Open_Time", direction="backward")
        except Exception:
            df["Funding_Rate"] = 0.0
    else:
        df["Funding_Rate"] = 0.0

    # Get Macro Data
    df_1d = get_macro_data(symbol, "1d")
    df_1w = get_macro_data(symbol, "1w")
    
    # Base H4 fallback for enrichment if user queries something else
    df_macro_h4 = df if interval == "4h" else get_klines_rest(symbol, "4h", limit=100)
    
    # Sinkronisasi Enrichment: use protocol_96_enrichment.enrich_dataset
    df = enrichment.enrich_dataset(df_base=df, df_macro_h4=df_macro_h4, df_macro_d1=df_1d, df_macro_w1=df_1w)
    
    # Pastikan CVD tidak hilang
    if 'CVD' not in df.columns and "Volume_Delta" in df.columns:
        df["CVD"] = df["Volume_Delta"].cumsum()
        
    return df
