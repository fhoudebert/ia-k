@echo off
rem Copy this file to server\local.bat and adapt it. local.bat is not versioned.
rem Every variable is optional: remove a line to keep the default.

set "MODELS_DIR=D:\ia\models"
rem set "LLAMA_DIR=D:\ia\llama.cpp"

rem set "CHAT_PORT=8080"
rem set "EMBED_PORT=8081"

rem --- memory tuning (see SERVERS.md, "Memory") ---
rem set "CTX=8192"
rem set "KV_Q8=1"
rem set "VISION=0"
rem set "N_CPU_MOE=8"
rem set "UBATCH=2048"
rem set "MMPROJ_CPU=0"

rem --- explicit paths, when the default layout under MODELS_DIR does not fit ---
rem set "GEMMA26_MODEL=E:\models\gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
rem set "GEMMA26_MMPROJ=E:\models\mmproj-gemma-4-26B-A4B-it-QAT-BF16.gguf"
rem set "QWENVL_MODEL=E:\models\Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
rem set "QWENVL_MMPROJ=E:\models\mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

rem --- translation server (TranslateGemma) ---
rem set "TRANSLATE_PORT=8083"
rem set "TRANSLATE_CTX=8192"
rem set "TRANSLATE_MODEL=E:\models\translategemma-12b-it-q4_k_m.gguf"

rem --- CPU threads of every server (empty = llama.cpp's choice) ---
rem set "THREADS=8"
