"""The portable key: what is left out of the Python, and the merged zip."""
import importlib.util
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_python_filter():
    mp = script("make_portable")
    skip = lambda p, d=False: mp.python_skipped(Path(p), d)  # noqa: E731
    sp = "lib/python3.12/site-packages"
    for p in ["lib/python3.12/encodings/utf_8.py", "lib/python3.12/sqlite3/__init__.py",
              "lib/python3.12/unittest/case.py", "lib/libpython3.12.so.1.0",
              "lib/python3.12/lib-dynload/_ssl.cpython-312-x86_64-linux-gnu.so",
              f"{sp}/numpy/testing/__init__.py", f"{sp}/pymupdf/__init__.py",
              f"{sp}/pip/_internal/cli/main.py", "bin/python3.12", ".complete",
              "Lib/encodings/utf_8.py", "Lib/site-packages/lxml/etree.pyd", "python.exe",
              "DLLs/_sqlite3.pyd", "Lib/test_support.py"]:
        assert not skip(p), p
    for p in ["lib/python3.12/test/test_os.py", "lib/python3.12/idlelib/run.py",
              "lib/python3.12/tkinter/__init__.py", "lib/python3.12/ensurepip/__init__.py",
              "lib/python3.12/__pycache__/os.cpython-312.pyc", "lib/tcl9.0/init.tcl",
              "lib/tk9.0/tk.tcl", "lib/itcl4.3.8/itcl.tcl", f"{sp}/numpy/_core/tests/t.py",
              f"{sp}/bs4/tests/test_tree.py", "share/terminfo/N/ncr260vt", "Lib/test/x.py",
              "Lib/tkinter/ttk.py", "tcl/tcl8.6/init.tcl", "Lib/os.pyc"]:
        assert skip(p), p
    assert skip("lib/python3.12/test", True) and skip(f"{sp}/numpy/tests", True)
    assert not skip(f"{sp}/numpy", True) and not skip("lib/python3.12", True)


def make_zip(path, top, files):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo(f"{top}/data/"), "")
        for name, (data, mode) in files.items():
            info = zipfile.ZipInfo(f"{top}/{name}")
            info.external_attr = mode << 16
            info.create_system = 3
            z.writestr(info, data)
    return path


def test_merge(tmp_path):
    mp = script("merge_portable")
    lin = make_zip(tmp_path / "ia-k-linux.zip", "ia-k-linux", {
        "ia-k": (b"ELF", 0o100755), "iak.sh": (b"#!/bin/sh\n", 0o100755),
        "build/python/linux/bin/python3.12": (b"py", 0o100755),
        "backend/iak/api.py": (b"a = 1\nb = 2\n", 0o100644)})
    win = make_zip(tmp_path / "ia-k-windows.zip", "ia-k-windows", {
        "ia-k.exe": (b"MZ", 0o100666), "build/python/windows/python.exe": (b"MZ", 0o100666),
        "backend/iak/api.py": (b"a = 1\r\nb = 2\r\n", 0o100666)})
    out = tmp_path / "dist" / "ia-k.zip"
    c = mp.merge([lin, win], out)
    assert c == {"files": 6, "shared": 1}
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert "ia-k/data/" in names and "ia-k/ia-k.exe" in names and "ia-k/ia-k" in names
        assert z.read("ia-k/backend/iak/api.py") == b"a = 1\nb = 2\n"        # the Linux one
        assert z.getinfo("ia-k/iak.sh").external_attr >> 16 == 0o100755
        assert z.getinfo("ia-k/build/python/linux/bin/python3.12").external_attr >> 16 \
            == 0o100755
    bad = make_zip(tmp_path / "ia-k-mac.zip", "ia-k-mac",
                   {"backend/iak/api.py": (b"a = 3\n", 0o100644)})
    with pytest.raises(SystemExit, match="differs"):
        mp.merge([lin, bad], tmp_path / "x.zip")
    case = make_zip(tmp_path / "ia-k-case.zip", "ia-k-mac", {"IAK.sh": (b"", 0o100755)})
    with pytest.raises(SystemExit, match="case"):
        mp.merge([lin, case], tmp_path / "y.zip")
