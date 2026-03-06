package symbols

const (
    TableName = "nse_cm_symbols"

    ColFyToken = "fy_token"
    ColSymbol  = "symbol"
    ColName    = "name"
    ColISIN    = "isin"

    CSVURL = "https://public.fyers.in/sym_details/NSE_CM.csv"
	
	// Column indices in the CSV (0-based, no header row)
    idxFyToken = 0
    idxName    = 1
    idxISIN    = 5
    idxSymbol  = 9
)