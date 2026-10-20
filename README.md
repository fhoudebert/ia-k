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
> example server launchers, a speed test, a portable Python, and the backend
> (import, extraction, OCR, hybrid keyword and semantic search, sourced
> answers, image analysis, summaries, comparisons), usable from the command
> line, through a local HTTP API, and in a first desktop application.

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

## Desktop application

The interface is a Tauri 2 application (`src-tauri/`, Rust) showing the web
pages of `app/` (plain HTML, CSS and JavaScript, no build step). At start it
launches the backend (`python -m iak serve`) with the portable Python, reads
its URL and token, and stops it on exit; the backend also stops by itself if
the application is killed. Its log is `data/iak-backend.log`.

Screens, in French or English:

- **Library**: import by dialog or by dropping files on the window (copied,
  or only referenced), list with state, pages, OCR pages and passages,
  summaries, comparison of the checked documents, deletion, running jobs.
- **Document**: pages or sections, the page image beside its text; the text
  can be corrected (OCR confidence shown); page analysis (describe,
  transcribe, explain an error, extract, free question) and the stored
  analyses. Opened from a search result or a citation, the passage is
  highlighted on the page image (from the word boxes kept at extraction).
- **Search**: hybrid, keyword or meaning search, limited to a type of
  document or to one document if wished; each result opens its page.
