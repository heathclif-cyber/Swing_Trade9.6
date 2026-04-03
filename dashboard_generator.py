"""
Protocol 9.6 — CSV Dashboard Generator
Reads a Protocol 9.6 enriched CSV, runs the 71-point scoring engine,
and produces a standalone glassmorphism HTML analysis dashboard.

Usage:
    python dashboard_generator.py <path_to_csv>          # analyzes specified file
    python dashboard_generator.py                         # tries enriched_export.csv
"""

import os
import sys
import re
import pandas as pd
import numpy as np
from io import StringIO
from datetime import datetime

# Import the spec-compliant scoring engine
try:
    from algo_scoring import calculate_71point_score
except ImportError:
    print("⚠️  algo_scoring.py not found in same directory. Exiting.")
    sys.exit(1)


# ────────────────────────────────────────────────────────────────────────────
# CSV PARSING
# ────────────────────────────────────────────────────────────────────────────

def parse_csv_and_metadata(filepath: str):
    """Parse Protocol 9.6 CSV: extract '#' comment metadata + load DataFrame."""
    metadata = {
        'Symbol': 'UNKNOWN',
        'Timeframe': '4H',
        'AVG_ENTRY_PRICE': None,
        'TOTAL_QTY': None,
        'TOTAL_COST': None,
        'Export_Time': None,
    }

    if not os.path.exists(filepath):
        print(f"Error: '{filepath}' not found.")
        return metadata, pd.DataFrame()

    data_lines = []
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            ls = line.strip()
            if ls.startswith('#'):
                if 'Symbol' in ls:
                    p = ls.split('Symbol')
                    if len(p) > 1: metadata['Symbol'] = p[1].replace(':', '').replace('=', '').strip()
                elif 'Timeframe' in ls:
                    p = ls.split('Timeframe')
                    if len(p) > 1: metadata['Timeframe'] = p[1].replace(':', '').replace('=', '').strip()
                elif 'AVG ENTRY PRICE' in ls or 'AVG_ENTRY_PRICE' in ls:
                    m = re.search(r'[\d\.]+', ls.split('PRICE')[-1])
                    if m: metadata['AVG_ENTRY_PRICE'] = float(m.group())
                elif 'Entry #1: Price=' in ls and metadata['AVG_ENTRY_PRICE'] is None:
                    m = re.search(r'Price=([\d\.]+)', ls)
                    if m: metadata['AVG_ENTRY_PRICE'] = float(m.group(1))
                elif 'TOTAL QTY' in ls:
                    m = re.search(r'[\d\.]+', ls.split('QTY')[-1])
                    if m: metadata['TOTAL_QTY'] = float(m.group())
                elif 'TOTAL COST' in ls:
                    m = re.search(r'[\d\.]+', ls.split('COST')[-1])
                    if m: metadata['TOTAL_COST'] = float(m.group())
                elif 'Export Time' in ls:
                    p = ls.split('Export Time')
                    if len(p) > 1: metadata['Export_Time'] = p[1].replace(':', '').strip()
            else:
                data_lines.append(ls)

    if not data_lines:
        return metadata, pd.DataFrame()

    df = pd.read_csv(StringIO('\n'.join(data_lines)))
    return metadata, df


