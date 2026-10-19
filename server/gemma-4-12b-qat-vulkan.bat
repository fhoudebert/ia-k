
@echo off
setlocal
title Gemma 4 12B QAT - Vulkan

set "LLAMA=D:\ia\llama-vulkan\llama-server.exe"
set "DIR=D:\ia\models\lmstudio-community\gemma-4-12B-it-QAT-GGUF"

set "MODEL=%DIR%\gemma-4-12B-it-QAT-Q4_0.gguf"
set "MMPROJ=%DIR%\mmproj-gemma-4-12B-it-QAT-BF16.gguf"

if not exist "%LLAMA%" (
    echo ERREUR : llama-server.exe introuvable.
    pause
    exit /b 1
)

if not exist "%MODEL%" (
    echo ERREUR : modele GGUF introuvable.
    echo %MODEL%
    pause
    exit /b 1
)

if not exist "%MMPROJ%" (
    echo ERREUR : fichier mmproj introuvable.
    echo %MMPROJ%
    pause
    exit /b 1
)

echo.
echo Demarrage de Gemma 4 12B QAT Vulkan...
echo Serveur : http://127.0.0.1:8080
echo API     : http://127.0.0.1:8080/v1
echo.

"%LLAMA%" ^
  --model "%MODEL%" ^
  --mmproj "%MMPROJ%" ^
  --alias "gemma-4-12b-qat" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --ctx-size 8192 ^
  --n-gpu-layers 99 ^
  --threads 8 ^
  --threads-batch 8 ^
  --jinja

echo.
echo Serveur Gemma 4 arrete.
pause
