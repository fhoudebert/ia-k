#!/usr/bin/env bash
# Run the IA-K backend with the portable Python of the key (setup_python.sh).
#   ./iak.sh import doc.pdf    ./iak.sh search "words"    ./iak.sh --help
# The library defaults to data/ next to this script (IAK_LIBRARY overrides).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) OS=mac-aarch64 ;;
    Darwin-*) OS=mac-x86_64 ;;
    *) OS=linux ;;
esac
PY="$ROOT/build/python/$OS/bin/python3.12"
# the Mac Python of earlier versions, before setup_python.sh renames it
[ -x "$PY" ] || [ "${OS%%-*}" != mac ] || PY="$ROOT/build/python/mac/bin/python3.12"
if [ ! -x "$PY" ]; then
    echo "Portable Python missing: run ./setup_python.sh first" >&2
    exit 1
fi
export IAK_LIBRARY="${IAK_LIBRARY:-$ROOT/data}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m iak "$@"
