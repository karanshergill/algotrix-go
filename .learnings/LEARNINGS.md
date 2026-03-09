
## 2026-03-23: Squeeze Detection Too Simplistic — False Positives

**Problem:** Squeeze plugin flagged Reliance Feb 20-Mar 2 as a 7-day squeeze, but chart shows clear downtrend (₹1420→₹1320) with big red candles — not a squeeze.

**Root cause:** `is_compressed = bbw_percentile <= 20 and adx < 25` is too naive. Caught post-selloff band tightening as "squeeze" when it was trend exhaustion.

**Missing for v2:**
- Absolute BBW threshold (not just percentile)
- Price range check (tight candles, not big directional moves)
- Direction filter (5%+ move in lookback = not a squeeze)
- Volume contraction check

**Lesson:** Statistical definition ≠ market structure. Validate outputs against charts, not just data sanity.
