#!/bin/sh
# Open the organizer board for an event without echoing the API key.
#   scripts/board.sh <event_id>
# Needs STANDBY_API and STANDBY_API_KEY (e.g. in .env, which is gitignored).
set -e
cd "$(dirname "$0")/.."
[ -f .env ] && . ./.env
: "${STANDBY_API:?set STANDBY_API}" "${STANDBY_API_KEY:?set STANDBY_API_KEY}" "${1:?usage: scripts/board.sh <event_id>}"
PORT=${PORT:-8787}
if ! curl -s -o /dev/null "http://localhost:$PORT/board/"; then
  python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
  sleep 1
fi
open "http://localhost:$PORT/board/#api=$STANDBY_API&event=$1&key=$STANDBY_API_KEY" >/dev/null 2>&1
echo "board opened for event $1 (server on 127.0.0.1:$PORT)"
