# Developing IA-K

[Back to the README](README.md) · [SERVERS.md](SERVERS.md) · [BACKEND.md](BACKEND.md)

## Architecture

| Part | Choice |
|---|---|
| Interface | Tauri 2 (`src-tauri/`, Rust) showing `app/` (plain HTML, CSS, JavaScript, no build step) |
| Engine | Python package `iak` (`backend/`), local HTTP API on 127.0.0.1 with a random token |
| Inference | llama.cpp `llama-server` (Vulkan), OpenAI-compatible API, outside IA-K |
| OCR | Tesseract, command line |
| PDF, EPUB, images | PyMuPDF 1.x (2.0 pulls onnxruntime and more) |
| DOCX | python-docx |
| EPUB and HTML rewriting | Beautiful Soup 4 on lxml (books) |
| Storage | SQLite: documents, units, passages, FTS5, vectors as float32 BLOBs searched with numpy, analyses, translations, settings |

At start the application finds the IA-K folder from its own location (or
`IAK_ROOT`; an AppImage counts from the `.AppImage` file), launches
`python -m iak serve --watch-stdin` with the portable Python, reads the
`IAK_API` line for the URL and token, and gives them to the interface
(`backend_info` command). The engine stops when the application closes its
standard input, even after a crash. The library is `data/`, or the folder of
`config/app.json` (written by `set_library`), or `IAK_LIBRARY`.

Repository layout:

```
app/                 interface: index.html, main.js, i18n.js (fr, en), style.css
backend/iak/         engine: db, extract, ocr, chunk, library, search, embed,
                     answer, analyze, translate, book, servers, api, __main__ (command line)
backend/tests/       pytest, on generated documents and fake model servers
src-tauri/           Rust: backend.rs (engine process, library folder), lib.rs
config/              servers.example.toml
server/              llama-server launchers, start-servers.*, settings (SERVERS.md)
serveurs-*.bat, .sh  servers of a use + IA-K in one go
scripts/             smoke_test.py (model servers), make_portable.py (portable zip)
setup_python.*       portable Python; iak.* command line
.github/workflows/   ci.yml
```

## Portable Python

A virtual environment is not portable (it points to the Python that created
it). `setup_python.sh` / `.bat` install a complete, relocatable CPython from
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(stripped build, checked against its SHA-256) in `build/python/<os>/`, with
the exact packages of `backend/requirements.lock`. A `.complete` marker is
written only once the standard library and pip import; an install without it
is redone. Windows extracts on the key itself, so the last step is a rename,
not a cross-drive copy.

exFAT and FAT32 keys: they hold no symbolic links and do not tell apart names
differing only by case. The Linux setup therefore drops the symbolic links
(second names of files that are there anyway, such as `python3`), drops
`share/terminfo` (folders `N/` and `n/`…, read only by `curses`) and
`share/man`, and refuses to copy if any other such pair of names appears.
Checked on a real exFAT volume: setup, command line and application.

## Building the application

Requirements: Rust (rustup), Node.js for the Tauri command line, and on Linux
`libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev`; on Windows the MSVC build
tools (WebView2 is part of Windows 10 and 11).

```
npm install
npm run dev                                   # the application with its engine
npm run build                                 # installers in src-tauri/target/release/bundle
npx tauri build --no-bundle                   # only src-tauri/target/release/ia-k[.exe]
python scripts/make_portable.py --exe src-tauri/target/release/ia-k.exe --os windows
```

`make_portable.py` assembles `dist/ia-k-<os>/` and its zip: the application,
the engine, the setup scripts, the launchers and the guides (the interface is
inside the application; Python, models and library stay out). Builds are made
on each system.

## Continuous integration

`.github/workflows/ci.yml` runs on every push, on `ubuntu-22.04` /
`ubuntu-latest` and `windows-latest`: the engine tests (Tesseract with French
data), the Rust tests, a release build and the portable zip, kept 30 days as
the run's *Artifacts* (`ia-k-windows`, `ia-k-linux`). Check it locally with
[actionlint](https://github.com/rhysd/actionlint). A macOS job (`macos-latest`,
artifact `ia-k-mac`) is on the roadmap: it needs a macOS `setup_python`
(python-build-standalone has `aarch64-apple-darwin` builds) and the
`.app` bundle in the zip.

## Tests

```
cd backend && python -m pytest          # OCR tests skipped without Tesseract
cd src-tauri && cargo test
```

The engine tests generate their documents (PDF, scans, DOCX, EPUB…) and use
fake chat, embedding and TranslateGemma servers speaking the llama-server protocol
(including streaming and its error lines). The interface was checked with
Playwright against real models; `scripts/smoke_test.py` checks real servers.

## Working on the interface in a browser

```
./iak.sh serve --allow-origin http://127.0.0.1:8899
cd app && python -m http.server 8899 --bind 127.0.0.1
# open http://127.0.0.1:8899/index.html?url=<engine url>&token=<token>
```

File dialogs and drag and drop exist only inside the application. Texts are
in `app/i18n.js` (French and English, same keys). Document text is always
inserted as text; model output goes through a small escaping Markdown
renderer.

## Security notes

- The API listens on 127.0.0.1 with a per-start token, checks `Host`, and
  answers CORS only for the application's origins; the CSP of the window
  allows connections to 127.0.0.1 only.
- Text from documents is data: it reaches the model inside tags, with an
  instruction never to follow it, and citations are checked against the
  passages sent. Nothing the model writes is executed.
- The database is backed up before deletions and schema upgrades; originals
  are never modified.