- **Questions**: a conversation: follow-up questions ("and when did he
  return it?") are understood from the last three exchanges, which are sent
  to the model with the new passages and also used to search. The answer
  appears as it is written, then its citations `[S1]` become clickable and
  the sources are listed. Questions can be limited to a type or a document;
  "New conversation" starts afresh. A warning shows when the chat server is
  not on this computer.
- **Settings**: the model servers of this computer (written to
  `config/servers.toml`, in a section matched to the computer name; other
  computers' sections are kept, the previous file saved as `.bak`), an API
  key, the OCR languages (with those installed), and the library folder.

### Portable layout

The program goes at the root of the IA-K folder, on the key or a disk:

```
ia-k/                      the folder from this repository
├── ia-k  or  ia-k.exe     the application (built below)
├── backend/  app/  config/servers.toml
├── build/python/linux/   build/python/windows/   build/tesseract/ (optional)
└── data/                  library, created at first start
```

It finds that folder from its own location (or `IAK_ROOT`); the library is
`data/` unless another folder was chosen in the settings (`config/app.json`,
stored relative to the IA-K folder when it is inside it, so that it follows
the key) or `IAK_LIBRARY` says otherwise. An AppImage counts from where the
`.AppImage` file is.

### Downloads and continuous integration

Every push to GitHub runs `.github/workflows/ci.yml` on Linux and Windows:
the backend tests (with Tesseract and its French data), the Rust tests, a
release build, and the portable folder made by `scripts/make_portable.py`.
The zips `ia-k-windows` and `ia-k-linux` are in the run's *Artifacts*
(GitHub, Actions tab, kept 30 days). Unzip one on the key, run
`setup_python`, add `config/servers.toml`, and start `ia-k`.

### Building

Requirements: Rust (rustup), Node.js for the Tauri command line, and on
Linux `libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev`; on Windows the MSVC
build tools (WebView2 is part of Windows 10 and 11).

```
npm install
npm run dev          # development: the application with its backend
npm run build        # release: src-tauri/target/release/ia-k[.exe] and installers
```

Copy `src-tauri/target/release/ia-k` (or `ia-k.exe`) to the root of the
IA-K folder, or make the portable folder and its zip in `dist/`:

```
python scripts/make_portable.py --exe src-tauri/target/release/ia-k.exe --os windows
```

Builds are made on each system (Windows on Windows).

For interface work in a browser, start the backend with
`./iak.sh serve --allow-origin http://127.0.0.1:8899`, serve `app/` on that
port and open `index.html?url=<backend url>&token=<token>`.

## Backend

`backend/` is the Python package `iak`. It runs on a portable Python carried
with IA-K (next section); a Python 3.11+ with `pip install -r
backend/requirements.lock` works too, for development.

Until the interface exists, the library is used from the command line,
`iak.sh` on Linux, `iak.bat` on Windows:

```
./iak.sh import ~/docs/*.pdf scans/page12.png     # copied into the library
./iak.sh import --reference big-archive.pdf       # only its path is recorded
./iak.sh list
./iak.sh search "atlas relieur"
./iak.sh ask "Où l'atlas a-t-il été envoyé ?"
./iak.sh show 3                                   # pages with their text
./iak.sh edit 42 corrected.txt                    # correct an OCR page
./iak.sh reindex 3 --extract                      # extract and OCR again
./iak.sh delete 3
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

### Semantic search

With an embedding server (EmbeddingGemma 300M, port 8081, see *Model
servers*), every passage also gets a vector, and searches combine both ways:

```
./iak.sh import doc.pdf                 # vectors computed right after import
./iak.sh embed                          # or later, for passages without one
./iak.sh search "qui a restauré la carte endommagée ?"            # hybrid
./iak.sh search "..." --mode kw|sem     # one way only
./iak.sh status                         # counts and the model of the index
```

- **Hybrid by default.** Keyword and meaning results are fused by
  reciprocal rank; a passage found both ways comes first. Keywords catch
  names, numbers and exact terms; meaning catches reformulations and old
  spellings. Without an embedding server, search falls back to keywords.
- **Prefixes.** Passages are embedded as `title: <document> | text: ...`,
  queries as `task: search result | query: ...`, as EmbeddingGemma expects.
- **Cut-off.** Meaning search always has neighbours, related or not: hits
  below a cosine of 0.10 or under half the best score are dropped. With the
  real model, a reformulated question scored 0.43 on the right passage and
  about 0.05 on unrelated ones.
- **One model per index.** The index records its embedding model (file
  name, dimension, vector of a probe sentence). A server with another model
  is refused — keyword search keeps working — until `embed --rebuild`;
  another file or quantisation of the same model is accepted.
- **Storage and speed.** Vectors are float32 BLOBs in SQLite, about 3 KB
  per passage (100,000 passages ≈ 70,000 pages ≈ 400 MB), searched with
  numpy: 0.7 s to load 100,000 vectors once, then 14 ms per query. Embedding
  ran at 12 passages/s on a CPU-only test machine; the iGPU should do
  several times better.

### Images, summaries, comparisons

These use the chat server; images need vision (Gemma 4 started with its
`mmproj`, or a dedicated vision server in `config/servers.toml`):

```
./iak.sh look 42 --task describe        # what the page or image shows
./iak.sh look 42 --task transcribe      # read the text (alternative to OCR)
./iak.sh look 42 --task error           # screenshot: quote the error, cause, fix
./iak.sh look 42 --task extract         # names, dates, amounts, references
./iak.sh look 42 --ask "Quel est le montant total ?"
./iak.sh summarize 3                    # summary with [p. N] labels
./iak.sh compare 3 7 --question "dates" # common points, differences, contradictions
./iak.sh analyses 3                     # stored results
```

- `look` sends the page (PDF) or image rendered at up to 1,600 pixels, with
  the page's OCR text as a reading aid (except for `transcribe`).
- `summarize` works part by part (about 9,000 characters each) and merges
  the partial summaries; the page or section labels are kept as citations.
  A summary is stored and reused (`--refresh` to redo it), per language
  (`--lang fr|en`, or `IAK_LANG`).
- `compare` works from the documents' summaries (made first when missing);
  documents are named D1, D2… in the result.
- Document text inside prompts is wrapped in tags and declared as data, as
  for answers. Results are stored with the task, language and model, and
  deleted with their document.

Checked with a real Gemma 4 (E2B, CPU-only test machine): `look --task
error` on a Windows error screenshot quoted the message and code exactly and
gave causes and steps (84 s on two CPU cores); `ask` answered with the right
page cited, and refused a question the documents do not cover without
calling the model.

### Local API

The desktop interface talks to the backend through a local HTTP API:

```
./iak.sh serve            # prints: IAK_API {"url": "http://127.0.0.1:PORT", "token": "..."}
```

- **Access.** It listens on 127.0.0.1 only, on a free port (`--port` to
  fix one). Every request needs `Authorization: Bearer <token>`; the token is
  random at each start (`--token` or `IAK_TOKEN` to fix it) and is given to
  the interface on the first stdout line. Requests whose `Host` is not
  127.0.0.1 or localhost are refused (DNS rebinding), and only the Tauri
  origins get CORS answers (`--allow-origin` adds one, e.g. a development
  server).
- **Lifetime.** With `--watch-stdin` (used by the application) the backend
  stops as soon as its standard input closes, that is when the application
  ends, even abruptly.
- **Jobs.** Import (with OCR and vectors), embedding, re-extraction, image
  analysis, summaries and comparisons are jobs, run one at a time: the call
  returns `{"job": id}` (202) and `GET /api/jobs/<id>` gives the status,
  progress and result. Questions (`POST /api/ask`) answer directly.
- **Errors.** JSON `{"error": "..."}` with 400 (bad request), 401 (token),
  403 (host or origin), 404, 409 (server not configured, wrong embedding
  model, task not possible), 502 (model server error).

| Method and path | Purpose |
|---|---|
| `GET /api/health`, `/api/status`, `/api/servers` | version; counts; model servers and their state |
| `GET /api/documents[?kind=pdf]`, `GET /api/documents/<id>` | library, one document |
| `GET /api/documents/<id>/units[?boxes=1]` | pages or sections with their text |
| `GET /api/documents/<id>/original` | the original file, for the viewer |
| `DELETE /api/documents/<id>` | remove (database backed up first) |
| `POST /api/import` `{"paths": [...], "reference": false}` | job |
| `POST /api/documents/<id>/reindex` `{"extract": true}` | job |
| `GET /api/units/<id>`, `GET /api/units/<id>/image?max=1600` | text and word boxes; page as PNG |
| `PUT /api/units/<id>/text` `{"text": "..."}` | OCR correction, reindexed |
| `GET /api/search?q=&mode=auto|hybrid|sem|kw&n=&doc=&kind=` | passages with citations |
| `POST /api/ask` `{"question": "...", "doc_ids": [...]}` | sourced answer |
| `POST /api/ask/stream` (same body) | the same as server-sent events: `sources`, `token`…, `done` (or `error`) |
| (both) `"history": [{"question", "answer"}]`, `"kinds": ["pdf"]` | follow-up context (last 3 kept), filters |
| `GET /api/passages/<id>/boxes` | rectangles of a passage's lines on its page, in page units |
| `GET /api/settings`, `PUT /api/settings` `{"servers": {...}, "ocr_langs": "fra+eng"}` | servers of this computer, OCR languages |
| `POST /api/look` `{"unit_id": 42, "task": "error"}` or `"question"` | job |
| `POST /api/summarize` `{"doc_id": 3}`, `POST /api/compare` `{"doc_ids": [3, 7]}` | jobs |
| `GET /api/analyses[?doc=3]`, `GET /api/jobs`, `GET /api/jobs/<id>` | stored results, jobs |
| `POST /api/embed` `{"rebuild": false}` | job |

Checked end to end with real models (Gemma 4 E2B with vision and
EmbeddingGemma 300M on llama.cpp b11696, CPU-only test machine): import of
a PDF, a screenshot and a Markdown file with OCR and vectors in 5 s, hybrid
search, a cited answer in 10 s, an image analysis job in 66 s.

Tests: `cd backend && python -m pytest` (the OCR tests are skipped when
Tesseract is missing).

### Portable setup

A virtual environment is not portable: it keeps using the standard library
of the Python that created it, at that Python's path. IA-K carries instead a
complete, relocatable Python per system, from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(stripped build), with the exact packages of `backend/requirements.lock`:

```
./setup_python.sh        # Linux   -> build/python/linux    (~260 MB)
setup_python.bat         # Windows -> build\python\windows
```

Both copies can sit on the same key; each script checks the download against
its SHA-256 and can be run again safely (`--force` reinstalls Python). On
Linux, symbolic links are left out so the key can be exFAT; a key mounted
`noexec` must be remounted with `exec`.

Tesseract:

- Linux: the system package (`tesseract-ocr`, `tesseract-ocr-fra`, and
  `tesseract-ocr-frm` for older French).
- Windows: install it (e.g. the UB Mannheim build), or copy its folder
  (`tesseract.exe`, its DLLs and `tessdata\`) to `build\tesseract\` on the
  key; `iak.bat` uses that copy first. `IAK_TESSERACT` points to any other.

The desktop application (step 7) will ship the backend frozen into one
executable per system instead; until then these scripts are the way to run
IA-K from the key.

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
4. Embeddings and hybrid search *(done, command line)*
5. Document chat and image analysis *(done: command line and local API)*
6. Tauri interface: library, viewer, search, chat, OCR correction, settings *(first version)*
7. Windows and Linux builds, portable layout, documentation *(CI builds; to be checked on Windows)*
8. Translation of documents (as in [truchement](https://github.com/fhoudebert/transcribe))
