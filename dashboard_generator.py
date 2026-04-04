"""
Protocol 9.6 — CSV Dashboard Generator (Full Spec)
Reads a Protocol 9.6 enriched CSV, runs the 71-point scoring engine,
and produces a standalone interactive HTML analysis dashboard.

Usage:
    python dashboard_generator.py <path_to_csv>
    python dashboard_generator.py                  # tries enriched_export.csv
"""
import os, sys, re
import pandas as pd
import numpy as np
from io import StringIO
from datetime import datetime

try:
    from algo_scoring import calculate_71point_score
except ImportError:
    print("⚠️  algo_scoring.py not found. Exiting.")
    sys.exit(1)


# ────────────────────────────────────────────────────────────────────
# CSV PARSING
# ────────────────────────────────────────────────────────────────────
def parse_csv_and_metadata(filepath: str):
    meta = {
        'Symbol': 'UNKNOWN', 'Timeframe': '4H',
        'AVG_ENTRY_PRICE': None, 'TOTAL_QTY': None,
        'TOTAL_COST': None, 'Export_Time': None, 'ENTRY_DATE': None,
    }
    if not os.path.exists(filepath):
        print(f"Error: '{filepath}' not found.")
        return meta, pd.DataFrame()

    data_lines = []
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            ls = line.strip()
            if ls.startswith('#'):
                if 'Symbol' in ls:
                    p = ls.split('Symbol')
                    if len(p) > 1: meta['Symbol'] = p[1].replace(':', '').replace('=', '').strip()
                elif 'Timeframe' in ls:
                    p = ls.split('Timeframe')
                    if len(p) > 1: meta['Timeframe'] = p[1].replace(':', '').replace('=', '').strip()
                elif 'AVG ENTRY PRICE' in ls or 'AVG_ENTRY_PRICE' in ls:
                    m = re.search(r'[\d\.]+', ls.split('PRICE')[-1])
                    if m: meta['AVG_ENTRY_PRICE'] = float(m.group())
                elif 'Entry #1: Price=' in ls and meta['AVG_ENTRY_PRICE'] is None:
                    m = re.search(r'Price=([\d\.]+)', ls)
                    if m: meta['AVG_ENTRY_PRICE'] = float(m.group(1))
                if 'Entry #1: Date=' in ls:
                    m = re.search(r'Date=(.+?)(?:,|$)', ls)
                    if m: meta['ENTRY_DATE'] = m.group(1).strip()
                if 'TOTAL QTY' in ls:
                    m = re.search(r'[\d\.]+', ls.split('QTY')[-1])
                    if m: meta['TOTAL_QTY'] = float(m.group())
                elif 'TOTAL COST' in ls:
                    m = re.search(r'[\d\.]+', ls.split('COST')[-1])
                    if m: meta['TOTAL_COST'] = float(m.group())
                elif 'Export Time' in ls:
                    p = ls.split('Export Time')
                    if len(p) > 1: meta['Export_Time'] = p[1].replace(':', '', 1).strip()
            else:
                data_lines.append(ls)
    if not data_lines:
        return meta, pd.DataFrame()
    df = pd.read_csv(StringIO('\n'.join(data_lines)))
    return meta, df


