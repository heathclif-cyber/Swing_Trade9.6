# UI Dashboard — Redundant Data Display Analysis

## Data Flow Overview

The dashboard receives data from [`protocol_96_ui.py`](protocol_96_ui.py:775) endpoint `/api/data` which returns a single JSON payload with these top-level keys:

| Key | Source | Purpose |
|-----|--------|---------|
| `raw_data` | `get_klines_df()` | OHLCV tables for 15m/1h/4h/1d/1w |
| `computed` | Derived from raw + algo_scoring | Indicators, liquidity borders, SMT, battle plan |
| `state` | BotState + trade entries | Status strip, position tracking |
| `feature_cols` | `FEATURE_COLS` constant | 85 feature column names |
| `feature_live` | `calculate_features_realtime()` | Live values for all 85 features |
| `feature_quality` | Audit of feature_live | Missing/NaN tracking |
| `shap_ranking` | `models/shap_ranking.json` | Top 15 SHAP features with live values |
| `quant_analysis` | `algo_scoring.calculate_71point_score()` | Full quant results incl. `variables` dict |

---

## Dashboard Sections & Data Points

### 1. Status Strip (9 cards)
**Template:** [`templates/dashboard.html`](templates/dashboard.html:135-190)
**JS:** [`static/dashboard.js`](static/dashboard.js:56-93)

| Card | Data Source | Variable |
|------|-------------|----------|
| Coin Pair | `state.user_input.coin_pair` | — |
| Current Price | `state.active_tracker.current_price` | — |
| Unrealized P&L (%) | `state.active_tracker.current_pnl_pct` | — |
| Entry Price | `state.user_input.entry_price` | — |
| Active Stop Loss | `state.active_tracker.active_sl` | — |
| Remaining Qty | `state.position.remaining_qty` | — |
| Floating P&L ($) | `state.active_tracker.floating_pnl_usd` | — |
| Realized P&L ($) | `state.position.realized_pnl` | — |
| Trade Status | `state.user_input.status` | — |

### 2. ML Analyst Section
**Template:** [`templates/dashboard.html`](templates/dashboard.html:193-277)
**JS:** [`static/dashboard.js`](static/dashboard.js:474-705)

#### 2a. Decision Banner
- ML Decision (LONG/SHORT/FLAT)
- Confidence percentage + bar
- Active position banner (Entry, Qty, P&L%)

#### 2b. ML Feature Inputs (9 items)
**JS:** [`static/dashboard.js`](static/dashboard.js:533-543)

| Label | Variable | Source |
|-------|----------|--------|
| RSI 6 Momentum | `quant.variables.O_rsi` | algo_scoring |
| Volatility ATR | `quant.variables.H_atr_pct` | algo_scoring |
| Open Interest Norm | `quant.variables.C_oi_norm` | algo_scoring |
| Volume Norm | `quant.variables.F_vol_norm` | algo_scoring |
| CVD Norm | `quant.variables.K_cvd_norm` | algo_scoring |
| Taker Buy | `quant.variables.G_taker_buy` | algo_scoring |
| Dist EMA 21 | `quant.variables.L_ema21` | algo_scoring |
| Dist EMA 50 | `quant.variables.M_ema50` | algo_scoring |
| Dist EMA 200 | `quant.variables.N_ema200` | algo_scoring |

#### 2c. Model Probabilities
- LONG / FLAT / SHORT probabilities from `quant.long.ml_proba`

#### 2d. Top SHAP Drivers
- Top 5 SHAP features from `quant.variables.shap_top_features`

#### 2e. Risk Management
- SL Levels (Structural, Ketat 1.0 ATR, Normal 1.5 ATR, Lebar 2.0 ATR)
- TP Targets (TP1, TP2, TP3)
- R:R Matrix (3×3 grid)
- Exit Signal Monitor

#### 2f. Analyst Narrative
- Kondisi Pasar, Keputusan Rasional, Skenario

#### 2g. Live Market Variables (13+ items)
**JS:** [`static/dashboard.js`](static/dashboard.js:672-703)

