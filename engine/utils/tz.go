package utils

import "time"

// IST is the Indian Standard Time location (UTC+05:30).
// Used across the engine and screeners for market-hour calculations.
var IST = time.FixedZone("IST", 5*3600+30*60)
