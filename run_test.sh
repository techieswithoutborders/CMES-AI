#!/usr/bin/env bash
# One command: build a dev pack with the test pipeline, then serve it with the test console.
#   ./run_test.sh            # auto: NVIDIA NIM if NVIDIA_API_KEY is set, else offline stand-ins
#   ./run_test.sh nim        # require NVIDIA NIM
#   ./run_test.sh offline
# First time:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   (or set PYTHON=/path/to/python)
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
MODE="${1:-auto}"
echo "== running pipeline (mode: $MODE)"
"$PY" -m pipeline.run --mode "$MODE"
PACK="$(ls -dt build/packs/kb-* | head -1)"
echo "== serving $PACK  (Ctrl-C to stop)"
rm -rf state/slots state/current.json state/previous.json 2>/dev/null || true
( sleep 2; command -v open >/dev/null && open "http://127.0.0.1:8080/ui/" || true ) &
exec "$PY" -m edge.server --dev --backend "$MODE" --bootstrap "$PACK"
