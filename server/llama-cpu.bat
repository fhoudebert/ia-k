
@echo off
setlocal
title Traduction locale - TranslateGemma 12B CPU

set "LLAMA=D:\ia\llama-cpu\llama-server.exe"
set "MODEL=D:\ia\models\NikolayKozloff\translategemma-12b-it-Q4_K_M-GGUF\translategemma-12b-it-q4_k_m.gguf"

if not exist "%LLAMA%" (
    echo ERREUR : llama-server.exe CPU introuvable.
    pause
    exit /b 1
)

if not exist "%MODEL%" (
    echo ERREUR : modele GGUF introuvable.
    pause
    exit /b 1
)

"%LLAMA%" ^
  --model "%MODEL%" ^
  --alias "translategemma-12b" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --ctx-size 8192 ^
  --threads 12 ^
  --jinja

pause
