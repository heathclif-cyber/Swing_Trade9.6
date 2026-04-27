/* ═══════════════════════════════════════════════════════════════════════════
   PROTOCOL 9.6 — DASHBOARD JAVASCRIPT ENGINE v2.1
   ═══════════════════════════════════════════════════════════════════════════ */

let APP_DATA = null;
let activeTimeframe = '15m';
let activePair = 'coin';
let activeIndicator = '1h';
let activeQuantTab = 'long';
let currentBackendPair = '';
let tradeEntries = {};
let tradeSummaries = {};

/* ── MAIN DATA FETCH ────────────────────────────────────────────────────── */
async function fetchData() {
    const btn = document.getElementById('btnRefresh');
    btn.textContent = '⏳ Loading...'; btn.classList.add('btn-loading');
    document.getElementById('loadingOverlay').classList.remove('hidden');
    try {
        const pair = currentBackendPair || '';
        const url = pair ? `/api/data?pair=${pair}` : '/api/data';
        const res = await fetch(url);
        const json = await res.json();
        if (!json.success) throw new Error(json.error || 'API Error');
        APP_DATA = json;
        await loadTradeEntries();
        const s = json.state?.user_input;
        if (s?.available_pairs) {
            const sel = document.getElementById('pairSelect');
            sel.innerHTML = s.available_pairs.map(p =>
                `<option value="${p}" ${p === s.coin_pair ? 'selected' : ''}>${p}</option>`).join('');
        }
        currentBackendPair = s?.coin_pair || '';
        document.getElementById('headerTimestamp').textContent = json.timestamp || '—';
        renderStatusStrip(json);
        renderRawTable();
        renderIndicatorTable();
        renderTacticalCompass(json);
        renderKillSwitch(json);
        renderQuantAnalysis(json.state?.quant_analysis, json.state);
        renderEmergency(json.state?.quant_analysis, json.state);
        // ML Confidence chart kini digabung di Price Performance Monitor
    } catch (e) {
        showAlert('danger', '❌ ' + e.message);
        console.error(e);
    } finally {
        btn.textContent = '🔄 Refresh'; btn.classList.remove('btn-loading');
        document.getElementById('loadingOverlay').classList.add('hidden');
    }
}

/* ── STATUS STRIP ────────────────────────────────────────────────────────── */
function renderStatusStrip(json) {
    const s = json.state, ui = s?.user_input || {}, at = s?.active_tracker || {}, pos = s?.position || {};
    document.getElementById('statCoin').textContent = ui.coin_pair || '—';
    const cp = at.current_price || 0;
    document.getElementById('statPrice').textContent = cp ? '$' + cp.toFixed(5) : '—';
    const ep = ui.entry_price || 0;
    // Build position badge: LONG/SHORT + Spot/Futures + Leverage
    let posBadge = '';
    if (pos.side) {
        const sideColor = pos.side === 'SHORT' ? 'var(--accent-red)' : 'var(--accent-green)';
        const sideIcon = pos.side === 'SHORT' ? '🔴' : '🟢';
        posBadge += `<span style="color:${sideColor};font-size:10px;margin-left:5px;font-weight:700">${sideIcon} ${pos.side}</span>`;
    }
    if (pos.market_type === 'FUTURES' && pos.leverage > 1) {
        posBadge += `<span style="color:var(--accent-yellow);font-size:10px;margin-left:4px;font-weight:700">⚡${pos.leverage}x</span>`;
    }
    const entryEl = document.getElementById('statEntry');
    if (ep) { entryEl.innerHTML = `$${ep.toFixed(5)}${posBadge}`; }
    else { entryEl.textContent = 'No Entry'; }
    const pnl = at.current_pnl_pct || 0;
    const pnlEl = document.getElementById('statPnl');
    pnlEl.textContent = ep ? (pnl >= 0 ? '+' : '') + pnl.toFixed(2) + '%' : '—';
    pnlEl.className = 'status-value ' + (pnl >= 0 ? 'val-pos' : 'val-neg');
    const flt = at.floating_pnl_usd || 0;
    const fltEl = document.getElementById('statRealizedPnL');
    fltEl.textContent = ep ? (flt >= 0 ? '+$' : '-$') + Math.abs(flt).toFixed(2) : '—';
    fltEl.className = 'status-value ' + (flt >= 0 ? 'val-pos' : 'val-neg');
    const rPnl = pos.realized_pnl || 0;
    const rEl = document.getElementById('statRealizedPnLTotal');
    rEl.textContent = (rPnl >= 0 ? '+$' : '-$') + Math.abs(rPnl).toFixed(2);
    rEl.className = 'status-value ' + (rPnl >= 0 ? 'val-pos' : 'val-neg');
    document.getElementById('statSL').textContent = at.active_sl ? '$' + at.active_sl.toFixed(5) : '—';
    document.getElementById('statRemainingQty').textContent = pos.remaining_qty != null ? pos.remaining_qty.toFixed(4) : '—';
    const stEl = document.getElementById('statStatus');
    const st = ui.status || 'ACTIVE';
    stEl.textContent = st;
    stEl.className = 'status-tag ' + (st === 'KILL_SWITCH' ? 'tag-killed' : 'tag-active');
}

/* ── EMERGENCY BANNER ────────────────────────────────────────────────────── */
function renderEmergency(quant, state) {
    const bar = document.getElementById('emergencyBar');
    const em = quant?.emergency, ep = state?.user_input?.entry_price;
    if (em && ep && (em.sl_touched || em.rsi_ob)) {
        bar.style.display = 'flex';
        document.getElementById('emergencyMsg').textContent =
            em.sl_touched ? '⚠️ SL SUDAH TERSENTUH — EVALUASI EXIT SEGERA' : '⚠️ RSI OVERBOUGHT — CEK EXIT SIGNAL';
    } else { bar.style.display = 'none'; }
}

/* ── RAW TABLE ───────────────────────────────────────────────────────────── */
function renderRawTable() {
    if (!APP_DATA?.raw_data) return;
    const data = APP_DATA.raw_data[activeTimeframe]?.[activePair] || [];
    const tbody = document.getElementById('rawTableBody');
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-3);padding:24px">No data</td></tr>'; return; }
    tbody.innerHTML = [...data].reverse().map(r => {
        const d = r.vol_delta || 0;
        const dCls = d > 0 ? 'val-pos' : (d < 0 ? 'val-neg' : '');
        return `<tr>
            <td>${r.time}</td><td>${r.open?.toFixed(5) || '—'}</td><td>${r.high?.toFixed(5) || '—'}</td>
            <td>${r.low?.toFixed(5) || '—'}</td><td>${r.close?.toFixed(5) || '—'}</td>
            <td>${fmtVol(r.total_vol || 0)}</td><td class="val-pos">${fmtVol(r.buy_vol || 0)}</td>
            <td class="val-neg">${fmtVol(r.sell_vol || 0)}</td><td class="${dCls}">${d >= 0 ? '+' : ''}${fmtVol(d)}</td></tr>`;
    }).join('');
}

/* ── INDICATOR TABLE ─────────────────────────────────────────────────────── */
function renderIndicatorTable() {
    if (!APP_DATA?.computed) return;
    const data = APP_DATA.computed[`indicators_${activeIndicator}`] || [];
    const tbody = document.getElementById('indicatorTableBody');
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="12" style="text-align:center;color:var(--text-3);padding:24px">No data</td></tr>'; return; }
    tbody.innerHTML = [...data].reverse().map(r => {
        const rsi = r.rsi_6; const rsiCls = rsi < 30 ? 'val-pos' : rsi > 70 ? 'val-neg' : '';
        return `<tr>
            <td>${r.time}</td><td>${r.close?.toFixed(5) || '—'}</td>
            <td>${r.ema_7?.toFixed(4) || '—'}</td><td>${r.ema_21?.toFixed(4) || '—'}</td>
            <td>${r.ema_50?.toFixed(4) || '—'}</td><td>${r.ema_200?.toFixed(4) || '—'}</td>
            <td class="${rsiCls}">${rsi?.toFixed(1) || '—'}</td>
            <td>${r.stochrsi_k?.toFixed(2) || '—'}</td><td>${r.stochrsi_d?.toFixed(2) || '—'}</td>
            <td class="val-pos">${fmtVol(r.buy_vol || 0)}</td><td class="val-neg">${fmtVol(r.sell_vol || 0)}</td>
            <td class="${(r.vol_delta || 0) > 0 ? 'val-pos' : 'val-neg'}">${fmtVol(r.vol_delta || 0)}</td></tr>`;
    }).join('');
}