def ensure_indicators(df):
    try:
        import pandas_ta as ta
        if 'EMA_21' not in df.columns: df['EMA_21'] = ta.ema(df['Close'], length=21)
        if 'EMA_50' not in df.columns: df['EMA_50'] = ta.ema(df['Close'], length=50)
        if 'EMA_200' not in df.columns: df['EMA_200'] = ta.ema(df['Close'], length=200)
        if 'RSI_6' not in df.columns: df['RSI_6'] = ta.rsi(df['Close'], length=6)
        if 'ATR_14' not in df.columns: df['ATR_14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    except ImportError:
        pass
    return df


# ────────────────────────────────────────────────────────────────────
# HTML GENERATION
# ────────────────────────────────────────────────────────────────────
FEATURE_LABELS = {
    'OI': 'Open Interest', 'Vol': 'Volume Relatif',
    'TakerBuy': 'Taker Buy Pressure', 'ATR': 'Volatilitas ATR%',
    'CVD': 'Cumulative Vol Delta', 'EMA21': 'Jarak EMA 21',
    'EMA50': 'Jarak EMA 50', 'EMA200': 'Jarak EMA 200',
    'RSI': 'RSI 6 Momentum',
}
FEATURE_UNIT = {
    'OI': '%', 'Vol': '%', 'TakerBuy': '%', 'ATR': '%',
    'CVD': '%', 'EMA21': '%', 'EMA50': '%', 'EMA200': '%', 'RSI': '',
}
DOT_COLORS = {3: '#1D9E75', 2: '#BA7517', 1: '#D85A30', 0: '#E24B4A'}
DECISION_COLORS = {
    'FULL': ('#10b981', 'rgba(16,185,129,0.12)', 'rgba(16,185,129,0.35)'),
    'HALF': ('#3b82f6', 'rgba(59,130,246,0.12)', 'rgba(59,130,246,0.35)'),
    'WAIT': ('#f59e0b', 'rgba(245,158,11,0.12)', 'rgba(245,158,11,0.35)'),
    'SKIP': ('#ef4444', 'rgba(239,68,68,0.12)', 'rgba(239,68,68,0.35)'),
}

def _fmt(v, d=4):
    if v is None: return '—'
    return f"${v:.{d}f}"

def _pct(v):
    if v is None: return '—'
    return f"{'+' if v >= 0 else ''}{v:.2f}%"


def render_feature_rows(scores):
    rows = ''
    for key, (pts, mx, raw, stars) in scores.items():
        fill = (pts / mx * 100) if mx > 0 else 0
        c = DOT_COLORS.get(stars, '#E24B4A')
        unit = FEATURE_UNIT.get(key, '')
        raw_f = ('+' if isinstance(raw, (int, float)) and raw >= 0 else '') + f'{raw:.2f}{unit}' if isinstance(raw, (int, float)) else str(raw)
        rows += f'''
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--gbr)">
            <div style="width:9px;height:9px;border-radius:50%;background:{c};flex-shrink:0"></div>
            <div style="flex:1;font-size:12px;color:var(--t2)">{FEATURE_LABELS.get(key, key)}</div>
            <div style="font-family:var(--mono);font-size:12px;font-weight:600;min-width:60px;text-align:right">{raw_f}</div>
            <div style="width:72px;height:5px;background:var(--gbr);border-radius:3px;overflow:hidden;flex-shrink:0">
                <div style="width:{fill:.0f}%;height:100%;background:{c};border-radius:3px"></div>
            </div>
            <div style="font-size:10px;color:var(--t3);font-family:var(--mono);min-width:40px;text-align:right">{pts}/{mx}</div>
        </div>'''
    return rows


def render_sl_tp(lv, is_long, vdata):
    sign = '+' if is_long else '−'
    sl_lbl = lv.get('sl_label', 'ATR')
    rr_badge = lambda r: f'<span style="display:inline-block;padding:2px 7px;border-radius:8px;font-size:10px;font-weight:700;{"background:rgba(16,185,129,.2);color:#10b981" if r >= 2 else "background:rgba(239,68,68,.2);color:#ef4444"}">R:R {r}×</span>'

    tp_triggers_L = [
        "Exit 30% · Trigger: RSI>70 + price>TP1",
        "Exit 40% · Trigger: RSI>78 + price>TP2",
        "Exit 30% · Trigger: RSI>85 atau divergence",
    ]
    tp_triggers_S = [
        "Exit 30% · Trigger: RSI<30 + price<TP1",
        "Exit 40% · Trigger: RSI<22 + price<TP2",
        "Exit 30% · Trigger: RSI<15 atau divergence",
    ]
    triggers = tp_triggers_L if is_long else tp_triggers_S

    return f'''
    <div style="font-size:11px;color:var(--t3);font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">Stop Loss</div>
    <div class="pill" style="border-left:3px solid #ef4444">
        <div><span style="font-weight:700;color:#ef4444">SL Utama</span> — <span style="color:var(--t2)">{sl_lbl}</span></div>
        <div style="text-align:right"><span class="val-neg">{_fmt(lv["sl_structure"])}</span> <span style="font-size:10px;color:var(--t3)">{_pct(lv["dist_sl"])}</span></div>
    </div>
    <div style="font-size:10px;color:var(--t3);margin:6px 0 4px;font-weight:600">Referensi ATR:</div>
    <div class="pill"><span>Ketat 1×ATR</span><span class="val-neg">{_fmt(lv["sl_ketat"])}</span></div>
    <div class="pill"><span>Normal 1.5×ATR</span><span class="val-neg">{_fmt(lv["sl_normal"])}</span></div>
    <div class="pill"><span>Lebar 2×ATR</span><span class="val-neg">{_fmt(lv["sl_lebar"])}</span></div>

    <div style="font-size:11px;color:var(--t3);font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin:14px 0 8px">Take Profit</div>
    <div class="pill" style="flex-wrap:wrap;gap:4px;border-left:3px solid #10b981">
        <div><span style="font-weight:700;color:#10b981">TP1</span> <span style="color:var(--t2)">{lv["tp1_label"]}</span></div>
        <div style="text-align:right"><span class="val-pos">{_fmt(lv["tp1"])}</span> <span style="font-size:10px;color:var(--t3)">{_pct(lv["dist_tp1"])}</span> {rr_badge(lv["rr1"])}</div>
        <div style="width:100%;font-size:10px;color:var(--t3)">{triggers[0]}</div>
    </div>
    <div class="pill" style="flex-wrap:wrap;gap:4px;border-left:3px solid #10b981">
        <div><span style="font-weight:700;color:#10b981">TP2</span> <span style="color:var(--t2)">{lv["tp2_label"]}</span></div>
        <div style="text-align:right"><span class="val-pos">{_fmt(lv["tp2"])}</span> <span style="font-size:10px;color:var(--t3)">{_pct(lv["dist_tp2"])}</span> {rr_badge(lv["rr2"])}</div>
        <div style="width:100%;font-size:10px;color:var(--t3)">{triggers[1]}</div>
    </div>
    <div class="pill" style="flex-wrap:wrap;gap:4px;border-left:3px solid #10b981">
        <div><span style="font-weight:700;color:#10b981">TP3</span> <span style="color:var(--t2)">{lv["tp3_label"]}</span></div>
        <div style="text-align:right"><span class="val-pos">{_fmt(lv["tp3"])}</span> <span style="font-size:10px;color:var(--t3)">{_pct(lv["dist_tp3"])}</span> {rr_badge(lv["rr3"])}</div>
        <div style="width:100%;font-size:10px;color:var(--t3)">{triggers[2]}</div>
    </div>
    {"<div style='margin-top:8px;padding:8px;border-radius:8px;font-size:11px;font-weight:600;color:#ef4444;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);text-align:center'>⚠️ R:R TP3 di bawah minimum 2.0×</div>" if lv["rr3"] < 2.0 else ""}
    <div style="margin-top:8px;font-size:10px;color:var(--t3)">
        Vol: MA20={_pct(vdata.get("F_vol_short"))} · MA100={_pct(vdata.get("F_vol_long"))} · avg={_pct(vdata.get("F_final"))}<br>
        OI: MA20={_pct(vdata.get("C_oi_short"))} · MA100={_pct(vdata.get("C_oi_long"))} · avg={_pct(vdata.get("C_final"))}<br>
        ATR_MULT = {vdata.get("ATR_MULT", "?")} ({vdata.get("atr_mult_reason", "?")})
    </div>'''


def render_narrative(n, is_long):
    bc = '#10b981' if is_long else '#ef4444'
    bull_c, bear_c = '#0F6E56', '#993C1D'
    return f'''
    <div style="background:var(--gb);border-left:3px solid {bc};border-radius:8px;padding:14px;font-size:12.5px;line-height:1.75;color:var(--t2)">
        <div style="margin-bottom:10px"><span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--t3);display:block;margin-bottom:3px">📍 Kondisi</span>{n["kondisi"]}</div>
        <div style="margin-bottom:10px"><span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--t3);display:block;margin-bottom:3px">🎯 Keputusan</span><strong style="color:{bull_c if is_long else bear_c}">{n["keputusan"]}</strong></div>
        <div><span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--t3);display:block;margin-bottom:3px">🗺️ Skenario</span>{n["skenario"]}</div>
    </div>'''


def render_exit_signals(exit_data):
    if not exit_data or not exit_data.get('signals'):
        return '<div style="font-size:12px;color:var(--t3);padding:10px 0">✅ Semua indikator dalam batas aman</div>'
    rows = ''
    for icon, name, val, thresh in exit_data['signals']:
        vf = f'{val:.2f}' if isinstance(val, float) else str(val)
        rows += f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--gbr);font-size:12px"><span>{icon}</span><span style="flex:1;color:var(--t2)">{name}</span><span style="font-family:var(--mono);font-size:11px;color:var(--t3)">{vf} ({thresh})</span></div>'
    h = exit_data.get('hard_count', 0)
    w = exit_data.get('warn_count', 0)
    mc = '#ef4444' if h > 0 else '#f59e0b' if w > 0 else '#10b981'
    mb = 'rgba(239,68,68,.1)' if h > 0 else 'rgba(245,158,11,.1)' if w > 0 else 'rgba(16,185,129,.1)'
    return rows + f'<div style="margin-top:10px;padding:10px;border-radius:8px;text-align:center;font-weight:700;font-size:13px;color:{mc};background:{mb};border:1px solid {mc}30">{exit_data["recommendation"]}</div>'


