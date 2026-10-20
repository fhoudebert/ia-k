# Copy this file to server/local.sh and adapt it. local.sh is not versioned.
# Every variable is optional: remove a line to keep the default.
# Values given in the environment still win: use ${VAR:-value} as below.

MODELS_DIR="${MODELS_DIR:-$HOME/ia/models}"
# LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp/build/bin}"

# --- memory tuning (see SERVERS.md, "Memory") ---
# CTX="${CTX:-8192}"
# KV_Q8="${KV_Q8:-1}"
# VISION="${VISION:-0}"
# N_CPU_MOE="${N_CPU_MOE:-8}"
# UBATCH="${UBATCH:-2048}"
# MMPROJ_CPU="${MMPROJ_CPU:-0}"

# --- explicit paths ---
# QWENVL_MODEL="$HOME/ia/models/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
# QWENVL_MMPROJ="$HOME/ia/models/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"

# --- translation server (TranslateGemma) ---
# TRANSLATE_PORT="${TRANSLATE_PORT:-8083}"
# TRANSLATE_CTX="${TRANSLATE_CTX:-8192}"
# TRANSLATE_MODEL="$HOME/ia/models/translategemma-12b-it-q4_k_m.gguf"

# --- CPU threads of every server (empty = llama.cpp's choice) ---
# THREADS="${THREADS:-8}"
