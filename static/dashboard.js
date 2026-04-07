/* ═══════════════════════════════════════════════════════════════════════════
   PROTOCOL 9.6 — DASHBOARD JAVASCRIPT ENGINE v2.1
   ═══════════════════════════════════════════════════════════════════════════ */

let APP_DATA       = null;
let activeTimeframe = '15m';
let activePair      = 'coin';
let activeIndicator = '1h';
let activeQuantTab  = 'long';
let currentBackendPair = '';
let tradeEntries    = {};
let tradeSummaries  = {};

/* ── MAIN DATA FETCH ────────────────────────────────────────────────────── */
async function fetchData() {
    const btn = document.getElementById('btnRefresh');
    btn.textContent = '⏳ Loading...'; btn.classList.add('btn-loading');
    document.getElementById('loadingOverlay').classList.remove('hidden');
    try {
        const pair = currentBackendPair || '';
        const url  = pair ? `/api/data?pair=${pair}` : '/api/data';
        const res  = await fetch(url);
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
    } catch(e) {
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
    document.getElementById('statEntry').textContent = ep ? '$' + ep.toFixed(5) : 'No Entry';
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
            <td>${r.time}</td><td>${r.open?.toFixed(5)||'—'}</td><td>${r.high?.toFixed(5)||'—'}</td>
            <td>${r.low?.toFixed(5)||'—'}</td><td>${r.close?.toFixed(5)||'—'}</td>
            <td>${fmtVol(r.total_vol||0)}</td><td class="val-pos">${fmtVol(r.buy_vol||0)}</td>
            <td class="val-neg">${fmtVol(r.sell_vol||0)}</td><td class="${dCls}">${d>=0?'+':''}${fmtVol(d)}</td></tr>`;
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
            <td>${r.time}</td><td>${r.close?.toFixed(5)||'—'}</td>
            <td>${r.ema_7?.toFixed(4)||'—'}</td><td>${r.ema_21?.toFixed(4)||'—'}</td>
            <td>${r.ema_50?.toFixed(4)||'—'}</td><td>${r.ema_200?.toFixed(4)||'—'}</td>
            <td class="${rsiCls}">${rsi?.toFixed(1)||'—'}</td>
            <td>${r.stochrsi_k?.toFixed(2)||'—'}</td><td>${r.stochrsi_d?.toFixed(2)||'—'}</td>
            <td class="val-pos">${fmtVol(r.buy_vol||0)}</td><td class="val-neg">${fmtVol(r.sell_vol||0)}</td>
            <td class="${(r.vol_delta||0)>0?'val-pos':'val-neg'}">${fmtVol(r.vol_delta||0)}</td></tr>`;
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
        ['Volume Bias', tvol ? ((bvol/tvol*100).toFixed(1)+'% Buy') : '—', 'Buy < 45%', 'Buy > 55%', bvol/tvol, r => r > 0.55 ? 'bull' : r < 0.45 ? 'bear' : 'neutral', 'Taker pressure'],
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

/* ── KILL SWITCH ─────────────────────────────────────────────────────────── */
function renderKillSwitch(json) {
    const ind = APP_DATA?.computed?.indicators_4h || [];
    const smt = json.computed?.smt_divergence || {};
    const last4 = ind.length ? ind[ind.length - 1] : null;
    const items = document.querySelectorAll('#killSwitchChecklist input');
    const spans = [document.getElementById('alertKillEma'), document.getElementById('alertKillSmt'),
                   document.getElementById('alertKillVol'), document.getElementById('alertKillOi')];
    let triggered = 0;
    if (last4) { const ok = last4.close < last4.ema_21; items[0].checked = ok; if (ok) triggered++; spans[0].textContent = ok ? '⚠️ Triggered' : '✅ OK'; spans[0].className = 'panel-row-value ' + (ok ? 'val-neg' : 'val-pos'); }
    const smtOk = smt.bearish_smt; items[1].checked = smtOk; if (smtOk) triggered++; spans[1].textContent = smtOk ? '⚠️ Triggered' : '✅ OK'; spans[1].className = 'panel-row-value ' + (smtOk ? 'val-neg' : 'val-pos');
    if (last4) { const tvol = (last4.buy_vol||0) + (last4.sell_vol||0); const sfake = tvol > 0 && (last4.sell_vol / tvol > 0.6); items[2].checked = sfake; if (sfake) triggered++; spans[2].textContent = sfake ? '⚠️ Triggered' : '✅ OK'; spans[2].className = 'panel-row-value ' + (sfake ? 'val-neg' : 'val-pos'); }
    items[3].checked = false; spans[3].textContent = '✅ OK'; spans[3].className = 'panel-row-value val-pos';
    const vEl = document.getElementById('killSwitchVerdict');
    if (triggered > 0) { vEl.style.cssText = 'background:rgba(248,113,113,.12);color:var(--accent-red);border:1px solid rgba(248,113,113,.25)'; vEl.textContent = `🚨 ${triggered} KILL CONDITION(S) ACTIVE — INITIATE ABORT PROTOCOL`; }
    else { vEl.style.cssText = 'background:rgba(52,211,153,.08);color:var(--accent-green);border:1px solid rgba(52,211,153,.18)'; vEl.textContent = '✅ ALL CLEAR — No kill switch conditions triggered'; }
    document.getElementById('valPDH').textContent = (json.computed?.liquidity_borders?.PDH || 0).toFixed(5);
    document.getElementById('valPDL').textContent = (json.computed?.liquidity_borders?.PDL || 0).toFixed(5);
    document.getElementById('valPWH').textContent = (json.computed?.liquidity_borders?.PWH || 0).toFixed(5);
    document.getElementById('valPWL').textContent = (json.computed?.liquidity_borders?.PWL || 0).toFixed(5);
    document.getElementById('valBtcTrend').textContent = smt.btc_trend_12h || '—';
    document.getElementById('valCoinTrend').textContent = smt.coin_trend_12h || '—';
    const smtEl = document.getElementById('valSMT');
    smtEl.innerHTML = smtOk ? '<span class="badge badge-red">YES ⚠️</span>' : '<span class="badge badge-green">NO ✅</span>';
    const oiDelta = json.computed?.oi_delta_pct || 0;
    const oiEl = document.getElementById('valOIDelta');
    oiEl.textContent = (oiDelta >= 0 ? '+' : '') + oiDelta.toFixed(2) + '%';
    oiEl.className = 'panel-row-value ' + (oiDelta >= 0 ? 'val-pos' : 'val-neg');
}

/* ── QUANT ANALYSIS ──────────────────────────────────────────────────────── */
const FEATURE_LABELS = { OI:'Open Interest Change', Vol:'Relative Volume (MA20)', TakerBuy:'Taker Buy Pressure', ATR:'Volatility ATR %', CVD:'Cumulative Vol Delta', EMA21:'Distance EMA 21', EMA50:'Distance EMA 50', EMA200:'Distance EMA 200', RSI:'RSI 6 Momentum' };
const FEATURE_UNIT = { TakerBuy:'%',RSI:'',OI:'%',Vol:'%',ATR:'%',CVD:'%',EMA21:'%',EMA50:'%',EMA200:'%' };

function renderQuantAnalysis(quant, state) {
    if (!quant) { 
        document.getElementById('quantDecisionName').textContent = 'DATA INSUFFICIENT'; 
        document.getElementById('quantScoreSummary').textContent = 'Need ≥22 candles of 4H data'; 
        document.getElementById('quantDecisionName').className = 'decision-name color-SKIP';
        if (document.getElementById('decisionBanner')) document.getElementById('decisionBanner').className = 'quant-decision-banner decision-SKIP';
        if (document.getElementById('quantTotalBar')) document.getElementById('quantTotalBar').style.width = '0%';
        if (document.getElementById('quantTotalPts')) document.getElementById('quantTotalPts').textContent = '—/71';
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
    const data = quant[activeQuantTab];
    if (!data) return;
    const ep = state?.user_input?.entry_price || 0;
    const hasEntry = ep > 0;
    // Decision banner
    const banner = document.getElementById('decisionBanner');
    banner.className = `quant-decision-banner decision-${data.code}`;
    document.getElementById('quantDecisionName').className = `decision-name color-${data.code}`;
    let blockMsg = '';
    if (data.code === 'SKIP') {
        const sBlock = state.quant_analysis?.variables?.session_override_reason || '';
        const stGate = state.quant_analysis?.variables?.stoch_gate_override || '';
        let gateMsg = '';
        for (const [gk, [status, msg]] of Object.entries(data.gate.gates)) {
            if (status === 'FAIL') gateMsg += `${gk}: ${msg} `;
        }
        blockMsg = [gateMsg, sBlock, stGate].filter(x => x).join(' | ');
    }

    document.getElementById('quantDecisionName').textContent = data.decision;
    document.getElementById('quantScoreSummary').innerHTML = `Score: ${data.total}/71 (${data.pct.toFixed(1)}%)${blockMsg ? `<br><span style="font-size:11px;color:rgba(255,255,255,0.7);display:block;margin-top:8px">${blockMsg}</span>` : ''}`;
    // Total bar
    const pct = data.total / 71 * 100;
    document.getElementById('quantTotalPts').textContent = `${data.total}/71`;
    const barEl = document.getElementById('quantTotalBar');
    barEl.style.width = pct + '%';
    barEl.style.background = data.code === 'FULL' ? 'var(--accent-green)' : data.code === 'HALF' ? 'var(--accent-blue)' : data.code === 'WAIT' ? 'var(--accent-yellow)' : 'var(--accent-red)';
    // Active pos banner
    const apb = document.getElementById('activePosBanner');
    if (hasEntry && state?.position?.remaining_qty > 0) {
        const pnl = state.active_tracker?.current_pnl_pct || 0;
        apb.innerHTML = `<div style="padding:10px 16px;border:1px solid rgba(52,211,153,.25);border-radius:var(--radius-sm);background:rgba(52,211,153,.05);margin-bottom:14px;font-size:13px;display:flex;gap:16px;flex-wrap:wrap">
            <span>📍 <strong>POSISI AKTIF</strong></span>
            <span>Entry: <strong>$${ep.toFixed(5)}</strong></span>
            <span>Qty: <strong>${state.position.remaining_qty.toFixed(4)}</strong></span>
            <span>P&L: <strong class="${pnl>=0?'val-pos':'val-neg'}">${pnl>=0?'+':''}${pnl.toFixed(2)}%</strong></span></div>`;
    } else { apb.innerHTML = ''; }
    // Feature scoring
    const scores = data.scores;
    let html = '';
    for (const [key, val] of Object.entries(scores)) {
        const [pts, max, raw, stars] = val;
        const fillPct = max > 0 ? (pts / max * 100) : 0;
        const dotCls = `dot-${stars}`;
        const fillColor = stars === 3 ? 'var(--accent-green)' : stars === 2 ? 'var(--accent-yellow)' : stars === 1 ? 'var(--accent-orange)' : 'var(--accent-red)';
        const unit = FEATURE_UNIT[key] || '';
        const rawFmt = typeof raw === 'number' ? (raw >= 0 ? '+' : '') + raw.toFixed(2) + unit : raw;
        
        let customName = FEATURE_LABELS[key] || key;
        let customVal = rawFmt;
        let pctx = quant.variables || {};
        
        if (key === 'ATR') {
            const lo = pctx.atr_thresholds?.score_sweet_lo?.toFixed(1) || '?';
            const hi = pctx.atr_thresholds?.score_sweet_hi?.toFixed(1) || '?';
            const h = pctx.H_atr_pct?.toFixed(2) || '?';
            customName = `ATR sweet spot EMPIRIS: ${lo}%–${hi}%`;
            customVal = `H=${h}% → skor ${stars}`;
        } else if (key === 'CVD') {
            const kval = pctx.K_cvd_norm?.toFixed(2) || '?';
            customName = `CVD_norm K=${kval}% = (CVD[-1] − CVD[-21]) / |CVD[-21]| × 100`;
            customVal = rawFmt;
        } else if (key === 'EMA50') {
            const m = pctx.M_ema50?.toFixed(2) || '?';
            customName = `vs EMA50 M=${m}%`;
            customVal = `skor ${stars} → poin ${pts}/${max}`;
        }
        
        html += `<div class="feature-row">
            <div class="feature-dot ${dotCls}"></div>
            <div class="feature-name">${customName}</div>
            <div class="feature-val">${customVal}</div>
            <div class="feature-bar-bg"><div class="feature-bar-fill" style="width:${fillPct}%;background:${fillColor}"></div></div>
            <div class="pts-label">${pts}/${max}</div></div>`;
    }
    document.getElementById('featureGrid').innerHTML = html;
    // SL/TP Levels
    const lv = data.levels;
    const isLong = activeQuantTab === 'long';
    const slStruct = lv.sl_structure;
    const slLabel = lv.sl_label || '';
    const hasStructSL = slStruct && slLabel && !slLabel.includes('fallback');
    document.getElementById('slLevels').innerHTML = `
        ${hasStructSL ? `<div class="level-pill" style="border-left:3px solid var(--accent-red)"><span><strong style="color:var(--accent-red)">SL Utama</strong> — ${slLabel}</span><span class="val-neg">$${slStruct.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_sl>=0?'+':''}${lv.dist_sl.toFixed(2)}%</span></div>` : ''}
        <div style="font-size:9px;color:var(--text-3);font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin:${hasStructSL?'8':'0'}px 0 4px">Referensi ATR</div>
        <div class="level-pill"><span>Ketat (1.0 ATR)</span><span class="val-neg">$${lv.sl_ketat.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_sl_ketat>=0?'+':''}${lv.dist_sl_ketat.toFixed(2)}%</span></div>
        <div class="level-pill"><span>Normal (1.5 ATR)</span><span class="val-neg">$${lv.sl_normal.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_sl_normal>=0?'+':''}${lv.dist_sl_normal.toFixed(2)}%</span></div>
        <div class="level-pill"><span>Lebar (2.0 ATR)</span><span class="val-neg">$${lv.sl_lebar.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_sl_lebar>=0?'+':''}${lv.dist_sl_lebar.toFixed(2)}%</span></div>`;
    const tp1Lbl = lv.tp1_label || (isLong?'+':'-')+'2.5%';
    const tp2Lbl = lv.tp2_label || (isLong?'+':'-')+'4.6%';
    const tp3Lbl = lv.tp3_label || (isLong?'+':'-')+'7.0%';
    const rrBadge = (rr) => `<span class="rr-badge ${rr>=2?'rr-good':'rr-bad'}">${rr.toFixed(1)}x</span>`;
    
    // Check if TP hits and Trailing SL active
    const tsl = quant.trailing_sl?.[activeQuantTab] || {};
    const slBadge = `<span style="font-size:10px;color:var(--accent-red);background:rgba(248,113,113,.12);padding:2px 6px;border-radius:6px;margin-left:8px;border:1px solid rgba(248,113,113,.3)">● SL</span>`;
    const bp1 = (lv.sl_structure === lv.tp1 && tsl.applicable) ? slBadge : '';
    
    document.getElementById('tpLevels').innerHTML = `
        <div class="level-pill"><span>TP1 <span style="font-size:10px;color:var(--text-3)">${tp1Lbl}</span></span><span class="val-pos">$${lv.tp1.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_tp1>=0?'+':''}${lv.dist_tp1.toFixed(2)}%</span>${lv.rr1!=null?rrBadge(lv.rr1):''}${bp1}</div>
        <div class="level-pill"><span>TP2 <span style="font-size:10px;color:var(--text-3)">${tp2Lbl}</span></span><span class="val-pos">$${lv.tp2.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_tp2>=0?'+':''}${lv.dist_tp2.toFixed(2)}%</span>${lv.rr2!=null?rrBadge(lv.rr2):''}</div>
        <div class="level-pill"><span>TP3 <span style="font-size:10px;color:var(--text-3)">${tp3Lbl}</span></span><span class="val-pos">$${lv.tp3.toFixed(5)}</span><span style="font-size:10px;color:var(--text-3);margin-left:6px">${lv.dist_tp3>=0?'+':''}${lv.dist_tp3.toFixed(2)}%</span>${lv.rr3!=null?rrBadge(lv.rr3):''}</div>`;
        
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
        if (!['StochRSI_K','StochRSI_D','Funding_Rate','Open_Interest','PDH','PDL','PWH','PWL'].includes(k)) continue;
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

    const ctxHtml = ctxItems.filter(([,v]) => v != null && v !== '—').map(([k, v]) =>
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
    } catch(e) {
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
            <div><span style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.6px">Symbol</span><br><span style="font-size:18px;font-weight:700">${meta.Symbol||'—'}</span></div>
            <div><span style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.6px">Timeframe</span><br><span style="font-size:16px;font-weight:600">${meta.Timeframe||'4H'}</span></div>
            <div><span style="font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.6px">Close Price</span><br><span style="font-size:16px;font-weight:600;font-family:var(--mono)">$${json.current_price?.toFixed(5)||'—'}</span></div>
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
            const stGate = json.variables?.stoch_gate_override || '';
            let gateMsg = '';
            for (const [gk, [status, msg]] of Object.entries(d.gate.gates)) {
                if (status === 'FAIL') gateMsg += `${gk}: ${msg} `;
            }
            blockMsg = [gateMsg, sBlock, stGate].filter(x => x).join(' | ');
        }
        
        let featureRows = '';
        for (const [key, val] of Object.entries(d.scores)) {
            const [pts, max, raw, stars] = val;
            const fillPct = max > 0 ? pts / max * 100 : 0;
            const fillColor = stars === 3 ? 'var(--accent-green)' : stars === 2 ? 'var(--accent-yellow)' : stars === 1 ? 'var(--accent-orange)' : 'var(--accent-red)';
            const unit = FEATURE_UNIT[key] || ''; const rawFmt = (raw >= 0 ? '+' : '') + raw.toFixed(2) + unit;
            
            let customName = FEATURE_LABELS[key] || key;
            let customVal = rawFmt;
            let pctx = json.variables || {};
            
            if (key === 'ATR') {
                const lo = pctx.atr_thresholds?.score_sweet_lo?.toFixed(1) || '?';
                const hi = pctx.atr_thresholds?.score_sweet_hi?.toFixed(1) || '?';
                const h = pctx.H_atr_pct?.toFixed(2) || '?';
                customName = `ATR sweet spot EMPIRIS: ${lo}%–${hi}%`;
                customVal = `H=${h}% → skor ${stars}`;
            } else if (key === 'CVD') {
                const kval = pctx.K_cvd_norm?.toFixed(2) || '?';
                customName = `CVD_norm K=${kval}% = (CVD[-1] − CVD[-21]) / |CVD[-21]| × 100`;
                customVal = rawFmt;
            } else if (key === 'EMA50') {
                const m = pctx.M_ema50?.toFixed(2) || '?';
                customName = `vs EMA50 M=${m}%`;
                customVal = `skor ${stars} → poin ${pts}/${max}`;
            }
            featureRows += `<div class="feature-row"><div class="feature-dot dot-${stars}"></div><div class="feature-name">${customName}</div><div class="feature-val">${customVal}</div><div class="feature-bar-bg"><div class="feature-bar-fill" style="width:${fillPct}%;background:${fillColor}"></div></div><div class="pts-label">${pts}/${max}</div></div>`;
        }
        // R:R Matrix for CSV
        let rrHtml = '';
        const rrm = lv.rr_matrix;
        if (rrm && rrm.length >= 3) {
            const slLbls = ['Ketat','Normal','Lebar'];
            rrHtml = '<table class="rr-matrix"><thead><tr><th>SL\\TP</th><th>TP1</th><th>TP2</th><th>TP3</th></tr></thead><tbody>';
            for (let i = 0; i < 3; i++) {
                rrHtml += `<tr><td style="text-align:left;font-size:10px;color:var(--text-2)">${slLbls[i]}</td>`;
                for (let j = 0; j < 3; j++) { const rr = rrm[i][j]||0; rrHtml += `<td class="${rr>=2?'val-pos':rr>=1?'val-warn':'val-neg'}">${rr.toFixed(1)}x</td>`; }
                rrHtml += '</tr>';
            }
            rrHtml += '</tbody></table>';
        }
        html += `<div class="glass quant-card">
            <div class="quant-decision-banner decision-${d.code}" style="margin-bottom:14px;padding:16px">
                <div class="decision-label">${tab === 'long' ? '🐂' : '🐻'} ${tab.toUpperCase()} Setup</div>
                <div class="decision-name color-${d.code}" style="font-size:22px">${d.decision}</div>
                <div class="decision-score">${d.total}/71 · ${d.pct.toFixed(1)}%${blockMsg ? `<br><span style="font-size:11px;color:rgba(255,255,255,0.7);display:block;margin-top:8px">${blockMsg}</span>` : ''}</div></div>
            <div style="margin-bottom:12px"><div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-3);margin-bottom:4px"><span>Score</span><span>${d.total}/71</span></div>
                <div style="height:6px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden"><div style="height:100%;border-radius:3px;width:${d.total/71*100}%;background:${d.code==='FULL'?'var(--accent-green)':d.code==='HALF'?'var(--accent-blue)':d.code==='WAIT'?'var(--accent-yellow)':'var(--accent-red)'}"></div></div></div>
            <div style="margin-bottom:14px">${featureRows}</div>
            <div><div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin-bottom:6px">Stop Loss</div>
                <div class="level-pill"><span>Ketat</span><span class="val-neg">$${lv.sl_ketat.toFixed(5)}</span></div>
                <div class="level-pill"><span>Normal</span><span class="val-neg">$${lv.sl_normal.toFixed(5)}</span></div>
                <div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin:10px 0 6px">Take Profit</div>
                <div class="level-pill"><span>TP1 ${tab==='long'?'+':'−'}2.5%</span><span class="val-pos">$${lv.tp1.toFixed(5)}</span></div>
                <div class="level-pill"><span>TP2 ${tab==='long'?'+':'−'}4.6%</span><span class="val-pos">$${lv.tp2.toFixed(5)}</span></div>
                <div class="level-pill"><span>TP3 ${tab==='long'?'+':'−'}7.0%</span><span class="val-pos">$${lv.tp3.toFixed(5)}</span></div>
                ${rrHtml ? `<div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin:10px 0 6px">R:R Matrix</div>${rrHtml}` : ''}</div>
            ${json.exit?.signals?.length > 0 && isActive ? `<div style="margin-top:12px;border-top:1px solid var(--glass-border);padding-top:12px"><div style="font-size:10px;color:var(--text-3);font-weight:600;text-transform:uppercase;margin-bottom:8px">Exit Signals</div>${json.exit.signals.map(([icon,name,val,thr])=>`<div class="exit-row"><span>${icon}</span><span style="flex:1;font-size:12px">${name}</span><span style="font-size:11px;color:var(--text-3)">${typeof val==='number'?val.toFixed(2):val} (${thr})</span></div>`).join('')}<div class="exit-mandate ${json.exit.hard_count>0?'mandate-exit':json.exit.warn_count>0?'mandate-watch':'mandate-hold'}">${json.exit.recommendation}</div></div>` : ''}
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
                const fVal = typeof ctx[k]==='number' ? (k === 'Funding_Rate' ? ctx[k].toFixed(6) : ctx[k].toFixed(4)) : ctx[k];
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
function changePair() { currentBackendPair = document.getElementById('pairSelect').value; fetchData(); }

/* ── PDF ──────────────────────────────────────────────────────────────────── */
function downloadPDF() {
    if (!APP_DATA) { showAlert('danger', '⚠️ Data belum dimuat'); return; }
    const btn = document.getElementById('btnPdf');
    btn.classList.add('btn-loading'); btn.textContent = '⏳ Generating...';
    const pair = (APP_DATA?.state?.user_input?.coin_pair || 'UNKNOWN').replace('/', '-');
    const now = new Date(); const pad = n => String(n).padStart(2,'0');
    const fname = `Laporan_Protokol_9.6_${pair}_${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.pdf`;
    html2pdf().set({ filename: fname, margin: [8,8,8,8], image: {type:'jpeg',quality:.95},
        html2canvas: {scale:2,useCORS:true,backgroundColor:'#0a0e1a'},
        jsPDF: {unit:'mm',format:'a3',orientation:'landscape'} })
        .from(document.getElementById('dashboardContent')).save()
        .then(() => { btn.classList.remove('btn-loading'); btn.textContent = '📄 PDF'; showAlert('success','✅ PDF berhasil diunduh!'); setTimeout(hideAlert,3000); })
        .catch(e => { btn.classList.remove('btn-loading'); btn.textContent = '📄 PDF'; showAlert('danger','❌ PDF Error: '+e.message); });
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
function fmtVol(v) { const a = Math.abs(v); if (a >= 1e6) return (v/1e6).toFixed(2)+'M'; if (a >= 1e3) return (v/1e3).toFixed(2)+'K'; return v.toFixed(2); }

/* ── TRADE ENTRIES ───────────────────────────────────────────────────────── */
async function loadTradeEntries() {
    try { const r = await fetch('/api/trade-entries'); const j = await r.json(); if (j.success) { tradeEntries = j.entries || {}; tradeSummaries = j.summaries || {}; } } catch(e) { console.error(e); }
}
function openEntryModal() {
    const modal = document.getElementById('entryModal');
    const sel = document.getElementById('entrySymbol');
    if (APP_DATA?.state?.user_input?.available_pairs) {
        sel.innerHTML = APP_DATA.state.user_input.available_pairs.map(p => `<option value="${p}" ${p === currentBackendPair ? 'selected' : ''}>${p}</option>`).join('');
    }
    prefillEntryForm(); sel.onchange = prefillEntryForm; renderEntryList(); modal.classList.add('show');
}
function prefillEntryForm() { document.getElementById('entryPrice').value = ''; const sym = document.getElementById('entrySymbol').value; document.getElementById('entryCapital').value = tradeEntries[sym]?.allocated_capital || 200; }
function closeEntryModal() { document.getElementById('entryModal').classList.remove('show'); }

async function saveEntry() {
    const sym = document.getElementById('entrySymbol').value;
    const ep = parseFloat(document.getElementById('entryPrice').value);
    const cap = parseFloat(document.getElementById('entryCapital').value);
    if (!sym) { showAlert('danger','⚠️ Pilih coin dulu'); return; }
    if (!ep || ep <= 0) { showAlert('danger','⚠️ Entry price harus > 0'); return; }
    try {
        const r = await fetch('/api/trade-entries', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ symbol:sym, entry_price:ep, allocated_capital: isNaN(cap)?200:cap }) });
        const j = await r.json();
        if (j.success) { showAlert('success', `✅ Entry #${j.summary.num_entries} added @ $${ep}`); setTimeout(hideAlert, 3000); await loadTradeEntries(); renderEntryList(); document.getElementById('entryPrice').value = ''; if (sym === currentBackendPair) fetchData(); }
        else { showAlert('danger','❌ '+j.error); }
    } catch(e) { showAlert('danger','❌ '+e.message); }
}

async function saveSell() {
    const sym = document.getElementById('sellSymbol').value;
    const sp = parseFloat(document.getElementById('sellPriceInput').value);
    const qty = parseFloat(document.getElementById('sellQtyInput').value);
    if (!sp || sp <= 0) { showAlert('danger','⚠️ Sell price harus > 0'); return; }
    if (!qty || qty <= 0) { showAlert('danger','⚠️ Quantity harus > 0'); return; }
    try {
        const r = await fetch('/api/trade-sales', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ symbol:sym, sell_price:sp, qty }) });
        const j = await r.json();
        if (j.success) { showAlert('success', `💰 Sale recorded @ $${sp} × ${qty}`); setTimeout(hideAlert, 3000); await loadTradeEntries(); renderEntryList(); document.getElementById('sellPriceInput').value = ''; document.getElementById('sellQtyInput').value = ''; if (sym === currentBackendPair) fetchData(); }
        else { showAlert('danger','❌ '+j.error); }
    } catch(e) { showAlert('danger','❌ '+e.message); }
}

