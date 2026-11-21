#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHONPATH="$ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}" python3 -m unittest discover -s "$ROOT/tests" -v

if command -v node >/dev/null 2>&1; then
  node "$ROOT/integrations/dsh/test.mjs"
  node "$ROOT/integrations/dsh-native-bridge/test.mjs"
fi
