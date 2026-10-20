#!/usr/bin/env bash
# Run the IA-K backend with the portable Python of the key (setup_python.sh).
#   ./iak.sh import doc.pdf    ./iak.sh search "words"    ./iak.sh --help
# The library defaults to data/ next to this script (IAK_LIBRARY overrides).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s)" in Darwin) OS=mac ;; *) OS=linux ;; esac
PY="$ROOT/build/python/$OS/bin/python3.12"
if [ ! -x "$PY" ]; then
    echo "Portable Python missing: run ./setup_python.sh first" >&2
    exit 1
fi
export IAK_LIBRARY="${IAK_LIBRARY:-$ROOT/data}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m iak "$@"
