#!/usr/bin/env bash
# One command: check everything, pick a free port, start the console WITH the voice bot, chat bot and Settings, and open the browser.
#   ./run_demo.sh            (add --no-browser to skip opening the browser; set DEMO_PORT=9000 to force a port)
# It never stops another program: if 8808 is already used it simply takes the next free port and tells you.
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || { echo "The project environment .venv is missing. Create it first (see README)."; exit 1; }
OPEN=1; [ "$1" = "--no-browser" ] && OPEN=0; [ -n "$RUN_DEMO_NO_BROWSER" ] && OPEN=0

$PY -m layer9_voice.doctor --quiet || { echo; echo "Fix the items marked FAIL above, then run ./run_demo.sh again."; exit 1; }

if [ -z "$DEMO_PORT" ]; then
  for p in $(seq 8808 8830); do
    if ! (echo > "/dev/tcp/127.0.0.1/$p") 2>/dev/null; then DEMO_PORT=$p; break; fi
  done
fi
[ -n "$DEMO_PORT" ] || { echo "No free port between 8808 and 8830."; exit 1; }
export DEMO_PORT DEMO_NO_BROWSER=1
URL="http://127.0.0.1:$DEMO_PORT"
echo
echo "Starting the demo on $URL  (loading the detector, about 10 seconds)"
[ "$DEMO_PORT" != "8808" ] && echo "(8808 is already in use, so port $DEMO_PORT is used. Nothing was stopped.)"
echo "Tabs: Voice bot, Chat bot, Settings  ->  $URL/console#chat"
if [ "$OPEN" = "1" ]; then
  ( for i in $(seq 1 120); do
      if curl -sf "$URL/voice/status" >/dev/null 2>&1; then xdg-open "$URL/console#chat" >/dev/null 2>&1 || true; break; fi
      sleep 1
    done ) &
fi
exec $PY -m layer9_voice.run
