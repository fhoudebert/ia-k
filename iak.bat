@echo off
rem Run the IA-K backend with the portable Python of the key (setup_python.bat).
rem   iak.bat import doc.pdf    iak.bat search "words"    iak.bat --help
rem The library defaults to data\ next to this file (IAK_LIBRARY overrides).
rem A Tesseract copied into build\tesseract is used first.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%build\python\windows\python.exe"
if not exist "%PY%" goto no_python
if not defined IAK_LIBRARY set "IAK_LIBRARY=%ROOT%data"
if exist "%ROOT%build\tesseract\tesseract.exe" set "IAK_TESSERACT=%ROOT%build\tesseract\tesseract.exe"
if exist "%ROOT%build\tesseract\tessdata" set "TESSDATA_PREFIX=%ROOT%build\tesseract\tessdata"
set "PYTHONPATH=%ROOT%backend"
set "PYTHONUTF8=1"
"%PY%" -m iak %*
exit /b %ERRORLEVEL%

:no_python
echo Portable Python missing: run setup_python.bat first
exit /b 1
