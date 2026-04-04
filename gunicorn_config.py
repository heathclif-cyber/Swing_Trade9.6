"""
Gunicorn config — server hooks untuk inisialisasi background thread
saat aplikasi berjalan di Railway dengan Gunicorn.
"""

# Bind sudah diatur via Procfile
workers = 1  # 1 worker agar state in-memory (alert dedup) konsisten
threads = 4  # 4 thread untuk handle concurrent requests

def post_fork(server, worker):
    """Dipanggil setelah worker di-fork — start signal monitor di sini."""
    import signal_monitor
    signal_monitor.start_background_monitor()