def ensure_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute missing EMA/RSI/ATR columns using pandas_ta if not already present."""
    try:
        import pandas_ta as ta
        if 'EMA_21' not in df.columns:
            df['EMA_21']  = ta.ema(df['Close'], length=21)
        if 'EMA_50' not in df.columns:
            df['EMA_50']  = ta.ema(df['Close'], length=50)
        if 'EMA_200' not in df.columns:
            df['EMA_200'] = ta.ema(df['Close'], length=200)
        if 'RSI_6' not in df.columns:
            df['RSI_6']   = ta.rsi(df['Close'], length=6)
        if 'ATR_14' not in df.columns:
            df['ATR_14']  = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    except ImportError:
        print("⚠️  pandas_ta not installed; some indicators may be missing.")
    return df


# ────────────────────────────────────────────────────────────────────────────
# HTML GENERATION
# ────────────────────────────────────────────────────────────────────────────

FEATURE_LABELS = {
    'OI':       'Open Interest Change',
    'Vol':      'Relative Volume (MA20)',
    'TakerBuy': 'Taker Buy Pressure',
    'ATR':      'Volatility ATR %',
    'CVD':      'Cumulative Vol Delta',
    'EMA21':    'Distance EMA 21',
    'EMA50':    'Distance EMA 50',
    'EMA200':   'Distance EMA 200',
    'RSI':      'RSI 6 Momentum',
}

FEATURE_UNIT = {
    'OI': '%', 'Vol': '%', 'TakerBuy': '%', 'ATR': '%',
    'CVD': '%', 'EMA21': '%', 'EMA50': '%', 'EMA200': '%', 'RSI': '',
}

DECISION_COLORS = {
    'FULL': ('#10b981', 'rgba(16,185,129,0.12)', 'rgba(16,185,129,0.35)'),
    'HALF': ('#3b82f6', 'rgba(59,130,246,0.12)',  'rgba(59,130,246,0.35)'),
    'WAIT': ('#f59e0b', 'rgba(245,158,11,0.12)',  'rgba(245,158,11,0.35)'),
    'SKIP': ('#ef4444', 'rgba(239,68,68,0.12)',   'rgba(239,68,68,0.35)'),
}

DOT_COLORS = {3: '#10b981', 2: '#f59e0b', 1: '#f97316', 0: '#ef4444'}


def _fmt(v, decimals=5):
    """Format a float price (altcoin precision)."""
    if v is None: return '—'
    return f"${v:.{decimals}f}"


def _pct(v, decimals=2, sign=True):
    if v is None: return '—'
    prefix = '+' if (sign and v >= 0) else ''
    return f"{prefix}{v:.{decimals}f}%"


def render_feature_rows(scores: dict) -> str:
    rows = ''
    for key, (pts, max_pts, raw, stars) in scores.items():
        fill_pct = (pts / max_pts * 100) if max_pts > 0 else 0
        bar_color = DOT_COLORS.get(stars, '#ef4444')
        dot_color = bar_color
        unit = FEATURE_UNIT.get(key, '')
        raw_fmt = ('+' if raw >= 0 else '') + f'{raw:.2f}{unit}' if isinstance(raw, (int, float)) else str(raw)
        rows += f'''
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">
            <div style="width:9px;height:9px;border-radius:50%;background:{dot_color};flex-shrink:0"></div>
            <div style="flex:1;font-size:12px;color:#94a3b8">{FEATURE_LABELS.get(key, key)}</div>
            <div style="font-family:monospace;font-size:12px;font-weight:600;min-width:56px;text-align:right">{raw_fmt}</div>
            <div style="width:72px;height:5px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden;flex-shrink:0">
                <div style="width:{fill_pct:.0f}%;height:100%;background:{bar_color};border-radius:3px"></div>
            </div>
            <div style="font-size:10px;color:#64748b;font-family:monospace;min-width:40px;text-align:right">{pts}/{max_pts}</div>
        </div>'''
    return rows


def render_levels(lv: dict, is_long: bool) -> str:
    sign = '+' if is_long else '−'
    rr1_cls = 'rr-good' if lv.get('rr1', 0) >= 2 else 'rr-bad'
    rr2_cls = 'rr-good' if lv.get('rr2', 0) >= 2 else 'rr-bad'
    rr3_cls = 'rr-good' if lv.get('rr3', 0) >= 2 else 'rr-bad'
    return f'''
    <div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px">Stop Loss (ATR-based)</div>
    <div class="pill"><span>Ketat 1.0×ATR</span><span class="val-neg">{_fmt(lv["sl_ketat"])}</span></div>
    <div class="pill"><span>Normal 1.5×ATR</span><span class="val-neg">{_fmt(lv["sl_normal"])}</span>
        <span class="rr {rr1_cls}">R:R {lv["rr1"]}×</span></div>
    <div class="pill"><span>Lebar 2.0×ATR</span><span class="val-neg">{_fmt(lv["sl_lebar"])}</span></div>
    <div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin:12px 0 6px">Take Profit Targets</div>
    <div class="pill"><span>TP1 {sign}2.5%</span><span class="val-pos">{_fmt(lv["tp1"])}</span>
        <span class="rr {rr1_cls}">R:R {lv["rr1"]}×</span></div>
    <div class="pill"><span>TP2 {sign}4.6%</span><span class="val-pos">{_fmt(lv["tp2"])}</span>
        <span class="rr {rr2_cls}">R:R {lv["rr2"]}×</span></div>
    <div class="pill"><span>TP3 {sign}7.0%</span><span class="val-pos">{_fmt(lv["tp3"])}</span>
        <span class="rr {rr3_cls}">R:R {lv["rr3"]}×</span></div>'''


def render_narrative(n: dict, is_long: bool) -> str:
    border = '#10b981' if is_long else '#ef4444'
    return f'''
    <div style="background:rgba(255,255,255,0.04);border-left:3px solid {border};
                border-radius:8px;padding:14px;font-size:12.5px;line-height:1.75;color:#94a3b8;margin-top:0">
        <div style="margin-bottom:10px"><span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#64748b;display:block;margin-bottom:3px">📍 Kondisi Pasar</span>{n["kondisi"]}</div>
        <div style="margin-bottom:10px"><span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#64748b;display:block;margin-bottom:3px">🎯 Keputusan</span><strong style="color:#3b82f6">{n["keputusan"]}</strong></div>
        <div><span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#64748b;display:block;margin-bottom:3px">🗺️ Skenario</span>{n["skenario"]}</div>
    </div>'''


def render_exit_signals(exit_data: dict) -> str:
    if not exit_data or not exit_data.get('signals'):
        return '<div style="font-size:12px;color:#64748b;padding:10px 0">✅ Semua indikator dalam batas aman</div>'
    rows = ''
    for icon, name, val, thresh in exit_data['signals']:
        val_fmt = f'{val:.2f}' if isinstance(val, float) else str(val)
        rows += f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:12px"><span>{icon}</span><span style="flex:1;color:#94a3b8">{name}</span><span style="font-family:monospace;font-size:11px;color:#64748b">{val_fmt} ({thresh})</span></div>'
    hard = exit_data.get('hard_count', 0)
    warn = exit_data.get('warn_count', 0)
    mcolor = '#ef4444' if hard > 0 else '#f59e0b' if warn > 0 else '#10b981'
    mbg    = 'rgba(239,68,68,.1)' if hard > 0 else 'rgba(245,158,11,.1)' if warn > 0 else 'rgba(16,185,129,.1)'
    mbd    = 'rgba(239,68,68,.3)' if hard > 0 else 'rgba(245,158,11,.3)' if warn > 0 else 'rgba(16,185,129,.2)'
    return rows + f'<div style="margin-top:10px;padding:10px;border-radius:8px;text-align:center;font-weight:700;font-size:13px;color:{mcolor};background:{mbg};border:1px solid {mbd}">MANDATE: {exit_data["recommendation"]}</div>'


def render_context_grid(ctx: dict) -> str:
    if not ctx:
        return '<span style="color:#64748b;font-size:12px">—</span>'
    items = ''
    for k, v in ctx.items():
        v_fmt = f'{v:.4f}' if isinstance(v, (int, float)) else str(v)
        items += f'<div class="pill"><span>{k}</span><span style="font-family:monospace">{v_fmt}</span></div>'
    return f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px">{items}</div>'


def render_setup_card(tab_data: dict, is_long: bool, exit_data: dict, has_entry: bool) -> str:
    d = tab_data
    lv = d['levels']
    code = d['code']
    text_color, bg_color, border_color = DECISION_COLORS.get(code, DECISION_COLORS['WAIT'])
    bar_pct = d['total'] / 71 * 100
    bar_width = f"{bar_pct:.1f}%"
    icon = '🐂' if is_long else '🐻'
    label = 'LONG' if is_long else 'SHORT'

    feature_rows = render_feature_rows(d['scores'])
    levels_html  = render_levels(lv, is_long)
    narrative_html = render_narrative(d['narrative'], is_long)

    exit_html = ''
    if has_entry:
        exit_html = f'''
        <div style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.06)">
            <div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">Exit Signal Monitor</div>
            {render_exit_signals(exit_data)}
        </div>'''

    return f'''
    <div class="glass" style="padding:22px;display:flex;flex-direction:column;gap:16px">
        <!-- Decision Banner -->
        <div style="text-align:center;padding:18px;border-radius:12px;
                    background:{bg_color};border:1px solid {border_color}">
            <div style="font-size:10px;text-transform:uppercase;letter-spacing:2px;color:#64748b;margin-bottom:4px">{icon} {label} Setup</div>
            <div style="font-size:26px;font-weight:800;letter-spacing:-0.5px;color:{text_color}">{d["decision"]}</div>
            <div style="font-size:14px;color:#94a3b8;margin-top:4px">{d["total"]}/71 · {d["pct"]:.1f}%</div>
        </div>
        <!-- Score bar -->
        <div>
            <div style="display:flex;justify-content:space-between;font-size:10px;color:#64748b;margin-bottom:4px"><span>Total Score</span><span>{d["total"]}/71</span></div>
            <div style="height:7px;background:rgba(255,255,255,0.07);border-radius:4px;overflow:hidden">
                <div style="height:100%;width:{bar_width};background:{text_color};border-radius:4px;transition:width .5s ease"></div>
            </div>
        </div>
        <!-- Feature scoring -->
        <div>
            <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px">9-Feature Weighted Scoring</div>
            {feature_rows}
        </div>
        <!-- Risk Levels -->
        <div>{levels_html}</div>
        <!-- Exit signals (if active position) -->
        {exit_html}
        <!-- Narrative -->
        <div>
            <div style="font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px">Analyst Narrative</div>
            {narrative_html}
        </div>
    </div>'''


def build_html(metadata: dict, df: pd.DataFrame, result: dict, output_file: str):
    """Build and write the complete standalone HTML dashboard."""

    last = df.iloc[-1]
    close_price = float(last.get('Close', 0))
    timestamp   = str(last.get('Timestamp', ''))
    is_active   = bool(metadata.get('AVG_ENTRY_PRICE'))
    has_entry   = is_active
    vdata = result.get('variables', {})
    em    = result.get('emergency', {})
    exit_data = result.get('exit', {})

    # Market context (optional columns)
    ctx_cols = ['MSB','BOS','CHoCH','SFP_Sweep','FVG_Up_Top','FVG_Up_Bottom',
                'FVG_Down_Top','FVG_Down_Bottom','OB_Price','Fib_0.618','Fib_0.786',
                'POC','VAH','VAL','Buy_Liq','Sell_Liq','PDH','PDL','PWH','PWL',
                'EMA_7','EMA_7_H4','EMA_21_H4','EMA_50_H4','EMA_200_H4',
                'StochRSI_K','StochRSI_D','Funding_Rate','BTC_Price','BTC_Dominance','Altcoin_Index']
    market_ctx = {}
    for col in ctx_cols:
        if col in df.columns:
            v = last.get(col)
            try:
                if pd.notna(v): market_ctx[col] = float(v)
            except Exception:
                pass

    # Emergency banners
    emergency_html = ''
    if is_active and (em.get('sl_touched') or em.get('rsi_ob')):
        msg = '⚠️ SL SUDAH TERSENTUH — EVALUASI EXIT SEGERA' if em.get('sl_touched') else '⚠️ RSI OVERBOUGHT — CEK EXIT SIGNAL'
        emergency_html = f'<div style="display:flex;align-items:center;justify-content:center;gap:12px;padding:14px 20px;margin-bottom:16px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.4);border-radius:12px;color:#fca5a5;font-weight:700;font-size:14px">{msg}</div>'

    # Header metadata strip
    meta_items = [
        ('Symbol', metadata['Symbol']),
        ('Timeframe', metadata['Timeframe']),
        ('Close Price', f"${close_price:.5f}"),
        ('Session', vdata.get('session', '—')),
        ('Last Candle', timestamp),
    ]
    if is_active:
        meta_items += [
            ('Avg Entry', f"${metadata['AVG_ENTRY_PRICE']:.5f}"),
        ]
        if metadata.get('TOTAL_QTY'):
            meta_items.append(('Total Qty', str(metadata['TOTAL_QTY'])))
        if metadata.get('TOTAL_COST'):
            meta_items.append(('Total Cost', f"${metadata['TOTAL_COST']:.2f}"))

    meta_html = ''.join(f'''
        <div style="text-align:center">
            <div style="font-size:10px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:.6px">{k}</div>
            <div style="font-size:16px;font-weight:600;font-family:monospace;margin-top:3px">{v}</div>
        </div>''' for k, v in meta_items)

    long_card  = render_setup_card(result['long'],  True,  exit_data, has_entry)
    short_card = render_setup_card(result['short'], False, exit_data, has_entry)
    ctx_html   = render_context_grid(market_ctx)

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    html = f'''<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Protocol 9.6 Analysis — {metadata["Symbol"]} {metadata["Timeframe"]}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
    --bg:   #080c14;
    --surf: #0d1220;
    --gb:   rgba(255,255,255,0.04);
    --gbr:  rgba(255,255,255,0.09);
    --t1:   #f1f5f9; --t2: #94a3b8; --t3: #64748b;
    --blue: #3b82f6; --cyan: #06b6d4; --green: #10b981;
    --red:  #ef4444; --yellow: #f59e0b; --purple: #8b5cf6;
    --font: 'Inter', sans-serif; --mono: 'JetBrains Mono', monospace;
}}
*,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
body {{
    font-family:var(--font); background:var(--bg); color:var(--t1);
    min-height:100vh; -webkit-font-smoothing:antialiased; line-height:1.5;
    background-image:radial-gradient(ellipse 80% 60% at 20% 0%,rgba(59,130,246,0.06) 0%,transparent 60%),
                     radial-gradient(ellipse 60% 50% at 80% 100%,rgba(139,92,246,0.05) 0%,transparent 60%);
}}
.container {{ max-width:1400px; margin:0 auto; padding:24px; }}
.glass {{
    background:var(--gb); backdrop-filter:blur(20px); -webkit-backdrop-filter:blur(20px);
    border:1px solid var(--gbr); border-radius:16px; box-shadow:0 4px 20px rgba(0,0,0,0.5);
}}
.val-pos {{ color:var(--green) !important; }}
.val-neg {{ color:var(--red)   !important; }}
.pill {{
    display:flex; justify-content:space-between; align-items:center;
    padding:8px 12px; background:var(--gb); border:1px solid var(--gbr);
    border-radius:8px; margin-bottom:5px; font-family:var(--mono); font-size:12px;
    gap:8px;
}}
.rr {{ display:inline-block; padding:2px 7px; border-radius:8px; font-size:10px; font-weight:700; }}
.rr-good {{ background:rgba(16,185,129,.2);  color:var(--green); }}
.rr-bad  {{ background:rgba(239,68,68,.2);   color:var(--red); }}
.two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
@media(max-width:900px) {{ .two-col {{ grid-template-columns:1fr; }} }}
.section-title {{ font-size:13px; font-weight:700; color:var(--t2); text-transform:uppercase; letter-spacing:.8px; margin-bottom:12px; display:flex; align-items:center; gap:8px; }}
.btn-toggle {{
    background:none; border:1px solid var(--gbr); border-radius:20px;
    color:var(--t2); padding:6px 16px; cursor:pointer; font-size:12px;
    font-family:var(--font); transition:.2s;
}}
.btn-toggle:hover {{ background:rgba(255,255,255,0.05); color:var(--t1); }}
@media print {{ .btn-toggle {{ display:none; }} }}
</style>
</head>
<body>
<div class="container">

<!-- HEADER -->
<div class="glass" style="padding:20px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
    <div style="display:flex;align-items:center;gap:14px">
        <div style="width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#3b82f6,#06b6d4);display:flex;align-items:center;justify-content:center;font-size:22px;box-shadow:0 0 24px rgba(59,130,246,0.2)">🛡️</div>
        <div>
            <div style="font-size:20px;font-weight:700;letter-spacing:-0.5px">Protocol 9.6</div>
            <div style="font-size:12px;color:var(--t3)">Quantitative Swing Analysis — 71-Point Scoring</div>
        </div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:11px;color:var(--t3);background:var(--gb);border:1px solid var(--gbr);padding:6px 16px;border-radius:20px">Generated: {now_str}</span>
        <button class="btn-toggle" onclick="toggleTheme()">🌓 Theme</button>
    </div>
</div>

<!-- EMERGENCY BANNER -->
{emergency_html}

<!-- META STRIP -->
<div class="glass" style="padding:18px 24px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:28px;align-items:center">
    {meta_html}
    {"<div style='margin-left:auto'><span style=\"font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.6px\">Mode</span><br><span style=\"font-weight:700;color:#f59e0b\">POSISI AKTIF</span></div>" if is_active else "<div style='margin-left:auto'><span style=\"font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.6px\">Mode</span><br><span style=\"font-weight:700;color:#06b6d4\">ENTRY BARU</span></div>"}
</div>

<!-- LONG & SHORT SIDE BY SIDE -->
<div class="two-col" style="margin-bottom:16px">
    {long_card}
    {short_card}
</div>

<!-- MARKET CONTEXT -->
{"" if not market_ctx else f"""
<div class="glass" style="padding:20px;margin-bottom:16px">
    <div class="section-title">🔍 Market Context (from CSV)</div>
    {ctx_html}
