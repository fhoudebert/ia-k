
@echo off
setlocal
title Gemma 4 26B A4B - Vulkan - Radeon 890M

set "LLAMA=D:\ia\llama-vulkan\llama-server.exe"
set "DIR=D:\ia\models\lmstudio-community\gemma-4-26B-A4B-it-QAT-GGUF"

set "MODEL=%DIR%\gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
set "MMPROJ=%DIR%\mmproj-gemma-4-26B-A4B-it-QAT-BF16.gguf"

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
echo ==========================================
echo Gemma 4 26B A4B Q4_0 - Vulkan
echo AMD Ryzen AI 9 HX 370 / Radeon 890M
echo ==========================================
echo.
echo Interface : http://127.0.0.1:8080
echo API       : http://127.0.0.1:8080/v1
echo.

"%LLAMA%" ^
  --model "%MODEL%" ^
  --mmproj "%MMPROJ%" ^
  --alias "gemma-4-26B-A4B-Q4_0" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --ctx-size 2048 ^
  --n-gpu-layers 99 ^
  --no-mmproj-offload ^
  --threads 8 ^
  --threads-batch 8 ^
  --parallel 1 ^
  --batch-size 64 ^
  --ubatch-size 32 ^
  --jinja

echo.
echo Serveur arrete.
pause
endlocal
