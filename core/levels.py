# core/levels.py
# [UPDATE] TP1 = Entry ± 2×ATR | SL = Entry ∓ 1×ATR
import logging

# ── ML Config (from models/inference_config.json) ──────────────────────
from core.helpers import load_inference_config
INFERENCE_CFG  = load_inference_config()
TP_ATR_MULT    = INFERENCE_CFG["labeling"]["tp_atr_mult"]
SL_ATR_MULT    = INFERENCE_CFG["labeling"]["sl_atr_mult"]
# ────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

def get_atr_projections_long(entry_val, atr, atr_mult, close_price=None, macro_trend=None):
    """
    [FIX P9.7] Menghitung fallback TP dinamis berbasis volatilitas untuk LONG.
    - TP1/TP2 tetap berbasis entry_val (konservatif, short-term anchor)
    - TP3 sekarang berbasis close_price (bukan entry_val yang bisa statis/lama)
      dan menggunakan 8.0x ATR (naik dari 5.0x) untuk menangkap price discovery.
    - Digunakan saat harga berada di fase Price Discovery (tidak ada resistance).
    """
    # [FIX P9.7] Gunakan close_price sebagai anchor TP3 jika tersedia
    _tp3_anchor = close_price if (close_price is not None and close_price > 0) else entry_val
    _used_fallback = (close_price is None or close_price <= 0)

    tp1 = entry_val + (atr * TP_ATR_MULT * atr_mult)  # was: 2.0
    tp2 = entry_val + (atr * 3.0 * atr_mult)
    tp3 = _tp3_anchor + (atr * 8.0 * atr_mult)  # [FIX P9.7] was entry_val + atr*5.0

    # [FIX P9.7] Validasi override: jika UPTREND dan TP3 masih < 15% dari close
    _tp3_overridden = False
    if (
        close_price is not None and close_price > 0
        and macro_trend == 'UPTREND'
        and tp3 < close_price * 1.15
    ):
        tp3_override = close_price + (atr * 10.0 * atr_mult)  # [FIX P9.7] ATR 10x projection
        logger.warning(
            f"[FIX P9.7] TP3 override aktif (UPTREND): TP3 lama=${tp3:.4f} < "
            f"close*1.15=${close_price * 1.15:.4f}. "
            f"Override ke ATR×10 = ${tp3_override:.4f}"
        )
        tp3 = tp3_override
        _tp3_overridden = True

    # [FIX P9.7] Log warning jika fallback TP dipakai (untuk deteksi mudah saat backtest)
    if _used_fallback:
        logger.warning(
            "[FIX P9.7] FALLBACK TP dipakai: close_price tidak tersedia, "
            f"menggunakan entry_val=${entry_val:.4f} sebagai anchor TP3."
        )
    else:
        logger.debug(
            f"[FIX P9.7] TP fallback LONG: anchor=close${close_price:.4f} "
            f"| TP1=${tp1:.4f} TP2=${tp2:.4f} TP3=${tp3:.4f} "
            f"| override={_tp3_overridden} | macro={macro_trend}"
        )

    return [
        (tp1, f"ATR Projection (+{TP_ATR_MULT * atr_mult:g}x)"),
        (tp2, f"ATR Projection (+{3.0 * atr_mult:g}x)"),
        (tp3, f"ATR Projection (+{8.0 * atr_mult:g}x){' [OVERRIDE 10x UPTREND]' if _tp3_overridden else ''} [FIX P9.7]"),
    ]

def get_atr_projections_short(entry_val, atr, atr_mult, close_price=None):
    """
    [FIX P9.7] Menghitung fallback TP dinamis berbasis volatilitas untuk SHORT.
    Digunakan saat harga jatuh ke ATL (tidak ada support).
    """
    # SHORT: anchor ke close_price jika tersedia (simetri dengan LONG fix)
    _tp3_anchor = close_price if (close_price is not None and close_price > 0) else entry_val
    _used_fallback = (close_price is None or close_price <= 0)

    tp1 = entry_val - (atr * TP_ATR_MULT * atr_mult)  # was: 2.0
    tp2 = entry_val - (atr * 3.0 * atr_mult)
    tp3 = _tp3_anchor - (atr * 8.0 * atr_mult)  # [FIX P9.7] was entry_val - atr*5.0

    # [FIX P9.7] Log warning jika fallback TP dipakai
    if _used_fallback:
        logger.warning(
            "[FIX P9.7] FALLBACK TP SHORT dipakai: close_price tidak tersedia, "
            f"menggunakan entry_val=${entry_val:.4f} sebagai anchor TP3."
        )

    return [
        (tp1, f"ATR Projection (-{TP_ATR_MULT * atr_mult:g}x)"),
        (tp2, f"ATR Projection (-{3.0 * atr_mult:g}x)"),
        (tp3, f"ATR Projection (-{8.0 * atr_mult:g}x) [FIX P9.7]"),
    ]


def get_entry_based_sl(entry_val: float, atr: float, atr_mult: float, direction: str = 'LONG') -> tuple:
    """
    Hitung SL awal berbasis entry price:
      LONG : SL = entry - 1×ATR   (harga turun 1×ATR dari entry)
      SHORT: SL = entry + 1×ATR   (harga naik 1×ATR dari entry)
    Return (sl_value, label)
    """
    raw_atr = atr * atr_mult
    eff_mult = SL_ATR_MULT * atr_mult
    if direction == 'SHORT':
        sl = entry_val + (raw_atr * SL_ATR_MULT)  # was: 1.0
        label = f"Entry + {eff_mult:g}×ATR (SL SHORT)"
    else:
        sl = entry_val - (raw_atr * SL_ATR_MULT)  # was: 1.0
        label = f"Entry − {eff_mult:g}×ATR (SL LONG)"
    return (round(sl, 8), label)