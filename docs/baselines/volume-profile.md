# Volume Profile Baseline Plugin

## What is a Volume Profile?

A volume profile shows **how much volume traded at each price level** during a session. Unlike a regular volume bar (which shows total volume per time period), a volume profile maps volume against **price** — answering the question: "Where did the market spend the most time and effort?"

## Core Concept — Buckets

The day's traded price range is divided into equal-width slices called **buckets**. Each bucket represents a small price range. Volume from every candle is distributed across the buckets that candle's price range touched.

### Example

RELIANCE trades between ₹1,200 and ₹1,210 today. With ₹1 bucket width:

```
₹1,200–1,201  →  bucket 0
₹1,201–1,202  →  bucket 1
₹1,202–1,203  →  bucket 2
₹1,203–1,204  →  bucket 3
₹1,204–1,205  →  bucket 4
₹1,205–1,206  →  bucket 5
₹1,206–1,207  →  bucket 6
₹1,207–1,208  →  bucket 7
₹1,208–1,209  →  bucket 8
₹1,209–1,210  →  bucket 9
```

Throughout the day, thousands of 5-second candles come in. Each candle has a high, low, and volume. We distribute that candle's volume across whichever buckets its price range touched.

**Example candle:** high=₹1,204, low=₹1,202, volume=10,000
- Range spans buckets 2, 3, 4
- Each bucket gets ~3,333 volume (proportional to overlap)

**Another candle:** high=₹1,203, low=₹1,202, volume=50,000
- Range spans buckets 2 and 3
- Each gets ~25,000

After processing all candles for the day:

```
₹1,200–1,201  ████                          120,000
₹1,201–1,202  ██████                        180,000
₹1,202–1,203  ████████████████████          600,000  ← most volume
₹1,203–1,204  ███████████████████           570,000
₹1,204–1,205  ████████████                  350,000
₹1,205–1,206  ██                             60,000  ← barely any volume
₹1,206–1,207  ███████████████               320,000
₹1,207–1,208  █████████████████             400,000
₹1,208–1,209  ███████                       210,000
₹1,209–1,210  ███                            90,000
```

## Key Metrics

### POC (Point of Control)
The bucket with the **highest volume**. This is the price where the market agreed most — the "fair price" for the session. In the example above: ₹1,202–1,203.

### Value Area (VA)
The cluster of contiguous buckets around POC that contain a specified percentage (typically 70%) of total volume. This is the "fair range" — where the majority of trading activity occurred.

- **VAH (Value Area High):** Upper boundary of the value area
- **VAL (Value Area Low):** Lower boundary of the value area

The value area is computed by starting at POC and expanding one bucket at a time toward whichever side has more volume, until 70% of total volume is captured.

### HVN (High Volume Node)
Buckets with **unusually high volume** (above the 75th percentile of all active buckets). These are price levels where the market spent significant time — indicating **price acceptance**. The market is comfortable trading here.

### LVN (Low Volume Node)
Buckets with **unusually low volume** (below the 25th percentile of all active buckets). These are price levels the market passed through quickly — indicating **price rejection**. LVNs often act as **support/resistance** because price tends not to stay at these levels.

## Trading Significance

- **Price near POC:** Market is at fair value. Expect range-bound behavior.
- **Price at VAH/VAL:** Boundary of fair value. Potential breakout or rejection.
- **Price at HVN:** Expect consolidation. Strong support/resistance.
- **Price at LVN:** Expect fast moves. Price tends to travel through LVNs quickly — either back into the value area or breaking into a new one.
- **Narrow value area:** Low participation, potential for expansion (breakout).
- **Wide value area:** High participation, balanced market.

## Volume Allocation Method — Range-Overlap

Each 5-second candle's volume is distributed across buckets proportional to how much of the candle's price range overlaps each bucket.

```
bar_range = max(high - low, tick_size)

For each bucket the candle touches:
    overlap = min(bucket_top, high) - max(bucket_bottom, low)
    allocated_volume = total_bar_volume × (overlap / bar_range)
```

- **Why not assign all volume to close?** Close is just the last traded price of the candle. Trades occurred across the entire high-low range. Assigning to close biases POC toward closing prices and distorts HVN/LVN detection.
- **Why range-overlap?** It's the most accurate method without tick-level data. Volume is distributed proportionally based on how much of each bucket the candle actually covered.
- **Zero-range candles (high == low):** The `tick_size` floor ensures division is safe. All volume goes to the single containing bucket — which is correct since all trades occurred at one price.

