import re
import os

def patch_ui():
    fpath = "protocol_96_ui.py"
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()

    new_endpoint = """

@app.route("/api/price-performance/<path:pair>")
def api_price_performance(pair: str):
    \"\"\"
    Gabungkan data OHLCV (M15) dengan sinyal ML dari confidence-history.
    Digunakan untuk Price Performance Monitor chart.
    \"\"\"
    pair = pair.upper()
    hours = int(flask_request.args.get("hours", 24))
    leverage = float(flask_request.args.get("leverage", 1.0))
    
    try:
        # Ambil confidence history (sudah ada)
        hist = signal_monitor.get_confidence_history(pair, hours=hours)
        
        # Ambil OHLCV M15 dari data_engine (sudah ada di aplikasi)
        from protocol_96_enrichment import get_fully_enriched_data
        df, _ = get_fully_enriched_data(pair, interval="15m", limit=hours*4)
        
        ohlcv = []
        if df is not None and not df.empty:
            for _, row in df.tail(hours*4).iterrows():
                ohlcv.append({
                    "ts":    int(row["Open_Time"].timestamp() * 1000) if hasattr(row["Open_Time"], "timestamp") else int(row["Open_Time"]),
                    "open":  float(row["Open"]),
                    "high":  float(row["High"]),
                    "low":   float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row.get("Total_Volume", 0) if "Total_Volume" in row else row.get("Volume", 0)),
                })
        
        # Hitung simulasi PnL dari sinyal ML
        pnl_simulation = []
        fee = 0.0004  # 0.04% per side
        for h in hist:
            if h.get("ml_signal") in ["LONG", "SHORT"]:
                entry = h.get("close", 0)
                conf  = h.get("ml_conf", 0)
                signal = h.get("ml_signal")
                # TP/SL sederhana berbasis conf (sesuaikan jika ada ATR)
                tp_pct = 0.015 * leverage  # 1.5% x leverage
                sl_pct = 0.008 * leverage  # 0.8% x leverage
                pnl_simulation.append({
                    "ts":     h.get("ts"),
                    "signal": signal,
                    "conf":   conf,
                    "entry":  entry,
                    "tp":     entry * (1 + tp_pct) if signal == "LONG" else entry * (1 - tp_pct),
                    "sl":     entry * (1 - sl_pct) if signal == "LONG" else entry * (1 + sl_pct),
                    "fee_pct": fee * 2 * leverage,
                })
        
        # Statistik performa
        long_signals  = [h for h in hist if h.get("ml_signal") == "LONG"]
        short_signals = [h for h in hist if h.get("ml_signal") == "SHORT"]
        flat_signals  = [h for h in hist if h.get("ml_signal") == "FLAT"]
        avg_conf      = sum(h.get("ml_conf", 0) for h in hist) / len(hist) if hist else 0
        
        stats = {
            "total_signals": len(hist),
            "long_count":    len(long_signals),
            "short_count":   len(short_signals),
            "flat_count":    len(flat_signals),
            "avg_confidence": round(avg_conf * 100, 1),
            "leverage":      leverage,
            "hours":         hours,
        }
        
        return jsonify({
            "success":    True,
            "pair":       pair,
            "ohlcv":      ohlcv,
            "signals":    hist,
            "simulation": pnl_simulation,
            "stats":      stats,
        })
    except Exception as e:
        logger.exception(f"price-performance error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# =========================================="""
    
    target = 'return jsonify({"success": False, "error": str(e)}), 500\n\n\n# =========================================='
    replacement = 'return jsonify({"success": False, "error": str(e)}), 500\n' + new_endpoint
    
    if target in content:
        content = content.replace(target, replacement)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        print("protocol_96_ui.py patched successfully.")
    else:
        print("Could not find target block in protocol_96_ui.py.")


