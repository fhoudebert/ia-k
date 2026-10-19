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

> **Status: early work (branch `init`).** For now: the server configuration
> format, example server launchers and a test to measure memory and speed.
> The Python backend and the Tauri interface come next.

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

The model servers run on each computer, outside IA-K, which connects to them
by URL (see below).

## Model servers

The model servers belong to each computer, next to its models: IA-K does not
start them, it **connects** to them. The same IA-K key can therefore be used on
several computers, each with its own servers, ports and models, local or on
the network.

Each computer runs up to three `llama-server` instances (OpenAI-compatible
API):

| Role | Usual port | Purpose |
|---|---|---|
| chat | 8080 | document chat, summaries; image questions too when started with a vision projector |
| embedding | 8081 | semantic search (EmbeddingGemma 300M) |
| vision | 8082 | optional dedicated vision model |

### Connecting IA-K

Copy `config/servers.example.toml` to `config/servers.toml` and describe each
computer. IA-K picks the section whose `match` list contains the computer name,
otherwise `[host.default]`. Context size and vision support are read from each
server (`/props`), not configured.

The semantic index only works with the embedding model that built it: IA-K
records that model in the index and turns semantic search off (keyword search
keeps working) when a computer serves a different one. Serve the same
EmbeddingGemma file everywhere.

A server that is not on `127.0.0.1` receives the passages of your documents;
IA-K shows it in the interface. Use `--api-key` on such servers.

### Starting the servers on a computer

`server/` holds examples for the target laptop (Ryzen AI 9 HX 370, Radeon 890M,
32 GB, Windows, llama.cpp Vulkan build): one `.bat` per model, tested, plus
`llama-server.bat` / `llama-server.sh`, a single launcher with profiles
(`gemma12`, `gemma26`, `qwenvl`, `embed`) and settings in `server/local.*`.
They are conveniences for the computer, not part of IA-K.

Gemma 4 26B-A4B does not fit entirely in the iGPU memory: its `.bat` keeps
the experts of N layers in system RAM (`--n-cpu-moe N`). Start with the
default (all of them), then lower N until loading fails and keep the last
value that worked.

### Testing a server

`scripts/smoke_test.py` (Python standard library only) measures what matters
for document chat on any server:

```
python scripts\smoke_test.py --url http://127.0.0.1:8080 --image assets\ia-k.png --prefill 6000
python scripts\smoke_test.py --url http://127.0.0.1:8080 --embed-url http://127.0.0.1:8081
```

It reports the server's context and vision support, prompt speed on a long
prompt (the wait before an answer starts), generation speed, an image
answer, embedding similarity and, on Linux, the iGPU memory in use.
`--api-key` (or `IAK_API_KEY`) is passed as a bearer token.

## Licences

IA-K's own code is under the MIT licence. PyMuPDF is AGPL-3.0: a packaged
build of IA-K that includes it is therefore distributed under the AGPL terms,
while the MIT source stays MIT. Models are not part of IA-K and keep their own
licences (Gemma terms of use, Apache 2.0 for Qwen).

## Roadmap

1. Feasibility study, model servers and their configuration *(this step)*
2. Python backend: import, text extraction, OCR, storage, chunking, citations by page
3. Full-text search and sourced answers
4. Embeddings and hybrid search
5. Document chat and image analysis
6. Tauri interface: library, viewer, search, chat, OCR correction
7. Windows and Linux builds, portable layout, documentation
8. Translation of documents (as in [truchement](https://github.com/fhoudebert/transcribe))
