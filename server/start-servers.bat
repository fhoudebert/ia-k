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
rem  stopped (unless --keep). When IA-K is already open (started by another
rem  serveurs-*.bat file), no second IA-K starts: the open one uses the new
rem  servers, and they are stopped when it closes.
rem  ONE_BIG_MODEL=1 (settings): starting a big model (gemma26, gemma12,
rem  qwenvl, translate) first stops the other big ones started by a
rem  serveurs-*.bat file, as one 32 GB laptop holds only one of them.
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
set "ONE_BIG_MODEL=0"
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
if not "%ONE_BIG_MODEL%"=="1" goto big_done
rem the big models asked for, then stop the other big ones
set "BIG="
for %%P in (%PROFILES%) do (
    for %%B in (gemma12 gemma26 qwenvl translate) do if /i "%%P"=="%%B" set "BIG=!BIG! %%B"
)
if not defined BIG goto big_done
set "STOPPED=0"
for %%B in (gemma12 gemma26 qwenvl translate) do (
    set "WANTED=0"
    for %%W in (!BIG!) do if /i "%%W"=="%%B" set "WANTED=1"
    if "!WANTED!"=="0" (
        tasklist /V /FI "WINDOWTITLE eq IA-K server %%B*" 2>nul | find /I "IA-K server %%B" >nul
        if !errorlevel!==0 (
            echo   stopping %%B: one big model at a time
            taskkill /F /T /FI "WINDOWTITLE eq IA-K server %%B*" >nul 2>nul
            set "STOPPED=1"
        )
    )
)
rem let the graphics memory and the port go
if "!STOPPED!"=="1" timeout /t 3 /nobreak >nul
:big_done

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
call :app_running
if "%RUNNING%"=="1" goto already_open
if not exist "%ROOT%\ia-k.exe" (
    echo ia-k.exe not found in %ROOT%
    goto stop
)
echo Starting IA-K...
start "" /wait "%ROOT%\ia-k.exe"
goto stop

:already_open
rem another serveurs-*.bat started IA-K: it uses these servers as they come
echo IA-K is already open: it uses these servers now. They stop when IA-K closes.
:open_loop
timeout /t 5 /nobreak >nul
call :app_running
if "%RUNNING%"=="1" goto open_loop

:stop
if "%KEEP%"=="1" goto end
if not defined STARTED goto end
echo Stopping the servers started here:%STARTED%
for %%P in (%STARTED%) do taskkill /F /T /FI "WINDOWTITLE eq IA-K server %%P*" >nul 2>nul
:end
endlocal
exit /b 0

rem ---------------------------------------------------------------------------
:app_running
set "RUNNING=0"
tasklist /FI "IMAGENAME eq ia-k.exe" /NH 2>nul | find /I "ia-k.exe" >nul
if %errorlevel%==0 set "RUNNING=1"
exit /b 0

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