/* ── TACTICAL COMPASS ────────────────────────────────────────────────────── */
function renderTacticalCompass(json) {
    const ind = APP_DATA?.computed?.indicators_4h || [];
    if (!ind.length) return;
    const last = ind[ind.length - 1];
    const cp = last.close, e21 = last.ema_21, e50 = last.ema_50, e200 = last.ema_200;
    const rsi = last.rsi_6, bvol = last.buy_vol, svol = last.sell_vol, tvol = bvol + svol;
    const rows = [
        ['Price vs EMA 21', cp?.toFixed(5), 'Close < EMA 21', 'Close > EMA 21', e21, r => r > e21 ? 'bull' : 'bear', 'Trend filter'],
        ['Price vs EMA 50', cp?.toFixed(5), 'Close < EMA 50', 'Close > EMA 50', e50, r => r > e50 ? 'bull' : 'bear', 'Medium-term'],
        ['Price vs EMA 200', cp?.toFixed(5), 'Close < EMA 200', 'Close > EMA 200', e200, r => r > e200 ? 'bull' : 'bear', 'Macro trend'],
        ['RSI_6', rsi?.toFixed(1), 'RSI > 70', 'RSI < 30', rsi, r => r < 30 ? 'bull' : r > 70 ? 'bear' : 'neutral', 'Momentum'],
        ['Volume Bias', tvol ? ((bvol / tvol * 100).toFixed(1) + '% Buy') : '—', 'Buy < 45%', 'Buy > 55%', bvol / tvol, r => r > 0.55 ? 'bull' : r < 0.45 ? 'bear' : 'neutral', 'Taker pressure'],
    ];
    const smt = json.computed?.smt_divergence;
    if (smt) rows.push(['SMT (BTC vs Coin)', smt.btc_trend_12h, 'Bearish SMT', 'No divergence', smt.bearish_smt, r => r ? 'bear' : 'bull', 'BTC/Coin div']);
    let bullCount = 0, bearCount = 0;
    const tbody = document.getElementById('tacticalCompassBody');
    tbody.innerHTML = rows.map(([param, live, bear_c, bull_c, val, fn, note]) => {
        const verdict = fn(val);
        if (verdict === 'bull') bullCount++; else if (verdict === 'bear') bearCount++;
        const rowCls = verdict === 'bull' ? 'row-bullish' : verdict === 'bear' ? 'row-bearish' : 'row-neutral';
        const icon = verdict === 'bull' ? '🟢' : verdict === 'bear' ? '🔴' : '🟡';
        return `<tr class="${rowCls}">
            <td style="font-weight:600">${param}</td><td style="text-align:center;font-family:var(--mono)">${live}</td>
            <td style="text-align:center;font-size:11px;color:var(--accent-red)">${bear_c}</td>
            <td style="text-align:center;font-size:11px;color:var(--accent-green)">${bull_c}</td>
            <td style="text-align:center;font-size:16px">${icon}</td>
            <td style="font-size:11px;color:var(--text-3)">${note}</td></tr>`;
    }).join('');
    const total = bullCount + bearCount;
    const vEl = document.getElementById('compassVerdict');
    if (bullCount > bearCount) { vEl.textContent = `🟢 MARKUP ${bullCount}/${total}`; vEl.style.cssText = 'background:rgba(52,211,153,.12);color:var(--accent-green)'; }
    else if (bearCount > bullCount) { vEl.textContent = `🔴 MARKDOWN ${bearCount}/${total}`; vEl.style.cssText = 'background:rgba(248,113,113,.12);color:var(--accent-red)'; }
    else { vEl.textContent = '🟡 NEUTRAL'; vEl.style.cssText = 'background:rgba(251,191,36,.12);color:var(--accent-yellow)'; }
}

/* ── KILL SWITCH & MARKET STRUCTURE ──────────────────────────────────────── */
function renderKillSwitch(json) {
    const smt = json.computed?.smt_divergence || {};
    const smtOk = smt.bearish_smt;

    // Update panel Liquidity Borders
    document.getElementById('valPDH').textContent = (json.computed?.liquidity_borders?.PDH || 0).toFixed(5);
    document.getElementById('valPDL').textContent = (json.computed?.liquidity_borders?.PDL || 0).toFixed(5);
    document.getElementById('valPWH').textContent = (json.computed?.liquidity_borders?.PWH || 0).toFixed(5);
    document.getElementById('valPWL').textContent = (json.computed?.liquidity_borders?.PWL || 0).toFixed(5);

    // Update panel SMT Divergence
    document.getElementById('valBtcTrend').textContent = smt.btc_trend_12h || '—';
    document.getElementById('valCoinTrend').textContent = smt.coin_trend_12h || '—';
    const smtEl = document.getElementById('valSMT');
    smtEl.innerHTML = smtOk ? '<span class="badge badge-red">YES ⚠️</span>' : '<span class="badge badge-green">NO ✅</span>';

    // Update panel OI Momentum
    const oiDelta = json.computed?.oi_delta_pct || 0;
    const oiEl = document.getElementById('valOIDelta');
    oiEl.textContent = (oiDelta >= 0 ? '+' : '') + oiDelta.toFixed(2) + '%';
    oiEl.className = 'panel-row-value ' + (oiDelta >= 0 ? 'val-pos' : 'val-neg');
}

/* ── QUANT ANALYSIS ──────────────────────────────────────────────────────── */
const FEATURE_LABELS = { OI: 'Open Interest Change', Vol: 'Relative Volume (MA20)', TakerBuy: 'Taker Buy Pressure', ATR: 'Volatility ATR %', CVD: 'Cumulative Vol Delta', EMA21: 'Distance EMA 21', EMA50: 'Distance EMA 50', EMA200: 'Distance EMA 200', RSI: 'RSI 6 Momentum' };
const FEATURE_UNIT = { TakerBuy: '%', RSI: '', OI: '%', Vol: '%', ATR: '%', CVD: '%', EMA21: '%', EMA50: '%', EMA200: '%' };

