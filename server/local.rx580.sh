# local.rx580.sh - settings for a desktop with a card of 8 GB and much
# memory: AMD Radeon RX 580 8 GB (Polaris, Vulkan), Ryzen 5 2600X (6 cores,
# 12 threads), 64 GB of DDR4, Linux (Fedora, Mesa radeonsi).
#
# Copy it to server/local.sh (not versioned) and adapt the paths. The card
# holds one model at a time, and not every one whole: the large ones keep
# part of their weights in the main memory, computed by the CPU, which 64 GB
# allow. Starting values, to tune by watching the card's memory (amdgpu_top,
# radeontop) and the server's log (data/server-<profile>.log): an
# "ErrorOutOfDeviceMemory" asks for fewer layers on the card (*_NGL) or a
# smaller context; memory left free allows more (SERVERS.md, "Memory").
# A RX 580 of 4 GB: halve the *_NGL values and keep N_CPU_MOE at 99.

MODELS_DIR="${MODELS_DIR:-$HOME/ia/models}"
# llama.cpp built with -DGGML_VULKAN=ON; the default is build/llama on the key
# LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp/build/bin}"

# --- one big model at a time on 8 GB: starting one stops the other
ONE_BIG_MODEL="${ONE_BIG_MODEL:-1}"

# --- every server: the 6 cores of the 2600X (not its 12 threads: the
#     matrix work of the CPU gains nothing from them); KV cache in q8
#     (half the memory of the context; if this llama.cpp refuses it on the
#     card, KV_Q8=0 and a smaller context)
THREADS="${THREADS:-6}"
KV_Q8="${KV_Q8:-1}"

# --- chat, Gemma 4 26B-A4B (serveurs-baseConnaissance.sh), the best choice
#     here: a mixture of experts whose experts (most of its 15 GB) stay in
#     the main memory, while attention, the context and the vision
#     projector go on the card (the gemma26 profile keeps the experts of
#     all its layers on the CPU, N_CPU_MOE=99). With room left on the card,
#     try N_CPU_MOE=24, 16... (the experts of fewer layers on the CPU), faster.
GEMMA26_MODEL="${GEMMA26_MODEL:-$MODELS_DIR/lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF/gemma-4-26B-A4B-it-QAT-Q4_0.gguf}"
MMPROJ_CPU="${MMPROJ_CPU:-0}"
CTX="${CTX:-16384}"

# --- chat, Gemma 4 12B (profile gemma12): 7 GB of weights with its
#     projector, more than the card leaves: part of its layers on the CPU.
#     Slower than the 26B here; for a try only.
GEMMA12_MODEL="${GEMMA12_MODEL:-$MODELS_DIR/lmstudio-community/gemma-4-12B-it-QAT-GGUF/gemma-4-12B-it-QAT-Q4_0.gguf}"
GEMMA12_NGL="${GEMMA12_NGL:-36}"

# --- vision, Qwen3-VL-8B (serveurs-vl.sh): 5 GB of weights and 1.2 GB of
#     projector, on the card; a context of 8192 (the page image, the OCR
#     text and the reading) and a micro-batch of 1536 (an image gets up to
#     that many tokens) leave room for the work buffers. Out of memory:
#     QWENVL_MMPROJ_CPU=1 (the projector on the CPU, images slower), then
#     QWENVL_NGL=30.
QWENVL_MODEL="${QWENVL_MODEL:-$MODELS_DIR/lmstudio-community/Qwen3-VL-8B-Instruct-GGUF/Qwen3-VL-8B-Instruct-Q4_K_M.gguf}"
QWENVL_CTX="${QWENVL_CTX:-8192}"
QWENVL_UBATCH="${QWENVL_UBATCH:-1536}"
QWENVL_MMPROJ_CPU="${QWENVL_MMPROJ_CPU:-0}"
# QWENVL_NGL="${QWENVL_NGL:-30}"

# --- translation, TranslateGemma 12B (serveurs-traduction.sh): 7.3 GB, part
#     of its layers on the CPU
TRANSLATE_MODEL="${TRANSLATE_MODEL:-$MODELS_DIR/NikolayKozloff/translategemma-12b-it-Q4_K_M-GGUF/translategemma-12b-it-q4_k_m.gguf}"
TRANSLATE_NGL="${TRANSLATE_NGL:-38}"
TRANSLATE_CTX="${TRANSLATE_CTX:-8192}"

# --- embeddings, EmbeddingGemma 300M (every serveurs-*.sh): on the CPU, the
#     card is kept for the big model
EMBED_MODEL="${EMBED_MODEL:-$MODELS_DIR/lmstudio-community/embeddinggemma-300m-qat-GGUF/embeddinggemma-300m-qat-Q4_0.gguf}"
EMBED_NGL="${EMBED_NGL:-0}"

# --- ports (IA-K's defaults)
CHAT_PORT="${CHAT_PORT:-8080}"
EMBED_PORT="${EMBED_PORT:-8081}"
VISION_PORT="${VISION_PORT:-8082}"
TRANSLATE_PORT="${TRANSLATE_PORT:-8083}"

# --- models read from a USB key load slowly
WAIT_SECONDS="${WAIT_SECONDS:-300}"
