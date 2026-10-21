#!/usr/bin/env python3
"""Merge the portable zips of each system into one key for all of them.

    python scripts/merge_portable.py dist/ia-k-linux.zip dist/ia-k-mac.zip \\
        dist/ia-k-windows.zip -o dist/ia-k.zip

Each zip holds one folder, ia-k-<os>/; the result holds ia-k/, with the
application, the scripts and the portable Python of every system side by
side, and one copy of what they share (engine, guides). A shared file must be
the same in every zip, line endings aside (a Windows checkout may write CRLF):
the first zip given wins, so give the Linux one first. Permissions (the
executable bit of the Linux and macOS files) are kept. Two names differing
only by case are refused: exFAT and FAT32 keys cannot hold both.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

TOP = "ia-k"


def merge(zips: list[Path], out: Path, top: str = TOP) -> dict:
    seen: dict[str, bytes] = {}
    folded: dict[str, str] = {}
    counts = {"files": 0, "shared": 0}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    try:
        _merge(zips, tmp, top, seen, folded, counts)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(out)
    return counts


def _merge(zips, tmp, top, seen, folded, counts):
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
        for src_path in zips:
            with zipfile.ZipFile(src_path) as src:
                for info in src.infolist():
                    head, _, rest = info.filename.partition("/")
                    if not head.startswith("ia-k"):
                        raise SystemExit(f"{src_path}: {info.filename} is outside ia-k-<os>/")
                    name = f"{top}/{rest}" if rest else f"{top}/"
                    other = folded.setdefault(name.rstrip("/").casefold(), name)
                    if other.rstrip("/") != name.rstrip("/"):
                        raise SystemExit(f"{other} and {name}: names differing only by case")
                    if info.is_dir():
                        if name not in seen:
                            seen[name] = b""
                            dst.writestr(zipfile.ZipInfo(name, info.date_time), "")
                        continue
                    data = src.read(info)
                    if name in seen:
                        if _normal(seen[name]) != _normal(data):
                            raise SystemExit(f"{rest} differs between the zips ({src_path})")
                        counts["shared"] += 1
                        continue
                    seen[name] = data
                    new = zipfile.ZipInfo(name, info.date_time)
                    new.external_attr = info.external_attr
                    new.create_system = info.create_system
                    new.compress_type = zipfile.ZIP_DEFLATED
                    dst.writestr(new, data)
                    counts["files"] += 1


def _normal(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zips", nargs="+", type=Path, help="ia-k-<os>.zip, the Linux one first")
    ap.add_argument("-o", "--out", type=Path, default=Path("dist") / "ia-k.zip")
    args = ap.parse_args()
    c = merge(args.zips, args.out)
    print(f"{args.out} ({args.out.stat().st_size / 1e6:.1f} MB): {c['files']} files, "
          f"{c['shared']} shared copies dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