function renderQuantAnalysis(quant, state) {
    if (!quant) {
        document.getElementById('quantDecisionName').textContent = 'DATA INSUFFICIENT';
        document.getElementById('quantScoreSummary').textContent = 'Need ≥22 candles of 4H data';
        document.getElementById('quantDecisionName').className = 'decision-name color-SKIP';
        if (document.getElementById('decisionBanner')) document.getElementById('decisionBanner').className = 'quant-decision-banner decision-SKIP';
        if (document.getElementById('quantTotalBar')) document.getElementById('quantTotalBar').style.width = '0%';
        if (document.getElementById('quantTotalPts')) document.getElementById('quantTotalPts').textContent = '—/78';
        if (document.getElementById('activePosBanner')) document.getElementById('activePosBanner').innerHTML = '';
        if (document.getElementById('featureGrid')) document.getElementById('featureGrid').innerHTML = '<div style="color:var(--text-3);padding:20px;text-align:center;grid-column:1/-1">Belum ada data cukup untuk analisis.</div>';
        if (document.getElementById('slLevels')) document.getElementById('slLevels').innerHTML = '';
        if (document.getElementById('tpLevels')) document.getElementById('tpLevels').innerHTML = '';
        if (document.getElementById('rrMatrix')) document.getElementById('rrMatrix').innerHTML = '';
        if (document.getElementById('exitSignalBox')) document.getElementById('exitSignalBox').innerHTML = '';
        if (document.getElementById('quantNarrative')) document.getElementById('quantNarrative').innerHTML = '';
        if (document.getElementById('marketContext')) document.getElementById('marketContext').innerHTML = '<span style="color:var(--text-3);font-size:12px">—</span>';
        return;
    }
    const ep = state?.user_input?.entry_price || 0;
    const hasEntry = ep > 0;
    const posSide = state?.position?.side || '';

    if (hasEntry && posSide === 'LONG') {
        activeQuantTab = 'long';
    } else if (hasEntry && posSide === 'SHORT') {
        activeQuantTab = 'short';
    } else {
        activeQuantTab = quant.long?.ml_signal === 'SHORT' ? 'short' : 'long';
    }

    const data = quant[activeQuantTab];
    if (!data) return;
    // Decision banner
    const banner = document.getElementById('decisionBanner');
    banner.className = `quant-decision-banner decision-${data.code}`;
    document.getElementById('quantDecisionName').className = `decision-name color-${data.code}`;
    let blockMsg = quant.variables?.ml_error ? `⚠ ML: ${quant.variables.ml_error}` : '';

    document.getElementById('quantDecisionName').textContent = quant.long?.ml_signal || 'FLAT';
    document.getElementById('quantScoreSummary').innerHTML = `Confidence: ${(data.ml_confidence * 100).toFixed(1)}%${blockMsg ? `<br><span style="font-size:11px;color:rgba(255,255,255,0.7);display:block;margin-top:8px">${blockMsg}</span>` : ''}`;
    // Total bar
    const pct = data.ml_confidence * 100;
    document.getElementById('quantTotalPts').textContent = `${pct.toFixed(1)}%`;
    const barEl = document.getElementById('quantTotalBar');
    barEl.style.width = pct + '%';
    barEl.style.background = data.ml_signal === 'LONG' ? 'var(--accent-green)' : data.ml_signal === 'SHORT' ? 'var(--accent-red)' : 'var(--accent-yellow)';
    // Active pos banner
    const apb = document.getElementById('activePosBanner');
    if (hasEntry && state?.position?.remaining_qty > 0) {
        const pnl = state.active_tracker?.current_pnl_pct || 0;
        apb.innerHTML = `<div style="padding:10px 16px;border:1px solid rgba(52,211,153,.25);border-radius:var(--radius-sm);background:rgba(52,211,153,.05);margin-bottom:14px;font-size:13px;display:flex;gap:16px;flex-wrap:wrap">
            <span>📍 <strong>POSISI AKTIF</strong></span>
            <span>Entry: <strong>$${ep.toFixed(5)}</strong></span>
            <span>Qty: <strong>${state.position.remaining_qty.toFixed(4)}</strong></span>
            <span>P&L: <strong class="${pnl >= 0 ? 'val-pos' : 'val-neg'}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</strong></span></div>`;
    } else { apb.innerHTML = ''; }
    // ML Probabilities & Feature Inputs
    let html = '';
    const pctx = quant.variables || {};
    const mlFeatures = [
        { label: 'RSI 6 Momentum', value: pctx.O_rsi, unit: '' },
        { label: 'Volatility ATR', value: pctx.H_atr_pct, unit: '%' },
        { label: 'Open Interest Norm', value: pctx.C_oi_norm, unit: '%' },
        { label: 'Volume Norm', value: pctx.F_vol_norm, unit: '%' },
        { label: 'CVD Norm', value: pctx.K_cvd_norm, unit: '%' },
        { label: 'Taker Buy', value: pctx.G_taker_buy, unit: '%' },
        { label: 'Dist EMA 21', value: pctx.L_ema21, unit: '%' },
        { label: 'Dist EMA 50', value: pctx.M_ema50, unit: '%' },
        { label: 'Dist EMA 200', value: pctx.N_ema200, unit: '%' }
    ];

    html += '<div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin-bottom:8px">ML Feature Inputs</div>';
    mlFeatures.forEach(f => {
        if (f.value !== undefined && f.value !== null) {
            html += `<div class="feature-row" style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,.05)">
                <div class="feature-name" style="color:var(--text-2); font-size:11px">${f.label}</div>
                <div class="feature-val" style="font-family:var(--mono);color:var(--text-1); font-size:11px; margin-left:auto">${f.value.toFixed(2)}${f.unit}</div>
            </div>`;
        }
    });

    html += '<div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin:12px 0 8px">Model Probabilities</div>';
    const proba = data.ml_proba || {};
    for (const [key, val] of Object.entries(proba)) {
        const fillPct = val * 100;
        const fillColor = key === 'LONG' ? 'var(--accent-green)' : key === 'SHORT' ? 'var(--accent-red)' : 'var(--accent-yellow)';
        html += `<div class="feature-row">
            <div class="feature-dot" style="background:${fillColor}"></div>
            <div class="feature-name" style="text-transform:uppercase; font-weight:bold">${key}</div>
            <div class="feature-val">${fillPct.toFixed(1)}%</div>
            <div class="feature-bar-bg" style="width:60px; margin-left:10px"><div class="feature-bar-fill" style="width:${fillPct}%;background:${fillColor}"></div></div>
            </div>`;
    }

    console.log("[SHAP DEBUG] received in UI:", pctx.shap_top_features);
    if (pctx.shap_top_features && pctx.shap_top_features.length > 0) {
        html += `<div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin:16px 0 8px">🔍 Top SHAP Drivers — Mengapa ${data.ml_signal || 'FLAT'}?</div>`;
        html += `<div style="background:rgba(0,0,0,0.2); border-radius:6px; padding:8px; border:1px solid rgba(255,255,255,0.05);">`;
        pctx.shap_top_features.forEach(f => {
            const isPos = f.direction === 'positive';
            const color = isPos ? 'var(--accent-red)' : 'var(--accent-green)';
            const icon = isPos ? '🔴' : '🟢';
            const barWidth = Math.min(100, Math.abs(f.shap_value) * 100);
            html += `<div style="display:flex; align-items:center; padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.02);">
                <div style="color:var(--text-2); font-size:11px; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${f.feature.replace(/_/g, ' ')}</div>
                <div style="font-family:var(--mono); color:${color}; font-size:11px; width:45px; text-align:right; margin-right:8px;">${f.shap_value > 0 ? '+' : ''}${f.shap_value}</div>
                <div style="font-size:10px; margin-right:6px;">${icon}</div>
                <div style="width:50px; height:6px; background:rgba(255,255,255,0.05); border-radius:3px; overflow:hidden;">
                    <div style="width:${barWidth}%; height:100%; background:${color};"></div>
                </div>
            </div>`;
        });
        html += `<div style="font-size:9px; color:var(--text-3); margin-top:6px; text-align:center;">* Merah = mendorong ${data.ml_signal || 'FLAT'} | Hijau = melawan ${data.ml_signal || 'FLAT'}</div></div>`;
    }

    // ML Confidence chart dipindahkan ke Price Performance Monitor

    document.getElementById('featureGrid').innerHTML = html;
    // SL/TP Levels
    const lv = data.levels;
    const isLong = activeQuantTab === 'long';
    const slStruct = lv.sl_structure;
    const slLabel = lv.sl_label || '';
    const hasStructSL = slStruct && slLabel && !slLabel.includes('fallback');
    document.getElementById('slLevels').innerHTML = `
        ${hasStructSL ? `<div class="level-pill" style="border-left:3px solid var(--accent-red)"><span><strong style="color:var(--accent-red)">SL Utama</strong> — ${slLabel}</span><span class="val-neg">$${slStruct.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_sl >= 0 ? '+' : ''}${lv.dist_sl.toFixed(2)}%</span></div>` : ''}
        <div style="font-size:9px;color:var(--text-3);font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin:${hasStructSL ? '8' : '0'}px 0 4px">Referensi ATR</div>
        <div class="level-pill"><span>Ketat (1.0 ATR)</span><span class="val-neg">$${lv.sl_ketat.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_sl_ketat >= 0 ? '+' : ''}${lv.dist_sl_ketat.toFixed(2)}%</span></div>
        <div class="level-pill"><span>Normal (1.5 ATR)</span><span class="val-neg">$${lv.sl_normal.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_sl_normal >= 0 ? '+' : ''}${lv.dist_sl_normal.toFixed(2)}%</span></div>
        <div class="level-pill"><span>Lebar (2.0 ATR)</span><span class="val-neg">$${lv.sl_lebar.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_sl_lebar >= 0 ? '+' : ''}${lv.dist_sl_lebar.toFixed(2)}%</span></div>`;
    const tp1Lbl = lv.tp1_label || (isLong ? '+' : '-') + '2.5%';
    const tp2Lbl = lv.tp2_label || (isLong ? '+' : '-') + '4.6%';
    const tp3Lbl = lv.tp3_label || (isLong ? '+' : '-') + '7.0%';
    const rrBadge = (rr) => `<span class="rr-badge ${rr >= 2 ? 'rr-good' : 'rr-bad'}">${rr.toFixed(1)}x</span>`;

    // Check if TP hits and Trailing SL active
    const tsl = quant.trailing_sl?.[activeQuantTab] || {};
    const slBadge = `<span style="font-size:10px;color:var(--accent-red);background:rgba(248,113,113,.12);padding:2px 6px;border-radius:6px;margin-left:8px;border:1px solid rgba(248,113,113,.3)">● SL</span>`;
    const bp1 = (lv.sl_structure === lv.tp1 && tsl.applicable) ? slBadge : '';

    document.getElementById('tpLevels').innerHTML = `
        <div class="level-pill"><span>TP1 <span style="font-size:10px;color:var(--text-3)">${tp1Lbl}</span></span><span class="val-pos">$${lv.tp1.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_tp1 >= 0 ? '+' : ''}${lv.dist_tp1.toFixed(2)}%</span>${lv.rr1 != null ? rrBadge(lv.rr1) : ''}${bp1}</div>
        <div class="level-pill"><span>TP2 <span style="font-size:10px;color:var(--text-3)">${tp2Lbl}</span></span><span class="val-pos">$${lv.tp2.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_tp2 >= 0 ? '+' : ''}${lv.dist_tp2.toFixed(2)}%</span>${lv.rr2 != null ? rrBadge(lv.rr2) : ''}</div>
        <div class="level-pill"><span>TP3 <span style="font-size:10px;color:var(--text-3)">${tp3Lbl}</span></span><span class="val-pos">$${lv.tp3.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_tp3 >= 0 ? '+' : ''}${lv.dist_tp3.toFixed(2)}%</span>${lv.rr3 != null ? rrBadge(lv.rr3) : ''}</div>`;

    if (tsl.applicable) {
        document.getElementById('tpLevels').innerHTML += `
        <div style="margin-top:10px;padding:10px 14px;background:rgba(52,211,153,.08);border:1px dashed rgba(52,211,153,.3);border-radius:8px">
            <div style="font-size:10px;color:var(--accent-green);font-weight:700;text-transform:uppercase;margin-bottom:4px">Trailing SL Aktif</div>
            <div style="font-size:12px;color:var(--text-1)">${tsl.action}</div>
            <div style="font-size:11px;color:var(--text-3);margin-top:4px">${tsl.note}</div>
        </div>`;
    }
    // R:R Matrix
    const rrm = lv.rr_matrix;
    if (rrm && rrm.length >= 3) {
        const slLabels = ['Ketat (1.0×)', 'Normal (1.5×)', 'Lebar (2.0×)'];
        let rrHtml = '<table class="rr-matrix"><thead><tr><th>SL \\ TP</th><th>TP1</th><th>TP2</th><th>TP3</th></tr></thead><tbody>';
        for (let i = 0; i < 3; i++) {
            rrHtml += `<tr><td style="text-align:left;font-size:10px;color:var(--text-2)">${slLabels[i]}</td>`;
            for (let j = 0; j < 3; j++) {
                const rr = rrm[i][j] || 0;
                const cls = rr >= 2 ? 'val-pos' : rr >= 1 ? 'val-warn' : 'val-neg';
                rrHtml += `<td class="${cls}">${rr.toFixed(1)}x</td>`;
            }
            rrHtml += '</tr>';
        }
        rrHtml += '</tbody></table>';
        document.getElementById('rrMatrix').innerHTML = rrHtml;
    }
    // Exit signals
    const exitBox = document.getElementById('exitSignalBox');
    const exitData = quant.exit;
    if (hasEntry && exitData?.signals?.length >= 0) {
        let exHtml = '<div style="margin-top:16px"><div style="font-size:10.5px;color:var(--text-3);font-weight:600;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px">Exit Signal Monitor</div>';
        if (exitData.signals.length === 0) {
            exHtml += '<div style="font-size:12px;color:var(--text-3);padding:10px 0">✅ Semua indikator dalam batas aman</div>';
        } else {
            exitData.signals.forEach(([icon, name, val, thresh]) => {
                const valFmt = typeof val === 'number' ? val.toFixed(2) : val;
                exHtml += `<div class="exit-row"><span>${icon}</span><span style="flex:1;font-size:12px;color:var(--text-2)">${name}</span><span style="font-family:var(--mono);font-size:11px;color:var(--text-3)">${valFmt} (${thresh})</span></div>`;
            });
        }
        const mClass = exitData.hard_count > 0 ? 'mandate-exit' : exitData.warn_count > 0 ? 'mandate-watch' : 'mandate-hold';
        exHtml += `<div class="exit-mandate ${mClass}">MANDATE: ${exitData.recommendation}</div></div>`;
        exitBox.innerHTML = exHtml;
    } else { exitBox.innerHTML = ''; }
    // Narrative
    const n = data.narrative;
    const narCls = activeQuantTab === 'long' ? 'narrative-long' : 'narrative-short';
    document.getElementById('quantNarrative').className = `narrative-box ${narCls}`;
    document.getElementById('quantNarrative').innerHTML = `
        <div class="nar-section"><span class="nar-lbl">📍 Kondisi Pasar</span><span>${n.kondisi}</span></div>
        <div class="nar-section"><span class="nar-lbl">🎯 Keputusan Rasional</span><span style="color:var(--accent-blue);font-weight:600">${n.keputusan}</span></div>
        <div class="nar-section"><span class="nar-lbl">🗺️ Skenario</span><span>${n.skenario}</span></div>`;
    // Market context — combine variables + live market_context
    const ctx = quant.variables || {};
    const mctx = quant.market_context || {};
    let ctxItems = [
        ['Session', ctx.session],
        ['RSI 6', ctx.O_rsi?.toFixed(1)],
        ['ATR %', ctx.H_atr_pct?.toFixed(2) + '%'],
        ['OI Norm', ctx.C_oi_norm?.toFixed(2) + '%'],
        ['Vol Norm', ctx.F_vol_norm?.toFixed(2) + '%'],
        ['CVD Norm', ctx.K_cvd_norm?.toFixed(2) + '%'],
        ['CVD Bull Div', ctx.cvd_div_bull ? '🟢 YES' : '—'],
        ['CVD Bear Div', ctx.cvd_div_bear ? '🔴 YES' : '—'],
        ['Altcoin Mode', ctx.is_altcoin ? 'Yes (×2 ATR)' : 'No (BTC scale)'],
    ];
    // Add live context items
    for (const [k, v] of Object.entries(mctx)) {
        if (!['StochRSI_K', 'StochRSI_D', 'Funding_Rate', 'Open_Interest', 'PDH', 'PDL', 'PWH', 'PWL'].includes(k)) continue;
        // Exception for Funding Rate to show 6 decimal places instead of truncating to 4
        const fmtVal = (typeof v === 'number') ? (k === 'Funding_Rate' ? v.toFixed(6) : v.toFixed(4)) : v;
        ctxItems.push([k.replace(/_/g, ' '), fmtVal]);
    }

    // Extras for V13 requirements
    if (ctx.buy_liq_val) ctxItems.push(['Buy_Liq [CSV]', ctx.buy_liq_val?.toFixed(5)]);
    if (ctx.dyn_buy_liq) ctxItems.push(['Dyn_Buy_Liq 20', ctx.dyn_buy_liq?.toFixed(5)]);
    if (ctx.macro_slope !== null) ctxItems.push(['EMA200 Slope H4', ctx.macro_slope?.toFixed(2) + '%']);

    if (ctx.stoch_k != null) {
        let bns = ctx.stoch_bonus_points || 0;
        ctxItems.push(['StochGate', `K=${ctx.stoch_k} D=${ctx.stoch_d} | bonus=+${bns}`]);
    }

    const ctxHtml = ctxItems.filter(([, v]) => v != null && v !== '—').map(([k, v]) =>
        `<div class="level-pill"><span>${k}</span><span style="font-family:var(--mono)">${v}</span></div>`
    ).join('');
    document.getElementById('marketContext').innerHTML = ctxHtml || '<span style="color:var(--text-3);font-size:12px">—</span>';
}

