"""
paper_trading.py
=================
Paper Trading Engine — simulasi otomatis untuk mencatat dan mengevaluasi
setiap sinyal LONG/SHORT beserta Exit/TP/SL yang dihasilkan aplikasi.

Cara kerja:
  1. signal_monitor memanggil open_position() saat sinyal baru terkonfirmasi
  2. signal_monitor memanggil check_tp_sl() setiap siklus untuk memantau harga
  3. signal_monitor memanggil close_position() saat TP/SL/EXIT/EXPIRED terdeteksi
  4. Dashboard membaca state via API endpoints

Data disimpan di PostgreSQL (paper_trades table) dengan fallback JSON file.
In-memory state sebagai primary read source untuk performa.
"""
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("PaperTrading")

# ── Thread safety ──────────────────────────────────────────
_lock = threading.Lock()
_paper_positions: dict = {}  # {symbol: position_dict}
_equity_history: list = []   # [(ts, cumulative_pnl_usdt), ...]

# ── Config ─────────────────────────────────────────────────
ALLOCATED_CAPITAL = 200.0  # USDT — sync dengan protocol_96_ui.py
FEE_PCT = 0.0008           # 0.04% entry + 0.04% exit
DEFAULT_LEVERAGE = 3

# ── File path fallback ─────────────────────────────────────
_default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper_trades.json')
PAPER_TRADES_FILE = os.environ.get('PAPER_TRADES_PATH', _default_path)
_equity_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper_equity.json')


# ============================================================
# DATABASE HELPERS (sama pattern dengan signal_monitor)
# ============================================================
def _db_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def _get_pg_conn():
    db = _db_url()
    if not db:
        return None
    try:
        import psycopg2  # type: ignore
        from urllib.parse import urlparse
        url_str = db.replace("postgres://", "postgresql://", 1)
        p = urlparse(url_str)
        return psycopg2.connect(
            host=p.hostname, port=p.port or 5432,
            user=p.username, password=p.password,
            dbname=p.path.lstrip("/"), sslmode="require"
        )
    except Exception as e:
        logger.warning(f"PG conn failed: {e}")
        return None


