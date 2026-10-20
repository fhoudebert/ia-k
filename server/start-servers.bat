@echo off
rem ============================================================================
rem  start-servers.bat - start the model servers IA-K needs, then IA-K
rem
rem  Usage : server\start-servers.bat PROFILE... [--keep] [--no-app]
rem    PROFILE   gemma12 gemma26 qwenvl embed translate (see llama-server.bat)
rem    --keep    leave the servers running when IA-K is closed
rem    --no-app  only start the servers
rem
rem  Each server opens in its own minimised window (its messages stay visible
rem  there). A server already answering on its port is not started again.
rem  Once all answer, IA-K starts; when it closes, the servers started here are
rem  stopped (unless --keep).
rem
rem  Settings: IAK_SERVER_SETTINGS, else server\local.bat, else
rem  server\local.hx370.bat (the reference laptop).
rem  Called by serveurs-baseConnaissance.bat, serveurs-traduction.bat, ...
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion
set "HERE=%~dp0"
for %%R in ("%HERE%..") do set "ROOT=%%~fR"

if not defined IAK_SERVER_SETTINGS if exist "%HERE%local.bat" set "IAK_SERVER_SETTINGS=%HERE%local.bat"
if not defined IAK_SERVER_SETTINGS if exist "%HERE%local.hx370.bat" set "IAK_SERVER_SETTINGS=%HERE%local.hx370.bat"

rem ports: same defaults and settings file as llama-server.bat
set "CHAT_PORT=8080"
set "EMBED_PORT=8081"
set "VISION_PORT=8082"
set "TRANSLATE_PORT=8083"
rem seconds to wait for a model to load (a 15 GB model from a USB key is slow)
set "WAIT_SECONDS=300"
if defined IAK_SERVER_SETTINGS call "%IAK_SERVER_SETTINGS%"

set "KEEP=0"
set "APP=1"
set "PROFILES="
:args
if "%~1"=="" goto args_done
if /i "%~1"=="--keep" (set "KEEP=1") else if /i "%~1"=="--no-app" (set "APP=0") else set "PROFILES=!PROFILES! %~1"
shift
goto args
:args_done
if not defined PROFILES (
    echo usage: server\start-servers.bat PROFILE... [--keep] [--no-app]
    exit /b 1
)

echo IA-K - settings: %IAK_SERVER_SETTINGS%
set "STARTED="
for %%P in (%PROFILES%) do (
    call :port_of %%P
    if not defined PORT (
        echo   unknown profile %%P
        exit /b 1
    )
    curl.exe -s -f -m 2 http://127.0.0.1:!PORT!/health >nul 2>nul
    if !errorlevel!==0 (
        echo   %%P already running on port !PORT!
    ) else (
        echo   starting %%P on port !PORT!
        start "IA-K server %%P" /min "%HERE%llama-server.bat" %%P
        set "STARTED=!STARTED! %%P"
    )
)

rem wait until every server answers (503 while the model loads)
for %%P in (%PROFILES%) do (
    call :port_of %%P
    call :wait_for %%P !PORT!
)

if "%APP%"=="0" goto end
if not exist "%ROOT%\ia-k.exe" (
    echo ia-k.exe not found in %ROOT%
    goto stop
)
echo Starting IA-K...
start "" /wait "%ROOT%\ia-k.exe"

:stop
if "%KEEP%"=="1" goto end
if not defined STARTED goto end
echo Stopping the servers started here:%STARTED%
for %%P in (%STARTED%) do taskkill /F /T /FI "WINDOWTITLE eq IA-K server %%P*" >nul 2>nul
:end
endlocal
exit /b 0

rem ---------------------------------------------------------------------------
:port_of
set "PORT="
if /i "%~1"=="gemma12" set "PORT=%CHAT_PORT%"
if /i "%~1"=="gemma26" set "PORT=%CHAT_PORT%"
if /i "%~1"=="embed" set "PORT=%EMBED_PORT%"
if /i "%~1"=="qwenvl" set "PORT=%VISION_PORT%"
if /i "%~1"=="translate" set "PORT=%TRANSLATE_PORT%"
exit /b 0

:wait_for
set /a "LEFT=%WAIT_SECONDS%"
<nul set /p "=  waiting for %~1 (port %~2)"
:wait_loop
curl.exe -s -f -m 2 http://127.0.0.1:%~2/health >nul 2>nul
if %errorlevel%==0 (
    echo  ready
    exit /b 0
)
set /a "LEFT-=2"
if %LEFT% leq 0 (
    echo  not answering after %WAIT_SECONDS% s: see its window "IA-K server %~1"
    exit /b 1
)
<nul set /p "=."
timeout /t 2 /nobreak >nul
goto wait_loop