def patch_html():
    fpath = "templates/dashboard.html"
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add Chart.js to <head>
    if "Chart.js" not in content:
        target1 = '</head>'
        replacement1 = '    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>\n</head>'
        content = content.replace(target1, replacement1)
    
    # 2. Add CSS
    css_content = """
/* ══════════════════════════════════════════════════
   GLASSMORPHISM UPGRADE — Global
   ══════════════════════════════════════════════════ */
:root {
  --glass-bg:        rgba(255, 255, 255, 0.03);
  --glass-bg-hover:  rgba(255, 255, 255, 0.06);
  --glass-border:    rgba(255, 255, 255, 0.08);
  --glass-shadow:    0 8px 32px rgba(0, 0, 0, 0.4);
  --glass-blur:      blur(12px);
  --radius-lg:       16px;
  --radius-md:       12px;
}

.glass-card {
  background:    var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border:        1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow:    var(--glass-shadow);
  transition:    background 0.2s, box-shadow 0.2s;
}

.glass-card:hover {
  background:  var(--glass-bg-hover);
  box-shadow:  0 12px 40px rgba(0,0,0,0.5);
}

/* Input glassmorphism */
.glass-input {
  background:    rgba(255,255,255,0.05);
  border:        1px solid var(--glass-border);
  border-radius: var(--radius-md);
  color:         var(--text-1);
  padding:       10px 14px;
  font-size:     14px;
  outline:       none;
  transition:    border-color 0.2s, box-shadow 0.2s;
  backdrop-filter: blur(4px);
}
.glass-input:focus {
  border-color: var(--accent-blue);
  box-shadow:   0 0 0 3px rgba(99,125,255,0.15);
}

/* Select glassmorphism */
.glass-select {
  background:    rgba(20,20,35,0.8);
  border:        1px solid var(--glass-border);
  border-radius: var(--radius-md);
  color:         var(--text-1);
  padding:       10px 14px;
  font-size:     14px;
  outline:       none;
  cursor:        pointer;
  transition:    border-color 0.2s;
}
.glass-select:focus { border-color: var(--accent-blue); }

/* Button glassmorphism */
.glass-btn {
  background:    linear-gradient(135deg, rgba(99,125,255,0.2), rgba(167,139,250,0.2));
  border:        1px solid rgba(99,125,255,0.3);
  border-radius: var(--radius-md);
  color:         var(--text-1);
  padding:       10px 20px;
  font-size:     14px;
  font-weight:   600;
  cursor:        pointer;
  backdrop-filter: blur(4px);
  transition:    all 0.2s;
}
.glass-btn:hover {
  background:  linear-gradient(135deg, rgba(99,125,255,0.35), rgba(167,139,250,0.35));
  box-shadow:  0 0 20px rgba(99,125,255,0.3);
  transform:   translateY(-1px);
}
.glass-btn:active { transform: translateY(0); }

/* Stat card kecil */
.perf-stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.perf-stat-card {
  background:    rgba(255,255,255,0.04);
  border:        1px solid var(--glass-border);
  border-radius: var(--radius-md);
  padding:       14px 12px;
  text-align:    center;
  backdrop-filter: blur(8px);
}
.perf-stat-label {
  font-size:  11px;
  color:      var(--text-2);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.perf-stat-value {
  font-size:   20px;
  font-weight: 700;
  font-family: var(--mono);
}
.perf-stat-value.green { color: #34d399; }
.perf-stat-value.red   { color: #f87171; }
.perf-stat-value.blue  { color: var(--accent-blue); }
.perf-stat-value.purple{ color: var(--accent-purple); }

/* Chart container */
#perfChartContainer {
  position:      relative;
  width:         100%;
  height:        420px;
  border-radius: var(--radius-md);
  overflow:      hidden;
  border:        1px solid var(--glass-border);
  background:    rgba(10,10,20,0.6);
}

/* Loading state chart */
.chart-loading {
  position:   absolute;
  inset:      0;
  display:    flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 12px;
  color: var(--text-2);
  font-size: 14px;
}
.chart-spinner {
  width:  36px;
  height: 36px;
  border: 3px solid var(--glass-border);
  border-top-color: var(--accent-blue);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Signal badge di legend */
.signal-badge {
  display:       inline-flex;
  align-items:   center;
  gap:           5px;
  padding:       3px 10px;
  border-radius: 20px;
  font-size:     12px;
  font-weight:   600;
}
.signal-badge.LONG  { background: rgba(52,211,153,0.15); color: #34d399; border: 1px solid rgba(52,211,153,0.3); }
.signal-badge.SHORT { background: rgba(248,113,113,0.15); color: #f87171; border: 1px solid rgba(248,113,113,0.3); }
.signal-badge.FLAT  { background: rgba(148,163,184,0.15); color: #94a3b8; border: 1px solid rgba(148,163,184,0.3); }

/* ── QUANT SECTION ──────────────────────────────────────────────────────── */"""
    target2 = '/* ── QUANT SECTION ──────────────────────────────────────────────────────── */'
    content = content.replace(target2, css_content, 1)

    # 3. Add HTML Section
    html_section = """
    <!-- ═══════════════ SECTION: PRICE PERFORMANCE MONITOR ═══════════════ -->
    <div class="section glass-card" id="sectionPerfMonitor" style="padding:20px;">
      <div class="section-header section-collapsible open" onclick="toggleSection('perfMonitorContent', this)">
        <div class="section-icon-badge icon-cyan">📈</div>
        <div class="section-title">Price Performance Monitor — Validasi Performa Model</div>
        <span class="section-badge" style="background:rgba(52,211,153,.12);color:#34d399;border-color:rgba(52,211,153,.25)">LIVE</span>
        <span class="chevron" style="margin-left:auto;color:var(--text-2);font-size:12px;">▼</span>
      </div>

      <div id="perfMonitorContent" class="section-body">

        <!-- Input Controls -->
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin-bottom:18px;">
          
          <div style="display:flex;flex-direction:column;gap:6px;">
            <label style="font-size:12px;color:var(--text-2);text-transform:uppercase;letter-spacing:.5px;">Koin</label>
            <select id="perfCoinSelect" class="glass-select" style="min-width:160px;">
              <option value="DOGEUSDT">DOGEUSDT ⭐</option>
              <option value="TRXUSDT">TRXUSDT ⭐</option>
              <option value="ARBUSDT">ARBUSDT ⭐</option>
              <option value="ETHUSDT">ETHUSDT</option>
              <option value="BNBUSDT">BNBUSDT</option>
              <option value="SOLUSDT">SOLUSDT</option>
              <option value="SUIUSDT">SUIUSDT</option>
              <option value="XRPUSDT">XRPUSDT</option>
              <option value="ADAUSDT">ADAUSDT</option>
              <option value="AVAXUSDT">AVAXUSDT</option>
              <option value="LINKUSDT">LINKUSDT</option>
              <option value="NEARUSDT">NEARUSDT</option>
            </select>
          </div>

          <div style="display:flex;flex-direction:column;gap:6px;">
            <label style="font-size:12px;color:var(--text-2);text-transform:uppercase;letter-spacing:.5px;">Leverage</label>
            <input id="perfLeverageInput" type="number" class="glass-input" 
                   value="3" min="1" max="20" step="1" style="width:90px;" />
          </div>

          <div style="display:flex;flex-direction:column;gap:6px;">
            <label style="font-size:12px;color:var(--text-2);text-transform:uppercase;letter-spacing:.5px;">Periode</label>
            <select id="perfHoursSelect" class="glass-select">
              <option value="12">12 Jam</option>
              <option value="24" selected>24 Jam</option>
              <option value="48">48 Jam</option>
              <option value="72">72 Jam</option>
            </select>
          </div>

          <button class="glass-btn" onclick="loadPerfChart()" style="align-self:flex-end;">
            🔍 Analisis
          </button>

          <div id="perfLastUpdate" style="align-self:flex-end;font-size:12px;color:var(--text-2);padding-bottom:2px;"></div>
        </div>

        <!-- Stat Cards -->
        <div class="perf-stat-grid" id="perfStatGrid" style="display:none;">
          <div class="perf-stat-card">
            <div class="perf-stat-label">Total Sinyal</div>
            <div class="perf-stat-value blue" id="statTotalSignals">—</div>
          </div>
          <div class="perf-stat-card">
            <div class="perf-stat-label">🐂 LONG</div>
            <div class="perf-stat-value green" id="statLongCount">—</div>
          </div>
          <div class="perf-stat-card">
            <div class="perf-stat-label">🐻 SHORT</div>
            <div class="perf-stat-value red" id="statShortCount">—</div>
          </div>
          <div class="perf-stat-card">
            <div class="perf-stat-label">⏸ FLAT</div>
            <div class="perf-stat-value" id="statFlatCount" style="color:var(--text-2);">—</div>
          </div>
          <div class="perf-stat-card">
            <div class="perf-stat-label">Avg Confidence</div>
            <div class="perf-stat-value purple" id="statAvgConf">—</div>
          </div>
          <div class="perf-stat-card">
            <div class="perf-stat-label">Leverage</div>
            <div class="perf-stat-value blue" id="statLeverage">—</div>
          </div>
        </div>

        <!-- Chart Area -->
        <div id="perfChartContainer">
          <div class="chart-loading" id="perfChartPlaceholder">
            <div style="font-size:32px;">📈</div>
            <div>Pilih koin & klik <strong>Analisis</strong> untuk memuat grafik</div>
          </div>
          <canvas id="perfChart" style="display:none;"></canvas>
        </div>

        <!-- Legend -->
        <div id="perfLegend" style="display:none;margin-top:12px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
          <span style="font-size:12px;color:var(--text-2);">Sinyal:</span>
          <span class="signal-badge LONG">▲ LONG</span>
          <span class="signal-badge SHORT">▼ SHORT</span>
          <span class="signal-badge FLAT">— FLAT</span>
          <span style="font-size:12px;color:var(--text-2);margin-left:8px;">● = confidence ≥ 0.72 (aktif trading)</span>
        </div>

      </div>
    </div>
    <!-- ═══════════════ END PRICE PERFORMANCE MONITOR ═══════════════ -->

    <!-- ═══════════════ SECTION 2: KILL SWITCH + MARKET STRUCTURE ═══════════════ -->
    <div class="section glass-card" id="sectionMarketStructure" style="padding:20px;">"""
    target3 = """    <!-- ═══════════════ SECTION 2: KILL SWITCH + MARKET STRUCTURE ═══════════════ -->
    <div class="section" id="sectionMarketStructure">"""
    content = content.replace(target3, html_section, 1)

    # 4. Replace other sections
    content = content.replace('<div class="section" id="sectionScanner">', '<div class="section glass-card" id="sectionScanner" style="padding:20px;">')
    content = content.replace('<div class="section" id="sectionQuantitative">', '<div class="section glass-card" id="sectionQuantitative" style="padding:20px;">')
    content = content.replace('<div class="section" id="sectionRaw">', '<div class="section glass-card" id="sectionRaw" style="padding:20px;">')
    content = content.replace('<div class="section" id="sectionComputed">', '<div class="section glass-card" id="sectionComputed" style="padding:20px;">')

    # 5. Add JS before </script>
    js_content = """
// ══════════════════════════════════════════════════
// PRICE PERFORMANCE MONITOR
// ══════════════════════════════════════════════════

let perfChartInstance = null;

async function loadPerfChart() {
  const coin     = document.getElementById('perfCoinSelect').value;
  const leverage = parseFloat(document.getElementById('perfLeverageInput').value) || 3;
  const hours    = parseInt(document.getElementById('perfHoursSelect').value) || 24;

  // Show loading
  const container   = document.getElementById('perfChartContainer');
  const placeholder = document.getElementById('perfChartPlaceholder');
  const canvas      = document.getElementById('perfChart');
  placeholder.innerHTML = `
    <div class="chart-spinner"></div>
    <div>Memuat data ${coin}...</div>
  `;
  placeholder.style.display = 'flex';
  canvas.style.display = 'none';

  try {
    const res  = await fetch(`/api/price-performance/${coin}?hours=${hours}&leverage=${leverage}`);
    const data = await res.json();
    if (!data.success) throw new Error(data.error || 'Gagal memuat data');

    renderPerfChart(data, coin, leverage);
    updatePerfStats(data.stats);

    document.getElementById('perfLastUpdate').textContent =
      `Update: ${new Date().toLocaleTimeString('id-ID')}`;

  } catch (err) {
    placeholder.innerHTML = `
      <div style="color:#f87171;font-size:13px;">❌ Error: ${err.message}</div>
    `;
    placeholder.style.display = 'flex';
    console.error('PerfChart error:', err);
  }
}

function renderPerfChart(data, coin, leverage) {
  const canvas      = document.getElementById('perfChart');
  const placeholder = document.getElementById('perfChartPlaceholder');

  if (perfChartInstance) {
    perfChartInstance.destroy();
    perfChartInstance = null;
  }

  const ohlcv   = data.ohlcv   || [];
  const signals = data.signals || [];

  if (ohlcv.length === 0) {
    placeholder.innerHTML = '<div style="color:var(--text-2);">Tidak ada data OHLCV tersedia</div>';
    placeholder.style.display = 'flex';
    return;
  }

  // Siapkan data chart
  const labels     = ohlcv.map(d => {
    const dt = new Date(d.ts);
    return dt.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
  });
  const closes     = ohlcv.map(d => d.close);

  // Map sinyal ke timestamp
  const signalMap = {};
  signals.forEach(s => { if (s.ts) signalMap[s.ts] = s; });

  // Scatter points untuk sinyal
  const longPoints  = [];
  const shortPoints = [];
  const flatPoints  = [];
  
  ohlcv.forEach((d, i) => {
    // Cari sinyal yang paling dekat dengan timestamp candle ini
    const match = signals.find(s => {
      const diff = Math.abs((s.ts || 0) - d.ts);
      return diff < 15 * 60 * 1000; // dalam 1 candle M15
    });
    if (match) {
      const sig = match.ml_signal || match.signal;
      const conf = match.ml_conf  || match.conf || 0;
      const point = { x: i, y: d.close, conf: conf, ts: d.ts };
      if (sig === 'LONG')       longPoints.push(point);
      else if (sig === 'SHORT') shortPoints.push(point);
      else if (sig === 'FLAT')  flatPoints.push(point);
    }
  });

  // Warna gradient untuk price line
  const ctx = canvas.getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, 400);
  gradient.addColorStop(0, 'rgba(99,125,255,0.3)');
  gradient.addColorStop(1, 'rgba(99,125,255,0.0)');

  placeholder.style.display = 'none';
  canvas.style.display = 'block';

  perfChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        // Harga Close
        {
          label: `${coin} Close`,
          data: closes,
          borderColor: 'rgba(99,125,255,0.9)',
          borderWidth: 1.5,
          backgroundColor: gradient,
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          pointHoverRadius: 4,
          order: 3,
        },
        // Sinyal LONG
        {
          label: '▲ LONG',
          data: longPoints.map(p => ({ x: p.x, y: p.y })),
          type: 'scatter',
          borderColor: '#34d399',
          backgroundColor: p => {
            const pt = longPoints[p.dataIndex];
            return pt && pt.conf >= 0.72 
              ? 'rgba(52,211,153,0.9)' 
              : 'rgba(52,211,153,0.35)';
          },
          pointRadius: p => {
            const pt = longPoints[p.dataIndex];
            return pt && pt.conf >= 0.72 ? 10 : 6;
          },
          pointStyle: 'triangle',
          order: 1,
        },
        // Sinyal SHORT
        {
          label: '▼ SHORT',
          data: shortPoints.map(p => ({ x: p.x, y: p.y })),
          type: 'scatter',
          borderColor: '#f87171',
          backgroundColor: p => {
            const pt = shortPoints[p.dataIndex];
            return pt && pt.conf >= 0.72 
              ? 'rgba(248,113,113,0.9)' 
              : 'rgba(248,113,113,0.35)';
          },
          pointRadius: p => {
            const pt = shortPoints[p.dataIndex];
            return pt && pt.conf >= 0.72 ? 10 : 6;
          },
          pointStyle: 'triangle',
          rotation: 180,
          order: 1,
        },
        // Sinyal FLAT
        {
          label: '— FLAT',
          data: flatPoints.map(p => ({ x: p.x, y: p.y })),
          type: 'scatter',
          borderColor: '#94a3b8',
          backgroundColor: 'rgba(148,163,184,0.3)',
          pointRadius: 5,
          pointStyle: 'circle',
          order: 2,
        },
      ]
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      animation:   { duration: 400 },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: {
            color:    'rgba(255,255,255,0.7)',
            boxWidth: 12,
            font:     { size: 12 },
          }
        },
        tooltip: {
          backgroundColor: 'rgba(15,15,30,0.95)',
          borderColor:     'rgba(255,255,255,0.1)',
          borderWidth:     1,
          titleColor: '#fff',
          bodyColor:       'rgba(255,255,255,0.7)',
          padding:         12,
          callbacks: {
            afterBody: function(items) {
              const i = items[0]?.dataIndex;
              const sig = signals.find(s => {
                const d = ohlcv[i];
                return d && Math.abs((s.ts||0) - d.ts) < 15*60*1000;
              });
              if (!sig) return '';
              const conf = ((sig.ml_conf || sig.conf || 0) * 100).toFixed(1);
              const lev  = leverage;
              return [
                `Signal : ${sig.ml_signal || sig.signal || '—'}`,
                `Conf   : ${conf}%`,
                `Lev    : ${lev}x`,
              ];
            }
          }
        }
      },
      scales: {
        x: {
          ticks: {
            color:     'rgba(255,255,255,0.4)',
            maxTicksLimit: 12,
            font: { size: 11 },
          },
          grid: { color: 'rgba(255,255,255,0.04)' }
        },
        y: {
          position: 'right',
          ticks: {
            color: 'rgba(255,255,255,0.4)',
            font: { size: 11 },
            callback: v => v >= 1 ? v.toFixed(2) : v.toFixed(5),
          },
          grid: { color: 'rgba(255,255,255,0.04)' }
        }
      }
    }
  });

  // Tampilkan legend
  document.getElementById('perfLegend').style.display = 'flex';
}

function updatePerfStats(stats) {
  if (!stats) return;
  const grid = document.getElementById('perfStatGrid');
  grid.style.display = 'grid';
  document.getElementById('statTotalSignals').textContent = stats.total_signals ?? '—';
  document.getElementById('statLongCount').textContent    = stats.long_count    ?? '—';
  document.getElementById('statShortCount').textContent   = stats.short_count   ?? '—';
  document.getElementById('statFlatCount').textContent    = stats.flat_count    ?? '—';
  document.getElementById('statAvgConf').textContent      = (stats.avg_confidence ?? '—') + '%';
  document.getElementById('statLeverage').textContent     = (stats.leverage ?? '—') + 'x';
}

// Ensure the old script tag end matches
</script>"""
    
    target_js = "setInterval(checkSystemHealth, 30000);\n</script>"
    replacement_js = "setInterval(checkSystemHealth, 30000);\n" + js_content
    
    if target_js in content:
        content = content.replace(target_js, replacement_js)
    
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    print("dashboard.html patched successfully.")

patch_ui()
patch_html()