# ============================================================
# PERSISTENCE
# ============================================================
def _save_positions_to_db():
    """Write-through: simpan semua open positions ke DB."""
    db = _db_url()
    if not db:
        # Fallback ke JSON
        try:
            with open(PAPER_TRADES_FILE, "w") as f:
                json.dump(_paper_positions, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Failed to save paper_trades.json: {e}")
        return

    conn = _get_pg_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            for sym, pos in _paper_positions.items():
                if pos.get("status") != "OPEN":
                    continue
                cur.execute("""
                    INSERT INTO paper_trades (
                        id, symbol, direction, status,
                        entry_price, entry_ts, entry_conf, entry_ml_size,
                        tp1_price, tp2_price, tp3_price, sl_price,
                        leverage, fee_pct
                    ) VALUES (
                        %(id)s, %(symbol)s, %(direction)s, 'OPEN',
                        %(entry_price)s, %(entry_ts)s, %(entry_conf)s, %(entry_ml_size)s,
                        %(tp1_price)s, %(tp2_price)s, %(tp3_price)s, %(sl_price)s,
                        %(leverage)s, %(fee_pct)s
                    ) ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        updated_at = NOW()
                """, {
                    "id": pos["id"],
                    "symbol": sym,
                    "direction": pos["direction"],
                    "entry_price": pos["entry_price"],
                    "entry_ts": datetime.fromtimestamp(pos["entry_ts"], tz=timezone.utc),
                    "entry_conf": pos.get("entry_conf"),
                    "entry_ml_size": pos.get("entry_ml_size"),
                    "tp1_price": pos.get("tp1_price"),
                    "tp2_price": pos.get("tp2_price"),
                    "tp3_price": pos.get("tp3_price"),
                    "sl_price": pos["sl_price"],
                    "leverage": pos.get("leverage", DEFAULT_LEVERAGE),
                    "fee_pct": FEE_PCT,
                })
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to save paper positions to DB: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _load_positions_from_db() -> dict:
    """Load open positions dari DB ke in-memory state."""
    db = _db_url()
    if not db:
        # Fallback dari JSON
        path = PAPER_TRADES_FILE
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    conn = _get_pg_conn()
    if conn is None:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM paper_trades
                WHERE status = 'OPEN'
                ORDER BY entry_ts DESC
            """)
            rows = cur.fetchall()
            positions = {}
            for row in rows:
                sym = row[1]  # symbol
                positions[sym] = {
                    "id": str(row[0]),
                    "symbol": sym,
                    "direction": row[2],
                    "status": row[3],
                    "entry_price": float(row[4]),
                    "entry_ts": row[5].timestamp() if hasattr(row[5], 'timestamp') else time.time(),
                    "entry_conf": float(row[6]) if row[6] else None,
                    "entry_ml_size": row[7],
                    "tp1_price": float(row[8]) if row[8] else None,
                    "tp2_price": float(row[9]) if row[9] else None,
                    "tp3_price": float(row[10]) if row[10] else None,
                    "sl_price": float(row[11]),
                    "leverage": float(row[16]) if row[16] else DEFAULT_LEVERAGE,
                    "fee_pct": float(row[17]) if row[17] else FEE_PCT,
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "tp3_hit": False,
                }
            return positions
    except Exception as e:
        logger.error(f"Failed to load paper positions from DB: {e}")
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _save_equity_to_db():
    """Simpan equity history ke DB."""
    db = _db_url()
    if not db:
        try:
            with open(_equity_path, "w") as f:
                json.dump(_equity_history, f, indent=2, default=str)
        except Exception:
            pass
        return

    conn = _get_pg_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kv_store (key, value, updated_at)
                VALUES ('paper_equity', %s, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
            """, (json.dumps(_equity_history, default=str),))
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to save equity to DB: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _load_equity_from_db() -> list:
    """Load equity history dari DB."""
    db = _db_url()
    if not db:
        path = _equity_path
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    conn = _get_pg_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM kv_store WHERE key = 'paper_equity'")
            row = cur.fetchone()
            return json.loads(row[0]) if row else []
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# CORE FUNCTIONS
# ============================================================

def load_open_positions():
    """Load open positions from DB into memory. Panggil saat startup."""
    global _paper_positions, _equity_history
    with _lock:
        _paper_positions = _load_positions_from_db()
        _equity_history = _load_equity_from_db()
    logger.info(f"[PaperTrading] Loaded {len(_paper_positions)} open positions, {len(_equity_history)} equity points")


def open_position(
    symbol: str,
    direction: str,
    entry_price: float,
    conf: float,
    ml_size: str,
    tp1: float,
    tp2: float,
    tp3: float,
    sl: float,
    leverage: float = DEFAULT_LEVERAGE,
) -> dict:
    """
    Open a new paper trade position.

    - If there's already an OPEN position for the same symbol with opposite direction,
      it will be auto-closed (SIGNAL_FLIP) before opening the new one.
    - If there's already an OPEN position with the same direction, it's a no-op
      (cooldown prevents duplicate signals in signal_monitor).

    Returns the position dict.
    """
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        logger.warning(f"[PaperTrading] Invalid direction: {direction}")
        return {}

    with _lock:
        existing = _paper_positions.get(symbol)

        # If same direction already open — skip (cooldown handles this)
        if existing and existing.get("status") == "OPEN" and existing.get("direction") == direction:
            logger.info(f"[PaperTrading] {symbol} already has OPEN {direction} — skipping duplicate")
            return existing

        # If opposite direction open — close it first (signal flip)
        if existing and existing.get("status") == "OPEN" and existing.get("direction") != direction:
            _close_position_internal(symbol, entry_price, "SIGNAL_FLIP")
            logger.info(f"[PaperTrading] {symbol}: closed existing {existing['direction']} due to signal flip to {direction}")

        # Create new position
        pos_id = str(uuid.uuid4())
        now_ts = time.time()

        position = {
            "id": pos_id,
            "symbol": symbol,
            "direction": direction,
            "status": "OPEN",
            "entry_price": entry_price,
            "entry_ts": now_ts,
            "entry_conf": conf,
            "entry_ml_size": ml_size,
            "tp1_price": tp1,
            "tp2_price": tp2,
            "tp3_price": tp3,
            "sl_price": sl,
            "leverage": leverage,
            "fee_pct": FEE_PCT,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
        }

        _paper_positions[symbol] = position
        _save_positions_to_db()

        logger.info(
            f"[PaperTrading] ✅ OPEN {direction} {symbol} @ ${entry_price:.4f} "
            f"(conf={conf*100:.1f}%, size={ml_size}, SL=${sl:.4f})"
        )
        return position


