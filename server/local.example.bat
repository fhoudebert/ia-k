@echo off
rem Copy this file to server\local.bat and adapt it. local.bat is not versioned.
rem It holds the settings of llama-server.bat and of the serveurs-*.bat files
rem (start-servers.bat), for every profile: gemma26, gemma12, qwenvl,
rem translate, embed. The single launchers (Gemma-4-26-A4B-vulkan.bat,
rem embeddinggemma-rag.bat, ...) do not read it: their paths are in them.
rem server\local.hx370.bat is a complete example (the reference laptop).
rem Every variable is optional: remove a line to keep the default.

set "MODELS_DIR=D:\ia\models"
rem set "LLAMA_DIR=D:\ia\llama.cpp"

rem --- 1 = one big model at a time: starting the 26B, the 12B, Qwen3-VL or
rem     TranslateGemma stops the other one started by a serveurs-*.bat file
rem set "ONE_BIG_MODEL=1"

rem --- chat server (profiles gemma26, gemma12), memory tuning (SERVERS.md, "Memory") ---
rem set "CHAT_PORT=8080"
rem set "CTX=8192"
rem set "KV_Q8=1"
rem set "VISION=0"
rem set "N_CPU_MOE=8"
rem set "UBATCH=2048"
rem set "MMPROJ_CPU=0"
rem set "GEMMA26_MODEL=E:\models\gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
rem set "GEMMA26_MMPROJ=E:\models\mmproj-gemma-4-26B-A4B-it-QAT-BF16.gguf"
rem set "GEMMA12_MODEL=E:\models\gemma-4-12B-it-QAT-Q4_0.gguf"
rem set "GEMMA12_MMPROJ=E:\models\mmproj-gemma-4-12B-it-QAT-BF16.gguf"

rem --- vision server (profile qwenvl, serveurs-vl.bat) ---
rem set "VISION_PORT=8082"
rem set "QWENVL_UBATCH=2048"
rem set "QWENVL_CTX=12288"
rem set "QWENVL_MODEL=E:\models\Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
rem set "QWENVL_MMPROJ=E:\models\mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

rem --- translation server (profile translate, serveurs-traduction.bat) ---
rem set "TRANSLATE_PORT=8083"
rem set "TRANSLATE_CTX=8192"
rem set "TRANSLATE_MODEL=E:\models\translategemma-12b-it-q4_k_m.gguf"

rem --- embedding server (profile embed, every serveurs-*.bat), the same
rem     server as embeddinggemma-rag.bat: on the CPU by default (99 = iGPU)
rem set "EMBED_PORT=8081"
rem set "EMBED_NGL=0"
rem set "EMBED_MODEL=E:\models\embeddinggemma-300m-qat-Q4_0.gguf"

rem --- CPU threads of every server (empty = llama.cpp's choice) ---
rem set "THREADS=8"

rem --- seconds to wait for a model to load (slow from a USB key) ---
rem set "WAIT_SECONDS=300"