| Item | Variable | Source |
|------|----------|--------|
| Session | `quant.variables.session` | algo_scoring |
| RSI 6 | `quant.variables.O_rsi` | algo_scoring |
| ATR % | `quant.variables.H_atr_pct` | algo_scoring |
| OI Norm | `quant.variables.C_oi_norm` | algo_scoring |
| Vol Norm | `quant.variables.F_vol_norm` | algo_scoring |
| CVD Norm | `quant.variables.K_cvd_norm` | algo_scoring |
| CVD Bull Div | `quant.variables.cvd_div_bull` | algo_scoring |
| CVD Bear Div | `quant.variables.cvd_div_bear` | algo_scoring |
| Altcoin Mode | `quant.variables.is_altcoin` | algo_scoring |
| StochRSI_K | `quant.market_context.StochRSI_K` | enrichment |
| StochRSI_D | `quant.market_context.StochRSI_D` | enrichment |
| Funding Rate | `quant.market_context.Funding_Rate` | enrichment |
| Open Interest | `quant.market_context.Open_Interest` | enrichment |
| PDH | `quant.market_context.PDH` | enrichment |
| PDL | `quant.market_context.PDL` | enrichment |
| PWH | `quant.market_context.PWH` | enrichment |
| PWL | `quant.market_context.PWL` | enrichment |
| Buy_Liq [CSV] | `quant.variables.buy_liq_val` | algo_scoring |
| Dyn_Buy_Liq 20 | `quant.variables.dyn_buy_liq` | algo_scoring |
| EMA200 Slope H4 | `quant.variables.macro_slope` | algo_scoring |
| StochGate | `quant.variables.stoch_k/d` | algo_scoring |

### 3. Price Performance Monitor
**Template:** [`templates/dashboard.html`](templates/dashboard.html:280-410)
- Chart with signal markers
- 8 stat cards: Valid Entry, LONG count, SHORT count, Weak count, Win Rate, Total PnL, TP Hits, SL Hits

### 4. Kill Switch + Market Structure
**Template:** [`templates/dashboard.html`](templates/dashboard.html:414-506)
**JS:** [`static/dashboard.js`](static/dashboard.js:447-468)

#### 4a. Liquidity Borders
| Item | Source |
|------|--------|
| PDH | `computed.liquidity_borders.PDH` |
| PDL | `computed.liquidity_borders.PDL` |
| PWH | `computed.liquidity_borders.PWH` |
| PWL | `computed.liquidity_borders.PWL` |

#### 4b. SMT Divergence
| Item | Source |
|------|--------|
| BTC 12h | `computed.smt_divergence.btc_trend_12h` |
| Coin 12h | `computed.smt_divergence.coin_trend_12h` |
| Bearish SMT? | `computed.smt_divergence.bearish_smt` |

#### 4c. OI Momentum
| Item | Source |
|------|--------|
| OI Delta % | `computed.oi_delta_pct` |

#### 4d. Tactical Compass
**JS:** [`static/dashboard.js`](static/dashboard.js:410-444)
Reads from `computed.indicators_4h` last row:

| Parameter | Source Column |
|-----------|---------------|
| Price vs EMA 21 | `last.close`, `last.ema_21` |
| Price vs EMA 50 | `last.close`, `last.ema_50` |
| Price vs EMA 200 | `last.close`, `last.ema_200` |
| RSI_6 | `last.rsi_6` |
| Volume Bias | `last.buy_vol`, `last.sell_vol` |
| SMT (BTC vs Coin) | `computed.smt_divergence` |

### 5. ML Signals Intelligence — SHAP Top 15
**Template:** [`templates/dashboard.html`](templates/dashboard.html:509-546)
**JS:** [`static/dashboard.js`](static/dashboard.js:107-159)
- 15 rows from `shap_ranking.top15[]`
- Columns: Rank, Feature Name, SHAP Importance (bar), Live Value, Status, Category
- Live values sourced from `feature_live` dict

### 6. Raw OHLCV Data
**Template:** [`templates/dashboard.html`](templates/dashboard.html:549-637)
**JS:** [`static/dashboard.js`](static/dashboard.js:374-388)
- Multi-pair (coin/btc), multi-timeframe (15m/1h/4h/1d/1w)
- Columns: Time, Open, High, Low, Close, Total Vol, Buy Vol, Sell Vol, Delta

