# Plan: Real-Time Signal Alerts with Sound

**Author:** Gxozt  
**Date:** 2026-03-25  
**Status:** APPROVED — building now  
**Reviewed by:** Codex (approve), Gemx (approve)

## Problem
BUY signals fire in the Go engine but the trader has no instant notification. Need sub-second alert with sound when a signal fires, even if the dashboard tab is in background.

## Current Architecture (already built)

```
Fyers DataSocket → go-feed → FeatureEngine → Screener.ProcessTick()
                                                    ↓
                                              Signal fired
                                                    ↓
                                        ┌──── DB persist (signals table)
                                        └──── Hub.BroadcastSignal() → WebSocket :3002
                                                    ↓
                                        Hono feed-ws relay (/api/feed/ws)
                                                    ↓ ← BUG: signal messages dropped here
                                        Browser WebSocket client
```

**Key finding:** The Go Hub already broadcasts signal messages via WebSocket (`type: "signal"`). The Hono relay (`feed-ws.ts`) already proxies Hub → browser. But the relay filters by `parsed.symbol` which is `undefined` for signal messages (symbol is nested as `parsed.signal.symbol`) → signals get dropped silently.

## WS Signal Message Contract

The Go Hub broadcasts this shape:
```json
{
  "type": "signal",
  "signal": {
    "screener": "sniper",
    "signal_type": "buy",
    "symbol": "RELIANCE-EQ",
    "isin": "INE002A01018",
    "ltp": 2450.50,
    "trigger_price": 2450.50,
    "triggered_at": "2026-03-25T09:20:15+05:30",
    "dedup_key": "sniper:INE002A01018:2026-03-25",
    "percent_above": 1.25
  }
}
```

**Missing from current Go broadcast (must add):** `dedup_key`, `percent_above`

## Changes Required

### 1. Go Engine: Enrich Broadcast Payload (`engine/main.go`) — ~3 lines
- Add `dedup_key` and `percent_above` to the signal map in the `broadcastSignal` call
- `dedup_key` format: `"screenerName:ISIN:date"`

### 2. Hono Relay Fix (`server/routes/feed-ws.ts`) — ~10 lines
- In the `hubWs.on('message')` handler, detect `type: "signal"` messages
- Broadcast signal messages to ALL connected clients (no symbol filter)
- Don't touch tick/depth filtering logic

### 3. Dashboard: `useSignalAlerts` hook (`dashboard/src/hooks/use-signal-alerts.ts`) — ~80 lines
- Listen for `type: "signal"` messages on the existing WebSocket (from use-live-feed)
- **BUY signals only** — ignore ALERT/BREAKOUT
- On BUY signal:
  - Play alert sound (`/alert-buy.mp3`) via Audio API
  - Show browser Notification: "🟢 BUY: {symbol} @ ₹{ltp} — {screener}" (when tab backgrounded)
  - Sound-only when tab is focused
- **Dedup:** Track seen `dedup_key` values in a Set — skip duplicates
- **Tab dedup:** Use `BroadcastChannel` to prevent multiple tabs from alerting simultaneously
- **Throttle:** Max one sound every 2 seconds (prevent alert storm)
- **Staleness:** Check `triggered_at` is < 15 seconds old — drop stale signals
- **Alerts driven ONLY from WS** — not from polling. Polling stays for table rendering.

### 4. Alert Sound (`dashboard/public/alert-buy.mp3`) — 1 file
- Short alert chime (2-3 seconds)
- Generate via Web Audio API oscillator or use a free CC0 sound

### 5. Alert Toggle in Header (`dashboard/src/components/layout/header-toolbar.tsx`) — ~20 lines
- Bell icon button: toggles alerts on/off
- First click = request Notification permission + prime Audio (satisfies browser autoplay policy)
- Persists to localStorage (`algotrix-alerts-enabled`)
- Visual: filled bell when active, outline when off
- Tooltip: "Signal Alerts: ON/OFF"

### 6. Mount in Root Layout — ~3 lines
- Import and call `useSignalAlerts()` in the authenticated layout
- Pass enabled state from the bell toggle (or read from localStorage)

## Browser Autoplay Constraint
- Browsers block audio until user interaction
- The bell toggle click serves as the required user gesture
- First click: request Notification permission + create Audio context
- This is by design, not a bug

## Data Flow (after fix)

```
Signal fires in Go engine
    ↓ (< 1ms)
Hub.BroadcastSignal() → WS {type: "signal", signal: {..., dedup_key, percent_above}}
    ↓ (< 1ms)
Hono relay → broadcasts to ALL browser clients (no symbol filter for signals)
    ↓ (< 1ms)
Browser WS onmessage → useSignalAlerts hook
    ↓ staleness check (< 15s) → dedup check → throttle check → tab dedup
    ↓
Play sound + Show notification
```

**Total latency: < 10ms** from signal fire to sound playing.

## Files Changed
- `engine/main.go` — add dedup_key + percent_above to broadcast
- `server/routes/feed-ws.ts` — signal broadcast (no symbol filter)
- `dashboard/src/hooks/use-signal-alerts.ts` — new hook
- `dashboard/src/components/layout/header-toolbar.tsx` — bell toggle
- `dashboard/src/routes/_authenticated/route.tsx` — mount hook
- `dashboard/public/alert-buy.mp3` — sound file

## Notes
- No new npm dependencies — WebSocket, Audio, Notification, BroadcastChannel all native
- HTTPS required for Notifications — we have Cloudflare Tunnel (algotrix.gxozt.online)
- Keep polling-based table rendering separate from WS-based alert side effects