/* ── CSV UPLOAD & ANALYSIS ───────────────────────────────────────────────── */
function handleFileDrop(e) {
    e.preventDefault();
    document.getElementById('uploadZone').classList.remove('drag-over');
    const file = e.dataTransfer?.files?.[0];
    if (file) uploadCSVAnalysis(file);
}

async function uploadCSVAnalysis(file) {
    if (!file) return;
    const zone = document.getElementById('uploadZone');
    zone.textContent = '⏳ Analyzing CSV...'; zone.style.opacity = '.6';
    const fd = new FormData(); fd.append('file', file);
    try {
        const res = await fetch('/api/analyze-csv', { method: 'POST', body: fd });
        const json = await res.json();
        zone.textContent = '✅ ' + file.name; zone.style.opacity = '1';
        if (!json.success) { showAlert('danger', '❌ ' + json.error); return; }
        renderCSVResult(json);
    } catch (e) {
        zone.innerHTML = '📤 Upload CSV · <span style="color:var(--accent-red)">Error: ' + e.message + '</span>';
        zone.style.opacity = '1';
    }
}

function renderCSVResult(json) {
    const el = document.getElementById('csvResult');
    el.style.display = 'block';
    const meta = json.metadata || {};
    const isActive = !!meta.AVG_ENTRY_PRICE;
    const modes = ['long', 'short'];
    let html = `<div class="glass" style="padding:18px;margin-bottom:14px">
        <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center">
            <div><span style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.6px">Symbol</span><br><span style="font-size:18px;font-weight:700">${meta.Symbol || '—'}</span></div>
            <div><span style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.6px">Timeframe</span><br><span style="font-size:16px;font-weight:600">${meta.Timeframe || '4H'}</span></div>
            <div><span style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.6px">Close Price</span><br><span style="font-size:16px;font-weight:600;font-family:var(--mono)">$${json.current_price?.toFixed(5) || '—'}</span></div>
            <div><span style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.6px">Rows</span><br><span style="font-size:16px;font-weight:600">${json.rows}</span></div>
            ${isActive ? `<div style="background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.25);padding:10px 16px;border-radius:var(--radius-sm)"><span style="font-size:11px;color:var(--text-3)">Avg Entry</span><br><span style="font-size:16px;font-weight:700;color:var(--accent-green)">$${meta.AVG_ENTRY_PRICE}</span></div>` : '<div class="badge badge-yellow">Mode: ENTRY BARU</div>'}
            ${json.timestamp ? `<div style="margin-left:auto"><span style="font-size:11px;color:var(--text-3)">Last Candle</span><br><span style="font-family:var(--mono);font-size:12px">${json.timestamp}</span></div>` : ''}
        </div></div>`;
    if (json.emergency?.sl_touched || json.emergency?.rsi_ob) {
        html += `<div class="emergency-bar" style="display:flex;margin-bottom:14px">⚠️ ${json.emergency.sl_touched ? 'SL SUDAH TERSENTUH' : 'RSI OVERBOUGHT — CEK EXIT'}</div>`;
    }
    html += '<div class="quant-grid">';
    modes.forEach(tab => {
        const d = json[tab]; if (!d) return;
        const lv = d.levels; const narCls = tab === 'long' ? 'narrative-long' : 'narrative-short';
        let blockMsg = '';
        if (d.code === 'SKIP') {
            const sBlock = json.variables?.session_override_reason || '';
            // [PERBAIKAN] Ambil pesan yang sesuai dengan tab yang di-loop (LONG/SHORT)
            const stGateKey = tab === 'long' ? 'stoch_gate_override' : 'stoch_gate_override_s';
            const stGate = json.variables?.[stGateKey] || '';
            let gateMsg = '';
            for (const [gk, [status, msg]] of Object.entries(d.gate.gates)) {
                if (status === 'FAIL') gateMsg += `${gk}: ${msg} `;
            }
            blockMsg = [gateMsg, sBlock, stGate].filter(x => x).join(' | ');
        }

        const pctx = json.variables || {};
        const mlFeatures = [
            { label: 'RSI 6 Momentum', value: pctx.O_rsi, unit: '' },
            { label: 'Volatility ATR', value: pctx.H_atr_pct, unit: '%' },
            { label: 'Open Interest Norm', value: pctx.C_oi_norm, unit: '%' },
            { label: 'Volume Norm', value: pctx.F_vol_norm, unit: '%' },
            { label: 'CVD Norm', value: pctx.K_cvd_norm, unit: '%' },
            { label: 'Taker Buy', value: pctx.G_taker_buy, unit: '%' },
            { label: 'Dist EMA 21', value: pctx.L_ema21, unit: '%' },
            { label: 'Dist EMA 50', value: pctx.M_ema50, unit: '%' },
            { label: 'Dist EMA 200', value: pctx.N_ema200, unit: '%' }
        ];

        let featureRows = '<div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin-bottom:8px">ML Feature Inputs</div>';
        mlFeatures.forEach(f => {
            if (f.value !== undefined && f.value !== null) {
                featureRows += `<div class="feature-row" style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,.05)">
                    <div class="feature-name" style="color:var(--text-2); font-size:11px">${f.label}</div>
                    <div class="feature-val" style="font-family:var(--mono);color:var(--text-1); font-size:11px; margin-left:auto">${f.value.toFixed(2)}${f.unit}</div>
                </div>`;
            }
        });

        featureRows += '<div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin:12px 0 8px">Model Probabilities</div>';
        const proba = d.ml_proba || {};
        for (const [key, val] of Object.entries(proba)) {
            const fillPct = val * 100;
            const fillColor = key === 'LONG' ? 'var(--accent-green)' : key === 'SHORT' ? 'var(--accent-red)' : 'var(--accent-yellow)';
            featureRows += `<div class="feature-row">
                <div class="feature-dot" style="background:${fillColor}"></div>
                <div class="feature-name" style="text-transform:uppercase; font-weight:bold">${key}</div>
                <div class="feature-val">${fillPct.toFixed(1)}%</div>
                <div class="feature-bar-bg" style="width:60px; margin-left:10px"><div class="feature-bar-fill" style="width:${fillPct}%;background:${fillColor}"></div></div>
                </div>`;
        }
        
        console.log("[SHAP DEBUG] received in UI (CSV):", pctx.shap_top_features);
        if (pctx.shap_top_features && pctx.shap_top_features.length > 0) {
            featureRows += `<div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin:16px 0 8px">🔍 Top SHAP Drivers — Mengapa ${d.ml_signal || 'FLAT'}?</div>`;
            featureRows += `<div style="background:rgba(0,0,0,0.2); border-radius:6px; padding:8px; border:1px solid rgba(255,255,255,0.05);">`;
            pctx.shap_top_features.forEach(f => {
                const isPos = f.direction === 'positive';
                const color = isPos ? 'var(--accent-red)' : 'var(--accent-green)';
                const icon = isPos ? '🔴' : '🟢';
                const barWidth = Math.min(100, Math.abs(f.shap_value) * 100);
                featureRows += `<div style="display:flex; align-items:center; padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.02);">
                    <div style="color:var(--text-2); font-size:11px; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${f.feature.replace(/_/g, ' ')}</div>
                    <div style="font-family:var(--mono); color:${color}; font-size:11px; width:45px; text-align:right; margin-right:8px;">${f.shap_value > 0 ? '+' : ''}${f.shap_value}</div>
                    <div style="font-size:10px; margin-right:6px;">${icon}</div>
                    <div style="width:50px; height:6px; background:rgba(255,255,255,0.05); border-radius:3px; overflow:hidden;">
                        <div style="width:${barWidth}%; height:100%; background:${color};"></div>
                    </div>
                </div>`;
            });
            featureRows += `<div style="font-size:9px; color:var(--text-3); margin-top:6px; text-align:center;">* Merah = mendorong ${d.ml_signal || 'FLAT'} | Hijau = melawan ${d.ml_signal || 'FLAT'}</div></div>`;
        }

        // R:R Matrix for CSV
        let rrHtml = '';
        const rrm = lv.rr_matrix;
        if (rrm && rrm.length >= 3) {
            const slLbls = ['Ketat', 'Normal', 'Lebar'];
            rrHtml = '<table class="rr-matrix"><thead><tr><th>SL\\TP</th><th>TP1</th><th>TP2</th><th>TP3</th></tr></thead><tbody>';
            for (let i = 0; i < 3; i++) {
                rrHtml += `<tr><td style="text-align:left;font-size:10px;color:var(--text-2)">${slLbls[i]}</td>`;
                for (let j = 0; j < 3; j++) { const rr = rrm[i][j] || 0; rrHtml += `<td class="${rr >= 2 ? 'val-pos' : rr >= 1 ? 'val-warn' : 'val-neg'}">${rr.toFixed(1)}x</td>`; }
                rrHtml += '</tr>';
            }
            rrHtml += '</tbody></table>';
        }
        html += `<div class="glass quant-card">
            <div class="quant-decision-banner decision-${d.code}" style="margin-bottom:14px;padding:16px">
                <div class="decision-label">${tab === 'long' ? '🐂' : '🐻'} ${tab.toUpperCase()} Setup</div>
                <div class="decision-name color-${d.code}" style="font-size:22px">${d.decision}</div>
                <div class="decision-score">${d.total}/78 · ${d.pct.toFixed(1)}%${blockMsg ? `<br><span style="font-size:11px;color:rgba(255,255,255,0.7);display:block;margin-top:8px">${blockMsg}</span>` : ''}</div></div>
            <div style="margin-bottom:12px"><div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-3);margin-bottom:4px"><span>Score</span><span>${d.total}/78</span></div>
                <div style="height:6px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden"><div style="height:100%;border-radius:3px;width:${d.total / 78 * 100}%;background:${d.code === 'FULL' ? 'var(--accent-green)' : d.code === 'HALF' ? 'var(--accent-blue)' : d.code === 'WAIT' ? 'var(--accent-yellow)' : 'var(--accent-red)'}"></div></div></div>
            <div style="margin-bottom:14px">${featureRows}</div>
            <div><div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin-bottom:6px">Stop Loss</div>
                <div class="level-pill"><span>Ketat</span><span class="val-neg">$${lv.sl_ketat.toFixed(5)}</span></div>
                <div class="level-pill"><span>Normal</span><span class="val-neg">$${lv.sl_normal.toFixed(5)}</span></div>
                <div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin:10px 0 6px">Take Profit</div>
                <div class="level-pill"><span>TP1 <span style="font-size:10px;color:var(--text-3)">${lv.tp1_label || (tab === 'long' ? '+2.0×ATR' : '−2.0×ATR')}</span></span><span class="val-pos">$${lv.tp1.toFixed(5)}</span></div>
                <div class="level-pill"><span>TP2 <span style="font-size:10px;color:var(--text-3)">${lv.tp2_label || (tab === 'long' ? '+3.0×ATR' : '−3.0×ATR')}</span></span><span class="val-pos">$${lv.tp2.toFixed(5)}</span></div>
                <div class="level-pill"><span>TP3 <span style="font-size:10px;color:var(--text-3)">${lv.tp3_label || (tab === 'long' ? '+8.0×ATR' : '−8.0×ATR')}</span></span><span class="val-pos">$${lv.tp3.toFixed(5)}</span></div>
                ${rrHtml ? `<div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin:10px 0 6px">R:R Matrix</div>${rrHtml}` : ''}</div>
            ${json.exit?.signals?.length > 0 && isActive ? `<div style="margin-top:12px;border-top:1px solid var(--glass-border);padding-top:12px"><div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin-bottom:8px">Exit Signals</div>${json.exit.signals.map(([icon, name, val, thr]) => `<div class="exit-row"><span>${icon}</span><span style="flex:1;font-size:12px">${name}</span><span style="font-size:11px;color:var(--text-3)">${typeof val === 'number' ? val.toFixed(2) : val} (${thr})</span></div>`).join('')}<div class="exit-mandate ${json.exit.hard_count > 0 ? 'mandate-exit' : json.exit.warn_count > 0 ? 'mandate-watch' : 'mandate-hold'}">${json.exit.recommendation}</div></div>` : ''}
            <div class="narrative-box ${narCls}" style="margin-top:14px;font-size:12px">
                <div class="nar-section"><span class="nar-lbl">Kondisi</span>${d.narrative.kondisi}</div>
                <div class="nar-section"><span class="nar-lbl">Keputusan</span><strong>${d.narrative.keputusan}</strong></div>
                <div class="nar-section"><span class="nar-lbl">Skenario</span>${d.narrative.skenario}</div></div></div>`;
    });
    // Market context
    const ctx = json.market_context || {};
    const ctxKeys = Object.keys(ctx);
    if (ctxKeys.length) {
        html += `<div class="glass quant-card" style="grid-column:1/-1"><div class="panel-title">🔍 Market Context (from CSV)</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px">
            ${ctxKeys.map(k => {
            const fVal = typeof ctx[k] === 'number' ? (k === 'Funding_Rate' ? ctx[k].toFixed(6) : ctx[k].toFixed(4)) : ctx[k];
            return `<div class="level-pill"><span>${k}</span><span style="font-family:var(--mono)">${fVal}</span></div>`;
        }).join('')}</div></div>`;
    }
    html += '</div>';
    el.innerHTML = html;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ── SWITCH FUNCTIONS ────────────────────────────────────────────────────── */
function switchQuantTab(tab) {
    activeQuantTab = tab;
    document.querySelectorAll('#quantSetupTabs .tab-btn').forEach((b, i) => b.classList.toggle('active', (i === 0) === (tab === 'long')));
    if (APP_DATA?.state?.quant_analysis) renderQuantAnalysis(APP_DATA.state.quant_analysis, APP_DATA.state);
}
function switchPair(btn) { document.querySelectorAll('#pairTabs .tab-btn').forEach(b => b.classList.remove('active')); btn.classList.add('active'); activePair = btn.dataset.pair; renderRawTable(); }
function switchTimeframe(btn) { document.querySelectorAll('#timeframeTabs .tab-btn').forEach(b => b.classList.remove('active')); btn.classList.add('active'); activeTimeframe = btn.dataset.tf; renderRawTable(); }
function switchIndicator(btn) { document.querySelectorAll('#indicatorTabs .tab-btn').forEach(b => b.classList.remove('active')); btn.classList.add('active'); activeIndicator = btn.dataset.ind; renderIndicatorTable(); }
function changePair() {
    currentBackendPair = document.getElementById('pairSelect').value;
    // Sync label di Price Performance Monitor
    const lbl = document.getElementById('perfCoinLabel');
    if (lbl) lbl.textContent = currentBackendPair;
    fetchData();
}

/* ── PDF ──────────────────────────────────────────────────────────────────── */
function downloadPDF() {
    if (!APP_DATA) { showAlert('danger', '⚠️ Data belum dimuat'); return; }
    const btn = document.getElementById('btnPdf');
    btn.classList.add('btn-loading'); btn.textContent = '⏳ Generating...';
    const pair = (APP_DATA?.state?.user_input?.coin_pair || 'UNKNOWN').replace('/', '-');
    const now = new Date(); const pad = n => String(n).padStart(2, '0');
    const fname = `Laporan_Protokol_9.6_${pair}_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.pdf`;
    html2pdf().set({
        filename: fname, margin: [8, 8, 8, 8], image: { type: 'jpeg', quality: .95 },
        html2canvas: { scale: 2, useCORS: true, backgroundColor: '#0a0e1a' },
        jsPDF: { unit: 'mm', format: 'a3', orientation: 'landscape' }
    })
        .from(document.getElementById('dashboardContent')).save()
        .then(() => { btn.classList.remove('btn-loading'); btn.textContent = '📄 PDF'; showAlert('success', '✅ PDF berhasil diunduh!'); setTimeout(hideAlert, 3000); })
        .catch(e => { btn.classList.remove('btn-loading'); btn.textContent = '📄 PDF'; showAlert('danger', '❌ PDF Error: ' + e.message); });
}

/* ── CSV DOWNLOAD ────────────────────────────────────────────────────────── */
function downloadCSV() {
    if (!APP_DATA) { showAlert('danger', '⚠️ Data belum dimuat'); return; }
    const btn = document.getElementById('btnCsv');
    btn.classList.add('btn-loading'); btn.textContent = '⏳ Exporting...';
    const years = parseFloat(document.getElementById('csvYears').value);
    let limit = 250;
    if (years === 0.1) limit = 250; else if (years === 0.5) limit = 1000; else limit = Math.floor(years * 2190);
    window.location.href = `/api/export-csv?tf=4h&limit=${limit}&pair=${currentBackendPair}`;
    setTimeout(() => { btn.classList.remove('btn-loading'); btn.textContent = '📥 HARVEST'; showAlert('success', `✅ Export CSV (${limit} bars) dikirim!`); }, 2000);
}

/* ── ALERT ───────────────────────────────────────────────────────────────── */
function showAlert(type, msg) { const b = document.getElementById('alertBanner'); b.className = `alert-banner show ${type}`; b.textContent = msg; }
function hideAlert() { document.getElementById('alertBanner').classList.remove('show'); }

/* ── UTILITIES ───────────────────────────────────────────────────────────── */
function fmtVol(v) { const a = Math.abs(v); if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M'; if (a >= 1e3) return (v / 1e3).toFixed(2) + 'K'; return v.toFixed(2); }

/* ── SIGNAL TIMESTAMP FORMATTER ──────────────────────────────────────────── */
// Format Unix timestamp → WITA (UTC+8) + UTC string
function formatSignalTimestamp(unixTs) {
    if (!unixTs || unixTs === 0) return '';
    const d = new Date(unixTs * 1000);
    // UTC string
    const utcStr = d.toISOString().replace('T', ' ').substring(0, 16) + ' UTC';
    // WITA (Asia/Makassar = UTC+8)
    const witaStr = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Makassar',
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit', hour12: false
    }).format(d).replace(',', ' ') + ' WITA';
    // Relative time (berapa jam lalu)
    const hoursAgo = ((Date.now() - unixTs * 1000) / 3.6e6).toFixed(1);
    const relStr = hoursAgo < 1 ? 'baru saja' : `${hoursAgo} jam lalu`;
    return `<div style="margin-top:5px;padding:5px 8px;background:rgba(0,0,0,0.25);border-radius:6px;border-left:2px solid rgba(251,191,36,.5)">
        <div style="font-size:9.5px;color:#fbbf24;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px">⏱️ Signal Recorded</div>
        <div style="font-family:var(--mono);font-size:10px;color:#cbd5e1">${witaStr}</div>
        <div style="font-family:var(--mono);font-size:9.5px;color:#64748b">${utcStr} &nbsp;&middot;&nbsp; ${relStr}</div>
    </div>`;
}

/* ── TRADE ENTRIES ───────────────────────────────────────────────────────── */
async function loadTradeEntries() {
    try { const r = await fetch('/api/trade-entries'); const j = await r.json(); if (j.success) { tradeEntries = j.entries || {}; tradeSummaries = j.summaries || {}; } } catch (e) { console.error(e); }
}

function openEntryModal() {
    const modal = document.getElementById('entryModal');
    const sel = document.getElementById('entrySymbol');
    if (APP_DATA?.state?.user_input?.available_pairs) {
        sel.innerHTML = APP_DATA.state.user_input.available_pairs.map(p => `<option value="${p}" ${p === currentBackendPair ? 'selected' : ''}>${p}</option>`).join('');
    }
    prefillEntryForm(); sel.onchange = prefillEntryForm; renderEntryList(); modal.classList.add('show');
}

function prefillEntryForm() {
    // Mengosongkan form saat koin diganti
    document.getElementById('entryPrice').value = '';
    document.getElementById('entryUsdt').value = '';
    document.getElementById('entryQty').value = '';
}

function closeEntryModal() { document.getElementById('entryModal').classList.remove('show'); }

/* ── UI HELPERS ── Market Type / Side toggles ───────────────────────────── */
function onMarketTypeChange() {
    const mt = document.getElementById('entryMarketType').value;
    const lg = document.getElementById('leverageGroup');
    lg.style.display = mt === 'FUTURES' ? 'flex' : 'none';
    // SHORT diperbolehkan hanya di FUTURES; jika pilih SPOT paksa LONG
    const sideEl = document.getElementById('entrySide');
    if (mt === 'SPOT') {
        sideEl.value = 'LONG';
        sideEl.disabled = true;   // SPOT selalu LONG
    } else {
        sideEl.disabled = false;
    }
    onEntrySideChange();
    calcQtyFromUsdt(); // Update hitungan saat switch Spot ↔ Futures
}
function onEntrySideChange() {
    const side = document.getElementById('entrySide').value;
    const btn = document.getElementById('btnSaveEntry');
    if (side === 'SHORT') {
        btn.style.background = 'var(--accent-red)';
        btn.textContent = '🔴 Open SHORT Position';
    } else {
        btn.style.background = 'var(--accent-blue)';
        btn.textContent = '🟢 Open LONG Position';
    }
}

/* ── AUTO-CALCULATE QTY DARI MARGIN USDT ───────────────────────────────── */
function calcQtyFromUsdt() {
    const price = parseFloat(document.getElementById('entryPrice').value);
    const usdt = parseFloat(document.getElementById('entryUsdt').value);
    const leverage = parseInt(document.getElementById('entryLeverage').value) || 1;
    const marketType = document.getElementById('entryMarketType').value;

    if (price > 0 && usdt > 0) {
        let qty = 0;
        // Futures: Quantity = (Margin USDT × Leverage) / Harga Koin
        if (marketType === 'FUTURES') {
            qty = (usdt * leverage) / price;
        } else {
            // Spot: Quantity = Amount USDT / Harga Koin
            qty = usdt / price;
        }
        document.getElementById('entryQty').value = qty.toFixed(6);
    }
}

async function saveEntry() {
    const sym = document.getElementById('entrySymbol').value;
    const side = document.getElementById('entrySide').value;
    const marketType = document.getElementById('entryMarketType').value;
    const leverage = parseInt(document.getElementById('entryLeverage').value) || 1;
    const ep = parseFloat(document.getElementById('entryPrice').value);
    const qty = parseFloat(document.getElementById('entryQty').value);
    if (!sym) { showAlert('danger', '⚠️ Pilih coin dulu'); return; }
    if (!ep || ep <= 0) { showAlert('danger', '⚠️ Entry price harus > 0'); return; }
    if (!qty || qty <= 0) { showAlert('danger', '⚠️ Quantity harus > 0'); return; }
    if (marketType === 'FUTURES' && leverage < 1) { showAlert('danger', '⚠️ Leverage minimal 1x'); return; }
    try {
        const r = await fetch('/api/trade-entries', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: sym, entry_price: ep, qty: qty, side: side,
                market_type: marketType, leverage: leverage
            })
        });
        const j = await r.json();
        if (j.success) {
            showAlert('success', `✅ ${side} Entry #${j.summary.num_entries} @ $${ep} [${marketType}${marketType === 'FUTURES' ? ' x' + leverage : ''}]`);
            setTimeout(hideAlert, 4000);
            await loadTradeEntries(); renderEntryList();
            document.getElementById('entryPrice').value = '';
            document.getElementById('entryQty').value = '';
            if (sym === currentBackendPair) fetchData();
        } else { showAlert('danger', '❌ ' + j.error); }
    } catch (e) { showAlert('danger', '❌ ' + e.message); }
}

