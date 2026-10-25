@echo off
rem ============================================================================
rem  local.hx370.bat - settings of the reference laptop (Ryzen AI 9 HX 370,
rem  Radeon 890M, 32 GB, Windows), the same as the tested single launchers
rem  Gemma-4-26-A4B-vulkan.bat, gemma-4-12b-qat-vulkan.bat,
rem  qwen3-vl-8b-vulkan.bat and embeddinggemma-rag.bat.
rem
rem  Used by serveurs-*.bat (start-servers.bat, llama-server.bat) when
rem  server\local.bat does not exist. To adapt it to another computer, copy
rem  it to server\local.bat (not versioned) and edit the copy.
rem ============================================================================

rem --- where things are ---
set "LLAMA_DIR=D:\ia\llama-vulkan"
set "MODELS_DIR=D:\ia\models"

rem --- one big model at a time: 32 GB do not hold two of the 26B, the 12B,
rem     TranslateGemma and Qwen3-VL. Starting one (serveurs-vl.bat after
rem     serveurs-baseConnaissance.bat, for instance) stops the other big
rem     server started by a serveurs-*.bat file; IA-K stays open.
set "ONE_BIG_MODEL=1"

rem --- chat server, Gemma 4 26B-A4B (profile gemma26,
rem     serveurs-baseConnaissance.bat): experts and vision on the CPU, as in
rem     Gemma-4-26-A4B-vulkan.bat (the gemma26 profile's own defaults:
rem     N_CPU_MOE=99, MMPROJ_CPU=1); KV cache in q8 and the default
rem     micro-batch to leave graphics memory for the answers (SERVERS.md,
rem     "Memory")
set "GEMMA26_MODEL=%MODELS_DIR%\lmstudio-community\gemma-4-26B-A4B-it-QAT-GGUF\gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
set "GEMMA26_MMPROJ=%MODELS_DIR%\lmstudio-community\gemma-4-26B-A4B-it-QAT-GGUF\mmproj-gemma-4-26B-A4B-it-QAT-BF16.gguf"
set "CTX=8192"
set "KV_Q8=1"
set "UBATCH=512"
set "THREADS=8"

rem --- smaller chat server, Gemma 4 12B (profile gemma12), all on the iGPU
rem     as in gemma-4-12b-qat-vulkan.bat; it shares CTX, KV_Q8, UBATCH and
rem     THREADS above
set "GEMMA12_MODEL=%MODELS_DIR%\lmstudio-community\gemma-4-12B-it-QAT-GGUF\gemma-4-12B-it-QAT-Q4_0.gguf"
set "GEMMA12_MMPROJ=%MODELS_DIR%\lmstudio-community\gemma-4-12B-it-QAT-GGUF\mmproj-gemma-4-12B-it-QAT-BF16.gguf"

rem --- vision server, Qwen3-VL-8B (profile qwenvl, serveurs-vl.bat), all on
rem     the iGPU as in qwen3-vl-8b-vulkan.bat: a page image gets up to 2048
rem     tokens to be read in detail
set "QWENVL_MODEL=%MODELS_DIR%\lmstudio-community\Qwen3-VL-8B-Instruct-GGUF\Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
set "QWENVL_MMPROJ=%MODELS_DIR%\lmstudio-community\Qwen3-VL-8B-Instruct-GGUF\mmproj-Qwen3-VL-8B-Instruct-F16.gguf"
set "QWENVL_UBATCH=2048"
set "QWENVL_CTX=12288"

rem --- translation server, TranslateGemma 12B (profile translate,
rem     serveurs-traduction.bat), all on the iGPU
set "TRANSLATE_MODEL=%MODELS_DIR%\NikolayKozloff\translategemma-12b-it-Q4_K_M-GGUF\translategemma-12b-it-q4_k_m.gguf"
set "TRANSLATE_CTX=8192"

rem --- embedding server, EmbeddingGemma 300M (profile embed, every
rem     serveurs-*.bat) on the CPU, as in embeddinggemma-rag.bat
set "EMBED_MODEL=%MODELS_DIR%\lmstudio-community\embeddinggemma-300m-qat-GGUF\embeddinggemma-300m-qat-Q4_0.gguf"
set "EMBED_NGL=0"

rem --- ports (IA-K's defaults) ---
set "CHAT_PORT=8080"
set "EMBED_PORT=8081"
set "VISION_PORT=8082"
set "TRANSLATE_PORT=8083"
