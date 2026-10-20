@echo off
rem ============================================================================
rem  llama-server.bat - start a local llama.cpp server for IA-K (Windows)
rem
rem  Usage : server\llama-server.bat [profile] [extra llama-server options]
rem    gemma12  Gemma 4 12B QAT + vision projector   (default, port CHAT_PORT)
rem    gemma26  Gemma 4 26B-A4B QAT + vision          (port CHAT_PORT)
rem    qwenvl   optional dedicated vision model       (port VISION_PORT)
rem    embed    EmbeddingGemma 300M, embeddings only  (port EMBED_PORT)
rem
rem  Personal settings (model paths, ports, memory options) go in
rem  server\local.bat : copy local.example.bat and edit it. local.bat is
rem  ignored by git.
rem
rem  The server listens on 127.0.0.1 only. Stop it with Ctrl+C.
rem ============================================================================
setlocal EnableExtensions
set "HERE=%~dp0"
for %%R in ("%HERE%..") do set "ROOT=%%~fR"

rem --- defaults (any of them can be redefined in local.bat) ------------------
set "MODELS_DIR=D:\ia\models"
set "LLAMA_DIR=%ROOT%\build\llama"
set "HOST=127.0.0.1"
set "CHAT_PORT=8080"
set "EMBED_PORT=8081"
rem embeddings on the CPU by default: the model is small, and the iGPU memory
rem is better left to the chat model (99 = on the iGPU)
set "EMBED_NGL=0"
set "VISION_PORT=8082"
rem context of the chat server (prompt + answer). 16384 leaves room for
rem ~8000 tokens of retrieved passages; lower it to save memory.
set "CTX=16384"
rem layers offloaded to the iGPU (99 = all)
set "NGL=99"
rem MoE models: keep the experts of N layers in system RAM, computed by the
rem CPU. Empty = profile default: 99 (all) for gemma26, which does not fit
rem in the iGPU memory otherwise; 0 = everything on the iGPU. Lower values
rem are faster as long as the model still loads.
set "N_CPU_MOE="
rem 1 = run the vision projector on the CPU (saves ~1 GB of iGPU memory,
rem images ~4x slower). Empty = profile default: 1 for gemma26, else 0.
set "MMPROJ_CPU="
rem 1 = quantize the KV cache to q8_0 (about half the cache memory)
set "KV_Q8=0"
rem 0 = do not load the vision projector (saves ~1 GB, text chat only)
set "VISION=1"
rem micro-batch for prompt processing. Empty = 1280 when vision is on (an
rem image may use up to 1120 tokens, llama.cpp caps it to the micro-batch),
rem else the llama.cpp default (512). Above 2048, add -b in the arguments.
set "UBATCH="

if exist "%HERE%local.bat" call "%HERE%local.bat"

rem --- model paths (defaults built from MODELS_DIR after local.bat) ----------
if not defined GEMMA12_MODEL set "GEMMA12_MODEL=%MODELS_DIR%\lmstudio-community\gemma-4-12B-it-QAT-GGUF\gemma-4-12B-it-QAT-Q4_0.gguf"
if not defined GEMMA26_MODEL set "GEMMA26_MODEL=%MODELS_DIR%\lmstudio-community\gemma-4-26B-A4B-it-QAT-GGUF\gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
if not defined QWENVL_MODEL  set "QWENVL_MODEL=%MODELS_DIR%\lmstudio-community\Qwen3-VL-8B-Instruct-GGUF\Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
if not defined EMBED_MODEL   set "EMBED_MODEL=%MODELS_DIR%\lmstudio-community\embeddinggemma-300m-qat-GGUF\embeddinggemma-300m-qat-Q4_0.gguf"

rem --- arguments ---------------------------------------------------------------
set "PROFILE=%~1"
if "%PROFILE%"=="" set "PROFILE=gemma12"
set "EXTRA="
:collect
shift
if "%~1"=="" goto collected
set EXTRA=%EXTRA% %1
goto collect
:collected

rem --- llama-server executable: LLAMA_DIR first, then PATH -----------------
set "SERVER=%LLAMA_DIR%\llama-server.exe"
if exist "%SERVER%" goto server_ok
set "SERVER="
for /f "delims=" %%S in ('where llama-server.exe 2^>nul') do if not defined SERVER set "SERVER=%%S"
if defined SERVER goto server_ok
echo [ia-k] llama-server.exe not found.
echo        Unzip a Vulkan build of llama.cpp (llama-bXXXX-bin-win-vulkan-x64.zip)
echo        into "%LLAMA_DIR%" or set LLAMA_DIR in server\local.bat.
exit /b 1
:server_ok

