# core/levels.py

def get_atr_projections_long(entry_val, atr, atr_mult):
    """
    Menghitung fallback TP dinamis berbasis volatilitas untuk LONG.
    Digunakan saat harga berada di fase Price Discovery (tidak ada resistance).
    """
    tp1 = entry_val + (atr * 1.5 * atr_mult)
    tp2 = entry_val + (atr * 3.0 * atr_mult)
    tp3 = entry_val + (atr * 5.0 * atr_mult)
    
    return [
        (tp1, "ATR Projection (+1.5x)"),
        (tp2, "ATR Projection (+3.0x)"),
        (tp3, "ATR Projection (+5.0x)")
    ]

def get_atr_projections_short(entry_val, atr, atr_mult):
    """
    Menghitung fallback TP dinamis berbasis volatilitas untuk SHORT.
    Digunakan saat harga jatuh ke ATL (tidak ada support).
    """
    tp1 = entry_val - (atr * 1.5 * atr_mult)
    tp2 = entry_val - (atr * 3.0 * atr_mult)
    tp3 = entry_val - (atr * 5.0 * atr_mult)
    
    return [
        (tp1, "ATR Projection (-1.5x)"),
        (tp2, "ATR Projection (-3.0x)"),
        (tp3, "ATR Projection (-5.0x)")
    ]