#!/usr/bin/env python3
"""Assemble the portable IA-K folder (and a zip of it) around a built
application, for a key or a disk.

    python scripts/make_portable.py --exe src-tauri/target/release/ia-k.exe --os windows
    python scripts/make_portable.py --exe src-tauri/target/release/ia-k --os linux
    python scripts/make_portable.py --exe src-tauri/target/universal-apple-darwin/release/bundle/macos/IA-K.app --os mac

Result: dist/ia-k-<os>/ (and dist/ia-k-<os>.zip) holding the application,
the backend, the setup scripts, the server launchers and the documentation.
The portable Python, Tesseract, the models and the library are not included:
setup_python.* installs Python on the key, the rest stays outside.
The interface (app/) is inside the application, it is not copied.
"""
from __future__ import annotations

import argparse
import shutil
import stat
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


def make(exe: Path, os_name: str, out: Path, zip_it: bool = True) -> Path:
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
    args = ap.parse_args()
    result = make(args.exe, args.os_name, args.out, not args.no_zip)
    size = result.stat().st_size if result.is_file() else sum(
        p.stat().st_size for p in result.rglob("*") if p.is_file())
    print(f"{result} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
