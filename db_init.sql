-- ============================================================
-- Protocol 9.6 — Database Initialization Script
-- Jalankan SEKALI di Supabase SQL Editor atau Railway PostgreSQL
-- Aman dijalankan berulang kali (idempotent: IF NOT EXISTS)
-- ============================================================

-- Tabel utama: Key-Value Store untuk semua persistent state
CREATE TABLE IF NOT EXISTS kv_store (
    key        VARCHAR(255) PRIMARY KEY,
    value      TEXT         NOT NULL,
    updated_at TIMESTAMPTZ  DEFAULT NOW()
);

-- Index untuk mempercepat query monoton
CREATE INDEX IF NOT EXISTS idx_kv_store_updated_at ON kv_store (updated_at DESC);

-- ── Verifikasi ──────────────────────────────────────────────
-- Setelah membuat tabel, pastikan dua key utama sudah ada (opsional):
-- SELECT key, updated_at FROM kv_store WHERE key IN ('trade_entries', 'alert_state');

-- ── CATATAN ──────────────────────────────────────────────────
-- Dua key yang digunakan aplikasi:
--   'trade_entries' : JSON dict posisi trading semua koin
--   'alert_state'   : JSON dict status alert Telegram per koin
--
-- Format value adalah JSON string (bukan JSONB) agar kompatibel
-- dengan json.dumps() / json.loads() Python tanpa casting khusus.

-- ============================================================
-- Paper Trading — Simulasi Otomatis
-- ============================================================
CREATE TABLE IF NOT EXISTS paper_trades (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(20) NOT NULL,
    direction       VARCHAR(10) NOT NULL,
    status          VARCHAR(10) NOT NULL DEFAULT 'OPEN',
    entry_price     NUMERIC NOT NULL,
    entry_ts        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entry_conf      NUMERIC,
    entry_ml_size   VARCHAR(10),
    tp1_price       NUMERIC,
    tp2_price       NUMERIC,
    tp3_price       NUMERIC,
    sl_price        NUMERIC NOT NULL,
    exit_price      NUMERIC,
    exit_ts         TIMESTAMPTZ,
    exit_reason     VARCHAR(20),
    pnl_pct         NUMERIC,
    pnl_usdt        NUMERIC,
    hold_hours      NUMERIC,
    leverage        NUMERIC NOT NULL DEFAULT 3,
    fee_pct         NUMERIC DEFAULT 0.0008,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol   ON paper_trades (symbol);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status   ON paper_trades (status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_entry_ts ON paper_trades (entry_ts DESC);
