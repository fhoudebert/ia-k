
@echo off
setlocal
title Traduction locale - TranslateGemma 12B Vulkan

set "LLAMA=D:\ia\llama-vulkan\llama-server.exe"
set "MODEL=D:\ia\models\NikolayKozloff\translategemma-12b-it-Q4_K_M-GGUF\translategemma-12b-it-q4_k_m.gguf"

if not exist "%LLAMA%" (
    echo ERREUR : llama-server.exe introuvable.
    echo Verifie la variable LLAMA dans ce fichier.
    pause
    exit /b 1
)

if not exist "%MODEL%" (
    echo ERREUR : modele GGUF introuvable.
    pause
    exit /b 1
)

echo.
echo Demarrage du serveur TranslateGemma 12B...
echo Adresse locale : http://127.0.0.1:8080
echo API compatible OpenAI : http://127.0.0.1:8080/v1
echo.
echo Laisse cette fenetre ouverte pendant la traduction.
echo Arret du serveur : Ctrl+C
echo.

"%LLAMA%" ^
  --model "%MODEL%" ^
  --alias "translategemma-12b" ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --ctx-size 8192 ^
  --n-gpu-layers 99 ^
  --threads 8 ^
  --jinja

echo.
echo Le serveur est arrete.
pause