def close_position(symbol: str, exit_price: float, exit_reason: str) -> Optional[dict]:
    """
    Close an open paper trade position.

    exit_reason: 'TP1' | 'TP2' | 'TP3' | 'SL' | 'EXIT' | 'EXPIRED' | 'SIGNAL_FLIP'

    Returns the closed position dict with PnL, or None if no open position.
    """
    with _lock:
        return _close_position_internal(symbol, exit_price, exit_reason)


def _close_position_internal(symbol: str, exit_price: float, exit_reason: str) -> Optional[dict]:
    """Internal close — must be called with _lock held."""
    pos = _paper_positions.get(symbol)
    if not pos or pos.get("status") != "OPEN":
        logger.warning(f"[PaperTrading] No open position for {symbol} to close")
        return None

    direction = pos["direction"]
    entry_price = pos["entry_price"]
    leverage = pos.get("leverage", DEFAULT_LEVERAGE)
    fee_pct = pos.get("fee_pct", FEE_PCT)

    # Calculate PnL
    if direction == "LONG":
        pnl_raw = (exit_price - entry_price) / entry_price
    else:
        pnl_raw = (entry_price - exit_price) / entry_price

    pnl_pct = pnl_raw * leverage - fee_pct
    pnl_usdt = ALLOCATED_CAPITAL * pnl_pct

    hold_hours = (time.time() - pos["entry_ts"]) / 3600

    # Update position
    pos["status"] = "CLOSED"
    pos["exit_price"] = exit_price
    pos["exit_ts"] = time.time()
    pos["exit_reason"] = exit_reason
    pos["pnl_pct"] = round(pnl_pct * 100, 2)  # in %
    pos["pnl_usdt"] = round(pnl_usdt, 2)
    pos["hold_hours"] = round(hold_hours, 2)

    # Persist close to DB
    _persist_close_to_db(pos)

    # Remove from in-memory open positions
    del _paper_positions[symbol]

    # Record equity point
    cumulative = _get_cumulative_pnl()
    _equity_history.append({
        "ts": pos["exit_ts"],
        "pnl_usdt": pos["pnl_usdt"],
        "cumulative_pnl": round(cumulative + pos["pnl_usdt"], 2),
    })
    _save_equity_to_db()

    logger.info(
        f"[PaperTrading] ❌ CLOSED {direction} {symbol} — "
        f"exit_reason={exit_reason}, exit=${exit_price:.4f}, "
        f"PnL={pos['pnl_pct']:+.2f}% (${pos['pnl_usdt']:+.2f}), hold={hold_hours:.1f}h"
    )
    return pos


