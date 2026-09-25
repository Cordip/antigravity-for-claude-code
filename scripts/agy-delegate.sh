#!/usr/bin/env bash
# agy-delegate: run one agy turn headless (in the bubblewrap jail by default) and print
# agy's reply. The implementation is Python (src/agy_runner, standard library only); this
# shim keeps the path that commands, other scripts and older docs call.
# `agy-delegate --help` documents the options and exit codes.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then echo "agy-delegate: python3 (>= 3.9) is required" >&2; exit 1; fi
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m agy_runner delegate "$@"