def render_context_grid(ctx, sl_labels, tp_labels):
    if not ctx:
        return ''
    items = ''
    for k, v in ctx.items():
        vf = f'{v:.4f}' if isinstance(v, (int, float)) else str(v)
        badge = ''
        if k in sl_labels:
            badge = ' <span style="font-size:8px;padding:1px 5px;border-radius:4px;background:rgba(239,68,68,.2);color:#ef4444;font-weight:700">● SL</span>'
        for i, tl in enumerate(tp_labels):
            if k in tl:
                badge += f' <span style="font-size:8px;padding:1px 5px;border-radius:4px;background:rgba(16,185,129,.2);color:#10b981;font-weight:700">● TP{i+1}</span>'
        items += f'<div class="pill"><span>{k}{badge}</span><span style="font-family:var(--mono)">{vf}</span></div>'
    return f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:6px">{items}</div>'


def build_html(meta, df, result, output_file):
    last = df.iloc[-1]
    close = float(last.get('Close', 0))
    ts = str(last.get('Timestamp', ''))
    is_active = bool(meta.get('AVG_ENTRY_PRICE'))
    vdata = result.get('variables', {})
    em = result.get('emergency', {})
    exit_data = result.get('exit', {})
    val_data = result.get('validation', {})
    ctx = result.get('market_context', {})

    # Validation badge
    if val_data.get('ok'):
        val_badge = '<span style="font-size:11px;padding:4px 12px;border-radius:20px;background:rgba(16,185,129,.12);color:#10b981;font-weight:600;border:1px solid rgba(16,185,129,.3)">✅ Kalkulasi valid</span>'
    else:
        issues = ' | '.join(val_data.get('issues', []))
        val_badge = f'<span style="font-size:11px;padding:4px 12px;border-radius:20px;background:rgba(245,158,11,.12);color:#f59e0b;font-weight:600;border:1px solid rgba(245,158,11,.3)">{issues}</span>'

    # Emergency banners
    emer_html = ''
    if is_active:
        banners = []
        if em.get('sl_touched'):
            banners.append('⚠️ SL SUDAH TERSENTUH — EVALUASI EXIT SEGERA')
        if em.get('rsi_ob'):
            banners.append('⚠️ RSI OVERBOUGHT — CEK EXIT SIGNAL')
        if em.get('stale'):
            banners.append('❌ POSISI STALE >14 HARI — PERTIMBANGKAN EXIT')
        for msg in banners:
            emer_html += f'<div style="display:flex;align-items:center;justify-content:center;gap:12px;padding:14px 20px;margin-bottom:12px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.4);border-radius:12px;color:#fca5a5;font-weight:700;font-size:14px">{msg}</div>'

    # Meta strip
    mi = [('Symbol', meta['Symbol']), ('TF', meta['Timeframe']),
          ('Close', f"${close:.4f}"), ('Session', vdata.get('session', '—')),
          ('S.Mult', f"×{vdata.get('SESSION_MULT', '?')}"), ('Candle', ts)]
    if is_active:
        mi += [('Entry', f"${meta['AVG_ENTRY_PRICE']:.4f}")]
        if meta.get('TOTAL_QTY'): mi.append(('Qty', str(meta['TOTAL_QTY'])))
        if meta.get('TOTAL_COST'): mi.append(('Cost', f"${meta['TOTAL_COST']:.2f}"))
        pnl = vdata.get('pnl_pct')
        if pnl is not None: mi.append(('P&L', _pct(pnl)))
        mi.append(('Aging', vdata.get('aging_status', 'N/A')))

    meta_html = ''.join(f'<div style="text-align:center"><div style="font-size:10px;color:var(--t3);font-weight:600;text-transform:uppercase;letter-spacing:.6px">{k}</div><div style="font-size:15px;font-weight:600;font-family:var(--mono);margin-top:2px">{v}</div></div>' for k, v in mi)

    mode_html = '<span style="font-weight:700;color:#f59e0b">POSISI AKTIF</span>' if is_active else '<span style="font-weight:700;color:#06b6d4">ENTRY BARU</span>'

    # SL/TP labels for context grid badges
    long_lv = result['long']['levels']
    short_lv = result['short']['levels']
    sl_labels_set = set()
    tp_labels_list = [set(), set(), set()]
    for lbl_key in ['sl_label']:
        for side in [long_lv, short_lv]:
            sl_labels_set.add(side.get(lbl_key, ''))
    for i, tl_key in enumerate(['tp1_label', 'tp2_label', 'tp3_label']):
        for side in [long_lv, short_lv]:
            tp_labels_list[i].add(side.get(tl_key, ''))

    # Tab content builders
    def build_tab(side_data, is_long):
        d = side_data
        lv = d['levels']
        code = d['code']
        tc, bg, bd = DECISION_COLORS.get(code, DECISION_COLORS['WAIT'])
        bar_pct = d['total'] / 71 * 100
        icon = '🐂' if is_long else '🐻'
        label = 'LONG' if is_long else 'SHORT'

        features = render_feature_rows(d['scores'])
        sl_tp = render_sl_tp(lv, is_long, vdata)
        narr = render_narrative(d['narrative'], is_long)

        exit_html = ''
        if is_active:
            exit_html = f'''
            <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--gbr)">
                <div style="font-size:11px;color:var(--t3);font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">Exit Signal Monitor</div>
                {render_exit_signals(exit_data)}
            </div>'''

        return f'''
        <div style="padding:22px;display:flex;flex-direction:column;gap:16px">
            <div style="text-align:center;padding:18px;border-radius:12px;background:{bg};border:1px solid {bd}">
                <div style="font-size:10px;text-transform:uppercase;letter-spacing:2px;color:var(--t3);margin-bottom:4px">{icon} {label} Setup</div>
                <div style="font-size:26px;font-weight:800;letter-spacing:-0.5px;color:{tc}">{d["decision"]}</div>
                <div style="font-size:13px;color:var(--t2);margin-top:4px">RAW {d["raw"]} → ADJ {d["total"]}/71 · {d["pct"]:.1f}%</div>
            </div>
            <div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--t3);margin-bottom:4px"><span>RAW Score</span><span>{d["raw"]}/71</span></div>
                <div style="height:5px;background:var(--gbr);border-radius:4px;overflow:hidden;margin-bottom:6px">
                    <div style="height:100%;width:{d['raw']/71*100:.1f}%;background:{tc}80;border-radius:4px"></div>
                </div>
                <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--t3);margin-bottom:4px"><span>ADJ Score (×{vdata.get('SESSION_MULT','?')})</span><span>{d["total"]}/71</span></div>
                <div style="height:7px;background:var(--gbr);border-radius:4px;overflow:hidden">
                    <div style="height:100%;width:{bar_pct:.1f}%;background:{tc};border-radius:4px;transition:width .5s"></div>
                </div>
            </div>
            <div>
                <div style="font-size:11px;color:var(--t3);font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px">9-Feature Scoring</div>
                {features}
            </div>
            <div>{sl_tp}</div>
            {exit_html}
            <div>
                <div style="font-size:11px;color:var(--t3);font-weight:700;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px">Narasi Analis</div>
                {narr}
            </div>
        </div>'''

    long_content = build_tab(result['long'], True)
    short_content = build_tab(result['short'], False)
    ctx_html = render_context_grid(ctx, sl_labels_set, tp_labels_list)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    html = f'''<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Protocol 9.6 — {meta["Symbol"]} {meta["Timeframe"]}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
    --bg:#080c14;--surf:#0d1220;--gb:rgba(255,255,255,0.04);--gbr:rgba(255,255,255,0.09);
    --t1:#f1f5f9;--t2:#94a3b8;--t3:#64748b;
    --blue:#3b82f6;--cyan:#06b6d4;--green:#10b981;--red:#ef4444;--yellow:#f59e0b;--purple:#8b5cf6;
    --font:'Inter',sans-serif;--mono:'JetBrains Mono',monospace;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--font);background:var(--bg);color:var(--t1);min-height:100vh;-webkit-font-smoothing:antialiased;line-height:1.5;
    background-image:radial-gradient(ellipse 80% 60% at 20% 0%,rgba(59,130,246,0.06) 0%,transparent 60%),
                     radial-gradient(ellipse 60% 50% at 80% 100%,rgba(139,92,246,0.05) 0%,transparent 60%)}}
.container{{max-width:1000px;margin:0 auto;padding:20px}}
.glass{{background:var(--gb);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid var(--gbr);border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,0.5)}}
.val-pos{{color:var(--green)!important}}.val-neg{{color:var(--red)!important}}
.pill{{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:var(--gb);border:1px solid var(--gbr);border-radius:8px;margin-bottom:5px;font-family:var(--mono);font-size:12px;gap:8px;flex-wrap:wrap}}
.tab-btn{{background:none;border:1px solid var(--gbr);border-radius:20px;color:var(--t2);padding:8px 24px;cursor:pointer;font-size:13px;font-family:var(--font);font-weight:600;transition:.2s}}
.tab-btn:hover{{background:rgba(255,255,255,0.05);color:var(--t1)}}
.tab-btn.active-long{{background:rgba(16,185,129,0.15);color:#10b981;border-color:rgba(16,185,129,0.4)}}
.tab-btn.active-short{{background:rgba(239,68,68,0.15);color:#ef4444;border-color:rgba(239,68,68,0.4)}}
.tab-content{{display:none}}.tab-content.active{{display:block}}
@media print{{.tab-btn{{display:none}}.tab-content{{display:block!important}}}}
</style>
</head>
<body>
<div class="container">

<!-- HEADER -->
<div class="glass" style="padding:18px;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
    <div style="display:flex;align-items:center;gap:14px">
        <div style="width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,#3b82f6,#06b6d4);display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 0 24px rgba(59,130,246,0.2)">🛡️</div>
        <div>
            <div style="font-size:18px;font-weight:700;letter-spacing:-0.5px">Protocol 9.6</div>
            <div style="font-size:11px;color:var(--t3)">71-Point Quantitative Swing Engine</div>
        </div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        {val_badge}
        <span style="font-size:11px;color:var(--t3);background:var(--gb);border:1px solid var(--gbr);padding:4px 14px;border-radius:20px">{now_str}</span>
    </div>
</div>

{emer_html}

<!-- META STRIP -->
<div class="glass" style="padding:14px 20px;margin-bottom:12px;display:flex;flex-wrap:wrap;gap:20px;align-items:center">
    {meta_html}
    <div style="margin-left:auto"><span style="font-size:10px;color:var(--t3);text-transform:uppercase;letter-spacing:.6px">Mode</span><br>{mode_html}</div>
</div>

<!-- TABS -->
<div style="display:flex;gap:8px;margin-bottom:12px">
    <button class="tab-btn active-long" id="btn-long" onclick="switchTab('long')">🐂 LONG Setup</button>
    <button class="tab-btn" id="btn-short" onclick="switchTab('short')">🐻 SHORT Setup</button>
</div>

<div class="glass tab-content active" id="tab-long">{long_content}</div>
<div class="glass tab-content" id="tab-short">{short_content}</div>

<!-- MARKET CONTEXT -->
{"" if not ctx else f"""
<div class="glass" style="padding:18px;margin-top:12px">
    <div style="font-size:13px;font-weight:700;color:var(--t2);text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px;display:flex;align-items:center;gap:8px">🔍 Konteks Pasar</div>
    {ctx_html}
</div>"""}

<!-- VARIABLES -->
<div class="glass" style="padding:18px;margin-top:12px">
    <div style="font-size:13px;font-weight:700;color:var(--t2);text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px">📐 Intermediate Variables</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:6px">
        {"".join(f'<div class="pill"><span>{k}</span><span>{v if isinstance(v, str) else f"{v:.4f}" if isinstance(v, float) else str(v)}</span></div>' for k, v in vdata.items())}
    </div>
</div>

<div style="text-align:center;padding:18px;color:var(--t3);font-size:11px">
    Protocol 9.6 · 71-Point Quantitative Swing Engine · {now_str}
</div>

</div>
<script>
function switchTab(tab) {{
    document.getElementById('tab-long').classList.toggle('active', tab === 'long');
    document.getElementById('tab-short').classList.toggle('active', tab === 'short');
    const bl = document.getElementById('btn-long');
    const bs = document.getElementById('btn-short');
    bl.className = tab === 'long' ? 'tab-btn active-long' : 'tab-btn';
    bs.className = tab === 'short' ? 'tab-btn active-short' : 'tab-btn';
}}
</script>
</body>
</html>'''

    with open(output_file, 'w', encoding='utf-8') as fh:
        fh.write(html)
    print(f"✅ Dashboard written to: {output_file}")


