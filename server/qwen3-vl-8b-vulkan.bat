@echo off
setlocal
title Qwen3-VL 8B - vision - Vulkan

rem Dedicated vision model for IA-K (port 8082, the "vision" server of
rem config\servers.toml): reading difficult scans, screenshots, tables.
rem It takes about 6 GB: on a 32 GB laptop, run it instead of the 26B-A4B
rem (with Gemma 4 12B as chat model, for instance), not beside it.
rem Model: https://huggingface.co/lmstudio-community/Qwen3-VL-8B-Instruct-GGUF

set "LLAMA=D:\ia\llama-vulkan\llama-server.exe"
set "DIR=D:\ia\models\lmstudio-community\Qwen3-VL-8B-Instruct-GGUF"
set "MODEL=%DIR%\Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
set "MMPROJ=%DIR%\mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

if not exist "%LLAMA%" (
    echo ERREUR : llama-server.exe introuvable.
    pause
    exit /b 1
)
if not exist "%MODEL%" (
    echo ERREUR : modele introuvable.
    echo %MODEL%
    pause
    exit /b 1
)
if not exist "%MMPROJ%" (
    echo ERREUR : mmproj introuvable.
    echo %MMPROJ%
    pause
    exit /b 1
)

echo.
echo Qwen3-VL 8B - API : http://127.0.0.1:8082/v1
echo.

rem --ubatch-size 2048: an image may need more than 1,000 tokens, and
rem llama.cpp caps them to the micro-batch.
"%LLAMA%" ^
  --model "%MODEL%" ^
  --mmproj "%MMPROJ%" ^
  --alias "qwen3-vl-8b" ^
  --host 127.0.0.1 ^
  --port 8082 ^
  --ctx-size 8192 ^
  --n-gpu-layers 99 ^
  --ubatch-size 2048 ^
  --threads 8 ^
  --parallel 1 ^
  --jinja

echo.
echo Serveur arrete.
pause
endlocal
