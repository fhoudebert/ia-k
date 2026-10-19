@echo off
rem Copy this file to server\local.bat and adapt it. local.bat is not versioned.
rem Every variable is optional: remove a line to keep the default.

set "MODELS_DIR=D:\ia\models"
rem set "LLAMA_DIR=D:\ia\llama.cpp"

rem set "CHAT_PORT=8080"
rem set "EMBED_PORT=8081"

rem --- memory tuning (see README, "Testing a server") ---
rem set "CTX=8192"
rem set "KV_Q8=1"
rem set "VISION=0"
rem set "N_CPU_MOE=8"
rem set "UBATCH=1024"

rem --- explicit paths, when the default layout under MODELS_DIR does not fit ---
rem set "GEMMA26_MODEL=E:\models\gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
rem set "GEMMA26_MMPROJ=E:\models\mmproj-gemma-4-26B-A4B-it-QAT-BF16.gguf"
rem set "QWENVL_MODEL=E:\models\Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
rem set "QWENVL_MMPROJ=E:\models\mmproj-Qwen3-VL-8B-Instruct-F16.gguf"
