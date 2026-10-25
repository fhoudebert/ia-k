# Copy this file to server/local.sh and adapt it. local.sh is not versioned.
# It holds the settings of llama-server.sh and of the serveurs-*.sh files
# (start-servers.sh), for every profile: gemma26, gemma12, qwenvl, translate,
# embed. Every variable is optional: remove a line to keep the default.
# Values given in the environment still win: use ${VAR:-value} as below.

MODELS_DIR="${MODELS_DIR:-$HOME/ia/models}"
# LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp/build/bin}"

# --- 1 = one big model at a time: starting the 26B, the 12B, Qwen3-VL or
#     TranslateGemma stops the other one started by a serveurs-*.sh file
# ONE_BIG_MODEL="${ONE_BIG_MODEL:-1}"

# --- chat server (profiles gemma26, gemma12), memory tuning (SERVERS.md, "Memory") ---
# CHAT_PORT="${CHAT_PORT:-8080}"
# CTX="${CTX:-8192}"
# KV_Q8="${KV_Q8:-1}"
# VISION="${VISION:-0}"
# N_CPU_MOE="${N_CPU_MOE:-8}"
# UBATCH="${UBATCH:-2048}"
# MMPROJ_CPU="${MMPROJ_CPU:-0}"
# GEMMA26_MODEL="$HOME/ia/models/gemma-4-26B-A4B-it-QAT-Q4_0.gguf"
# GEMMA26_MMPROJ="$HOME/ia/models/mmproj-gemma-4-26B-A4B-it-QAT-BF16.gguf"
# GEMMA12_MODEL="$HOME/ia/models/gemma-4-12B-it-QAT-Q4_0.gguf"
# GEMMA12_MMPROJ="$HOME/ia/models/mmproj-gemma-4-12B-it-QAT-BF16.gguf"

# --- vision server (profile qwenvl, serveurs-vl.sh) ---
# VISION_PORT="${VISION_PORT:-8082}"
# QWENVL_UBATCH="${QWENVL_UBATCH:-2048}"
# QWENVL_CTX="${QWENVL_CTX:-12288}"
# QWENVL_MODEL="$HOME/ia/models/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
# QWENVL_MMPROJ="$HOME/ia/models/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

# --- translation server (profile translate, serveurs-traduction.sh) ---
# TRANSLATE_PORT="${TRANSLATE_PORT:-8083}"
# TRANSLATE_CTX="${TRANSLATE_CTX:-8192}"
# TRANSLATE_MODEL="$HOME/ia/models/translategemma-12b-it-q4_k_m.gguf"

# --- embedding server (profile embed, every serveurs-*.sh): on the CPU by
#     default (99 = on the GPU) ---
# EMBED_PORT="${EMBED_PORT:-8081}"
# EMBED_NGL="${EMBED_NGL:-0}"
# EMBED_MODEL="$HOME/ia/models/embeddinggemma-300m-qat-Q4_0.gguf"

# --- CPU threads of every server (empty = llama.cpp's choice) ---
# THREADS="${THREADS:-8}"

# --- seconds to wait for a model to load (slow from a USB key) ---
# WAIT_SECONDS="${WAIT_SECONDS:-300}"