async function deleteSingleEntry(sym, idx) {
    if (!confirm(`Hapus entry #${idx+1} untuk ${sym}?`)) return;
    const r = await fetch('/api/trade-entries/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym,index:idx})});
    const j = await r.json();
    if (j.success) { showAlert('success','🗑️ Entry dihapus'); await loadTradeEntries(); renderEntryList(); if(sym===currentBackendPair) fetchData(); }
}
async function deleteSaleEntry(sym, idx) {
    if (!confirm(`Hapus sale #${idx+1} untuk ${sym}?`)) return;
    const r = await fetch('/api/trade-sales/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym,index:idx})});
    const j = await r.json();
    if (j.success) { showAlert('success','🗑️ Sale dihapus'); await loadTradeEntries(); renderEntryList(); if(sym===currentBackendPair) fetchData(); }
}
async function deleteAllEntries(sym) {
    if (!confirm(`Hapus SEMUA data untuk ${sym}?`)) return;
    const r = await fetch('/api/trade-entries/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym})});
    const j = await r.json();
    if (j.success) { showAlert('success','🗑️ All cleared'); await loadTradeEntries(); renderEntryList(); if(sym===currentBackendPair) fetchData(); }
}

function switchModalTab(tab) {
    document.querySelectorAll('.modal .tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab'+tab.charAt(0).toUpperCase()+tab.slice(1))?.classList.add('active');
    document.getElementById('modalEntryForm').style.display = tab==='entry' ? 'grid' : 'none';
    document.getElementById('modalSellForm').style.display = tab==='sell' ? 'grid' : 'none';
    document.getElementById('modalHistory').style.display = tab==='history' ? 'block' : 'none';
    if (tab === 'sell' && APP_DATA) {
        const sel = document.getElementById('sellSymbol');
        sel.innerHTML = (APP_DATA.state?.user_input?.available_pairs||[]).map(p => `<option value="${p}" ${p===currentBackendPair?'selected':''}>${p}</option>`).join('');
        function upQty() { const sm = tradeSummaries[sel.value]||{}; const rq = sm.remaining_qty||0; document.getElementById('sellQtyInput').value = rq > 0 ? rq : ''; document.getElementById('sellQtyInput').placeholder = rq > 0 ? `Max: ${rq}` : 'No position'; }
        sel.onchange = upQty; upQty();
    }
}

function renderEntryList() {
    const c = document.getElementById('entryListBody');
    const syms = Object.keys(tradeEntries);
    if (!syms.length) { c.innerHTML = '<div class="entry-empty">Belum ada entry. Tambahkan via form Buy/Entry.</div>'; return; }
    let html = '';
    syms.forEach(sym => {
        const cd = tradeEntries[sym]; const entries = cd.entries||[]; const sales = cd.sales||[];
        const sm = tradeSummaries[sym]||{};
        if (!entries.length && !sales.length) return;
        html += `<div style="background:rgba(99,125,255,.06);border:1px solid rgba(99,125,255,.18);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center">
            <div><strong>${sym}</strong> — Rem: <strong>${(sm.remaining_qty||0).toFixed(4)}</strong> | P&L: <strong class="${(sm.realized_pnl||0)>=0?'val-pos':'val-neg'}">$${(sm.realized_pnl||0).toFixed(2)}</strong></div>
            <button class="btn-del-e" onclick="deleteAllEntries('${sym}')">✕ Clear</button></div>`;
        entries.forEach((e,i) => {
            html += `<div class="entry-item" style="margin-left:12px;border-left:3px solid var(--accent-green)">
                <div><div style="font-size:11px;color:var(--accent-green);font-weight:700">BUY #${i+1}</div><div style="font-size:11px;color:var(--text-3)">Qty: ${e.qty} · ${e.date||''}</div></div>
                <div style="display:flex;align-items:center;gap:10px"><span style="font-family:var(--mono);color:var(--accent-green);font-weight:600">$${e.price}</span><button class="btn-del-e" onclick="deleteSingleEntry('${sym}',${i})">✕</button></div></div>`;
        });
        sales.forEach((s,i) => {
            html += `<div class="entry-item" style="margin-left:12px;border-left:3px solid var(--accent-red)">
                <div><div style="font-size:11px;color:var(--accent-red);font-weight:700">SELL #${i+1}</div><div style="font-size:11px;color:var(--text-3)">Qty: ${s.qty} · ${s.date||''}</div></div>
                <div style="display:flex;align-items:center;gap:10px"><span style="font-family:var(--mono);color:var(--accent-red);font-weight:600">$${s.price}</span><button class="btn-del-e" onclick="deleteSaleEntry('${sym}',${i})">✕</button></div></div>`;
        });
    });
    c.innerHTML = html;
}

/* ── THEME ───────────────────────────────────────────────────────────────── */
function toggleTheme() {
    const h = document.documentElement;
    const isDark = h.getAttribute('data-theme') === 'dark';
    h.setAttribute('data-theme', isDark ? 'light' : 'dark');
    localStorage.setItem('theme', isDark ? 'light' : 'dark');
}

/* ── INIT ────────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    const t = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', t);
    document.addEventListener('click', e => { if (e.target.id === 'entryModal') closeEntryModal(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeEntryModal(); });
    fetchData();
});
