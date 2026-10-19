
@echo off
setlocal
title Qwen2.5-VL 7B - Vulkan

set "LLAMA=D:\ia\llama-vulkan\llama-server.exe"
set "MODEL=D:\ia\models\PatataAliena\Qwen2.5-VL-7B-Instruct-Q4_K_M-GGUF\qwen2.5-vl-7b-instruct-q4_k_m.gguf"

if not exist "%LLAMA%" (
    echo ERREUR : llama-server.exe introuvable.
    pause
    exit /b 1
)

if not exist "%MODEL%" (
    echo ERREUR : modele Qwen2.5-VL introuvable.
    echo Verifie le chemin exact du fichier GGUF.
    pause
    exit /b 1
)

echo.
echo Demarrage de Qwen2.5-VL 7B...
echo API : http://127.0.0.1:8082/v1
echo.

"%LLAMA%" ^
  --model "%MODEL%" ^
  --alias "qwen2.5-vl-7b" ^
  --host 127.0.0.1 ^
  --port 8082 ^
  --ctx-size 8192 ^
  --n-gpu-layers 99 ^
  --threads 8 ^
  --jinja

echo.
echo Serveur arrete.
pause
