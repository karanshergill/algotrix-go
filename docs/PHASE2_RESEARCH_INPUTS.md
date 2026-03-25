# Phase 2: Research Inputs — What We Know Before Building

**Purpose:** Distill everything from our research that's directly relevant to the regime classifier scoring engine. This doc feeds the Phase 2 plan.

---

## The Core Question Phase 2 Answers

**"What is the market doing today, and what is it likely to do tomorrow?"**

The scorer outputs:
- A **continuous 0-100 market score** across 5 dimensions
- A **regime label** (Bullish / Neutral / Bearish) derived from thresholds
- A **next-day prediction** using leading indicators

---

## The 5 Dimensions (From Unified Review + Prototyping)

### 1. Volatility
**What we know:**
- India VIX < 13 = extreme complacency → range trades work beautifully (Edge Encyclopedia)
- VIX 13-18 = normal. VIX > 20 = fear → trend-following, mean-reversion breaks
- **VIX rate of change** is more predictive than VIX level (prototype v2 finding)
- Parkinson volatility alone is insufficient — Yang-Zhang handles opening jumps better, Garman-Klass handles close-to-open (Watchlist Builder Research)
- **Pooled cross-sectional vol models beat per-stock** — train on all stocks together, add market RV as global input (Zhang et al., J. Financial Econometrics 2024)
- IB width / ATR ratio classifies day type: >0.6 = range day, <0.3 = trend day (Institutional Range Detection research)

**Data available (Phase 1):**
- India VIX daily from `nse_indices_daily` (2020-present)
- Nifty OHLCV for ATR/Parkinson computation
- Full equity OHLCV for cross-sectional vol (`nse_cm_bhavcopy`)

**Research gap:**
- No intraday VIX (only daily close). Intraday VIX change rate would be more useful.
- Multiple vol estimators (Yang-Zhang, Garman-Klass, Rogers-Satchell) not yet computed — all doable from existing OHLCV.

---

### 2. Trend
**What we know:**
- Nifty position relative to moving averages (20/50/200 EMA) is the standard trend measure
- **EMA slope** matters more than price-vs-EMA crossover
- Momentum lookback: cross-sectional momentum works on Nifty-50 with Sharpe 2.90 OOS (Kumar, SSRN 5744965, Dec 2025) — optimal formation period 2-12 months
- **Recency weighting** is critical — flat 30-day averages miss regime changes entirely (CUPID lesson: 81M→6.8M volume over 7 days, 30-day avg blind)
- Breadth delta (A/D ratio change) predicts regime shifts 1-2 days ahead (prototype v2 finding)
- **Score momentum** (3-day delta in composite score) showed modest predictive value in prototype

**Data available:**
- Nifty 50 OHLCV from `nse_indices_daily`
- Advances/Declines/Unchanged from `nse_indices_daily`
- Full equity OHLCV for breadth computation

