# IA-K

![IA-K](assets/ia-k.png)

IA-K is a personal, offline desktop assistant that turns a collection of
documents (PDF, scans, screenshots, EPUB/HTML, DOCX, TXT, Markdown) into a
searchable knowledge base and answers questions about it with **sourced
answers** (document, page, passage), using local language models only.

- Runs entirely offline: no document ever leaves the computer.
- Portable: the application can live on a USB key or an external disk; models
  and data stay outside the program and their location is a preference.
- Windows and Linux, English and French interface.
- Open source, MIT licence.

> **Status: early work (branch `init`).** This first step only provides the
> scripts that start the local model servers and a test to measure memory and
> speed on the target machine. The Python backend and the Tauri interface come
> next.

## Architecture

| Part | Choice |
|---|---|
| Interface | Tauri 2 + web frontend |
| Backend | Python, local HTTP API on 127.0.0.1 with a random access token |
| Inference | [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` (Vulkan build), OpenAI-compatible API; LM Studio also usable |
| Chat and vision | Gemma 4 with its vision projector (one model for both); a dedicated vision model is optional |
| Embeddings | EmbeddingGemma 300M, separate small server |
| OCR | Tesseract (`fra`, `eng`, optionally `frm` for Middle French); system package on Linux, bundled on Windows |
| PDF / EPUB | PyMuPDF (see *Licences*) |
| DOCX | python-docx |
| Storage | SQLite: documents, pages, passages, FTS5 full-text index, vectors as BLOBs searched with numpy |

The application starts and stops the model servers itself; only one large
model is loaded at a time, the embedding model stays loaded.

## Model servers

`server/llama-server.bat` (Windows) and `server/llama-server.sh` (Linux) start
`llama-server` with settings tuned for an integrated GPU sharing system memory
(tested target: Ryzen AI 9 HX 370, Radeon 890M, 32 GB).

```
server\llama-server.bat gemma12      Gemma 4 12B + vision        port 8080
server\llama-server.bat gemma26      Gemma 4 26B-A4B + vision    port 8080
server\llama-server.bat qwenvl       dedicated vision model      port 8080
server\llama-server.bat embed        EmbeddingGemma 300M         port 8081
```

Extra arguments are passed to `llama-server`, for example
`server\llama-server.bat gemma26 --verbose`.

### Setup

1. Get a **Vulkan** build of llama.cpp:
   - Windows: unzip `llama-bXXXX-bin-win-vulkan-x64.zip` from the
     [releases](https://github.com/ggml-org/llama.cpp/releases) into
     `build\llama\` (or anywhere, then set `LLAMA_DIR`);
   - Linux: build with `cmake -B build -DGGML_VULKAN=ON` and copy
     `llama-server` into `build/llama/`, or have it in the `PATH`.
   Use a recent build: Gemma 4 needs it.
2. Copy `server/local.example.bat` to `server/local.bat` (or the `.sh`
   pair on Linux) and set `MODELS_DIR`. The default layout below it is the
   LM Studio one (`lmstudio-community/gemma-4-12B-it-QAT-GGUF/...`); any model
   can be given explicitly with `GEMMA12_MODEL`, `GEMMA26_MODEL`,
   `QWENVL_MODEL`, `EMBED_MODEL`.
3. The vision projector is the first `mmproj*.gguf` found next to the model,
   unless `*_MMPROJ` is set.

Settings (in `local.bat` / `local.sh`, or in the environment on Linux):

| Variable | Default | Effect |
|---|---|---|
| `CTX` | 16384 | chat context, prompt + answer |
| `NGL` | 99 | layers on the iGPU |
| `VISION` | 1 | 0 = do not load the vision projector (saves ~1 GB) |
| `KV_Q8` | 0 | 1 = KV cache in q8_0 (about half the cache memory) |
| `N_CPU_MOE` | empty | 26B-A4B: keep the experts of N layers on the CPU |
| `CHAT_PORT`, `EMBED_PORT` | 8080, 8081 | ports, always bound to 127.0.0.1 |

The chat server uses one slot (`-np 1`), `--jinja` for the model's chat
template and `--no-mmap` so the weights are read once into GPU memory instead
of staying mapped from the disk as well.

### Memory test

Run these in order and keep the numbers; they decide which model becomes the
default. Start a server in one terminal, the test in another.

```
server\llama-server.bat gemma12
python scripts\smoke_test.py --image assets\ia-k.png --prefill 6000
```

Then the same with `gemma26`, then `gemma26` while `embed` runs in a third
terminal (with `--embed-url http://127.0.0.1:8081` added to the test). The
test reports prompt speed (the wait before an answer starts), generation
speed, the image answer and, on Linux, the iGPU memory in use.

If the 26B model fails to load, or the system starts swapping, try in this
order: `KV_Q8=1`, `CTX=8192`, `VISION=0` (to see whether vision is the
difference), then `N_CPU_MOE=8` and higher. In the server log, the lines
`model buffer size` and `KV buffer size` give the memory used per device.

On Windows the iGPU can usually use about half of the system memory; on
Linux the limit is the amdgpu GTT size (see `dmesg | grep -i gtt`).

## Licences

IA-K's own code is under the MIT licence. PyMuPDF is AGPL-3.0: a packaged
build of IA-K that includes it is therefore distributed under the AGPL terms,
while the MIT source stays MIT. Models are not part of IA-K and keep their own
licences (Gemma terms of use, Apache 2.0 for Qwen).

## Roadmap

1. Feasibility study and model servers *(this step)*
2. Python backend: import, text extraction, OCR, storage, chunking, citations by page
3. Full-text search and sourced answers
4. Embeddings and hybrid search
5. Document chat and image analysis
6. Tauri interface: library, viewer, search, chat, OCR correction
7. Windows and Linux builds, portable layout, documentation
8. Translation of documents (as in [truchement](https://github.com/fhoudebert/transcribe))
