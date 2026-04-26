"""
trade_logger.py
===============
Modul auto-capture trade log ke Supabase (PostgreSQL via psycopg2).
Taruh file ini di root folder Swing_Trade9.6.

Cara pakai di endpoint Flask:
    from trade_logger import log_entry, log_exit, get_logbook, get_summary_stats

Dependencies:
    pip install psycopg2-binary pandas
"""

import os
import uuid
import logging
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ── Koneksi ──────────────────────────────────────────────────────────────────
# Ambil DATABASE_URL dari environment variable (Railway sudah inject otomatis)
# Format: postgresql://user:password@host:port/dbname
# Support format postgres:// (Railway) → otomatis dikonversi ke postgresql://
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def _build_conn_kwargs() -> dict:
    """Parse DATABASE_URL menjadi kwargs psycopg2.connect (dengan sslmode=require)."""
    from urllib.parse import urlparse
    url_str = (DATABASE_URL or "").replace("postgres://", "postgresql://", 1)
    p = urlparse(url_str)
    return dict(
        host=p.hostname,
        port=p.port or 5432,
        user=p.username,
        password=p.password,
        dbname=(p.path or "").lstrip("/"),
        sslmode="require",
    )


@contextmanager
def get_conn():
    """Context manager untuk koneksi psycopg2. Auto-commit dan auto-close."""
    if not DATABASE_URL:
        raise EnvironmentError(
            "DATABASE_URL tidak ditemukan. "
            "Set environment variable DATABASE_URL dengan connection string Supabase/Railway."
        )
    conn = psycopg2.connect(**_build_conn_kwargs(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Helper: Deteksi Regime ───────────────────────────────────────────────────

def _detect_regime(features: dict) -> str:
    """
    Deteksi regime pasar dari snapshot fitur.
    Return: 'BULLISH' | 'BEARISH' | 'SIDEWAYS' | 'HIGH_VOL'

    Logic:
    - HIGH_VOL   : atr_ratio > 1.5 (ATR h4 relatif tinggi vs m15)
    - BULLISH    : adx >= 25 AND close > ema_50_h4 > ema_200_h4
    - BEARISH    : adx >= 25 AND close < ema_50_h4 < ema_200_h4
    - SIDEWAYS   : adx < 25
    """
    try:
        atr_m15    = float(features.get("atr_14_m15", 0) or 0)
        atr_h4     = float(features.get("atr_14_h4", 0) or 0)
        adx        = float(features.get("adx", 0) or 0)
        close      = float(features.get("close", 0) or 0)
        ema_50_h4  = float(features.get("ema_50_h4", 0) or 0)
        ema_200_h4 = float(features.get("ema_200_h4", 0) or 0)

        # HIGH_VOL: ATR H4 lebih dari 1.5x ATR M15
        if atr_m15 > 0 and (atr_h4 / atr_m15) > 1.5:
            return "HIGH_VOL"

        if adx >= 25:
            if close > ema_50_h4 and ema_50_h4 > ema_200_h4:
                return "BULLISH"
            elif close < ema_50_h4 and ema_50_h4 < ema_200_h4:
                return "BEARISH"

        return "SIDEWAYS"

    except Exception as e:
        logger.warning(f"Regime detection gagal: {e}")
        return "UNKNOWN"


# ── log_entry ────────────────────────────────────────────────────────────────

def log_entry(
    symbol: str,
    direction: str,
    price_entry: float,
    features: dict,
    lgbm_proba: dict,
    lstm_proba: dict,
    ensemble_proba: dict,
    tp_price: Optional[float] = None,
    sl_price: Optional[float] = None,
    position_size_usdt: Optional[float] = None,
    leverage: Optional[float] = None,
    note: Optional[str] = None,
) -> str:
    """
    Catat entry trade ke Supabase. Dipanggil saat user klik tombol entry.

    Parameters
    ----------
    symbol              : e.g. 'SOLUSDT'
    direction           : 'LONG' atau 'SHORT'
    price_entry         : harga entry aktual
    features            : dict snapshot fitur dari candle terakhir
    lgbm_proba          : {'LONG': 0.65, 'SHORT': 0.20, 'FLAT': 0.15}
    lstm_proba          : {'LONG': 0.60, 'SHORT': 0.25, 'FLAT': 0.15}
    ensemble_proba      : {'LONG': 0.63, 'SHORT': 0.22, 'FLAT': 0.15}
    tp_price            : harga TP (opsional)
    sl_price            : harga SL (opsional)
    position_size_usdt  : modal yang dipakai dalam USDT
    leverage            : leverage yang dipakai
    note                : catatan manual opsional

    Returns
    -------
    trade_id : UUID string — simpan ini untuk dipasangkan saat log_exit
    """

    def _signal(proba: dict) -> str:
        """Ambil kelas dengan probabilitas tertinggi."""
        if not proba:
            return "FLAT"
        return max(proba, key=proba.get)

    trade_id = str(uuid.uuid4())
    regime   = _detect_regime(features)

    sql = """
        INSERT INTO trade_log (
            trade_id, timestamp_entry, symbol, direction,
            price_entry, tp_price, sl_price,
            position_size_usdt, leverage,

            lgbm_signal, lgbm_conf_long, lgbm_conf_short, lgbm_conf_flat,
            lstm_signal, lstm_conf_long, lstm_conf_short, lstm_conf_flat,
            ensemble_signal, ensemble_conf_long, ensemble_conf_short, ensemble_conf_flat,

            atr_14_m15, atr_14_h4, rsi_6, ema_7_h4, ema_200_h4,
            adx, regime, funding_rate, fear_greed, btc_dominance,
            market_session, msb_bos, choch, fvg_up, fvg_down,
            cvd, open_interest,

            status, note
        ) VALUES (
            %(trade_id)s, %(timestamp_entry)s, %(symbol)s, %(direction)s,
            %(price_entry)s, %(tp_price)s, %(sl_price)s,
            %(position_size_usdt)s, %(leverage)s,

            %(lgbm_signal)s, %(lgbm_conf_long)s, %(lgbm_conf_short)s, %(lgbm_conf_flat)s,
            %(lstm_signal)s, %(lstm_conf_long)s, %(lstm_conf_short)s, %(lstm_conf_flat)s,
            %(ensemble_signal)s, %(ensemble_conf_long)s, %(ensemble_conf_short)s, %(ensemble_conf_flat)s,

            %(atr_14_m15)s, %(atr_14_h4)s, %(rsi_6)s, %(ema_7_h4)s, %(ema_200_h4)s,
            %(adx)s, %(regime)s, %(funding_rate)s, %(fear_greed)s, %(btc_dominance)s,
            %(market_session)s, %(msb_bos)s, %(choch)s, %(fvg_up)s, %(fvg_down)s,
            %(cvd)s, %(open_interest)s,

            'OPEN', %(note)s
        )
        RETURNING trade_id;
    """

    params = {
        "trade_id":           trade_id,
        "timestamp_entry":    datetime.now(timezone.utc),
        "symbol":             symbol.upper(),
        "direction":          direction.upper(),
        "price_entry":        price_entry,
        "tp_price":           tp_price,
        "sl_price":           sl_price,
        "position_size_usdt": position_size_usdt,
        "leverage":           leverage,

        # LGBM
        "lgbm_signal":        _signal(lgbm_proba),
        "lgbm_conf_long":     lgbm_proba.get("LONG", 0),
        "lgbm_conf_short":    lgbm_proba.get("SHORT", 0),
        "lgbm_conf_flat":     lgbm_proba.get("FLAT", 0),

        # LSTM
        "lstm_signal":        _signal(lstm_proba),
        "lstm_conf_long":     lstm_proba.get("LONG", 0),
        "lstm_conf_short":    lstm_proba.get("SHORT", 0),
        "lstm_conf_flat":     lstm_proba.get("FLAT", 0),

        # Ensemble
        "ensemble_signal":    _signal(ensemble_proba),
        "ensemble_conf_long":  ensemble_proba.get("LONG", 0),
        "ensemble_conf_short": ensemble_proba.get("SHORT", 0),
        "ensemble_conf_flat":  ensemble_proba.get("FLAT", 0),

        # Fitur snapshot dari candle terakhir
        "atr_14_m15":     features.get("atr_14_m15"),
        "atr_14_h4":      features.get("atr_14_h4"),
        "rsi_6":          features.get("rsi_6"),
        "ema_7_h4":       features.get("ema_7_h4"),
        "ema_200_h4":     features.get("ema_200_h4"),
        "adx":            features.get("adx"),
        "regime":         regime,
        "funding_rate":   features.get("funding_rate"),
        "fear_greed":     features.get("fear_greed"),
        "btc_dominance":  features.get("btc_dominance"),
        "market_session": features.get("market_session"),
        "msb_bos":        features.get("MSB_BOS"),
        "choch":          features.get("CHoCH"),
        "fvg_up":         features.get("FVG_up"),
        "fvg_down":       features.get("FVG_down"),
        "cvd":            features.get("cvd"),
        "open_interest":  features.get("open_interest"),

        "note": note,
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cur.fetchone()  # consume RETURNING

    logger.info(f"[TradeLog] Entry logged: {trade_id} | {symbol} {direction} @ {price_entry}")
    return trade_id


# ── log_exit ─────────────────────────────────────────────────────────────────

def log_exit(
    trade_id: str,
    price_exit: float,
    outcome: str,
    pnl_usdt: float,
    note: Optional[str] = None,
) -> dict:
    """
    Catat exit trade dan hitung metrik evaluasi. Dipanggil saat user klik exit/sell.

    Parameters
    ----------
    trade_id    : UUID dari log_entry (disimpan di kv_store PostgreSQL)
    price_exit  : harga exit aktual
    outcome     : 'TP' | 'SL' | 'MANUAL'
    pnl_usdt    : PnL aktual dalam USDT (sudah termasuk fee jika ada)
    note        : catatan exit opsional

    Returns
    -------
    dict ringkasan trade yang baru ditutup
    """
    outcome = outcome.upper()
    if outcome not in ("TP", "SL", "MANUAL"):
        raise ValueError("outcome harus TP, SL, atau MANUAL")

    fetch_sql = """
        SELECT timestamp_entry, price_entry, position_size_usdt,
               direction, ensemble_signal, lgbm_signal, lstm_signal
        FROM trade_log
        WHERE trade_id = %(trade_id)s AND status = 'OPEN'
    """

    update_sql = """
        UPDATE trade_log SET
            timestamp_exit      = %(timestamp_exit)s,
            price_exit          = %(price_exit)s,
            outcome             = %(outcome)s,
            pnl_usdt            = %(pnl_usdt)s,
            pnl_pct             = %(pnl_pct)s,
            hold_duration_bars  = %(hold_duration_bars)s,
            hold_duration_hours = %(hold_duration_hours)s,
            model_correct       = %(model_correct)s,
            lgbm_correct        = %(lgbm_correct)s,
            lstm_correct        = %(lstm_correct)s,
            status              = 'CLOSED',
            note                = COALESCE(%(note)s, note)
        WHERE trade_id = %(trade_id)s
        RETURNING *;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:

            # Fetch entry data
            cur.execute(fetch_sql, {"trade_id": trade_id})
            entry = cur.fetchone()

            if not entry:
                raise ValueError(f"Trade {trade_id} tidak ditemukan atau sudah CLOSED.")

            timestamp_exit  = datetime.now(timezone.utc)
            timestamp_entry = entry["timestamp_entry"]

            # Hitung durasi
            duration_hours = (timestamp_exit - timestamp_entry).total_seconds() / 3600
            duration_bars  = int(duration_hours * 4)  # 1 bar M15 = 15 menit

            # Hitung PnL persen
            price_entry_val    = float(entry["price_entry"])
            position_size_usdt = float(entry["position_size_usdt"] or 0)
            pnl_pct            = (pnl_usdt / position_size_usdt) if position_size_usdt > 0 else 0

            # Evaluasi apakah sinyal model benar
            direction     = entry["direction"]
            model_correct = (outcome == "TP")
            lgbm_correct  = (entry["lgbm_signal"] == direction) and (outcome == "TP")
            lstm_correct  = (entry["lstm_signal"] == direction) and (outcome == "TP")

            params = {
                "trade_id":            trade_id,
                "timestamp_exit":      timestamp_exit,
                "price_exit":          price_exit,
                "outcome":             outcome,
                "pnl_usdt":            pnl_usdt,
                "pnl_pct":             pnl_pct,
                "hold_duration_bars":  duration_bars,
                "hold_duration_hours": round(duration_hours, 2),
                "model_correct":       model_correct,
                "lgbm_correct":        lgbm_correct,
                "lstm_correct":        lstm_correct,
                "note":                note,
            }

            cur.execute(update_sql, params)
            closed = dict(cur.fetchone())

    logger.info(
        f"[TradeLog] Exit logged: {trade_id} | {outcome} | "
        f"PnL: {pnl_usdt:+.2f} USDT ({pnl_pct*100:+.2f}%)"
    )
    return closed


# ── get_logbook ───────────────────────────────────────────────────────────────

def get_logbook(
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    regime: Optional[str] = None,
    limit: int = 100,
) -> list:
    """
    Ambil data logbook dari Supabase dengan filter opsional.

    Parameters
    ----------
    symbol  : filter per koin, e.g. 'SOLUSDT'
    status  : 'OPEN' | 'CLOSED'
    regime  : 'BULLISH' | 'BEARISH' | 'SIDEWAYS' | 'HIGH_VOL'
    limit   : jumlah record maksimal

    Returns
    -------
    list of dict — siap dijadikan JSON response di Flask
    """
    conditions = []
    params: dict = {"limit": limit}

    if symbol:
        conditions.append("symbol = %(symbol)s")
        params["symbol"] = symbol.upper()
    if status:
        conditions.append("status = %(status)s")
        params["status"] = status.upper()
    if regime:
        conditions.append("regime = %(regime)s")
        params["regime"] = regime.upper()

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT * FROM trade_log
        {where_clause}
        ORDER BY timestamp_entry DESC
        LIMIT %(limit)s;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [dict(r) for r in rows]


# ── get_summary_stats ─────────────────────────────────────────────────────────

def get_summary_stats() -> dict:
    """
    Hitung statistik ringkasan dari semua trade yang sudah CLOSED.
    Berguna untuk dashboard logbook.
    """
    sql = """
        SELECT
            COUNT(*)                                        AS total_trades,
            COUNT(*) FILTER (WHERE outcome = 'TP')         AS total_tp,
            COUNT(*) FILTER (WHERE outcome = 'SL')         AS total_sl,
            COUNT(*) FILTER (WHERE outcome = 'MANUAL')     AS total_manual,
            ROUND(AVG(pnl_usdt)::NUMERIC, 2)               AS avg_pnl_usdt,
            ROUND(SUM(pnl_usdt)::NUMERIC, 2)               AS total_pnl_usdt,
            ROUND(AVG(hold_duration_hours)::NUMERIC, 2)    AS avg_hold_hours,
            ROUND(
                COUNT(*) FILTER (WHERE model_correct)::NUMERIC
                / NULLIF(COUNT(*) FILTER (WHERE status = 'CLOSED'), 0),
            4)                                             AS ensemble_winrate,
            ROUND(
                COUNT(*) FILTER (WHERE lgbm_correct)::NUMERIC
                / NULLIF(COUNT(*) FILTER (WHERE status = 'CLOSED'), 0),
            4)                                             AS lgbm_winrate,
            ROUND(
                COUNT(*) FILTER (WHERE lstm_correct)::NUMERIC
                / NULLIF(COUNT(*) FILTER (WHERE status = 'CLOSED'), 0),
            4)                                             AS lstm_winrate
        FROM trade_log
        WHERE status = 'CLOSED';
    """

    regime_sql = """
        SELECT
            regime,
            COUNT(*)                                        AS trades,
            ROUND(
                COUNT(*) FILTER (WHERE outcome = 'TP')::NUMERIC
                / NULLIF(COUNT(*), 0),
            4)                                             AS winrate,
            ROUND(SUM(pnl_usdt)::NUMERIC, 2)              AS total_pnl
        FROM trade_log
        WHERE status = 'CLOSED'
        GROUP BY regime
        ORDER BY winrate DESC;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            summary = dict(cur.fetchone())

            cur.execute(regime_sql)
            regime_breakdown = [dict(r) for r in cur.fetchall()]

    summary["regime_breakdown"] = regime_breakdown
    return summary