### Tick Size

The `tick_size` used in `max(high - low, tick_size)` is the NSE-mandated minimum price increment, which varies by price band (effective April 15, 2025):

| Stock Price (₹) | Tick Size (₹) |
|---|---|
| Below 250 | 0.01 |
| 250 – 1,000 | 0.05 |
| 1,000 – 5,000 | 0.10 |
| 5,000 – 10,000 | 0.50 |
| 10,000 – 20,000 | 1.00 |
| Above 20,000 | 5.00 |

Tick bands are defined in `baseline_config.yaml` and looked up per bar based on the candle's close price.

## Outlier Filtering

Before computing the profile, bars with prices outside a reasonable range are filtered out using **Median Absolute Deviation (MAD)**:

```
median_price = median(all closes for the day)
mad = median(|closes - median_price|)
Filter out bars where: high > median + k × MAD  or  low < median - k × MAD
```

- `k` defaults to 10 (very permissive — only catches truly absurd prints)
- MAD is used instead of standard deviation because it's robust to the very outliers we're filtering
- Prevents a single bad print from stretching the bucket range and diluting the entire profile

## HVN/LVN Detection — Percentile-Based

Rather than using mean-based thresholds (fragile on skewed distributions), HVN/LVN are detected using percentiles of the **active** (non-zero) bucket volumes:

```
active_volumes = bucket_volumes where volume > 0
p75 = 75th percentile of active_volumes
p25 = 25th percentile of active_volumes

HVN = buckets with volume >= p75
LVN = buckets with volume <= p25 (and volume > 0)
```

This is distribution-agnostic and works correctly regardless of how skewed the volume profile is.

## Plugin Configuration

```yaml
# In baseline_config.yaml

# Top-level — shared across all plugins
tick_bands:
  - max_price: 250
    tick_size: 0.01
  - max_price: 1000
    tick_size: 0.05
  - max_price: 5000
    tick_size: 0.10
  - max_price: 10000
    tick_size: 0.50
  - max_price: 20000
    tick_size: 1.00
  - max_price: null
    tick_size: 5.00

# Plugin-specific
baselines:
  volume_profile:
    enabled: true
    table: nse_cm_baseline_volume_profile
    source: ohlcv_5s
    bucket_size: 1.0
    value_area_pct: 70
    hvn_percentile: 75
    lvn_percentile: 25
    outlier_mad_k: 10
    lookback_days: 30
```

### Parameter Reference

| Parameter | Type | Default | Description |
|---|---|---|---|
| `bucket_size` | float | 1.0 | Width of each price bucket in rupees |
| `value_area_pct` | float | 70 | Percentage of total volume that defines the value area |
| `hvn_percentile` | float | 75 | Percentile threshold for High Volume Node detection |
| `lvn_percentile` | float | 25 | Percentile threshold for Low Volume Node detection |
| `outlier_mad_k` | float | 10 | MAD multiplier for outlier bar filtering |
| `lookback_days` | int | 30 | Number of days of history to compute |

## Input

- **Source table:** `nse_cm_ohlcv_5s`
- **Columns:** `isin`, `timestamp`, `high`, `low`, `close`, `volume`
- **Scope:** All active ISINs, rolling lookback window

## Output

- **Target table:** `nse_cm_baseline_volume_profile`

| Column | Type | Description |
|---|---|---|
| `isin` | SYMBOL | Stock identifier |
| `trade_date` | TIMESTAMP | Trading day |
| `poc` | FLOAT | Point of Control price |
| `vah` | FLOAT | Value Area High |
| `val` | FLOAT | Value Area Low |
| `total_volume` | INT | Total volume for the day (post-filtering) |
| `bucket_count` | INT | Number of price buckets |
| `hvn_count` | INT | Number of High Volume Nodes |
| `lvn_count` | INT | Number of Low Volume Nodes |
| `price_buckets` | STRING (JSON) | Array of {price, volume, pct} per active bucket |
| `hvn_levels` | STRING (JSON) | Array of HVN price levels |
| `lvn_levels` | STRING (JSON) | Array of LVN price levels |

## Dependencies

- **Depends on:** Nothing (base plugin)
- **Depended on by:** `support_resistance` plugin
