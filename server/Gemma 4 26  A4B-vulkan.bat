@echo off
setlocal
title Gemma 4 26B A4B - Vulkan - Radeon 890M

rem Usage : "Gemma 4 26  A4B-vulkan.bat" [N]
rem   N = number of layers whose experts stay in system RAM, computed by the
rem       CPU (--n-cpu-moe). 99 (default) = all of them: only attention and
rem       shared weights (~3 GB) go to the iGPU, so the model always loads.
rem       Then lower N (30, 20, 15...) until loading fails, and keep the last
rem       value that worked: the lower N, the faster, the more iGPU memory.

set "LLAMA=D:\ia\llama-vulkan\llama-server.exe"
set "DIR=D:\ia\models\lmstudio-community\gemma-4-26B-A4B-it-QAT-GGUF"

set "MODEL=%DIR%\gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
set "MMPROJ=%DIR%\mmproj-gemma-4-26B-A4B-it-QAT-BF16.gguf"

set "NCPUMOE=%~1"
if "%NCPUMOE%"=="" set "NCPUMOE=99"

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
echo experts on CPU for %NCPUMOE% layers
echo ==========================================
echo.
echo Interface : http://127.0.0.1:8080
echo API       : http://127.0.0.1:8080/v1
echo.

rem Log kept next to this file: send the end of it if loading fails.
"%LLAMA%" ^
  --model "%MODEL%" ^
  --mmproj "%MMPROJ%" ^
  --alias "gemma-4-26B-A4B-Q4_0" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --ctx-size 8192 ^
  --n-gpu-layers 99 ^
  --n-cpu-moe %NCPUMOE% ^
  --threads 8 ^
  --threads-batch 8 ^
  --parallel 1 ^
  --jinja ^
  --log-file "%~dp0gemma-26b.log"

echo.
echo Serveur arrete.
pause
endlocal
