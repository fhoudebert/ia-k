@echo off
rem ============================================================================
rem  local.hx370.bat - settings of the reference laptop (Ryzen AI 9 HX 370,
rem  Radeon 890M, 32 GB, Windows), the same as the tested launchers
rem  Gemma-4-26-A4B-vulkan.bat and embeddinggemma-rag.bat.
rem
rem  Used by serveurs-*.bat when server\local.bat does not exist. To adapt it
rem  to another computer, copy it to server\local.bat (not versioned) and
rem  edit the copy.
rem ============================================================================

rem --- where things are ---
set "LLAMA_DIR=D:\ia\llama-vulkan"
set "MODELS_DIR=D:\ia\models"

rem --- chat server (Gemma 4 26B-A4B): experts and vision on the CPU, as in
rem     Gemma-4-26-A4B-vulkan.bat; KV cache in q8 and the default micro-batch
rem     to leave graphics memory for the answers (SERVERS.md, "Memory")
set "CTX=8192"
set "N_CPU_MOE=99"
set "MMPROJ_CPU=1"
set "KV_Q8=1"
set "UBATCH=512"
set "THREADS=8"

rem --- vision server (Qwen3-VL-8B, serveurs-vl.bat): a page image gets up to
rem     2048 tokens to be read in detail; run it without the 26B
set "QWENVL_UBATCH=2048"
set "QWENVL_CTX=12288"

rem --- embedding server (EmbeddingGemma 300M) on the CPU, as in
rem     embeddinggemma-rag.bat
set "EMBED_NGL=0"

rem --- ports (IA-K's defaults) ---
set "CHAT_PORT=8080"
set "EMBED_PORT=8081"
set "VISION_PORT=8082"
set "TRANSLATE_PORT=8083"

rem --- models, when not in the LM Studio layout under MODELS_DIR ---
rem set "GEMMA26_MODEL=D:\ia\models\lmstudio-community\gemma-4-26B-A4B-it-QAT-GGUF\gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
rem set "TRANSLATE_MODEL=D:\ia\models\NikolayKozloff\translategemma-12b-it-Q4_K_M-GGUF\translategemma-12b-it-q4_k_m.gguf"