### 7. Indicator Table
**Template:** [`templates/dashboard.html`](templates/dashboard.html:640-719)
**JS:** [`static/dashboard.js`](static/dashboard.js:391-407)
- Timeframes: H1, H4
- Columns: Time, Close, EMA 7, EMA 21, EMA 50, EMA 200, RSI 6, StochK, StochD, Buy Vol, Sell Vol, Delta

### 8. ML Feature Columns (85 features)
**Template:** [`templates/dashboard.html`](templates/dashboard.html:722-793)
**JS:** [`static/dashboard.js`](static/dashboard.js:236-318)
- All 85 features with live values from `feature_live` dict
- Filterable by: All, Smart Money, Structure, Derived
- Status badges: OK / MISSING / NaN

---

## ⚠️ REDUNDANCY FINDINGS

### REDUNDANCY #1: "ML Feature Inputs" vs "Live Market Variables"
**Severity: HIGH** — Same data, different labels, shown in the same section.

Both are rendered inside the ML Analyst section (`renderQuantAnalysis`). The "ML Feature Inputs" block (lines 533-553) and "Live Market Variables" grid (lines 672-703) share these **identical variables**:

| Variable | ML Feature Inputs Label | Live Market Variables Label |
|----------|------------------------|---------------------------|
| `O_rsi` | RSI 6 Momentum | RSI 6 |
| `H_atr_pct` | Volatility ATR | ATR % |
| `C_oi_norm` | Open Interest Norm | OI Norm |
| `F_vol_norm` | Volume Norm | Vol Norm |
| `K_cvd_norm` | CVD Norm | CVD Norm |

**5 out of 9 ML Feature Inputs are duplicated** in the Live Market Variables grid directly below.

### REDUNDANCY #2: "Live Market Variables" PDH/PDL/PWH/PWL vs "Liquidity Borders" Panel
**Severity: MEDIUM** — Same data, different sections.

The "Live Market Variables" grid (in ML Analyst) shows PDH, PDL, PWH, PWL from `quant.market_context`. The "Liquidity Borders" panel (in Kill Switch section) shows the same 4 values from `computed.liquidity_borders`.

Both read from the same underlying data (daily/weekly high/low), just accessed via different JSON paths.

### REDUNDANCY #3: "Live Market Variables" StochRSI_K/D vs "Indicator Table" StochK/StochD
**Severity: MEDIUM** — Same data, different sections.

The "Live Market Variables" grid shows StochRSI_K and StochRSI_D. The "Indicator Table" (H1/H4) also shows StochK and StochD columns. These are the same values.

### REDUNDANCY #4: "Live Market Variables" Funding Rate / Open Interest vs "OI Momentum" Panel
**Severity: LOW** — Related but different presentation.

"Live Market Variables" shows raw Funding Rate and Open Interest values. The "OI Momentum" panel shows OI Delta % (derived). These are complementary, not identical, but the raw OI value appears in both places.

### REDUNDANCY #5: Tactical Compass EMA comparisons vs Indicator Table
**Severity: MEDIUM** — Same underlying data, different presentation.

The Tactical Compass shows "Price vs EMA 21/50/200" comparisons. The Indicator Table shows the actual EMA 21/50/200 values. A user can derive the comparison from the table, but the compass adds directional verdict (bull/bear/neutral).

### REDUNDANCY #6: "SHAP Top 15" (ML Signals Intelligence) vs "ML Feature Columns" (85 features)
**Severity: MEDIUM** — Overlapping data.

The SHAP Top 15 section shows 15 features with their SHAP importance and live values. The ML Feature Columns section shows all 85 features with live values. The 15 SHAP features are a subset of the 85, so their live values appear in **both** sections.

Additionally, the "Top SHAP Drivers" inside the ML Analyst section (from `quant.variables.shap_top_features`, typically 5 items) is a subset of the SHAP Top 15 section. So SHAP data appears in **three** places:
1. ML Analyst → Top SHAP Drivers (5 items)
2. ML Signals Intelligence → SHAP Top 15 (15 items)
3. ML Feature Columns → All 85 features (includes the 15)