# ────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────
def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else 'enriched_export.csv'
    print(f"📂 Reading: {csv_path}")
    meta, df = parse_csv_and_metadata(csv_path)
    if df.empty:
        print("❌ No data loaded."); return
    print(f"📊 Rows: {len(df)} | Symbol: {meta['Symbol']} | TF: {meta['Timeframe']}")
    df = ensure_indicators(df)
    if len(df) < 22:
        print("❌ Need ≥ 22 candles."); return

    print("🤖 Running 71-point scoring...")
    result = calculate_71point_score(df, meta)
    if result is None:
        print("❌ Scoring returned None."); return

    ld, sd = result['long'], result['short']
    print(f"\n{'─'*55}")
    print(f"  LONG  → {ld['decision']:30s} RAW:{ld['raw']} ADJ:{ld['total']}/71 ({ld['pct']:.1f}%)")
    print(f"  SHORT → {sd['decision']:30s} RAW:{sd['raw']} ADJ:{sd['total']}/71 ({sd['pct']:.1f}%)")
    print(f"  Session: {result['variables']['session']} ×{result['variables']['SESSION_MULT']}")
    print(f"  ATR_MULT: {result['variables']['ATR_MULT']} ({result['variables']['atr_mult_reason']})")
    val = result.get('validation', {})
    print(f"  Validation: {'✅ OK' if val.get('ok') else '⚠️ ' + str(val.get('issues'))}")
    print(f"{'─'*55}\n")

    sym = meta['Symbol'].replace('/', '-')
    tf = meta['Timeframe'].replace('/', '-')
    now = datetime.now().strftime('%Y%m%d_%H%M')
    out_file = f"analysis_{sym}_{tf}_{now}.html"
    build_html(meta, df, result, out_file)

    try:
        import webbrowser
        webbrowser.open(os.path.abspath(out_file))
    except Exception:
        pass


if __name__ == '__main__':
    main()