**Research gap:**
- No regime-aware lookback yet. HMM boundary detection would reset lookback at regime changes (Watchlist Builder Research #6).

---

### 3. Participation (Breadth)
**What we know:**
- Advance/Decline ratio is the standard breadth measure
- **% of stocks above their 20-day EMA** is more robust than simple A/D (range: typically 20-80%)
- **Delivery %** is India-exclusive — high delivery on up day = institutional accumulation, low delivery = speculative churn (General Research #1, Edge Encyclopedia)
- Volume trend (expanding vs contracting over 5d vs 20d) differentiates real breakouts from fakeouts
- **Turnover determines momentum vs reversal** — high turnover stocks continue, low turnover stocks reverse (Medhat & Schmeling, RFS 2022). This is one of the most important findings across all our research.
- Closing auction volume as % of total day volume — >15% = heavy institutional activity (Edge Encyclopedia)

**Data available:**
- A/D/Unchanged from `nse_indices_daily`
- Full equity OHLCV + volume + traded_value from `nse_cm_bhavcopy`
- Delivery % is in the extended bhavcopy but **NOT currently in our table schema** (we have OHLCV + volume + traded_value + num_trades, not delivery_qty)

**Research gap:**
- Delivery % data needs to be added to the pipeline (separate NSE endpoint for delivery bhavcopy)
- Closing auction volume not separately available from daily bhavcopy (need intraday data)

---

### 4. Sentiment
**What we know:**
- **Put-Call Ratio (PCR):** >1.2 = too many puts = contrarian bullish. <0.7 = too many calls = contrarian bearish (Edge Encyclopedia)
- **Options OI distribution** defines ranges on expiry weeks — put walls = support, call walls = resistance
- **Max Pain** = strike where maximum option buyers lose. Market gravitates toward it on expiry (Edge Encyclopedia)
- F&O OI build-up patterns: Long build-up (price up + OI up) = conviction. Short covering (price up + OI down) = temporary (Edge Encyclopedia)
- **Client category-wise OI** — FII heavily short + retail heavily long = market likely drops (smart vs dumb money divergence)
- **MWPL** approaching stocks = volatility explosion incoming
- Retail F&O activity: 93% of individual F&O traders lose money (SEBI study). Retail piling into calls = top forming.
- **Option chain skew** (put IV vs call IV) precedes moves by 1-3 days

**Data available:**
- F&O bhavcopy from `nse_fo_bhavcopy` (72M+ rows, all contracts, OI, settlement prices)
- FII/DII participant OI from `nse_fii_dii_participant` (FII/DII/Client/Pro breakdown for futures + options)
- GIFT Nifty derivatives from `nseix_settlement_prices` and `nseix_combined_oi`

**What we can compute from existing data:**
- PCR (from F&O bhavcopy — sum put OI / sum call OI for Nifty)
- Max Pain (from F&O bhavcopy — compute pain at each strike)
- OI build-up classification (compare today OI vs yesterday + price direction)
- FII vs retail positioning (from participant data)
- Nifty futures basis (spot vs nearest future from bhavcopy)

**Research gap:**
- All the derivative metrics above CAN be computed from Phase 1 data but require non-trivial SQL/code
- Intraday OI changes not available (only end-of-day snapshots)

---

### 5. Institutional Flow
**What we know:**
- **FII/DII flows = #1 leading indicator** (practitioner consensus, Edge Encyclopedia)
- FII net buying > ₹2000Cr = bullish tailwind. FII selling > ₹3000Cr = market pressure
- FII flow direction predicts next-day Nifty direction ~60% of the time (Edge Encyclopedia)
- **FII/DII flow delta** (change in flow magnitude) is more predictive than absolute flow (prototype insight)
- Sector-wise FII data also available (not yet collected)
- **GIFT Nifty overnight settlement** = direct pre-market signal for next-day direction (our nseix data)
- **Global cues overnight:** S&P 500, DXY, US 10Y yield — reflect overnight risk-on/off. Available pre-market (our `global_market_daily` data)
- Crude-INR-Bond correlation chain: Crude up → INR weakens → bond yields up → bank stocks down (0-24h lag)
- US Fed hawkish = FII sell India, dovish = FII buy (macro overlay)

**Data available:**
- FII/DII participant data from `nse_fii_dii_participant` (since Jan 2020)
- GIFT Nifty settlement prices from `nseix_settlement_prices` (since Jul 2023)
- GIFT Nifty OI from `nseix_combined_oi` (since Jul 2023)
- S&P 500, DXY, US 10Y from `global_market_daily` (since Jan 2020)

**What we can compute:**
- FII net position (long - short for futures, from participant data)
- FII net change day-over-day
- GIFT Nifty overnight gap (compare IX settlement to NSE close)
- S&P 500 overnight return
- DXY and US 10Y overnight change
- Combined "global risk" composite from all three global cues

**Research gap:**
- Sector-wise FII flows not collected
- Crude oil and USD/INR not in our tables (would need additional yfinance symbols)
- GIFT Nifty overnight gap computation needs careful handling (IX settlement time vs NSE close time)

---

## Leading vs Coincident vs Lagging — What Prototype Proved

### Prototype v1 (coincident indicators only):
- 7 features: VIX level, Nifty return, breadth %, EMA position, Parkinson vol, range efficiency, momentum
- **Result: Pearson r = -0.008 (ZERO predictive power)**
- These describe today's regime but say nothing about tomorrow

### Prototype v2 (added 6 leading indicators):
- Added: overnight gap, volume surge, breadth momentum (5d Δ), VIX rate of change (5d), score momentum (3d Δ), mean-reversion contrarian
- **Result: Pearson r = +0.027, bullish hit rate 59%, p=0.13**
- Improvement is directional but not yet statistically significant
- Missing the biggest leading indicators: FII/DII flows and GIFT Nifty overnight

### What this tells us for Phase 2:
1. **Coincident-only scoring is useful for labeling, useless for prediction**
2. **Leading indicators are where predictive power lives**
3. **FII/DII and GIFT Nifty (not yet tested) are expected to add the most value** based on practitioner consensus
4. The scoring engine needs TWO outputs: (a) today's regime label (coincident), (b) tomorrow's regime prediction (leading)

---

## Ground Truth — How to Know If Regimes Are Right

This is the hardest part. We need to objectively label what ACTUALLY happened each day to validate our predictions.

**Proposed ground truth definition (from Ricky's 9:28 PM discussion):**
- Look at actual Nifty return + breadth + volatility that day
- Bullish: Nifty > +0.3%, breadth > 55%, contained volatility
- Bearish: Nifty < -0.3%, breadth < 45%, or VIX spike > 10%
- Neutral: everything else

**From research:**
- HMM-based labeling (hmmlearn) can automatically segment history into regimes from returns + vol (5+ repos available)
- Market Profile day-type classification (Normal, Trend, Neutral, Double-Dist) is another ground truth source (Institutional Range Detection research)
- The 80% rule: if market opens outside yesterday's Value Area and re-enters → 80% probability it traverses the full VA

**Key decision needed:** Do we define ground truth manually (threshold-based) or learn it (HMM)? Threshold-based is simpler and interpretable. HMM finds natural clusters but may produce regimes that don't map to trading decisions.

---

## Relevant Patterns from Edge Encyclopedia

### Calendar/Seasonal Effects (regime modifiers):
- **Day-of-week:** Monday slightly negative, Wednesday strongest, Friday compression
- **Month-of-year:** Nov-Dec-Jan strongest, May-Jun weakest, March tax-loss/rally
- **Turn-of-month:** SIP flows (₹29,000Cr) concentrated in first 5 trading days
- **F&O expiry week:** Monday = positioning, Wednesday = max pain pull, Thursday = pin risk
- **Budget day (Feb 1):** 3-5x normal range

### Structural forced flows (predictable regime impacts):
- MSCI/FTSE rebalance = billions in forced FII flows (quarterly, pre-announced)
- RBI policy dates (6/year) = Bank Nifty 1-3% moves
- Margin changes (announced Friday evenings) = forced position adjustment Monday
- NFO deployment windows = predictable buying in specific stocks

---

## What Phase 2 Should NOT Try to Do

Based on research complexity and our current data:

1. ❌ **Intraday regime adaptation** — that's Layer 3/4, needs tick data pipeline
2. ❌ **Per-stock regime classification** — Phase 2 is market-wide regime only
3. ❌ **Options-based regime (PCR, max pain, OI walls)** — important but complex to compute correctly; defer to Phase 2.5 or 3
4. ❌ **Hindi news sentiment** — no corpus exists, very hard
5. ❌ **ML-based regime prediction (HMM/XGBoost)** — start with simple scoring, prove it works, THEN add ML
6. ❌ **Sector-level regime** — market-wide first, sector-level later

---

## What Phase 2 SHOULD Build (Research Consensus)

### Scoring Engine (coincident — "what is the market doing today"):
1. **Volatility dimension:** VIX level + VIX percentile rank + multiple vol estimators (Parkinson + Yang-Zhang)
2. **Trend dimension:** Nifty vs EMAs + EMA slope + Nifty return (recency-weighted)
3. **Participation dimension:** A/D ratio + % stocks above EMA20 + volume trend (expanding/contracting)
4. **Sentiment dimension:** Nifty futures basis + FII vs retail positioning (from participant OI)
5. **Institutional flow dimension:** FII net position + FII flow delta

### Prediction Layer (leading — "what is the market likely to do tomorrow"):
6. **GIFT Nifty overnight gap** (from nseix settlement vs NSE close)
7. **Global cues overnight** (S&P 500, DXY, US 10Y returns)
8. **FII/DII flow delta** (today's change in institutional positioning)
9. **VIX rate of change** (5-day)
10. **Breadth momentum** (5-day Δ in A/D or % above EMA20)
11. **Score momentum** (3-day Δ in composite score — mean-reversion at extremes)

### Validation:
12. **Ground truth labeling** — threshold-based from actual Nifty performance
13. **Historical backtest** — run scorer on 1,538 days, compare predictions vs outcomes
14. **Hit rate + directional accuracy** as primary metrics (not correlation — too noisy)

---

## Data Gaps to Fill Before or During Phase 2

| Gap | Priority | Effort | Source |
|-----|----------|--------|--------|
| Crude oil (CL=F) + USD/INR (USDINR=X) in `global_market_daily` | Medium | 30 min (add to yfinance script) | Yahoo Finance |
| Delivery % in `nse_cm_bhavcopy` | Medium | 1-2 days (separate NSE endpoint) | NSE delivery bhavcopy |
| GIFT Nifty overnight gap computation | High | 2-3 hours (SQL/Go) | Already in `nseix_settlement_prices` |
| Multiple vol estimators | Low | Few hours (pure math) | Already in `nse_cm_bhavcopy` |

---

*This document captures what we know. The Phase 2 build plan should reference it for every design decision.*

*Written: March 22, 2026 | Author: Gxozt*