### REDUNDANCY #7: Status Strip "Unrealized P&L (%)" vs "Floating P&L ($)"
**Severity: LOW** — Same metric, different units.

Card 3 shows `current_pnl_pct` (percentage). Card 7 shows `floating_pnl_usd` (dollar value). These are the same P&L expressed differently. The Active Position Banner (inside ML Analyst) also shows P&L%.

### REDUNDANCY #8: "Live Market Variables" RSI 6 vs Tactical Compass RSI_6
**Severity: LOW** — Same value, different sections.

RSI 6 appears in:
1. ML Feature Inputs (as "RSI 6 Momentum")
2. Live Market Variables (as "RSI 6")
3. Tactical Compass (as "RSI_6" row)
4. Indicator Table (as RSI 6 column)

### REDUNDANCY #9: "Live Market Variables" EMA200 Slope vs Tactical Compass "Price vs EMA 200"
**Severity: LOW** — Related but different.

EMA200 Slope H4 (from `macro_slope`) shows the slope direction. Tactical Compass shows whether price is above/below EMA 200. These are related but not identical.

---

## Summary Table

| # | Redundancy | Sections Involved | Variables | Severity |
|---|-----------|-------------------|-----------|----------|
| 1 | ML Feature Inputs ↔ Live Market Variables | ML Analyst (same section) | RSI, ATR%, OI Norm, Vol Norm, CVD Norm | **HIGH** |
| 2 | PDH/PDL/PWH/PWL | Live Market Variables ↔ Liquidity Borders | PDH, PDL, PWH, PWL | MEDIUM |
| 3 | StochRSI_K/D | Live Market Variables ↔ Indicator Table | StochRSI_K, StochRSI_D | MEDIUM |
| 4 | Funding Rate / OI | Live Market Variables ↔ OI Momentum | Funding_Rate, Open_Interest | LOW |
| 5 | EMA comparisons | Tactical Compass ↔ Indicator Table | EMA 21/50/200 | MEDIUM |
| 6 | SHAP live values | SHAP Top 15 ↔ ML Feature Columns ↔ ML Analyst | 15 feature live values | MEDIUM |
| 7 | P&L (%) vs P&L ($) | Status Strip cards 3 & 7 | current_pnl_pct, floating_pnl_usd | LOW |
| 8 | RSI 6 | ML Feature Inputs + Live Market Variables + Tactical Compass + Indicator Table | O_rsi / rsi_6 | LOW |
| 9 | EMA200 Slope | Live Market Variables ↔ Tactical Compass | macro_slope vs close vs ema_200 | LOW |

---

## Recommended Actions

### 1. Remove "Live Market Variables" grid (HIGH priority)
The "Live Market Variables" section at [`templates/dashboard.html`](templates/dashboard.html:270-274) duplicates 5 of 9 ML Feature Inputs. Instead of showing a separate grid, the unique items from Live Market Variables (CVD Bull/Bear Div, Altcoin Mode, StochRSI, Funding Rate, OI, PDH/PDL/PWH/PWL, Buy_Liq, Dyn_Buy_Liq, EMA200 Slope, StochGate) should be **merged into the ML Feature Inputs grid** or shown as an expandable "Advanced Variables" section.

### 2. Remove PDH/PDL/PWH/PWL from Live Market Variables (MEDIUM priority)
These 4 values are already shown in the dedicated "Liquidity Borders" panel in the Kill Switch section. Remove them from the Live Market Variables grid.

### 3. Remove StochRSI_K/D from Live Market Variables (MEDIUM priority)
These are already visible in the Indicator Table. Remove from Live Market Variables.

### 4. Consolidate SHAP display (MEDIUM priority)
The "Top SHAP Drivers" inside ML Analyst (5 items) is a subset of "SHAP Top 15" (15 items). Consider removing the inline SHAP drivers from ML Analyst and relying solely on the dedicated SHAP Top 15 section. Or keep the inline version but note it's a subset.

### 5. Remove redundant RSI 6 from Live Market Variables (LOW priority)
RSI 6 already appears in ML Feature Inputs. Remove the duplicate entry.

### 6. Consider merging P&L display (LOW priority)
Combine Unrealized P&L (%) and Floating P&L ($) into a single card showing both values, freeing up a status strip slot.
