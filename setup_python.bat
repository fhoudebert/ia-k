@echo off
rem ============================================================================
rem  setup_python.bat - portable Python for IA-K on Windows (key or disk)
rem
rem  Installs a complete, relocatable CPython (python-build-standalone) in
rem  build\python\windows and the packages of backend\requirements.lock.
rem  Unlike a venv, it does not depend on a Python installed on the computer.
rem  Linux has its own copy (setup_python.sh) on the same key.
rem  Needs Windows 10 1803 or later (curl.exe and tar.exe are built in).
rem
rem  Safe to run again. "setup_python.bat --force" reinstalls Python.
rem ============================================================================
setlocal EnableExtensions
set "PBS_TAG=20261013"
set "PY_VERSION=3.12.15"
set "ASSET=cpython-%PY_VERSION%+%PBS_TAG%-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
set "SHA256=7a2aef48e5ee569e310b29b32681debbc5d888e92790d85331a47736c6b06aad"
set "URL=https://github.com/astral-sh/python-build-standalone/releases/download/%PBS_TAG%/%ASSET%"

set "ROOT=%~dp0"
set "DEST=%ROOT%build\python\windows"
set "PY=%DEST%\python.exe"

rem A Python is only kept when the ".complete" marker of a finished install
rem is there: an interrupted install (missing files) is redone.
if /i "%~1"=="--force" if exist "%DEST%" rmdir /s /q "%DEST%"
if exist "%DEST%\.complete" goto have_python
if exist "%DEST%" (
    echo [1/3] Incomplete Python found, installing again
    rmdir /s /q "%DEST%"
)

rem Download and extract on the key itself (not in %%TEMP%%, usually on C:),
rem so that the final step is a rename on the same drive, not a copy.
if not exist "%ROOT%build\python" mkdir "%ROOT%build\python"
set "TMPD=%ROOT%build\python\tmp-%RANDOM%"
mkdir "%TMPD%" || goto fail
echo [1/3] Downloading Python %PY_VERSION%
curl.exe -fL --retry 3 -o "%TMPD%\py.tar.gz" "%URL%" || goto fail_dl
set "GOT="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%TMPD%\py.tar.gz" SHA256') do if not defined GOT set "GOT=%%H"
set "GOT=%GOT: =%"
if /i not "%GOT%"=="%SHA256%" (
    echo checksum mismatch, download refused
    goto fail_dl
)
echo        extracting...
tar.exe -xzf "%TMPD%\py.tar.gz" -C "%TMPD%" || goto fail_dl
move "%TMPD%\python" "%DEST%" >nul || goto fail_dl
rmdir /s /q "%TMPD%" 2>nul
rem the standard library and pip must be whole before going on
"%PY%" -c "import encodings, sqlite3, ssl, pip._vendor.rich, pip._internal.cli.main" || goto fail_broken
echo ok> "%DEST%\.complete"
goto installed

:have_python
echo [1/3] Python already present:
"%PY%" --version

:installed
echo [2/3] Installing packages (backend\requirements.lock)
"%PY%" -m pip install --disable-pip-version-check --no-warn-script-location -q -r "%ROOT%backend\requirements.lock" || goto fail

echo [3/3] Checking
"%PY%" -c "import pymupdf, docx, numpy, bs4, sqlite3; c = sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print('PyMuPDF', pymupdf.__version__, '- numpy', numpy.__version__, '- SQLite', sqlite3.sqlite_version, 'with FTS5')" || goto fail
if exist "%ROOT%build\tesseract\windows\tesseract.exe" (
    echo Tesseract: on the key, build\tesseract\windows
) else if exist "%ROOT%build\tesseract\tesseract.exe" (
    echo Tesseract: build\tesseract\tesseract.exe
) else (
    where tesseract.exe >nul 2>nul && echo Tesseract found in PATH
    where tesseract.exe >nul 2>nul || if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" echo Tesseract: C:\Program Files\Tesseract-OCR
    where tesseract.exe >nul 2>nul || if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" echo Tesseract not found: see README, "What you need"
)
echo Done. Run IA-K with: iak.bat --help
endlocal
exit /b 0

:fail_broken
echo The extracted Python is incomplete (files missing on the key?).
echo An antivirus or a full key are the usual causes; run setup_python.bat again.
if exist "%DEST%" rmdir /s /q "%DEST%"
goto fail
:fail_dl
rmdir /s /q "%TMPD%" 2>nul
:fail
echo Setup failed.
endlocal
exit /b 1
