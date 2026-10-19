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

> **Status: early work (branch `init`).** Available: the server configuration,
> example server launchers, a speed test, and the Python backend (import,
> extraction, OCR, keyword search, sourced answers) driven from the command
> line. Semantic search and the Tauri interface come next.

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

## Backend

`backend/` is the Python package `iak` (Python 3.11+). Install its
dependencies and Tesseract with the French data:

```
pip install -r backend/requirements.txt
# Linux: sudo apt install tesseract-ocr tesseract-ocr-fra
# Windows: install Tesseract, then set IAK_TESSERACT to tesseract.exe
```

Until the interface exists, the library is used from the command line
(run from `backend/`):

```
python -m iak import ~/docs/*.pdf scans/page12.png     # copied into the library
python -m iak import --reference big-archive.pdf       # only its path is recorded
python -m iak list
python -m iak search "atlas relieur"
python -m iak ask "Où l'atlas a-t-il été envoyé ?"
python -m iak show 3                                   # pages with their text
python -m iak edit 42 corrected.txt                    # correct an OCR page
python -m iak reindex 3 --extract                      # extract and OCR again
python -m iak delete 3
```

The library folder is `--library`, or `IAK_LIBRARY`, or `./data`; it holds
`iak.sqlite`, the copies of imported files under `originals/` (named by their
content hash) and database backups under `backups/`. It can live anywhere,
on the key or on a disk.

How documents are handled:

- **Units and citations.** A document is cut into units, the parts an
  answer can cite: pages for PDF and images (`p. 12`, using the PDF's own
  page labels), sections for DOCX, Markdown, HTML and EPUB (the heading, or
  `§ 3`, `chap. 3`), blocks of lines for TXT (`l. 120`).
- **Text and OCR.** PDF pages with fewer than 40 characters of text are
  rendered at 300 dpi and read by Tesseract (`fra+eng` by default,
  `--ocr-langs` or `IAK_OCR_LANGS`; `fra+frm` suits older French); images
  always are, small screenshots enlarged twice first. Word boxes and
  confidence are kept, for highlighting and for spotting doubtful pages.
- **Corrections.** `edit` replaces a unit's text and reindexes only that
  unit; the extracted text is kept beside it, and extracting again keeps
  the corrections.
- **Duplicates.** A file whose content is already in the library is not
  imported again; two different files with the same name are both kept.
- **Passages.** Each unit is cut into passages of about 1,200 characters
  overlapping by 200, on paragraph or sentence boundaries, indexed with
  SQLite FTS5 (accents ignored).
- **Answers.** The best passages (8 at most, 10,000 characters) are sent to
  the chat server inside `<passage>` tags, with an instruction to treat them
  as data, never as instructions. Only citations of passages actually sent
  are kept; no passage, or a model reply of `NO_ANSWER`, gives "the documents
  do not allow an answer"; an answer citing nothing is flagged. Thinking is
  turned off and the server's prompt cache is used.

Tests: `cd backend && python -m pytest` (the OCR tests are skipped when
Tesseract is missing).

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

Gemma 4 can reason before answering ("thinking"); the reasoning is returned
apart from the answer and costs generation time. The test turns it off for
each request (`chat_template_kwargs`), as IA-K will do by default; add
`--thinking` to measure it.

### Measured on the target laptop

Ryzen AI 9 HX 370, Radeon 890M, 32 GB, Windows, llama.cpp Vulkan build 11696,
context 8192, thinking off (`scripts/smoke_test.py --prefill 6000`):

| | Gemma 4 12B QAT, all on iGPU | Gemma 4 26B-A4B QAT, experts and vision on CPU |
|---|---|---|
| generation | 7–9 tok/s | 16–19 tok/s |
| prompt of 5,600 tokens | 125–134 tok/s, 42–45 s | 187 tok/s, 30 s (275 tok/s over the first 2,000) |
| image question | 517 tokens in 2.6 s | 517 tokens in 13 s |

- The 26B-A4B does not fit in the iGPU memory with its experts (Vulkan
  `ErrorOutOfDeviceMemory`); with `--n-cpu-moe 99 --no-mmproj-offload` it
  loads and is the faster model for text, about twice the 12B for
  generation. These are the defaults of the `gemma26` profile.
- Keeping fewer experts on the CPU barely helps on this shared-memory APU:
  `--n-cpu-moe` 20, 15, 10, 7 give 189–200 tok/s for the long prompt and the
  same 15–19 tok/s generation as 99. The default stays at 99, which leaves
  the most iGPU memory free.
- A larger micro-batch does not speed up text prompts here (`-ub 1024`:
  125 tok/s), but llama.cpp caps image tokens to it: with the default 512 an
  image gets 512 tokens instead of up to 1120, which matters for reading
  scans. The launchers use `-ub 1280` when vision is on: with the 26B-A4B the
  test image then takes 709 tokens and 20 s instead of 517 and 13 s.
- Prompt speed falls as the prompt grows: document chat will send about
  2,000–3,000 tokens of passages and reuse the server's prompt cache between
  questions on the same passages.
- llama-server warns that, without `--api-key`, any web page open in a
  browser on the same computer can call it (CORS allows all origins). Set a
  key on the server and the same `api_key` in `config/servers.toml`.

## Licences

IA-K's own code is under the MIT licence. PyMuPDF is AGPL-3.0: a packaged
build of IA-K that includes it is therefore distributed under the AGPL terms,
while the MIT source stays MIT. Models are not part of IA-K and keep their own
licences (Gemma terms of use, Apache 2.0 for Qwen).

## Roadmap

1. Feasibility study, model servers and their configuration *(done)*
2. Python backend: import, text extraction, OCR, storage, chunking, citations by page *(done, command line)*
3. Full-text search and sourced answers *(done, command line)*
4. Embeddings and hybrid search
5. Document chat and image analysis
6. Tauri interface: library, viewer, search, chat, OCR correction
7. Windows and Linux builds, portable layout, documentation
8. Translation of documents (as in [truchement](https://github.com/fhoudebert/transcribe))
