#!/usr/bin/env bash
# =============================================================================
#  llama-server.sh - start a local llama.cpp server for IA-K (Linux)
#
#  Usage : server/llama-server.sh [profile] [extra llama-server options]
#    gemma12  Gemma 4 12B QAT + vision projector   (default, port CHAT_PORT)
#    gemma26  Gemma 4 26B-A4B QAT + vision          (port CHAT_PORT)
#    qwenvl   optional dedicated vision model       (port VISION_PORT)
#    embed    EmbeddingGemma 300M, embeddings only  (port EMBED_PORT)
#
#  Personal settings go in server/local.sh (copy local.example.sh); any
#  variable can also be given in the environment: CTX=8192 server/llama-server.sh
#  The server listens on 127.0.0.1 only. Stop it with Ctrl+C.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# shellcheck source=/dev/null
[ -f "$HERE/local.sh" ] && . "$HERE/local.sh"

MODELS_DIR="${MODELS_DIR:-$HOME/ia/models}"
LLAMA_DIR="${LLAMA_DIR:-$ROOT/build/llama}"
HOST="${HOST:-127.0.0.1}"
CHAT_PORT="${CHAT_PORT:-8080}"
EMBED_PORT="${EMBED_PORT:-8081}"
EMBED_NGL="${EMBED_NGL:-0}"     # embeddings on the CPU (99 = on the GPU)
VISION_PORT="${VISION_PORT:-8082}"
CTX="${CTX:-16384}"          # chat context (prompt + answer)
NGL="${NGL:-99}"             # layers on the iGPU (99 = all)
N_CPU_MOE="${N_CPU_MOE:-}"   # MoE: experts of N layers on the CPU (gemma26 default 99, 0 = none)
MMPROJ_CPU="${MMPROJ_CPU:-}" # 1 = vision projector on the CPU (gemma26 default 1)
KV_Q8="${KV_Q8:-0}"          # 1 = KV cache in q8_0
VISION="${VISION:-1}"        # 0 = no vision projector (saves ~1 GB)
UBATCH="${UBATCH:-}"         # prompt micro-batch (empty: 1280 with vision, else 512)

GEMMA12_MODEL="${GEMMA12_MODEL:-$MODELS_DIR/lmstudio-community/gemma-4-12B-it-QAT-GGUF/gemma-4-12B-it-QAT-Q4_0.gguf}"
GEMMA26_MODEL="${GEMMA26_MODEL:-$MODELS_DIR/lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF/gemma-4-26B-A4B-it-QAT-Q4_0.gguf}"
QWENVL_MODEL="${QWENVL_MODEL:-$MODELS_DIR/lmstudio-community/Qwen3-VL-8B-Instruct-GGUF/Qwen3-VL-8B-Instruct-Q4_K_M.gguf}"
EMBED_MODEL="${EMBED_MODEL:-$MODELS_DIR/lmstudio-community/embeddinggemma-300m-qat-GGUF/embeddinggemma-300m-qat-Q4_0.gguf}"

PROFILE="${1:-gemma12}"
[ $# -gt 0 ] && shift
EXTRA=("$@")

if [ -x "$LLAMA_DIR/llama-server" ]; then
    SERVER="$LLAMA_DIR/llama-server"
elif command -v llama-server >/dev/null 2>&1; then
    SERVER="$(command -v llama-server)"
else
    echo "[ia-k] llama-server not found." >&2
    echo "       Build llama.cpp with -DGGML_VULKAN=ON (or unzip a Vulkan release)" >&2
    echo "       into $LLAMA_DIR, or set LLAMA_DIR in server/local.sh." >&2
    exit 1
fi

need_file() {
    if [ ! -f "$1" ]; then
        echo "[ia-k] model not found: $1" >&2
        echo "       set MODELS_DIR or the *_MODEL variables in server/local.sh" >&2
        exit 1
    fi
}

case "$PROFILE" in
    gemma12) MODEL="$GEMMA12_MODEL"; MMPROJ="${GEMMA12_MMPROJ:-}"; ALIAS=gemma-4-12b; PORT="$CHAT_PORT" ;;
    gemma26) MODEL="$GEMMA26_MODEL"; MMPROJ="${GEMMA26_MMPROJ:-}"; ALIAS=gemma-4-26b-a4b; PORT="$CHAT_PORT"
             N_CPU_MOE="${N_CPU_MOE:-99}"; MMPROJ_CPU="${MMPROJ_CPU:-1}" ;;
    qwenvl)  MODEL="$QWENVL_MODEL";  MMPROJ="${QWENVL_MMPROJ:-}";  ALIAS=vision; PORT="$VISION_PORT"; VISION=1 ;;
    embed)
        need_file "$EMBED_MODEL"
        echo "[ia-k] embeddings on http://$HOST:$EMBED_PORT/v1  (alias embeddinggemma)"
        echo "       model  : $EMBED_MODEL"
        # 4 slots of 2048 tokens (the model's maximum input)
        exec "$SERVER" -m "$EMBED_MODEL" --alias embeddinggemma --embeddings \
            --host "$HOST" --port "$EMBED_PORT" -c 8192 -np 4 -b 2048 -ub 2048 \
            -ngl "$EMBED_NGL" "${EXTRA[@]}"
        ;;
    *)
        echo "[ia-k] unknown profile: $PROFILE (expected gemma12 | gemma26 | qwenvl | embed)" >&2
        exit 1 ;;
esac

need_file "$MODEL"
ARGS=(-m "$MODEL")
if [ "$VISION" != "0" ]; then
    if [ -z "$MMPROJ" ]; then
        # first mmproj*.gguf next to the model
        MMPROJ="$(find "$(dirname "$MODEL")" -maxdepth 1 -name 'mmproj*.gguf' | sort | head -n 1)"
    fi
    if [ -z "$MMPROJ" ] || [ ! -f "$MMPROJ" ]; then
        echo "[ia-k] no vision projector (mmproj*.gguf) found next to $MODEL" >&2
        echo "       set the matching *_MMPROJ variable, or VISION=0 for text only." >&2
        exit 1
    fi
    ARGS+=(--mmproj "$MMPROJ")
    [ "$MMPROJ_CPU" = "1" ] && ARGS+=(--no-mmproj-offload)
    # an image may use up to 1120 tokens; llama.cpp caps it to the micro-batch
    UBATCH="${UBATCH:-1280}"
fi
[ "$KV_Q8" = "1" ] && ARGS+=(-ctk q8_0 -ctv q8_0)
[ -n "$N_CPU_MOE" ] && ARGS+=(--n-cpu-moe "$N_CPU_MOE")

echo "[ia-k] profile $PROFILE on http://$HOST:$PORT/v1  (alias $ALIAS)"
echo "       model  : $MODEL"
[ "$VISION" != "0" ] && echo "       mmproj : $MMPROJ"
echo "       ctx=$CTX ngl=$NGL kv_q8=$KV_Q8 n_cpu_moe=${N_CPU_MOE:-} mmproj_cpu=${MMPROJ_CPU:-0} ubatch=${UBATCH:-}"
exec "$SERVER" "${ARGS[@]}" --alias "$ALIAS" --host "$HOST" --port "$PORT" \
    -c "$CTX" -ngl "$NGL" -np 1 --jinja ${UBATCH:+-ub "$UBATCH"} "${EXTRA[@]}"
