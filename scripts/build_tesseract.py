#!/usr/bin/env python3
"""Build a portable Tesseract for the key, from its sources.

    python3 scripts/build_tesseract.py            # this system
    python3 scripts/build_tesseract.py --data-only

One program, build/tesseract/<os>/tesseract[.exe] (linux, windows,
mac-aarch64 or mac-x86_64), linked statically with Leptonica, libpng and
zlib: no library to install, nothing else to carry. Images are given to it
as PNG by IA-K, so the other image formats, the network, the archives and
OpenMP are left out. Its SIMD code (AVX2, FMA, NEON...) is chosen when it
runs, so it works on any processor of the family.

The language data is shared by all systems, in build/tesseract/tessdata:
the standard models of tessdata 4.1.0 (the ones Linux distributions and the
Windows installer ship) for French, English, Middle French and Latin,
checked against their SHA-256, with Tesseract's output configs (tsv...).

Needs git, CMake (3.16 or later) and a C/C++ compiler: Visual Studio's on
Windows (the Desktop C++ workload), the Xcode command line tools on macOS,
gcc or clang on Linux. Sources are fetched with git at fixed commits. Run it
again safely: what is built is kept (--force builds again).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "tesseract"

# name, repository, tag, commit (checked after the clone)
SOURCES = [
    ("zlib", "https://github.com/madler/zlib", "v1.3.1",
     "51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf"),
    ("libpng", "https://github.com/pnggroup/libpng", "v1.6.59",
     "cd952f49f95bb27154ae77dbb103032d95f6e580"),
    ("leptonica", "https://github.com/DanBloomberg/leptonica", "1.87.0",
     "13275a278eb55b5746e33f95fbf5a2c8f604b3ab"),
    ("tesseract", "https://github.com/tesseract-ocr/tesseract", "5.5.3",
     "db0ec62f81b0737fbbe184d8fea40af5738f8eef"),
]
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata/raw/4.1.0/{}.traineddata"
LANGS = {
    "fra": "eac01c1d72540d6090facb7b2f42dd0a2ee8fc57c5be1b20548ae668e2761913",
    "eng": "daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047",
    "frm": "c26f46c25981af621af3b8a9366d3d3603f6aff2557ebbffa29b82068df70629",
    "lat": "790fc854bc28655aa70a776e3e014548bd42e8811847b84a61c9e53ef0b0ea2c",
}
WINDOWS = os.name == "nt"
EXE = "tesseract.exe" if WINDOWS else "tesseract"


def os_dir() -> str:
    if WINDOWS:
        return "windows"
    if sys.platform == "darwin":
        return "mac-aarch64" if platform.machine() == "arm64" else "mac-x86_64"
    return "linux"


def run(cmd: list, cwd: Path | None = None) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True)


def fetch(work: Path) -> dict[str, Path]:
    dirs = {}
    for name, url, tag, commit in SOURCES:
        d = work / "src" / name
        if not (d / ".git").is_dir():
            if d.exists():
                shutil.rmtree(d)
            run(["git", "-c", "advice.detachedHead=false", "clone", "-q", "--depth", "1",
                 "--branch", tag, url, d])
        got = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True,
                             text=True, check=True).stdout.strip()
        if got != commit:
            raise SystemExit(f"{name} {tag}: commit {got}, expected {commit}")
        dirs[name] = d
    return dirs


def cmake(src: Path, build: Path, prefix: Path, *options: str) -> None:
    common = [f"-DCMAKE_INSTALL_PREFIX={prefix}", f"-DCMAKE_PREFIX_PATH={prefix}",
              "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=OFF",
              "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
              # the C runtime linked in too (no Visual C++ redistributable needed)
              "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
              "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
              "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"]
    if sys.platform == "darwin":
        common.append("-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0")
    elif not WINDOWS:        # the C++ runtime linked in: any Linux of the last years
        common.append("-DCMAKE_EXE_LINKER_FLAGS=-static-libstdc++ -static-libgcc")
    gen = ["-G", "Ninja"] if not WINDOWS and shutil.which("ninja") else []
    run(["cmake", "-S", src, "-B", build, *gen, *common, *options])
    run(["cmake", "--build", build, "--config", "Release", "--parallel"])
    run(["cmake", "--install", build, "--config", "Release"])


def static_lib(prefix: Path, *names: str) -> Path:
    for n in names:
        p = prefix / "lib" / n
        if p.is_file():
            return p
    raise SystemExit(f"none of {names} in {prefix / 'lib'}")


def build(work: Path, dest: Path) -> None:
    src = fetch(work)
    prefix = work / "prefix"
    b = work / "build"
    cmake(src["zlib"], b / "zlib", prefix, "-DZLIB_BUILD_EXAMPLES=OFF")
    zlib = static_lib(prefix, "zlibstatic.lib", "libz.a")
    if not WINDOWS:          # only the static zlib, so that nothing picks the shared one
        for p in (prefix / "lib").glob("libz.*"):
            if p.suffix != ".a":
                p.unlink()
    zopts = [f"-DZLIB_LIBRARY={zlib}", f"-DZLIB_INCLUDE_DIR={prefix / 'include'}",
             f"-DZLIB_ROOT={prefix}"]
    cmake(src["libpng"], b / "libpng", prefix, "-DPNG_SHARED=OFF", "-DPNG_STATIC=ON",
          "-DPNG_TESTS=OFF", "-DPNG_TOOLS=OFF", "-DPNG_FRAMEWORK=OFF", *zopts)
    png = static_lib(prefix, "libpng16_static.lib", "libpng16.a", "libpng.a")
    popts = [f"-DPNG_LIBRARY={png}", f"-DPNG_PNG_INCLUDE_DIR={prefix / 'include'}"]
    cmake(src["leptonica"], b / "leptonica", prefix, "-DSW_BUILD=OFF", "-DBUILD_PROG=OFF",
          "-DENABLE_GIF=OFF", "-DENABLE_JPEG=OFF", "-DENABLE_TIFF=OFF", "-DENABLE_WEBP=OFF",
          "-DENABLE_OPENJPEG=OFF", "-DENABLE_PNG=ON", "-DENABLE_ZLIB=ON",
          "-DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON", *zopts, *popts)
    cmake(src["tesseract"], b / "tesseract", prefix, "-DSW_BUILD=OFF",
          "-DBUILD_TRAINING_TOOLS=OFF", "-DBUILD_TESTS=OFF", "-DDISABLE_ARCHIVE=ON",
          "-DDISABLE_CURL=ON", "-DDISABLE_TIFF=ON", "-DGRAPHICS_DISABLED=ON",
          "-DOPENMP_BUILD=OFF", "-DENABLE_LTO=OFF", "-DENABLE_NATIVE=OFF",
          "-DINSTALL_CONFIGS=OFF", "-DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON",
          f"-DLeptonica_DIR={prefix / 'lib' / 'cmake' / 'leptonica'}", *zopts, *popts)
    exe = prefix / "bin" / EXE
    tmp = dest.with_name(dest.name + ".part")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    shutil.copy2(exe, tmp / EXE)
    if not WINDOWS:
        run(["strip", tmp / EXE] if sys.platform != "darwin" else ["strip", "-x", tmp / EXE])
    for f in ("LICENSE", "AUTHORS"):
        if (src["tesseract"] / f).is_file():
            shutil.copy2(src["tesseract"] / f, tmp / f"tesseract-{f}")
    if (src["leptonica"] / "leptonica-license.txt").is_file():
        shutil.copy2(src["leptonica"] / "leptonica-license.txt", tmp / "leptonica-license.txt")
    if dest.exists():
        shutil.rmtree(dest)
    tmp.rename(dest)


def configs_from(work: Path) -> Path | None:
    d = work / "src" / "tesseract" / "tessdata"
    return d if (d / "configs" / "tsv").is_file() else None


def data(work: Path, langs: dict[str, str]) -> Path:
    """The shared language data, and the output configs (tsv) it needs."""
    td = OUT / "tessdata"
    td.mkdir(parents=True, exist_ok=True)
    for lang, sha in langs.items():
        target = td / f"{lang}.traineddata"
        if target.is_file() and _sha256(target) == sha:
            continue
        print(f"downloading {lang}.traineddata", flush=True)
        part = target.with_name(target.name + ".part")
        with urllib.request.urlopen(TESSDATA_URL.format(lang), timeout=120) as r, \
                open(part, "wb") as fh:
            shutil.copyfileobj(r, fh)
        if _sha256(part) != sha:
            part.unlink()
            raise SystemExit(f"{lang}.traineddata: checksum mismatch, download refused")
        part.replace(target)
    if not (td / "configs" / "tsv").is_file():
        src = configs_from(work)
        if src is None:
            fetch(work)
            src = configs_from(work)
        for sub in ("configs", "tessconfigs"):
            if (src / sub).is_dir():
                shutil.copytree(src / sub, td / sub, dirs_exist_ok=True)
        for p in (td / "configs").glob("Makefile*"):
            p.unlink()
        for p in (td / "tessconfigs").glob("Makefile*"):
            p.unlink()
    return td


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check(exe: Path, td: Path) -> None:
    env = dict(os.environ, TESSDATA_PREFIX=str(td))
    v = subprocess.run([str(exe), "--version"], capture_output=True, text=True, env=env)
    langs = subprocess.run([str(exe), "--list-langs"], capture_output=True, text=True, env=env)
    if v.returncode or langs.returncode:
        raise SystemExit(f"{exe} does not run:\n{v.stderr}{langs.stderr}")
    first = (v.stdout or v.stderr).splitlines()[:2]
    found = (langs.stdout or langs.stderr).splitlines()[1:]
    print(f"{exe.relative_to(ROOT)}: {' / '.join(s.strip() for s in first)}; "
          f"languages: {' '.join(found)}")
    if sys.platform.startswith("linux"):
        ldd = subprocess.run(["ldd", str(exe)], capture_output=True, text=True).stdout
        extra = [ln.split()[0] for ln in ldd.splitlines()
                 if "=>" in ln and not any(k in ln for k in
                                           ("libc.so", "libm.so", "libstdc++", "libgcc_s",
                                            "libpthread", "libdl.so", "ld-linux", "librt"))]
        if extra:
            raise SystemExit(f"{exe} needs libraries that are not on every Linux: {extra}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, default=ROOT / "build" / "tesseract-work",
                    help="sources and build folders (can be deleted afterwards)")
    ap.add_argument("--force", action="store_true", help="build again")
    ap.add_argument("--data-only", action="store_true", help="only the language data")
    ap.add_argument("--langs", default="+".join(LANGS),
                    help=f"languages to install, among {'+'.join(LANGS)}")
    args = ap.parse_args()
    unknown = [k for k in args.langs.split("+") if k and k not in LANGS]
    if unknown:
        raise SystemExit(f"unknown languages {unknown}: add them to LANGS with their SHA-256")
    langs = {k: LANGS[k] for k in args.langs.split("+") if k}
    if not args.data_only:
        dest = OUT / os_dir()
        if args.force or not (dest / EXE).is_file():
            build(args.work, dest)
    td = data(args.work, langs)
    if not args.data_only:
        check(OUT / os_dir() / EXE, td)
    return 0


if __name__ == "__main__":
    sys.exit(main())
