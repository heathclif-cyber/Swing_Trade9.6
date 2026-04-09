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
