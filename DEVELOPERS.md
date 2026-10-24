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
| Storage | SQLite: documents, units, passages, FTS5, vectors as float32 BLOBs searched with numpy, analyses, translations, collections, folders, settings |

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
backend/iak/         engine: db, extract, ocr, chunk, library, collection, folders, search, embed,
                     answer, analyze, transcription, fulltext, translate, book, bookformats,
                     export, servers, api, __main__ (command line)
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

Requirements: Rust 1.90 or later (rustup; see *Rust and Tauri versions*
below), Node.js for the Tauri command line, and on Linux
`libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev`; on Windows the MSVC build
tools (WebView2 is part of Windows 10 and 11); on macOS the Xcode command
line tools (`xcode-select --install`).

```
npm install
npm run dev                                   # the application with its engine
npm run build                                 # installers in src-tauri/target/release/bundle
npx tauri build --no-bundle                   # only src-tauri/target/release/ia-k[.exe]
setup_python.bat                              # build\python\windows, for --python
python scripts/make_portable.py --exe src-tauri/target/release/ia-k.exe --os windows --python
# macOS, for both processors (rustup target add aarch64-apple-darwin x86_64-apple-darwin)
npx tauri build --target universal-apple-darwin --bundles app
python scripts/make_portable.py --os mac --python \
    --exe src-tauri/target/universal-apple-darwin/release/bundle/macos/IA-K.app
# one key for all systems, from the three zips (Linux first)
python scripts/merge_portable.py dist/ia-k-linux.zip dist/ia-k-mac.zip dist/ia-k-windows.zip
python scripts/build_tesseract.py             # build/tesseract, for --tesseract
```

`make_portable.py` assembles `dist/ia-k-<os>/` and its zip: the application,
the engine, the setup scripts, the launchers and the guides (the interface is
inside the application; models and library stay out). With `--python` it adds
the portable Python that `setup_python` installed in `build/python/` (every
`mac-*` folder on macOS), without what IA-K does not use: `__pycache__`
(stale once unzipped, as a zip keeps times to 2 seconds), the test suites of
the standard library and of the packages, `idlelib`, `tkinter`, Tcl/Tk,
`ensurepip`, `lib2to3`, `share/terminfo` and `share/man`; pip stays, for
`setup_python` updates. It then imports the engine and its packages with the
copied Python (`--no-check` skips it). About 215 MB unzipped, 80 MB zipped,
per system. With `--tesseract` it adds the Tesseract of this system and the
shared language data, and lists its languages with it. Builds are made on
each system.

## Portable Tesseract

`scripts/build_tesseract.py` builds Tesseract 5.5.3 from its sources (git
clones at fixed commits, checked) with CMake: zlib, libpng and Leptonica
(PNG only: IA-K gives it PNG images) linked statically, without OpenMP,
network, archives, TIFF or training tools, the C++ runtime linked in too
(`-static-libstdc++` on Linux, `/MT` with Visual Studio). The result is one
program of about 7 MB, `build/tesseract/<os>/tesseract[.exe]`, that needs
only the system's C library; its SIMD code is chosen when it runs
(`ENABLE_NATIVE` off). Built on `ubuntu-22.04`, it runs on any Linux with
glibc 2.35 or later. The language data, `build/tesseract/tessdata`, is the
standard models of tessdata 4.1.0 (fra, eng, frm, lat, checked against
their SHA-256) with the output configs Tesseract needs (`tsv`); one copy
serves all systems. About 5 minutes per system; CI keeps `build/tesseract`
in its cache until the script changes. `iak.ocr` finds it before an
installed Tesseract and runs it with `TESSDATA_PREFIX` set to that folder.

`merge_portable.py` merges the zips into `dist/ia-k.zip` (folder `ia-k/`):
shared files once (they must match, line endings aside), executable bits
kept, names differing only by case refused (exFAT).

## Continuous integration

`.github/workflows/ci.yml` runs on every push, on `ubuntu-22.04` /
`ubuntu-latest`, `windows-latest` and `macos-latest`: the engine tests
(Tesseract with French data), the Rust tests, a release build and the
portable zip with its Python and its Tesseract (`setup_python` and
`build_tesseract.py` run first), kept 30 days as
the run's *Artifacts* (`ia-k-windows`, `ia-k-linux`, `ia-k-mac`), and the
`key` job merges them into `ia-k` (`ia-k.zip`, all systems). The desktop
builds use the latest stable Rust; the `msrv` job builds and tests the
application with Rust 1.90, the minimum, and the locked dependencies. Check
it locally with [actionlint](https://github.com/rhysd/actionlint).

## Rust and Tauri versions

`src-tauri/Cargo.toml` declares `edition = "2024"` and `rust-version =
"1.90"`. With edition 2024 Cargo uses resolver 3, which follows
`rust-version`: `cargo update` locks the newest versions that still build
with Rust 1.90, and leaves out the ones that need a newer Rust.

| Crate | Locked | Needs Rust |
|---|---|---|
| tauri | 2.12.5 | 1.90 |
| tauri-build, tauri-codegen, tauri-macros | 2.7.4 | 1.90 |
| tauri-plugin-dialog | 2.8.3 | 1.90 |
| tauri-runtime / -wry, tauri-utils | 2.12 / 2.10 | 1.90 |
| muda | 0.20.0 | 1.90 |
| uuid | 1.28.0 | 1.89 |

Tauri 2.12 is what sets the minimum: with Rust 1.88 the resolver falls back
to Tauri 2.11 (muda 0.19, uuid 1.26). The Tauri command line
(`@tauri-apps/cli` in `package.json`, `^2.12.3`) is of the same minor version
as the crate, so `tauri build` reports no mismatch. Checked with Rust 1.90:
`cargo test --locked`, `cargo clippy --all-targets` (no warning) and
`tauri build`.

To move to a newer Tauri that needs a newer Rust: raise `rust-version`, run
`cargo update` in `src-tauri/`, update `dtolnay/rust-toolchain@1.xx` in the
`msrv` job and this section, then commit `Cargo.lock`. A dependency that
needs more than `rust-version` makes `cargo +1.90 test --locked` fail, and so
the `msrv` job.

Edition 2024 marks `std::env::set_var` as `unsafe`: the code reads the
environment but never changes it (the test of `find_root` gives its value
to `find_root_from` instead).

macOS: one universal application (`--target universal-apple-darwin`, Apple
silicon and Intel) bundled as `IA-K.app` (`--bundles app`), ad hoc signed
(`signingIdentity "-"`): it is not notarised, so a downloaded copy is
quarantined (README). The application finds the IA-K folder above
`IA-K.app/Contents/MacOS/`, and the portable Python of its processor in
`build/python/mac-aarch64` or `build/python/mac-x86_64` (`setup_python.sh`
installs the `aarch64-` or `x86_64-apple-darwin` build of
python-build-standalone of the Mac it runs on, and renames the
`build/python/mac` of earlier versions when it is of that processor; until
then the application and `iak.sh` still use it). The CI runner is an Apple
silicon Mac, so `ia-k-mac.zip` holds `mac-aarch64` only, for Python and for
Tesseract (`build/tesseract/mac-aarch64`).

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