</div>"""}

<!-- VARIABLES TABLE -->
<div class="glass" style="padding:20px;margin-bottom:16px">
    <div class="section-title">📐 Intermediate Variables</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px">
        {"".join(f'<div class="pill"><span>{k}</span><span>{v if isinstance(v, str) else f"{v:.4f}" if isinstance(v, float) else str(v)}</span></div>' for k, v in vdata.items())}
    </div>
</div>

<!-- FOOTER -->
<div style="text-align:center;padding:20px;color:var(--t3);font-size:11px">
    Protocol 9.6 · 71-Point Quantitative Swing Engine · Generated {now_str}
</div>

</div><!-- /container -->
<script>
function toggleTheme() {{
    const h = document.documentElement;
    const d = h.getAttribute('data-theme') === 'dark';
    h.setAttribute('data-theme', d ? 'light' : 'dark');
    document.body.style.background  = d ? '#f0f4ff' : '#080c14';
    document.body.style.color       = d ? '#0f172a' : '#f1f5f9';
}}
</script>
</body>
</html>'''

    with open(output_file, 'w', encoding='utf-8') as fh:
        fh.write(html)
    print(f"✅ Dashboard written to: {output_file}")


# ────────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────────

def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else 'enriched_export.csv'

    print(f"📂 Reading: {csv_path}")
    metadata, df = parse_csv_and_metadata(csv_path)

    if df.empty:
        print("❌ No data loaded. Exiting."); return

    print(f"📊 Rows loaded: {len(df)} | Symbol: {metadata['Symbol']} | Timeframe: {metadata['Timeframe']}")

    df = ensure_indicators(df)

    if len(df) < 22:
        print("❌ Need ≥ 22 candles."); return

    print("🤖 Running 71-point quantitative scoring...")
    result = calculate_71point_score(df, metadata)

    if result is None:
        print("❌ Scoring returned None — check indicator columns."); return

    long_d  = result['long']
    short_d = result['short']
    print(f"\n{'─'*55}")
    print(f"  LONG  → {long_d['decision']:20s}  Score: {long_d['total']}/71  ({long_d['pct']:.1f}%)")
    print(f"  SHORT → {short_d['decision']:20s}  Score: {short_d['total']}/71  ({short_d['pct']:.1f}%)")
    print(f"  Session: {result['variables']['session']}")
    print(f"{'─'*55}\n")

    # Output filename
    sym = metadata['Symbol'].replace('/', '-')
    tf  = metadata['Timeframe'].replace('/', '-')
    now = datetime.now().strftime('%Y%m%d_%H%M')
    out_file = f"analysis_{sym}_{tf}_{now}.html"

    build_html(metadata, df, result, out_file)

    # Try to open in browser
    try:
        import webbrowser
        webbrowser.open(os.path.abspath(out_file))
    except Exception:
        pass


if __name__ == '__main__':
    main()
