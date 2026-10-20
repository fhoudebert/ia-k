#!/usr/bin/env bash
# =============================================================================
#  setup_python.sh - portable Python for IA-K on Linux and macOS (key or disk)
#
#  Installs a complete, relocatable CPython (python-build-standalone,
#  stripped build, about 100 MB; 260 MB with the packages) in
#  build/python/linux (build/python/mac on a Mac) and the packages of
#  backend/requirements.lock into it. Unlike a venv, it does not depend on the
#  Python of the computer: the key works on any x86-64 Linux, or any Mac of
#  the same processor family (Apple silicon or Intel). Windows has its own
#  copy (setup_python.bat); all can live side by side on the same key.
#
#  Safe to run again: an existing Python is kept, packages are brought to
#  the versions of the lock file. --force reinstalls Python.
# =============================================================================
set -Eeuo pipefail

PBS_TAG="20261013"
PY_VERSION="3.12.15"
case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)
        OS=linux; TRIPLE=x86_64-unknown-linux-gnu
        SHA256="178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c" ;;
    Darwin-arm64)
        OS=mac; TRIPLE=aarch64-apple-darwin
        SHA256="9866e11cf2ae9caa1e671383786f248859a52dd8bfb702fb4c0dc2c65f1d36de" ;;
    Darwin-x86_64)
        OS=mac; TRIPLE=x86_64-apple-darwin
        SHA256="d09998f85752b646873a8a6bc7eea442a0d251b1c8d2413aa8ea06ef131c3335" ;;
    *)
        echo "unsupported system: $(uname -s) $(uname -m) (Linux x86-64 or macOS)" >&2
        exit 1 ;;
esac
ASSET="cpython-${PY_VERSION}+${PBS_TAG}-${TRIPLE}-install_only_stripped.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${ASSET}"

# sha256sum on Linux, shasum on macOS
sha256_check() {
    if command -v sha256sum >/dev/null; then
        echo "$SHA256  $1" | sha256sum -c --quiet -
    else
        echo "$SHA256  $1" | shasum -a 256 -c --quiet -
    fi
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$ROOT/build/python/$OS"
PY="$DEST/bin/python3.12"

if [ "${1:-}" = "--force" ]; then
    rm -rf "$DEST"
fi
# A Python is kept only with the marker of a finished install: an
# interrupted one (files missing) is installed again.
if [ -d "$DEST" ] && [ ! -f "$DEST/.complete" ]; then
    echo "[1/3] Incomplete Python found, installing again"
    rm -rf "$DEST"
fi

if [ ! -x "$PY" ]; then
    command -v curl >/dev/null || { echo "curl is needed" >&2; exit 1; }
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    echo "[1/3] Downloading Python $PY_VERSION ($ASSET)"
    curl -fL --retry 3 -o "$TMP/py.tar.gz" "$URL"
    sha256_check "$TMP/py.tar.gz" \
        || { echo "checksum mismatch, download refused" >&2; exit 1; }
    # Extract on the local disk and make the tree fit an exFAT or FAT32 key:
    # - symbolic links dropped: those filesystems cannot hold them, and they
    #   only give second names to files that are there anyway
    #   (python3 -> python3.12);
    # - share/terminfo dropped: it has names differing only by case (N/ and
    #   n/...), which exFAT cannot tell apart; only the curses module, unused
    #   by IA-K, reads it; share/man is not needed either.
    tar -xzf "$TMP/py.tar.gz" -C "$TMP"
    find "$TMP/python" -type l -delete
    rm -rf "$TMP/python/share/terminfo" "$TMP/python/share/man"
    # any other pair of names differing only by case would break the copy
    collide="$("$TMP/python/bin/python3.12" - "$TMP/python" <<'PY'
import os, sys
seen = {}
for d, dirs, files in os.walk(sys.argv[1]):
    for n in dirs + files:
        p = os.path.join(d, n)
        k = p.lower()
        if k in seen:
            print(p)
        seen[k] = p
PY
)"
    if [ -n "$collide" ]; then
        echo "names differing only by case, impossible on exFAT/FAT32:" >&2
        echo "$collide" | head -5 >&2
        exit 1
    fi
    mkdir -p "$DEST"
    cp -r "$TMP/python/." "$DEST/"
    "$PY" -c "import encodings, sqlite3, ssl, pip._vendor.rich, pip._internal.cli.main" \
        || { echo "the copied Python is incomplete (key full?); run again" >&2; rm -rf "$DEST"; exit 1; }
    echo ok > "$DEST/.complete"
else
    echo "[1/3] Python already present: $("$PY" --version)"
fi

if ! "$PY" -c "pass" 2>/dev/null; then
    if [ "$OS" = mac ]; then
        echo "$PY cannot run: if macOS blocked it, run once:" >&2
        echo "  xattr -dr com.apple.quarantine \"$ROOT\"" >&2
    else
        echo "$PY cannot run: is the key mounted with 'noexec'?" >&2
        echo "Remount it with the exec option and try again." >&2
    fi
    exit 1
fi

echo "[2/3] Installing packages (backend/requirements.lock)"
"$PY" -m pip install --disable-pip-version-check --no-warn-script-location \
    -q -r "$ROOT/backend/requirements.lock"

echo "[3/3] Checking"
"$PY" -c "import pymupdf, docx, numpy, bs4, sqlite3; \
c = sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); \
print('PyMuPDF', pymupdf.__version__, '- numpy', numpy.__version__, '- SQLite', sqlite3.sqlite_version, 'with FTS5')"
if command -v tesseract >/dev/null; then
    langs="$(tesseract --list-langs 2>/dev/null | tail -n +2 | tr '\n' ' ')"
    echo "Tesseract: $(command -v tesseract) - languages: $langs"
    if [ "$OS" = mac ]; then fra_hint="brew install tesseract-lang"; else fra_hint="sudo apt install tesseract-ocr-fra"; fi
    case " $langs " in *" fra "*) ;; *) echo "  missing French data: $fra_hint";; esac
elif [ "$OS" = mac ]; then
    echo "Tesseract not found: brew install tesseract tesseract-lang (OCR off until then)"
else
    echo "Tesseract not found: sudo apt install tesseract-ocr tesseract-ocr-fra (OCR off until then)"
fi
echo "Done. Run IA-K with: ./iak.sh --help"