// Fungsi BARU untuk Tombol MAX di Form Sell
function setMaxSellQty() {
    const sel = document.getElementById('sellSymbol');
    const sm = tradeSummaries[sel.value] || {};
    const rq = sm.remaining_qty || 0;
    if (rq > 0) {
        document.getElementById('sellQtyInput').value = rq.toFixed(6);
    } else {
        showAlert('danger', '⚠️ Anda tidak memiliki sisa aset untuk dijual');
    }
}

async function saveSell() {
    const sym = document.getElementById('sellSymbol').value;
    const sp = parseFloat(document.getElementById('sellPriceInput').value);
    const qty = parseFloat(document.getElementById('sellQtyInput').value);
    if (!sp || sp <= 0) { showAlert('danger', '⚠️ Sell price harus > 0'); return; }
    if (!qty || qty <= 0) { showAlert('danger', '⚠️ Quantity harus > 0'); return; }
    try {
        const r = await fetch('/api/trade-sales', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: sym, sell_price: sp, qty: qty })
        });
        const j = await r.json();
        if (j.success) {
            showAlert('success', `💰 Sale recorded @ $${sp} × ${qty}`);
            setTimeout(hideAlert, 3000);
            await loadTradeEntries(); renderEntryList();
            document.getElementById('sellPriceInput').value = '';
            document.getElementById('sellQtyInput').value = '';
            if (sym === currentBackendPair) fetchData();
        } else { showAlert('danger', '❌ ' + j.error); }
    } catch (e) { showAlert('danger', '❌ ' + e.message); }
}

