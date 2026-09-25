#!/usr/bin/env bash
# agy-job: background agy jobs (start / list / status / result / wait / cancel). The
# implementation is Python (src/agy_runner, standard library only); this shim keeps the
# path that commands, other scripts and older docs call. `agy-job --help` shows usage.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then echo "agy-job: python3 (>= 3.9) is required" >&2; exit 1; fi
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m agy_runner job "$@"