def _persist_close_to_db(pos: dict):
    """Update DB record with close data."""
    db = _db_url()
    if not db:
        # JSON fallback — just save full state
        try:
            with open(PAPER_TRADES_FILE, "w") as f:
                json.dump(_paper_positions, f, indent=2, default=str)
        except Exception:
            pass
        return

    conn = _get_pg_conn()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE paper_trades SET
                    status = 'CLOSED',
                    exit_price = %(exit_price)s,
                    exit_ts = %(exit_ts)s,
                    exit_reason = %(exit_reason)s,
                    pnl_pct = %(pnl_pct)s,
                    pnl_usdt = %(pnl_usdt)s,
                    hold_hours = %(hold_hours)s,
                    updated_at = NOW()
                WHERE id = %(id)s
            """, {
                "id": pos["id"],
                "exit_price": pos["exit_price"],
                "exit_ts": datetime.fromtimestamp(pos["exit_ts"], tz=timezone.utc),
                "exit_reason": pos["exit_reason"],
                "pnl_pct": pos["pnl_pct"],
                "pnl_usdt": pos["pnl_usdt"],
                "hold_hours": pos["hold_hours"],
            })
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to persist close to DB: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _get_cumulative_pnl() -> float:
    """Calculate cumulative PnL from equity history."""
    if not _equity_history:
        return 0.0
    return _equity_history[-1].get("cumulative_pnl", 0.0)


# ============================================================
# TP/SL CHECK — dipanggil oleh signal_monitor setiap siklus
# ============================================================

def check_tp_sl(symbol: str, high_price: float, low_price: float) -> Optional[dict]:
    """
    Check if current candle's high/low has hit TP or SL levels.

    Returns dict with hit info if triggered, None otherwise.
    Called by signal_monitor._evaluate_pair() each cycle.
    """
    with _lock:
        pos = _paper_positions.get(symbol)
        if not pos or pos.get("status") != "OPEN":
            return None

        direction = pos["direction"]
        result = None

        if direction == "LONG":
            # Check TP levels (in order)
            if not pos.get("tp1_hit") and pos.get("tp1_price") and high_price >= pos["tp1_price"]:
                pos["tp1_hit"] = True
                result = {"hit": "TP1", "price": pos["tp1_price"], "full_close": True}
            elif not pos.get("tp2_hit") and pos.get("tp2_price") and high_price >= pos["tp2_price"]:
                pos["tp2_hit"] = True
                result = {"hit": "TP2", "price": pos["tp2_price"], "full_close": True}
            elif not pos.get("tp3_hit") and pos.get("tp3_price") and high_price >= pos["tp3_price"]:
                pos["tp3_hit"] = True
                result = {"hit": "TP3", "price": pos["tp3_price"], "full_close": True}
            # Check SL
            elif low_price <= pos["sl_price"]:
                result = {"hit": "SL", "price": pos["sl_price"], "full_close": True}

        elif direction == "SHORT":
            # Check TP levels (in order)
            if not pos.get("tp1_hit") and pos.get("tp1_price") and low_price <= pos["tp1_price"]:
                pos["tp1_hit"] = True
                result = {"hit": "TP1", "price": pos["tp1_price"], "full_close": True}
            elif not pos.get("tp2_hit") and pos.get("tp2_price") and low_price <= pos["tp2_price"]:
                pos["tp2_hit"] = True
                result = {"hit": "TP2", "price": pos["tp2_price"], "full_close": True}
            elif not pos.get("tp3_hit") and pos.get("tp3_price") and low_price <= pos["tp3_price"]:
                pos["tp3_hit"] = True
                result = {"hit": "TP3", "price": pos["tp3_price"], "full_close": True}
            # Check SL
            elif high_price >= pos["sl_price"]:
                result = {"hit": "SL", "price": pos["sl_price"], "full_close": True}

        if result:
            logger.info(
                f"[PaperTrading] 🎯 {result['hit']} detected for {symbol} {direction} "
                f"@ ${result['price']:.4f}"
            )

        return result


# ============================================================
# READ API — untuk dashboard
# ============================================================

def get_open_positions(current_prices: dict = None) -> list:
    """
    Return all open positions with optional floating PnL.

    current_prices: {symbol: current_price} — from main data fetch
    """
    with _lock:
        result = []
        for sym, pos in _paper_positions.items():
            if pos.get("status") != "OPEN":
                continue

            entry = pos["entry_price"]
            direction = pos["direction"]
            leverage = pos.get("leverage", DEFAULT_LEVERAGE)
            current_price = None
            floating_pnl_pct = None
            floating_pnl_usdt = None

            if current_prices and sym in current_prices:
                current_price = current_prices[sym]
                if direction == "LONG":
                    pnl_raw = (current_price - entry) / entry
                else:
                    pnl_raw = (entry - current_price) / entry
                floating_pnl_pct = round((pnl_raw * leverage - FEE_PCT) * 100, 2)
                floating_pnl_usdt = round(ALLOCATED_CAPITAL * floating_pnl_pct / 100, 2)

            hold_hours = round((time.time() - pos["entry_ts"]) / 3600, 1)

            result.append({
                "id": pos["id"],
                "symbol": sym,
                "direction": pos["direction"],
                "entry_price": entry,
                "entry_ts": pos["entry_ts"],
                "entry_conf": pos.get("entry_conf"),
                "entry_ml_size": pos.get("entry_ml_size"),
                "tp1_price": pos.get("tp1_price"),
                "tp2_price": pos.get("tp2_price"),
                "tp3_price": pos.get("tp3_price"),
                "sl_price": pos["sl_price"],
                "leverage": leverage,
                "current_price": current_price,
                "floating_pnl_pct": floating_pnl_pct,
                "floating_pnl_usdt": floating_pnl_usdt,
                "hold_hours": hold_hours,
            })
        return result


def get_closed_trades(limit: int = 50, symbol: Optional[str] = None) -> list:
    """Return closed trades from DB."""
    db = _db_url()
    if not db:
        # No DB — return empty (in-memory only has open positions)
        return []

    conn = _get_pg_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            if symbol:
                cur.execute("""
                    SELECT * FROM paper_trades
                    WHERE status = 'CLOSED' AND symbol = %(symbol)s
                    ORDER BY exit_ts DESC NULLS LAST
                    LIMIT %(limit)s
                """, {"symbol": symbol.upper(), "limit": limit})
            else:
                cur.execute("""
                    SELECT * FROM paper_trades
                    WHERE status = 'CLOSED'
                    ORDER BY exit_ts DESC NULLS LAST
                    LIMIT %(limit)s
                """, {"limit": limit})
            rows = cur.fetchall()
            return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to fetch closed trades: {e}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _row_to_dict(row) -> dict:
    """Convert a psycopg2 row (tuple) to dict by column index."""
    def _ts(v):
        """Convert DB timestamp to Unix epoch (seconds) for JS compatibility."""
        if v is None:
            return None
        if hasattr(v, 'timestamp'):
            return v.timestamp()
        return v

    return {
        "id": str(row[0]),
        "symbol": row[1],
        "direction": row[2],
        "status": row[3],
        "entry_price": float(row[4]) if row[4] else None,
        "entry_ts": _ts(row[5]),
        "entry_conf": float(row[6]) if row[6] else None,
        "entry_ml_size": row[7],
        "tp1_price": float(row[8]) if row[8] else None,
        "tp2_price": float(row[9]) if row[9] else None,
        "tp3_price": float(row[10]) if row[10] else None,
        "sl_price": float(row[11]) if row[11] else None,
        "exit_price": float(row[12]) if row[12] else None,
        "exit_ts": _ts(row[13]),
        "exit_reason": row[14],
        "pnl_pct": float(row[15]) if row[15] else None,
        "pnl_usdt": float(row[16]) if row[16] else None,
        "hold_hours": float(row[17]) if row[17] else None,
        "leverage": float(row[18]) if row[18] else DEFAULT_LEVERAGE,
        "fee_pct": float(row[19]) if row[19] else FEE_PCT,
    }


def get_stats() -> dict:
    """
    Compute aggregate metrics from all closed trades + open positions.

    Returns dict with:
      - total_pnl_usdt, total_pnl_pct
      - win_rate (TP count / total closed)
      - total_trades (closed count)
      - long_count, short_count
      - tp_hits, sl_hits
      - avg_hold_hours
      - best_trade_pct, worst_trade_pct
      - open_positions count
      - equity_curve (last 100 points)
    """
    db = _db_url()
    closed_trades = []
    if db:
        conn = _get_pg_conn()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT * FROM paper_trades
                        WHERE status = 'CLOSED'
                        ORDER BY exit_ts DESC NULLS LAST
                    """)
                    closed_trades = [_row_to_dict(r) for r in cur.fetchall()]
            except Exception as e:
                logger.error(f"Failed to fetch stats from DB: {e}")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    total = len(closed_trades)
    tp_hits = sum(1 for t in closed_trades if t.get("exit_reason") in ("TP1", "TP2", "TP3"))
    sl_hits = sum(1 for t in closed_trades if t.get("exit_reason") == "SL")
    long_count = sum(1 for t in closed_trades if t.get("direction") == "LONG")
    short_count = sum(1 for t in closed_trades if t.get("direction") == "SHORT")

    total_pnl_usdt = sum(t.get("pnl_usdt") or 0 for t in closed_trades)
    total_pnl_pct = sum(t.get("pnl_pct") or 0 for t in closed_trades)

    win_rate = round(tp_hits / total * 100, 1) if total > 0 else 0

    pnl_values = [t.get("pnl_pct") or 0 for t in closed_trades]
    best_trade = max(pnl_values) if pnl_values else 0
    worst_trade = min(pnl_values) if pnl_values else 0

    avg_hold = 0
    if total > 0:
        holds = [t.get("hold_hours") or 0 for t in closed_trades]
        avg_hold = round(sum(holds) / len(holds), 1)

    with _lock:
        open_count = sum(1 for p in _paper_positions.values() if p.get("status") == "OPEN")

    # Equity curve — last 100 points
    equity_curve = _equity_history[-100:] if _equity_history else []
    # Add current cumulative PnL as latest point if there are open positions
    if open_count > 0 and equity_curve:
        latest_equity = equity_curve[-1]["cumulative_pnl"]
    else:
        latest_equity = total_pnl_usdt

    return {
        "total_pnl_usdt": round(total_pnl_usdt, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "win_rate": win_rate,
        "total_trades": total,
        "long_count": long_count,
        "short_count": short_count,
        "tp_hits": tp_hits,
        "sl_hits": sl_hits,
        "avg_hold_hours": avg_hold,
        "best_trade_pct": round(best_trade, 2),
        "worst_trade_pct": round(worst_trade, 2),
        "open_positions": open_count,
        "latest_equity": round(latest_equity, 2),
        "equity_curve": equity_curve,
    }


def reset_all():
    """Clear all paper trades (for testing)."""
    with _lock:
        _paper_positions.clear()
        _equity_history.clear()

    db = _db_url()
    if db:
        conn = _get_pg_conn()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM paper_trades")
                    cur.execute("""
                        INSERT INTO kv_store (key, value, updated_at)
                        VALUES ('paper_equity', '[]', NOW())
                        ON CONFLICT (key) DO UPDATE SET value = '[]', updated_at = NOW()
                    """)
                    conn.commit()
            except Exception as e:
                logger.warning(f"Failed to reset paper_trades in DB: {e}")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    # JSON fallback
    try:
        with open(PAPER_TRADES_FILE, "w") as f:
            json.dump({}, f)
        with open(_equity_path, "w") as f:
            json.dump([], f)
    except Exception:
        pass

    logger.info("[PaperTrading] All paper trades reset")
