#!/usr/bin/env python3
"""Assemble the portable IA-K folder (and a zip of it) around a built
application, for a key or a disk.

    python scripts/make_portable.py --exe src-tauri/target/release/ia-k.exe --os windows
    python scripts/make_portable.py --exe src-tauri/target/release/ia-k --os linux
    python scripts/make_portable.py --exe src-tauri/target/universal-apple-darwin/release/bundle/macos/IA-K.app --os mac

Result: dist/ia-k-<os>/ (and dist/ia-k-<os>.zip) holding the application,
the backend, the setup scripts, the server launchers and the documentation.
With --python, the portable Python installed by setup_python.* (build/python/
<os>, every build/python/mac-* on macOS) goes in too, so that the key works
once unzipped; its bytecode caches, test suites and Tk are left out, and the
copy is checked by importing the engine with it. With --tesseract, the
Tesseract built by scripts/build_tesseract.py (build/tesseract/<os>, every
mac-* on macOS) and the shared language data (build/tesseract/tessdata) go
in too, checked by listing its languages. The models and the library are
never included. The interface (app/) is inside the application, it is not
copied.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMON = ["README.md", "FONCTIONNALITES.md", "SERVERS.md", "BACKEND.md", "DEVELOPERS.md", "LICENSE",
          "config/servers.example.toml", "scripts/smoke_test.py", "assets/icon.png",
          "assets/ia-k.png"]
BY_OS = {
    "windows": ["iak.bat", "setup_python.bat", "server/llama-server.bat",
                "server/local.example.bat", "server/Gemma-4-26-A4B-vulkan.bat",
                "server/gemma-4-12b-qat-vulkan.bat", "server/embeddinggemma-rag.bat",
                "server/qwen3-vl-8b-vulkan.bat", "server/start-servers.bat",
                "server/local.hx370.bat", "serveurs-baseConnaissance.bat",
                "serveurs-traduction.bat", "serveurs-vl.bat"],
    "linux": ["iak.sh", "setup_python.sh", "server/llama-server.sh",
              "server/local.example.sh", "server/start-servers.sh",
              "serveurs-baseConnaissance.sh", "serveurs-traduction.sh",
              "serveurs-vl.sh"],
}
# the same shell scripts on macOS
BY_OS["mac"] = list(BY_OS["linux"])
APP_NAME = {"windows": "ia-k.exe", "linux": "ia-k", "mac": "IA-K.app"}
SKIP_DIRS = {"__pycache__", "tests", ".pytest_cache"}

# Left out of the portable Python: unused by IA-K, and the bytecode caches
# would be stale anyway (a zip keeps times to 2 seconds, so Python rebuilds
# them on the key at the first start).
PY_SKIP_STDLIB = {"__pycache__", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3",
                  "test"}
PY_SKIP_TCL = ("tcl", "tk", "itcl", "thread")   # Tcl/Tk data: lib/tcl9.0..., tcl/
PY_SKIP_PACKAGE = {"tests", "test"}            # test suites inside site-packages
# what the engine imports, checked with the copied Python
CHECK = ("import encodings, sqlite3, ssl, pip._internal.cli.main, pymupdf, docx, numpy, bs4, "
         "lxml.etree, iak.api, iak.book, iak.fulltext, iak.translate, iak.folders; "
         "sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)'); "
         "print('engine ok:', pymupdf.__version__, numpy.__version__)")


def copy_tree(src: Path, dst: Path) -> None:
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or path.suffix in (".pyc", ".pyo"):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def python_dirs(os_name: str, base: Path) -> list[Path]:
    """The installed portable Pythons for this system, complete ones only."""
    if os_name == "mac":
        found = sorted(p for p in base.glob("mac-*") if p.is_dir())
    else:
        found = [base / os_name]
    for p in found:
        if not (p / ".complete").is_file():
            raise SystemExit(f"no complete portable Python in {p}: run setup_python first")
    if not found:
        raise SystemExit(f"no portable Python in {base}: run setup_python.sh first")
    return found


def python_skipped(rel: Path, is_dir: bool) -> bool:
    """Paths left out, inside a python-build-standalone tree: Linux and macOS
    (lib/python3.12/..., lib/tcl9.0) or Windows (Lib/..., tcl/)."""
    parts = [p.lower() for p in rel.parts]
    if "__pycache__" in parts or rel.suffix in (".pyc", ".pyo"):
        return True
    dirs = parts if is_dir else parts[:-1]
    # names differing only by case (exFAT keys), as setup_python.sh drops them
    if dirs[:2] in (["share", "terminfo"], ["share", "man"]):
        return True
    # Tcl/Tk data, used only by tkinter
    if dirs[:1] == ["tcl"] or (dirs[:1] == ["lib"] and len(dirs) > 1
                               and dirs[1].startswith(PY_SKIP_TCL)):
        return True
    if "site-packages" in dirs:
        inside = dirs[dirs.index("site-packages") + 1:]
        return any(p in PY_SKIP_PACKAGE for p in inside[1:])
    # the standard library: lib/python3.12/<name> or Lib/<name>
    stdlib = dirs[2:3] if dirs[:1] == ["lib"] and dirs[1:2] and dirs[1].startswith("python3") \
        else dirs[1:2] if dirs[:1] == ["lib"] else []
    return bool(stdlib) and stdlib[0] in PY_SKIP_STDLIB


def copy_python(src: Path, dst: Path) -> int:
    size = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if path.is_symlink() or python_skipped(rel, path.is_dir()):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            size += path.stat().st_size
    return size


def tesseract_dirs(os_name: str, base: Path) -> list[Path]:
    """The portable Tesseracts built for this system."""
    exe = "tesseract.exe" if os_name == "windows" else "tesseract"
    found = sorted(base.glob("mac-*")) if os_name == "mac" else [base / os_name]
    found = [p for p in found if (p / exe).is_file()]
    if not found:
        raise SystemExit(f"no portable Tesseract in {base}: run scripts/build_tesseract.py")
    if not (base / "tessdata" / "configs" / "tsv").is_file():
        raise SystemExit(f"no language data in {base / 'tessdata'}: run "
                         "scripts/build_tesseract.py --data-only")
    return found


def check_tesseract(exe: Path, tessdata: Path) -> None:
    env = dict(os.environ, TESSDATA_PREFIX=str(tessdata))
    r = subprocess.run([str(exe), "--list-langs"], env=env, capture_output=True, text=True)
    langs = [ln.strip() for ln in (r.stdout or r.stderr).splitlines()[1:] if ln.strip()]
    if r.returncode or "fra" not in langs:
        raise SystemExit(f"the portable Tesseract {exe} fails:\n{r.stdout}{r.stderr}")
    print(f"{exe.parent.name}: Tesseract ok, languages {' '.join(langs)}")


def check_python(folder: Path, py_dir: Path) -> None:
    exe = py_dir / ("python.exe" if (py_dir / "python.exe").exists() else "bin/python3.12")
    env = dict(os.environ, PYTHONPATH=str(folder / "backend"), PYTHONDONTWRITEBYTECODE="1",
               PYTHONNOUSERSITE="1", PYTHONUTF8="1")
    env.pop("PYTHONHOME", None)
    r = subprocess.run([str(exe), "-s", "-c", CHECK], env=env, cwd=folder,
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"the portable Python in {py_dir} fails:\n{r.stderr.strip()}")
    print(f"{py_dir.name}: {r.stdout.strip()}")


def make(exe: Path, os_name: str, out: Path, zip_it: bool = True,
         python: Path | None = None, check: bool = True,
         tesseract: Path | None = None) -> Path:
    if not (exe.is_dir() if os_name == "mac" else exe.is_file()):
        raise SystemExit(f"application not found: {exe}")
    folder = out / f"ia-k-{os_name}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    name = APP_NAME[os_name]
    if os_name == "mac":
        # an application bundle: a folder, its permissions kept
        shutil.copytree(exe, folder / name, symlinks=True)
    else:
        shutil.copy2(exe, folder / name)
    copy_tree(ROOT / "backend" / "iak", folder / "backend" / "iak")
    for f in ("requirements.lock", "requirements.txt", "pyproject.toml"):
        shutil.copy2(ROOT / "backend" / f, folder / "backend" / f)
    for rel in COMMON + BY_OS[os_name]:
        src = ROOT / rel
        if src.exists():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, folder / rel)
    (folder / "data").mkdir()
    if python is not None:
        for src in python_dirs(os_name, python):
            dst = folder / "build" / "python" / src.name
            size = copy_python(src, dst)
            print(f"{src.name}: {size / 1e6:.0f} MB of Python copied")
            if check:
                check_python(folder, dst)
    if tesseract is not None:
        dirs = tesseract_dirs(os_name, tesseract)
        for src in dirs + [tesseract / "tessdata"]:
            dst = folder / "build" / "tesseract" / src.name
            shutil.copytree(src, dst)
            print(f"{src.name}: Tesseract files copied")
        for src in dirs:
            exe = folder / "build" / "tesseract" / src.name
            exe = exe / ("tesseract.exe" if os_name == "windows" else "tesseract")
            if os_name != "windows":
                exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if check:
                check_tesseract(exe, folder / "build" / "tesseract" / "tessdata")
    # the lexicons of the doubtful OCR words (scripts/fetch_words.py), when fetched
    words = ROOT / "build" / "words"
    if (words / "fr.txt").is_file():
        shutil.copytree(words, folder / "build" / "words")
        print("words: lexicon copied")
    if os_name in ("linux", "mac"):
        exes = [r for r in BY_OS[os_name] if r.endswith(".sh")]
        exes.append(f"{name}/Contents/MacOS/ia-k" if os_name == "mac" else name)
        for rel in exes:
            p = folder / rel
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not zip_it:
        return folder
    archive = out / f"ia-k-{os_name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(folder.rglob("*")):
            arc = path.relative_to(out).as_posix()
            if path.is_dir():
                z.writestr(arc + "/", "")
                continue
            info = zipfile.ZipInfo.from_file(path, arc)
            info.compress_type = zipfile.ZIP_DEFLATED
            # keep the executable bit for Linux users unzipping with unzip
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            with open(path, "rb") as fh:
                z.writestr(info, fh.read())
    return archive


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", required=True, type=Path, help="the built application")
    ap.add_argument("--os", required=True, choices=sorted(BY_OS), dest="os_name")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--python", nargs="?", type=Path, const=ROOT / "build" / "python",
                    metavar="DIR", help="include the portable Python installed by setup_python "
                    "(from build/python, or DIR)")
    ap.add_argument("--tesseract", nargs="?", type=Path, const=ROOT / "build" / "tesseract",
                    metavar="DIR", help="include the Tesseract built by "
                    "scripts/build_tesseract.py and its languages (from build/tesseract, or DIR)")
    ap.add_argument("--no-check", action="store_true",
                    help="do not run the included Python and Tesseract (another processor)")
    args = ap.parse_args()
    result = make(args.exe, args.os_name, args.out, not args.no_zip, args.python,
                  not args.no_check, args.tesseract)
    size = result.stat().st_size if result.is_file() else sum(
        p.stat().st_size for p in result.rglob("*") if p.is_file())
    print(f"{result} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
