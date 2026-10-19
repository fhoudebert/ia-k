#!/usr/bin/env bash
# =============================================================================
#  setup_python.sh - portable Python for IA-K on Linux (key or disk)
#
#  Installs a complete, relocatable CPython (python-build-standalone,
#  stripped build, about 100 MB; 260 MB with the packages) in
#  build/python/linux and the packages of backend/requirements.lock into it.
#  Unlike a venv, it does not depend on the Python of the computer: the key
#  works on any x86-64 Linux. Windows has its own copy (setup_python.bat),
#  both can live side by side on the same key.
#
#  Safe to run again: an existing Python is kept, packages are brought to
#  the versions of the lock file. --force reinstalls Python.
# =============================================================================
set -Eeuo pipefail

PBS_TAG="20261013"
PY_VERSION="3.12.15"
ASSET="cpython-${PY_VERSION}+${PBS_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
SHA256="178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${ASSET}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$ROOT/build/python/linux"
PY="$DEST/bin/python3.12"

if [ "${1:-}" = "--force" ]; then
    rm -rf "$DEST"
fi

if [ ! -x "$PY" ]; then
    command -v curl >/dev/null || { echo "curl is needed" >&2; exit 1; }
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    echo "[1/3] Downloading Python $PY_VERSION ($ASSET)"
    curl -fL --retry 3 -o "$TMP/py.tar.gz" "$URL"
    echo "$SHA256  $TMP/py.tar.gz" | sha256sum -c --quiet - \
        || { echo "checksum mismatch, download refused" >&2; exit 1; }
    # Extract on the local disk, drop the symbolic links (exFAT and FAT32
    # keys cannot hold them; they only give second names to files that are
    # there anyway, e.g. python3 -> python3.12), then copy.
    tar -xzf "$TMP/py.tar.gz" -C "$TMP"
    find "$TMP/python" -type l -delete
    mkdir -p "$DEST"
    cp -r "$TMP/python/." "$DEST/"
else
    echo "[1/3] Python already present: $("$PY" --version)"
fi

if ! "$PY" -c "pass" 2>/dev/null; then
    echo "$PY cannot run: is the key mounted with 'noexec'?" >&2
    echo "Remount it with the exec option and try again." >&2
    exit 1
fi

echo "[2/3] Installing packages (backend/requirements.lock)"
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location \
    -q -r "$ROOT/backend/requirements.lock"

echo "[3/3] Checking"
"$PY" -c "import pymupdf, docx, numpy, sqlite3; \
c = sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); \
print('PyMuPDF', pymupdf.__version__, '- numpy', numpy.__version__, '- SQLite', sqlite3.sqlite_version, 'with FTS5')"
if command -v tesseract >/dev/null; then
    langs="$(tesseract --list-langs 2>/dev/null | tail -n +2 | tr '\n' ' ')"
    echo "Tesseract: $(command -v tesseract) - languages: $langs"
    case " $langs " in *" fra "*) ;; *) echo "  missing French data: sudo apt install tesseract-ocr-fra";; esac
else
    echo "Tesseract not found: sudo apt install tesseract-ocr tesseract-ocr-fra (OCR off until then)"
fi
echo "Done. Run IA-K with: ./iak.sh --help"
