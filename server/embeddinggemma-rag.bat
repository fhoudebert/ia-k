
@echo off
setlocal
title EmbeddingGemma 300M - RAG Vulkan

set "LLAMA=D:\ia\llama-vulkan\llama-server.exe"
set "MODEL=D:\ia\models\lmstudio-community\embeddinggemma-300m-qat-GGUF\embeddinggemma-300m-qat-Q4_0.gguf"

if not exist "%LLAMA%" (
    echo ERREUR : llama-server.exe introuvable.
    pause
    exit /b 1
)

if not exist "%MODEL%" (
    echo ERREUR : modele EmbeddingGemma introuvable.
    echo Verifie le chemin exact dans D:\ia\models.
    pause
    exit /b 1
)

echo.
echo Demarrage d'EmbeddingGemma 300M QAT...
echo API : http://127.0.0.1:8081/v1
echo.

"%LLAMA%" ^
  --model "%MODEL%" ^
  --alias "embeddinggemma-300m" ^
  --host 127.0.0.1 ^
  --port 8081 ^
  --ctx-size 2048 ^
  --n-gpu-layers 99 ^
  --threads 4 ^
  --embedding ^
  --pooling mean

echo.
echo Le serveur EmbeddingGemma est arrete.
pause
