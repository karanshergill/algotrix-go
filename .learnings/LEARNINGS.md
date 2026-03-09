
## 2026-03-23: Squeeze Detection Too Simplistic — False Positives

**Problem:** Squeeze plugin flagged Reliance Feb 20-Mar 2 as a 7-day squeeze, but chart shows clear downtrend (₹1420→₹1320) with big red candles — not a squeeze.

**Root cause:** `is_compressed = bbw_percentile <= 20 and adx < 25` is too naive. Caught post-selloff band tightening as "squeeze" when it was trend exhaustion.

**Missing for v2:**
- Absolute BBW threshold (not just percentile)
- Price range check (tight candles, not big directional moves)
- Direction filter (5%+ move in lookback = not a squeeze)
- Volume contraction check

**Lesson:** Statistical definition ≠ market structure. Validate outputs against charts, not just data sanity.

## 2026-03-23: Baseline Plugins Need Qualifying Criteria

**Problem:** Plugins compute blindly for every ISIN with data. No minimum data requirements. A stock with 3 days of history gets ATR(14) computed — producing garbage. Junk baselines can make illiquid stocks appear "valid" to the feed filter chain.

**Fix needed:** Per-plugin qualifying criteria in config (min_trading_days, min_candles, min_overlapping_days). Stocks that don't qualify → skip computation, flag as `insufficient_data`. Filter chain only considers stocks with valid baselines.

**Key thresholds to implement:**
- volume_profile: min 10 trading days, min 100 avg daily candles
- intraday_volume: min 15 trading days
- atr_volatility: min 20 trading days (ATR(14) needs runway)
- correlations: min 20 overlapping days
- squeeze: min 25 trading days (BB(20) needs 20+)
- spread_estimate: min 10 days, min 50 candles/day
- liquidity_tier: min 10 trading days
- autocorrelation: min 200 5m bars

**Architecture impact:** This is the missing link between baselines → feed filtering. Baselines define universe quality, feed subscribes to what passes. Without qualifying criteria, the whole chain is built on potentially garbage data.

## 2026-03-23: Fyers TBT Uses Protobuf, Not JSON — Go SDK Is Wrong

**Problem:** Fyers Go SDK's `FyersTbtSocket` uses JSON `handleMessage()` for TBT, but TBT actually sends binary protobuf. SDK connected fine but never decoded any messages. Zero callbacks fired.

**Root cause:** Go SDK v1.1.0 TBT implementation is incorrect/outdated. The real TBT protocol:
- Subscription: JSON `{"type":1, "data":{"subs":1, "symbols":[...], "mode":"depth", "channel":"1"}}`
- Channel resume: JSON `{"type":2, "data":{"resumeChannels":["1"], "pauseChannels":[]}}`
- Responses: **Binary protobuf** — must decode with `msg.proto` (SocketMessage → MarketFeed → Depth → MarketLevel)
- Prices are in **paisa** (divide by 100)
- Levels have a `num` field for position (0-49)
- Channel must be **string** `"1"`, not int `1`

**Fix:** Bypassed SDK entirely. Raw WebSocket with `gorilla/websocket`, Authorization header, protobuf decode with generated Go code from `msg.proto` (sourced from marketcalls/fyers-websockets repo).

**Also confirmed:** TBT works for NSECM equities (not just NFO). A random blog post from Feb 2025 said NFO-only — wrong. Tested with RELIANCE-EQ and HDFCBANK-EQ, got 50 bid + 50 ask levels.

**Symbols come as token IDs** (e.g., `10100000002885` = Reliance). Need mapping back to readable tickers.

**Lesson:** Don't trust third-party blog posts over actual testing. Don't trust SDK implementations without verifying against real wire protocol. READ THE DOCS, then TEST.

## 2026-03-09: Feed System — All 8 Review Fixes Verified

**All fixes from Gemini review implemented and tested:**
1. ✅ Batch ILP flusher (`feed/writer.go`) — single writer goroutine, flushes every 100ms
2. ✅ TBT reconnection — exponential backoff, configurable max attempts
3. ✅ Thread-safe writes — channel-based single writer, no concurrent LineSender access
4. ✅ Token → symbol mapping — calls Fyers `symbol-token` API at startup, resolves before QuestDB writes
5. ✅ Goroutine leak fix — `connDone` channel per connection, both readLoop/pingLoop exit together
6. ✅ No reflection in hot path — exhaustive type switch
7. ✅ Configurable depth levels — `max_depth_levels` + `best_bid/ask` top-level columns
8. ✅ Response body leak fixed

**Post-market test confirmed:** Token mapping works (3 symbols → 6 entries), readable symbols in logs, 50-level depth snapshots received even after close, graceful shutdown clean.
