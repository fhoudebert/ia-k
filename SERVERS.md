# Language model servers

IA-K does not run language models itself: it connects to
[llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` instances
(OpenAI-compatible API; LM Studio's server works too). They belong to the
computer that holds the models, so one IA-K key can be used on several
computers, each with its own servers.

[Back to the README](README.md)

## Roles and ports

| Role | Usual port | Model | Used for |
|---|---|---|---|
| chat | 8080 | Gemma 4 26B-A4B or 12B, with its vision projector (`mmproj`) | answers, summaries, comparisons; page analysis when it has vision |
| embedding | 8081 | EmbeddingGemma 300M | search by meaning |
| vision | 8082 | optional, e.g. Qwen3-VL-8B | page analysis instead of the chat model |
| translate | 8083 | optional, TranslateGemma 12B (or 4B) | translation |

Search by keywords needs no server. Without an embedding server, search by
meaning is off; without a chat server, questions and analyses are. Without a
translation server, the chat model translates (TranslateGemma, made for it,
translates better and faster).

## Getting llama.cpp and the models

- llama.cpp: a **Vulkan** build. Windows: unzip
  `llama-bXXXX-bin-win-vulkan-x64.zip` from the
  [releases](https://github.com/ggml-org/llama.cpp/releases). Linux: build with
  `cmake -B build -DGGML_VULKAN=ON` or take `llama-bXXXX-bin-ubuntu-vulkan-x64`.
  macOS: `brew install llama.cpp`, or `llama-bXXXX-bin-macos-arm64.zip`; it
  uses Metal, the Mac's graphics, whose memory is the main memory (not
  measured on a Mac yet: 32 GB should hold the 26B, as on the reference
  laptop). Gemma 4 needs a recent
  build (tested: b11696).
- Models (GGUF), for instance with LM Studio or from Hugging Face:
  - `lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF`: `gemma-4-26B-A4B-it-QAT-Q4_0.gguf`
    and `mmproj-gemma-4-26B-A4B-it-QAT-BF16.gguf`
  - `lmstudio-community/gemma-4-12B-it-QAT-GGUF`: the 12B and its `mmproj`
  - `lmstudio-community/embeddinggemma-300m-qat-GGUF`: `embeddinggemma-300m-qat-Q4_0.gguf`
  - optional: [`lmstudio-community/Qwen3-VL-8B-Instruct-GGUF`](https://huggingface.co/lmstudio-community/Qwen3-VL-8B-Instruct-GGUF):
    `Qwen3-VL-8B-Instruct-Q4_K_M.gguf` and `mmproj-Qwen3-VL-8B-Instruct-F16.gguf`
  - for translation: TranslateGemma 12B in GGUF, for instance
    `NikolayKozloff/translategemma-12b-it-Q4_K_M-GGUF`:
    `translategemma-12b-it-q4_k_m.gguf` (about 7 GB; the 4B is lighter and
    less accurate)

## Starting them

### Servers and IA-K in one go

At the root of IA-K, one file per use starts the servers it needs, waits for
them to answer (loading a model from a USB key can take a minute or two),
starts IA-K, and stops the servers it started when IA-K is closed:

| File (Windows / Linux) | Servers | For |
|---|---|---|
| `serveurs-baseConnaissance.bat` / `.sh` | `gemma26` (8080) + `embed` (8081) | library, search, questions, summaries, page analysis |
| `serveurs-traduction.bat` / `.sh` | `translate` (8083) + `embed` (8081) | translation, search |
| `serveurs-vl.bat` / `.sh` | `qwenvl` (8082) + `embed` (8081) | reading difficult scans again, page analysis, search |

A server already running on its port is used as it is. `--keep` leaves the
servers running after IA-K, `--no-app` starts only the servers. On Windows
each server has its own minimised window with its messages; on Linux they go
to `data/server-<profile>.log`. Both files call
`server/start-servers.bat PROFILE...` (or `.sh`), which can start any set of
profiles, for instance `server\start-servers.bat qwenvl embed`.

Starting a second file while IA-K is open does not start a second IA-K: the
open one uses the new servers as soon as they answer, and they stop when it
closes. On a 32 GB laptop the 26B, TranslateGemma 12B and Qwen3-VL-8B do not
fit in graphics memory together: with `ONE_BIG_MODEL=1` (set in
`local.hx370.bat`), starting one of them — `serveurs-vl` after
`serveurs-baseConnaissance`, for instance — first stops the other big one
started by a `serveurs-*` file (not one started by hand); EmbeddingGemma
stays.

On Windows their settings are read from `server/local.bat` when it exists,
otherwise from **`server/local.hx370.bat`**, the reference laptop: llama.cpp
in `D:\ia\llama-vulkan`, the files of every model in `D:\ia\models` (LM
Studio layout: Gemma 4 26B and 12B, Qwen3-VL-8B, TranslateGemma,
EmbeddingGemma), the 26B with its experts and vision on the CPU, KV cache in
q8, micro-batch 512, context 8192, 8 threads, Qwen3-VL with its own
micro-batch and context, EmbeddingGemma on the CPU, one big model at a time —
the same choices as the single launchers below. `local.example.bat` and
`local.example.sh` list every setting, commented. For another
computer, copy it to `server/local.bat` and edit the copy (`local.bat` is
not versioned). On Linux and macOS they come from `server/local.sh` (copy
`local.example.sh`); models downloaded by LM Studio on a Mac are in
`~/.lmstudio/models` (`MODELS_DIR`), and `llama-server` installed by Homebrew
is found without `LLAMA_DIR`. `IAK_SERVER_SETTINGS` can name another settings file.

### One server at a time

`server/` also holds single launchers for the reference laptop (Windows,
models in `D:\ia\models`, llama.cpp in `D:\ia\llama-vulkan`); edit the paths
at the top of each file. They do not read `local.bat`, which is why the
`local.example.*` files do not name them: the profiles of `llama-server.bat`
(used by the `serveurs-*` files) are the same servers, set by `local.bat` —
`embed` is `embeddinggemma-rag.bat`, `gemma26` is `Gemma-4-26-A4B-vulkan.bat`,
`qwenvl` is `qwen3-vl-8b-vulkan.bat`:

| File | Starts |
|---|---|
| `Gemma-4-26-A4B-vulkan.bat [N]` | Gemma 4 26B-A4B on 8080, experts of N layers on the CPU (default 99), vision on the CPU, KV cache in q8 |
| `gemma-4-12b-qat-vulkan.bat` | Gemma 4 12B on 8080, all on the iGPU |
| `embeddinggemma-rag.bat` | EmbeddingGemma on 8081, **on the CPU** (see *Memory*) |
| `qwen3-vl-8b-vulkan.bat` | Qwen3-VL-8B on 8082 (vision), instead of the 26B on a 32 GB laptop |
| `llama-server.bat` / `llama-server.sh` | one launcher for all, with profiles `gemma12`, `gemma26`, `qwenvl`, `embed`, `translate` and the settings above |

The single launcher, for instance:

```
server\llama-server.bat gemma26
server\llama-server.bat embed
server\llama-server.bat translate
./server/llama-server.sh gemma12 --verbose        # extra arguments go to llama-server
```

| Setting (`local.bat` / `local.sh`) | Default | Effect |
|---|---|---|
| `MODELS_DIR`, `LLAMA_DIR` | `D:\ia\models`, `build\llama` | where the models and `llama-server` are |
| `CTX` | 16384 | context of the chat server, prompt + answer |
| `NGL` | 99 | layers on the GPU |
| `N_CPU_MOE` | 99 for gemma26 | experts of N layers kept on the CPU |
| `MMPROJ_CPU` | 1 for gemma26 | vision projector on the CPU |
| `VISION` | 1 | 0 = no vision projector |
| `KV_Q8` | 0 | 1 = KV cache in q8_0, about half its memory |
| `UBATCH` | 1280 with vision | micro-batch; an image gets at most this many tokens |
| `EMBED_NGL` | 0 | embedding server on the CPU (99 = on the GPU) |
| `TRANSLATE_CTX` | 8192 | context of the translation server |
| `QWENVL_UBATCH`, `QWENVL_CTX` | 2048, 12288 | micro-batch and context of the vision server: a page image gets up to the micro-batch in tokens; the context holds the image, the OCR text to correct and the reading |
| `THREADS` | llama.cpp's choice | CPU threads of every server |
| `ONE_BIG_MODEL` | 0 | 1 = `serveurs-*` files stop the other big model they started (26B, 12B, Qwen3-VL, TranslateGemma) before starting one |
| `WAIT_SECONDS` | 300 | how long `serveurs-*` files wait for a model to load |
| `GEMMA26_MODEL`, `GEMMA12_MODEL`, `QWENVL_MODEL`, `TRANSLATE_MODEL`, `EMBED_MODEL`; `*_MMPROJ` | LM Studio layout under `MODELS_DIR`; the first `mmproj*.gguf` next to the model | explicit model files |
| `CHAT_PORT`, `EMBED_PORT`, `VISION_PORT`, `TRANSLATE_PORT` | 8080, 8081, 8082, 8083 | always bound to 127.0.0.1 |

The `translate` profile runs TranslateGemma with its own template
(`--jinja`); IA-K recognises it and sends it the exact prompt the model was
trained on, page by page or in pieces of about 3,000 characters.

## Telling IA-K where they are

In IA-K, *Settings*, or in `config/servers.toml` (start from
`config/servers.example.toml`):

```toml
[host.default]
chat      = "http://127.0.0.1:8080/v1"
embedding = "http://127.0.0.1:8081/v1"
translate = "http://127.0.0.1:8083/v1"   # optional

[host.office]
match     = ["OFFICE-PC"]              # computer name (COMPUTERNAME, hostname)
chat      = "http://192.168.1.20:8080/v1"
embedding = "http://192.168.1.20:8081/v1"
api_key   = "…"                        # when the servers run with --api-key
```

Without `config/servers.toml`, IA-K uses the usual ports above; a vision or
translation server that is not running is simply replaced by the chat
server. IA-K reads each server's context size and vision support itself. The
semantic index works only with the embedding model that built it: serve the
same EmbeddingGemma file on every computer (another quantisation of it is
accepted; another model turns search by meaning off until the index is
rebuilt).

Without `--api-key`, llama-server answers any program of the computer,
including web pages open in a browser. On a shared computer or a network
server, start it with `--api-key` and put the same key in IA-K.

## Memory

On a laptop whose graphics share the main memory (the Radeon 890M), the
iGPU can use only part of it: on Windows the *dedicated* graphics memory,
plus a share of the rest. When the models need more, llama.cpp stops with
`ErrorOutOfDeviceMemory` — at load time, or later, while answering
(`decode() failed`), when the prompt and its passages need working memory.
IA-K then shows the error with a pointer here. In order:

1. **Embedding server on the CPU.** `embeddinggemma-rag.bat` and the `embed`
   profile now do it (`--n-gpu-layers 0`): the model is small, searching is
   as fast, and importing stays reasonable (several dozen passages per
   second on 12 cores).
2. **KV cache in q8** for the chat model (`--cache-type-k q8_0
   --cache-type-v q8_0`, now in the 26B launcher; `KV_Q8=1` for the single
   launcher): half the memory of the context, no visible loss.
3. **Default micro-batch** (no `--ubatch-size`, or 512): a larger one needs
   more working memory during the prompt; keep 1280 only to read images in
   detail.
4. **Smaller context** (`--ctx-size 6144`): IA-K sends about 2,500 tokens of
   passages plus the conversation; 6,144 is enough.
5. **Give the iGPU more memory**: AMD Software (Adrenalin) offers *Variable
   Graphics Memory* on Ryzen AI 300 laptops; 16 GB dedicated lets the 26B
   keep more on the iGPU. Windows then keeps 16 GB for itself and the rest.
6. **Gemma 4 12B** instead of the 26B: smaller but about half as fast.

A more powerful computer is not needed for IA-K: the reference laptop runs
the 26B with the embedding server on the CPU.

## Testing a server

`scripts/smoke_test.py` (Python standard library only) measures what matters
for document chat:

```
python scripts\smoke_test.py --url http://127.0.0.1:8080 --image assets\ia-k.png --prefill 6000
python scripts\smoke_test.py --url http://127.0.0.1:8080 --embed-url http://127.0.0.1:8081
```

It reports the context and vision support, prompt speed on a long prompt (the
wait before an answer starts), generation speed, an image answer, embedding
similarity and, on Linux, the iGPU memory in use. `--api-key` (or
`IAK_API_KEY`) passes a key. Gemma 4 can "think" before answering, which
costs time; IA-K and the test turn it off (`--thinking` to measure it).

## Measured on the reference laptop

Ryzen AI 9 HX 370, Radeon 890M, 32 GB, Windows, llama.cpp Vulkan b11696,
context 8192, thinking off (`smoke_test.py --prefill 6000`):

| | Gemma 4 12B QAT, all on iGPU | Gemma 4 26B-A4B QAT, experts and vision on CPU |
|---|---|---|
| generation | 7–9 tok/s | 16–19 tok/s |
| prompt of 5,600 tokens | 125–134 tok/s, 42–45 s | 187 tok/s, 30 s (275 tok/s over the first 2,000) |
| image question | 517 tokens in 2.6 s | 517 tokens in 13 s |

- The 26B-A4B does not fit in the iGPU memory with its experts; with
  `--n-cpu-moe 99 --no-mmproj-offload` it loads and is the faster model for
  text, about twice the 12B for generation.
- Fewer experts on the CPU barely helps on this shared-memory chip:
  `--n-cpu-moe` 20, 15, 10, 7 give 189–200 tok/s for the long prompt and the
  same generation speed as 99, which leaves the most iGPU memory free.
- A larger micro-batch does not speed text prompts up (`-ub 1024`:
  125 tok/s), but llama.cpp caps image tokens to it: with 1280 the test image
  takes 709 tokens and 20 s instead of 517 and 13 s.
- Prompt speed falls as the prompt grows: IA-K sends about 2,000–3,000
  tokens of passages and reuses the server's prompt cache.