async function deleteSingleEntry(sym, idx) {
    if (!confirm(`Hapus entry #${idx + 1} untuk ${sym}?`)) return;
    const r = await fetch('/api/trade-entries/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: sym, index: idx }) });
    const j = await r.json();
    if (j.success) { showAlert('success', '🗑️ Entry dihapus'); await loadTradeEntries(); renderEntryList(); if (sym === currentBackendPair) fetchData(); }
}
async function deleteSaleEntry(sym, idx) {
    if (!confirm(`Hapus sale #${idx + 1} untuk ${sym}?`)) return;
    const r = await fetch('/api/trade-sales/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: sym, index: idx }) });
    const j = await r.json();
    if (j.success) { showAlert('success', '🗑️ Sale dihapus'); await loadTradeEntries(); renderEntryList(); if (sym === currentBackendPair) fetchData(); }
}
async function deleteAllEntries(sym) {
    if (!confirm(`Hapus SEMUA data untuk ${sym}?`)) return;
    const r = await fetch('/api/trade-entries/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: sym }) });
    const j = await r.json();
    if (j.success) { showAlert('success', '🗑️ All cleared'); await loadTradeEntries(); renderEntryList(); if (sym === currentBackendPair) fetchData(); }
}

function switchModalTab(tab) {
    document.querySelectorAll('.modal .tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1))?.classList.add('active');
    document.getElementById('modalEntryForm').style.display = tab === 'entry' ? 'grid' : 'none';
    document.getElementById('modalSellForm').style.display = tab === 'sell' ? 'grid' : 'none';
    document.getElementById('modalHistory').style.display = tab === 'history' ? 'block' : 'none';
    if (tab === 'sell' && APP_DATA) {
        const sel = document.getElementById('sellSymbol');
        sel.innerHTML = (APP_DATA.state?.user_input?.available_pairs || []).map(p => `<option value="${p}" ${p === currentBackendPair ? 'selected' : ''}>${p}</option>`).join('');
        function updateSellPlaceholder() {
            const sm = tradeSummaries[sel.value] || {};
            const rq = sm.remaining_qty || 0;
            // Kosongkan isian — biarkan user klik MAX untuk auto-fill
            document.getElementById('sellQtyInput').value = '';
            document.getElementById('sellQtyInput').placeholder = rq > 0 ? `Tersedia: ${rq.toFixed(4)}` : 'No position';
        }
        sel.onchange = updateSellPlaceholder;
        updateSellPlaceholder();
    }
}

function renderEntryList() {
    const c = document.getElementById('entryListBody');
    const syms = Object.keys(tradeEntries);
    if (!syms.length) { c.innerHTML = '<div class="entry-empty">Belum ada posisi. Buka via tab Open Pos.</div>'; return; }
    let html = '';
    syms.forEach(sym => {
        const cd = tradeEntries[sym];
        const entries = cd.entries || [];
        const sales = cd.sales || [];
        const sm = tradeSummaries[sym] || {};
        if (!entries.length && !sales.length) return;

        // Position meta badges
        const side = cd.position_side || 'LONG';
        const mkt = cd.market_type || 'SPOT';
        const lev = cd.leverage || 1;
        const sideClr = side === 'SHORT' ? 'badge-red' : 'badge-green';
        const sideIcon = side === 'SHORT' ? '🔴' : '🟢';
        const mktBadge = mkt === 'FUTURES'
            ? `<span class="badge badge-yellow" style="font-size:9px;padding:2px 7px;margin-left:4px">⚡${lev}x</span>`
            : `<span class="badge badge-blue" style="font-size:9px;padding:2px 7px;margin-left:4px">📈 SPOT</span>`;

        html += `<div style="background:rgba(99,125,255,.06);border:1px solid rgba(99,125,255,.18);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center">
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
                <strong>${sym}</strong>
                <span class="badge ${sideClr}" style="font-size:9px;padding:2px 7px">${sideIcon} ${side}</span>
                ${mktBadge}
                <span style="font-size:12px;color:var(--text-2)">Rem: <strong>${(sm.remaining_qty || 0).toFixed(4)}</strong></span>
                <span style="font-size:12px">P&L: <strong class="${(sm.realized_pnl || 0) >= 0 ? 'val-pos' : 'val-neg'}">$${(sm.realized_pnl || 0).toFixed(2)}</strong></span>
            </div>
            <button class="btn-del-e" onclick="deleteAllEntries('${sym}')">✕ Clear</button></div>`;

        entries.forEach((e, i) => {
            html += `<div class="entry-item" style="margin-left:12px;border-left:3px solid var(--accent-green)">
                <div><div style="font-size:11px;color:var(--accent-green);font-weight:700">OPEN #${i + 1}</div>
                <div style="font-size:11px;color:var(--text-3)">Qty: ${e.qty} · ${e.date || ''}</div></div>
                <div style="display:flex;align-items:center;gap:10px">
                    <span style="font-family:var(--mono);color:var(--accent-green);font-weight:600">$${e.price}</span>
                    <button class="btn-del-e" onclick="deleteSingleEntry('${sym}',${i})">✕</button>
                </div></div>`;
        });
        sales.forEach((s, i) => {
            html += `<div class="entry-item" style="margin-left:12px;border-left:3px solid var(--accent-red)">
                <div><div style="font-size:11px;color:var(--accent-red);font-weight:700">CLOSE #${i + 1}</div>
                <div style="font-size:11px;color:var(--text-3)">Qty: ${s.qty} · ${s.date || ''}</div></div>
                <div style="display:flex;align-items:center;gap:10px">
                    <span style="font-family:var(--mono);color:var(--accent-red);font-weight:600">$${s.price}</span>
                    <button class="btn-del-e" onclick="deleteSaleEntry('${sym}',${i})">✕</button>
                </div></div>`;
        });
    });
    c.innerHTML = html || '<div class="entry-empty">Belum ada data posisi.</div>';
}

/* ── THEME ───────────────────────────────────────────────────────────────── */
function toggleTheme() {
    const h = document.documentElement;
    const isDark = h.getAttribute('data-theme') === 'dark';
    h.setAttribute('data-theme', isDark ? 'light' : 'dark');
    localStorage.setItem('theme', isDark ? 'light' : 'dark');
}

/* ── MARKET SCANNER (ALL COINS) ──────────────────────────────────────────── */
async function fetchScannerData() {
    const grid = document.getElementById('scannerGrid');
    try {
        const res  = await fetch('/api/scanner');
        const json = await res.json();

        if (!json.success) throw new Error(json.error || 'Scanner gagal memuat.');
        if (!json.data || json.data.length === 0) {
            grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text-3);padding:40px">Tidak ada data.</div>';
            return;
        }

        // Grouping data
        const groups = { LONG: [], SHORT: [], WAIT: [] };
        json.data.forEach(d => {
            if (d.error) { groups.WAIT.push(d); return; }
            const mlSignal  = d.ml_signal  || 'FLAT';
            const mlSize    = d.ml_size    || 'SKIP';
            const mlSignalS = d.ml_signal_s || 'FLAT';
            const mlSizeS   = d.ml_size_s  || 'SKIP';

            const isLong  = (mlSignal  === 'LONG')  && (mlSize  === 'FULL' || mlSize  === 'HALF');
            const isShort = (mlSignalS === 'SHORT') && (mlSizeS === 'FULL' || mlSizeS === 'HALF');

            if (isLong) groups.LONG.push(d);
            else if (isShort) groups.SHORT.push(d);
            else groups.WAIT.push(d);
        });

        const renderGroup = (label, coins, color) => {
            if (!coins.length) return '';
            const coinHtml = coins.map(d => {
                if (d.error) {
                    return `<div class="scanner-card dec-wait">
                        <div class="sc-header"><span class="sc-pair">${d.pair}</span><span style="color:var(--accent-red);font-size:11px">⚠️ Error</span></div>
                    </div>`;
                }
                const mlSignal  = d.ml_signal  || 'FLAT';
                const mlConf    = d.ml_confidence || 0;
                const mlSize    = d.ml_size    || 'SKIP';
                const mlSignalS = d.ml_signal_s || 'FLAT';
                const mlConfS   = d.ml_confidence_s || 0;
                const mlSizeS   = d.ml_size_s  || 'SKIP';

                const longActive  = (mlSignal  === 'LONG')  && (mlSize  === 'FULL' || mlSize  === 'HALF');
                const shortActive = (mlSignalS === 'SHORT') && (mlSizeS === 'FULL' || mlSizeS === 'HALF');

                let decIcon, decLabel, decColor, decConf, decSize, decBg, decBorder, cardClass;
                if (longActive) {
                    decIcon = '🟢'; decLabel = 'LONG'; decColor = '#34d399'; decConf = mlConf; decSize = mlSize;
                    decBg = 'rgba(52,211,153,.08)'; decBorder = 'rgba(52,211,153,.25)'; cardClass = 'dec-long';
                } else if (shortActive) {
                    decIcon = '🔴'; decLabel = 'SHORT'; decColor = '#f87171'; decConf = mlConfS; decSize = mlSizeS;
                    decBg = 'rgba(248,113,113,.08)'; decBorder = 'rgba(248,113,113,.25)'; cardClass = 'dec-short';
                } else {
                    decIcon = '⚪'; decLabel = 'WAIT'; decColor = 'var(--text-3)'; decConf = Math.max(mlConf, mlConfS); decSize = null;
                    decBg = 'rgba(255,255,255,.03)'; decBorder = 'var(--glass-border)'; cardClass = 'dec-wait';
                }

                const sizePill = decSize ? `<span class="sc-size-pill" style="color:${decColor}">${decSize}</span>` : '';
                const histTs = d.last_signal_ts;
                let entryHtml = '';
                if (d.last_signal_type) {
                    const isL = d.last_signal_type.startsWith('LONG');
                    const hClr = isL ? '#34d399' : '#f87171';
                    const hTs = histTs ? new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Makassar', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(histTs * 1000)).replace(',', '') + ' WITA' : '—';
                    entryHtml = `<div class="sc-entry"><div class="sc-entry-body"><div class="sc-entry-label">⏳ Entry Terakhir</div><div class="sc-entry-signal" style="color:${hClr}">${d.last_signal_type}</div><div class="sc-entry-time">@ ${hTs}</div></div></div>`;
                }

                return `<div class="scanner-card ${cardClass}">
                    <div class="sc-header">
                        <div><div class="sc-pair">${d.pair}</div><div class="sc-price">$${d.close.toFixed(5)}</div></div>
                        <div class="sc-decision" style="background:${decBg};border-color:${decBorder}">
                            <span class="sc-dec-icon">${decIcon}</span>
                            <div><div style="display:flex;align-items:center;gap:6px"><span class="sc-dec-label" style="color:${decColor}">${decLabel}</span>${sizePill}</div><div class="sc-dec-sub" style="color:${decColor}">${(decConf*100).toFixed(1)}% conf</div></div>
                        </div>
                    </div>
                    ${entryHtml}
                    <div class="sc-footer">
                        <button class="btn btn-primary" style="padding:5px 16px;font-size:11px;border-radius:18px" onclick="openDetailWithHistory('${d.pair}')">Analisis Detail &#x1F50D;</button>
                    </div>
                </div>`;
            }).join('');

            return `<div style="grid-column:1/-1;margin-top:20px;margin-bottom:10px;display:flex;align-items:center;gap:12px">
                <div style="width:4px;height:24px;background:${color};border-radius:2px"></div>
                <h3 style="font-size:16px;margin:0;color:var(--text-1);letter-spacing:.5px">${label} <span style="font-size:12px;color:var(--text-3);font-weight:400">(${coins.length})</span></h3>
            </div>${coinHtml}`;
        };

        grid.innerHTML = renderGroup('🟢 SETUPS LONG', groups.LONG, '#34d399') +
                         renderGroup('🔴 SETUPS SHORT', groups.SHORT, '#f87171') +
                         renderGroup('⚪ WATCHLIST / WAIT', groups.WAIT, 'var(--text-3)');

    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--accent-red);padding:40px">❌ Error Scanner: ${e.message}</div>`;
    }
}


/* ── INIT ────────────────────────────────────────────────────────────────── */

/* ── CONFIDENCE HISTORY CHART ───────────────────────────────────────────── */
function openDetailWithHistory(pair) {
    document.getElementById('pairSelect').value = pair;
    changePair();
    // Scroll ke section analisis detail setelah data dimuat
    setTimeout(() => {
        const sec = document.getElementById('sectionScanner') || document.getElementById('quantPanel');
        if (sec) sec.scrollIntoView({ behavior: 'smooth' });
        // ML Confidence ditampilkan di Price Performance Monitor
    }, 400);
}

// loadDetailConfidenceChart dipindahkan ke Price Performance Monitor

const _confChartOpen = {};   // track open state per pair

async function toggleConfChart(pair) {
    const container = document.getElementById(`conf-chart-${pair}`);
    const btn       = document.getElementById(`conf-btn-${pair}`);
    if (!container) return;

    if (_confChartOpen[pair]) {
        container.style.display = 'none';
        _confChartOpen[pair] = false;
        if (btn) btn.textContent = '📈 History';
        return;
    }

    // Fetch data
    container.style.display = 'block';
    container.innerHTML = '<div style="text-align:center;color:var(--text-3);font-size:11px;padding:10px">⏳ Memuat history...</div>';
    if (btn) btn.textContent = '⏳';
    _confChartOpen[pair] = true;

    try {
        const res  = await fetch(`/api/confidence-history/${pair}`);
        const json = await res.json();
        if (!json.success || !json.data.length) {
            container.innerHTML = `<div style="text-align:center;color:var(--text-3);font-size:11px;padding:10px">
                📊 Belum ada data history.<br><span style="font-size:10px">Data terekam setiap 15 menit oleh signal monitor.</span>
            </div>`;
            if (btn) btn.textContent = '📈 History';
            return;
        }
        container.innerHTML = renderConfidenceChart(pair, json.data);
        if (btn) btn.textContent = '📉 Tutup';
    } catch (e) {
        container.innerHTML = `<div style="color:var(--accent-red);font-size:11px;padding:8px">❌ Error: ${e.message}</div>`;
        if (btn) btn.textContent = '📈 History';
        _confChartOpen[pair] = false;
    }
}

function renderConfidenceChart(pair, data, customWidth = 280) {
    if (!data || !data.length) return '<div style="color:var(--text-3);font-size:11px;padding:8px">Tidak ada data</div>';

    const W = customWidth, H = 80, PAD = 12;
    const n = data.length;
    const confs = data.map(d => d.ml_conf * 100);
    const minC = Math.max(0,  Math.min(...confs) - 5);
    const maxC = Math.min(100, Math.max(...confs) + 5);
    const range = maxC - minC || 1;

    const toX = i   => PAD + (i / Math.max(n - 1, 1)) * (W - 2 * PAD);
    const toY = val => H - PAD - ((val - minC) / range) * (H - 2 * PAD);

    // Build polyline points
    const pts = data.map((d, i) => `${toX(i).toFixed(1)},${toY(d.ml_conf * 100).toFixed(1)}`).join(' ');

    // Build signal-colored dots
    const dots = data.map((d, i) => {
        const sig = d.ml_signal;
        const col = sig === 'LONG' ? '#34d399' : sig === 'SHORT' ? '#f87171' : '#94a3b8';
        const r = i === n - 1 ? 4 : 2.5;
        const x = toX(i).toFixed(1), y = toY(d.ml_conf * 100).toFixed(1);
        const ts = new Date(d.ts * 1000);
        const wita = new Intl.DateTimeFormat('en-GB', {
            timeZone: 'Asia/Makassar', hour: '2-digit', minute: '2-digit', hour12: false
        }).format(ts);
        return `<circle cx="${x}" cy="${y}" r="${r}" fill="${col}" opacity="0.85">
            <title>${sig} ${(d.ml_conf * 100).toFixed(1)}% @ ${wita}</title>
        </circle>`;
    }).join('');

    // Latest point annotation
    const last = data[n - 1];
    const lastX = toX(n - 1).toFixed(1);
    const lastY = toY(last.ml_conf * 100).toFixed(1);
    const lastCol = last.ml_signal === 'LONG' ? '#34d399' : last.ml_signal === 'SHORT' ? '#f87171' : '#94a3b8';
    const lastTs = new Date(last.ts * 1000);
    const firstTs = new Date(data[0].ts * 1000);
    const fmtTime = d => new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Makassar', hour: '2-digit', minute: '2-digit', hour12: false
    }).format(d);

    // Area fill gradient path
    const areaPath = `M${toX(0).toFixed(1)},${H - PAD} ` +
        data.map((d, i) => `L${toX(i).toFixed(1)},${toY(d.ml_conf * 100).toFixed(1)}`).join(' ') +
        ` L${toX(n - 1).toFixed(1)},${H - PAD} Z`;

    return `
    <div style="background:rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:10px 12px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <span style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;letter-spacing:.5px">📊 ML Confidence — 72 Jam Terakhir</span>
            <span style="font-size:10px;color:var(--text-3)">${n} titik data</span>
        </div>
        <svg width="${W}" height="${H}" style="overflow:visible;display:block">
            <defs>
                <linearGradient id="cgrad-${pair}" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="${lastCol}" stop-opacity="0.25"/>
                    <stop offset="100%" stop-color="${lastCol}" stop-opacity="0"/>
                </linearGradient>
            </defs>
            <!-- Grid lines -->
            <line x1="${PAD}" y1="${toY(50).toFixed(1)}" x2="${W - PAD}" y2="${toY(50).toFixed(1)}"
                stroke="rgba(255,255,255,.06)" stroke-width="1" stroke-dasharray="3,3"/>
            <text x="${PAD - 2}" y="${toY(50).toFixed(1)}" fill="rgba(255,255,255,.2)"
                font-size="8" text-anchor="end" dominant-baseline="middle">50%</text>
            <!-- Area fill -->
            <path d="${areaPath}" fill="url(#cgrad-${pair})"/>
            <!-- Line -->
            <polyline points="${pts}" fill="none" stroke="${lastCol}" stroke-width="1.5" stroke-linejoin="round"/>
            <!-- Dots -->
            ${dots}
            <!-- Latest label -->
            <text x="${lastX}" y="${parseFloat(lastY) - 8}" fill="${lastCol}"
                font-size="9" text-anchor="middle" font-weight="bold">${(last.ml_conf * 100).toFixed(1)}%</text>
        </svg>
        <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--text-3);margin-top:2px">
            <span>${fmtTime(firstTs)} WITA</span>
            <span style="color:${lastCol};font-weight:700">${last.ml_signal} ${(last.ml_conf*100).toFixed(1)}%</span>
            <span>${fmtTime(lastTs)} WITA</span>
        </div>
        <div style="display:flex;gap:8px;margin-top:6px;flex-wrap:wrap">
            <span style="font-size:9px;color:#34d399">● LONG</span>
            <span style="font-size:9px;color:#f87171">● SHORT</span>
            <span style="font-size:9px;color:#94a3b8">● FLAT</span>
        </div>
    </div>`;
}

document.addEventListener('DOMContentLoaded', () => {
    const t = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', t);
    document.addEventListener('click', e => { if (e.target.id === 'entryModal') closeEntryModal(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeEntryModal(); });
    fetchData(); // Fungsi utama me-load single dashboard default
    fetchScannerData(); // Tambahan untuk memuat scanner
});
