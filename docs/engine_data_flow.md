# Go Engine Data Flow

The Go Engine (`engine/`) connects to data brokers (such as Fyers via WebSocket feeds or NSE API for Bhavcopy and Scrip data) and pushes the data to PostgreSQL (`atdb`) and local WebSocket consumers.

Below is the high-level data flow diagram represented using Mermaid.js.

```mermaid
flowchart TD
    %% External Data Sources
    subgraph Sources ["External Data Sources"]
        NSE["NSE (API)"]
        FyersTBT["Fyers TBT Feed (WebSocket)"]
        FyersDS["Fyers DataSocket (WebSocket)"]
    end

    %% Engine Subcommands
    subgraph CLI ["CLI Commands (main.go)"]
        CmdScrips["runScrips()"]
        CmdBhavcopy["runBhavcopy()"]
        CmdFeed["runFeed()"]
    end

    %% Market Data Fetching (Static Data)
    NSE --> |Scrip Info| CmdScrips
    NSE --> |Daily Bhavcopy| CmdBhavcopy

    CmdScrips --> |Upsert| PG_Scrips[(nse_cm_scrips)]
    CmdBhavcopy --> |Insert| PG_Bhavcopy[(nse_cm_bhavcopy)]

    %% Live Feed Pipeline
    CmdFeed --> FeedRecorder["feed.Recorder"]

    FyersTBT --> |protobuf depth| TBTFeed["feed.TBTFeed"]
    FyersDS --> |JSON ticks & depth| DataSocketFeed["feed.DataSocketFeed"]

    TBTFeed --> FeedRecorder
    DataSocketFeed --> FeedRecorder

    %% Internal Processing inside Recorder
    subgraph EngineFeed ["Live Market Feed Engine"]
        FeedRecorder
        TBTFeed
        DataSocketFeed
        PGWriter["feed.PGWriter (Batching)"]
        Hub["feed.Hub (WebSocket Broadcaster)"]
    end

    FeedRecorder --> |WriteTick / WriteDepth| PGWriter
    PGWriter --> |pgx COPY| PG_Ticks[(nse_cm_ticks)]
    PGWriter --> |pgx COPY| PG_Depth[(nse_cm_depth)]

    FeedRecorder --> |BroadcastTick / Depth| Hub
    Hub --> |WS Messages| HonoServer["Hono Server (Node.js)"]

    %% Analytics & Signals Processing
    subgraph Analytics ["Live Feature & Screener Engines"]
        FeatureEngine["features.FeatureEngine"]
        ScreenerEngine["screeners.ScreenerEngine"]
    end

    DataSocketFeed --> |onTickCb / onDepthCb| FeatureEngine
    FeatureEngine --> |ProcessTick| ScreenerEngine
    ScreenerEngine --> |Signals| Hub
    ScreenerEngine --> |Store Signals| PG_Signals[(signals table)]
```

### Components Summary:

- **Sources:**
  - **NSE APIs**: Provide static Scrip and Bhavcopy (EOD) data.
  - **Fyers TBT & DataSocket Feeds**: Real-time websocket data for ticks and order book depths.

- **Pipeline Handlers:**
  - `feed.TBTFeed` consumes the raw protobuf feeds for tick-by-tick depth data.
  - `feed.DataSocketFeed` handles standard tick and partial depth JSON updates.
  - `feed.Recorder` manages these connections and acts as a central coordinator.

- **Storage & Processing:**
  - `feed.PGWriter` buffers the real-time structs (`DepthRow`, `TickRow`) and flushes them to PostgreSQL via `pgx.CopyFrom` to sustain high throughput.
  - **Feature & Screener Engines:** Callbacks are triggered on incoming data which recalculate live analytics and trigger strategy signals.
  - `feed.Hub` acts as an internal WebSockets broadcaster passing real-time tick, depth, and signal JSON to downstream UI/Server clients (e.g. Hono Server).