if /i "%PROFILE%"=="gemma12" goto p_gemma12
if /i "%PROFILE%"=="gemma26" goto p_gemma26
if /i "%PROFILE%"=="qwenvl"  goto p_qwenvl
if /i "%PROFILE%"=="embed"   goto p_embed
echo [ia-k] unknown profile: %PROFILE%
echo        expected: gemma12 ^| gemma26 ^| qwenvl ^| embed
exit /b 1

:p_gemma12
set "MODEL=%GEMMA12_MODEL%"
set "MMPROJ=%GEMMA12_MMPROJ%"
set "ALIAS=gemma-4-12b"
set "PORT=%CHAT_PORT%"
goto chat

:p_gemma26
set "MODEL=%GEMMA26_MODEL%"
set "MMPROJ=%GEMMA26_MMPROJ%"
set "ALIAS=gemma-4-26b-a4b"
set "PORT=%CHAT_PORT%"
if not defined N_CPU_MOE set "N_CPU_MOE=99"
if not defined MMPROJ_CPU set "MMPROJ_CPU=1"
goto chat

:p_qwenvl
set "MODEL=%QWENVL_MODEL%"
set "MMPROJ=%QWENVL_MMPROJ%"
set "ALIAS=vision"
set "PORT=%VISION_PORT%"
set "VISION=1"
goto chat

rem ---------------------------------------------------------------------------
:chat
if not exist "%MODEL%" goto model_missing
set "MMOPT="
if "%VISION%"=="0" goto no_vision
rem vision projector: explicit path, else first mmproj*.gguf next to the model
if defined MMPROJ goto have_mmproj
for %%F in ("%MODEL%") do set "MDIR=%%~dpF"
for %%F in ("%MDIR%mmproj*.gguf") do if not defined MMPROJ set "MMPROJ=%%~fF"
:have_mmproj
if not defined MMPROJ goto mmproj_missing
if not exist "%MMPROJ%" goto mmproj_missing
set MMOPT=--mmproj "%MMPROJ%"
if "%MMPROJ_CPU%"=="1" set MMOPT=%MMOPT% --no-mmproj-offload
if not defined UBATCH set "UBATCH=1280"
:no_vision
set "KVOPT="
if "%KV_Q8%"=="1" set "KVOPT=-ctk q8_0 -ctv q8_0"
set "UBOPT="
if defined UBATCH set "UBOPT=-ub %UBATCH%"
set "MOEOPT="
if defined N_CPU_MOE set "MOEOPT=--n-cpu-moe %N_CPU_MOE%"

echo [ia-k] profile %PROFILE% on http://%HOST%:%PORT%/v1  (alias %ALIAS%)
echo        model  : %MODEL%
if defined MMOPT echo        mmproj : %MMPROJ%
echo        ctx=%CTX% ngl=%NGL% kv_q8=%KV_Q8% n_cpu_moe=%N_CPU_MOE% mmproj_cpu=%MMPROJ_CPU% ubatch=%UBATCH%
echo.
"%SERVER%" -m "%MODEL%" %MMOPT% --alias %ALIAS% --host %HOST% --port %PORT% ^
    -c %CTX% -ngl %NGL% -np 1 --jinja %UBOPT% %KVOPT% %MOEOPT% %EXTRA%
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------------------
:p_embed
set "MODEL=%EMBED_MODEL%"
if not exist "%MODEL%" goto model_missing
echo [ia-k] embeddings on http://%HOST%:%EMBED_PORT%/v1  (alias embeddinggemma)
echo        model  : %MODEL%
echo.
rem 4 slots of 2048 tokens (the model's maximum input); a request may batch
rem many passages at once.
"%SERVER%" -m "%MODEL%" --alias embeddinggemma --embeddings --host %HOST% --port %EMBED_PORT% ^
    -c 8192 -np 4 -b 2048 -ub 2048 -ngl %EMBED_NGL% %EXTRA%
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------------------
:model_missing
echo [ia-k] model not found: %MODEL%
echo        set MODELS_DIR or the *_MODEL variables in server\local.bat
exit /b 1

:mmproj_missing
echo [ia-k] no vision projector (mmproj*.gguf) found next to %MODEL%
echo        set the matching *_MMPROJ variable in server\local.bat,
echo        or VISION=0 for text only.
exit /b 1
