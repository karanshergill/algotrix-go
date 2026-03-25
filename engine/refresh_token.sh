#!/bin/bash
# Refresh Fyers access token using the refresh token
# Run this before market open if the access token has expired

set -e
cd /home/me/projects/algotrix-go/engine

APP_ID="EQHA0N51WU-100"
SECRET_KEY="J82TVV74SU"
APP_HASH=$(echo -n "${APP_ID}:${SECRET_KEY}" | sha256sum | cut -d' ' -f1)

REFRESH_TOKEN=$(python3 -c "import json; print(json.load(open('token.json'))['refresh_token'])")

echo "Refreshing Fyers token..."
RESPONSE=$(curl -s -X POST "https://api-t1.fyers.in/api/v3/validate-refresh-token" \
  -H "Content-Type: application/json" \
  -d "{\"grant_type\":\"refresh_token\",\"appIdHash\":\"${APP_HASH}\",\"refresh_token\":\"${REFRESH_TOKEN}\",\"pin\":\"1337\"}")

echo "Response: $RESPONSE"

# Check if successful
STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('s',''))")

if [ "$STATUS" = "ok" ]; then
    python3 -c "
import json, sys
from datetime import datetime, timezone, timedelta
IST = timezone(timedelta(hours=5, minutes=30))
resp = json.loads('''$RESPONSE''')
old = json.load(open('token.json'))
token = {
    'access_token': resp['access_token'],
    'refresh_token': resp.get('refresh_token', old['refresh_token']),
    'created_at': datetime.now(IST).isoformat()
}
with open('token.json', 'w') as f:
    json.dump(token, f, indent=2)
print('Token refreshed and saved!')
"
else
    echo "ERROR: Token refresh failed. You'll need to re-login."
    echo "Run: go run . feed --symbols NSE:NIFTY50-INDEX"
    exit 1
fi
