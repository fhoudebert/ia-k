# IA-K

![IA-K](assets/ia-k-512.png)

IA-K is a personal, offline desktop assistant for your documents. Give it
PDF files, scans, screenshots, EPUB, HTML, DOCX, TXT or Markdown: it reads
them (with OCR when needed), lets you search them by words or by meaning, and
answers questions about them with **sourced answers** that point to the
document, the page and the passage. It also translates texts and whole
documents with TranslateGemma.

- **Offline and private.** Everything runs on your computer with local
  language models; no document is sent anywhere.
- **Portable.** IA-K lives on a USB key or a disk folder and runs on
  Windows, Linux and macOS, from the same exFAT key if you like.
- **French and English** interface.
- Open source, MIT licence.

Other guides: [FEATURES.md](FEATURES.md) (what IA-K does and how to use
it; in French, [FONCTIONNALITES.md](FONCTIONNALITES.md)), [SERVERS.md](SERVERS.md) (running the language models
with llama.cpp), [BACKEND.md](BACKEND.md) (command line, how documents are
handled, local API), [DEVELOPERS.md](DEVELOPERS.md) (building, tests,
architecture).

## What you need

- A Windows 10/11 or Linux computer (x86-64), or a Mac (macOS 11 or later,
  Apple silicon or Intel). The reference machine is a laptop with a Ryzen AI
  9 HX 370, its Radeon 890M and 32 GB of memory.
- **Language model servers** on that computer (or on your network):
  [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` with
  Gemma 4 for answers and EmbeddingGemma for search by meaning. IA-K does
  not start them; [SERVERS.md](SERVERS.md) explains how, with ready-made
  launchers.
- Nothing else for scans and images: the zips carry
  [Tesseract](https://github.com/tesseract-ocr/tesseract) with French,
  English, Middle French and Latin. On an Intel Mac, which it does not cover
  yet, install it with `brew install tesseract tesseract-lang`
  ([Homebrew](https://brew.sh)); a Tesseract installed on the computer is
  used too when the key has none. Without any, documents with real text
  still work.
- About 260 MB on the key for each system (application, its Python and its
  Tesseract) and 65 MB of language data shared by all, plus your
  documents.

## Installing on a key or a disk

1. **Get IA-K and unzip it.** Download `ia-k.zip`, the key for all
   systems, or only `ia-k-windows.zip`, `ia-k-linux.zip` or `ia-k-mac.zip`
   (GitHub, *Actions* tab, last successful *CI* run, *Artifacts*), or build
   it ([DEVELOPERS.md](DEVELOPERS.md)). Unzip it at the root of the key, for
   instance `E:\ia-k`, `/media/me/KEY/ia-k` or `/Volumes/KEY/ia-k`. Each zip
   holds the application and its engine with their own Python: nothing else
   to install, nothing written on the computer. Zips of several systems can
   be unzipped in the same folder: the systems then share the library.

   On macOS, IA-K is not signed by an identified developer: before the first
   start, run once in the Terminal `xattr -dr com.apple.quarantine
   /Volumes/KEY/ia-k` (your folder), or right-click `IA-K.app` → *Open*.
2. **Intel Mac only**: the zip holds the Python of Apple silicon Macs. Run
   once, in the Terminal, `./setup_python.sh` (needs internet): it adds the
   Intel one next to it in `build/python/`, so the key then serves both.
   The same script (`setup_python.bat` on Windows) updates the Python of a
   key, or installs it in a folder built from the sources; it can be run
   again safely.
3. **Say where the model servers are.** With the usual ports (chat 8080,
   meaning 8081, vision 8082, translation 8083) there is nothing to do.
   Otherwise fill in *Settings*, or copy `config/servers.example.toml` to
   `config/servers.toml` and edit it. Each computer gets its own section,
   chosen by its name, so one key works on several computers.
4. **Start the model servers, then IA-K.** One file does both, and stops
   the servers when IA-K is closed:
   - `serveurs-baseConnaissance.bat` (Linux: `./serveurs-baseConnaissance.sh`):
     Gemma 4 26B-A4B and EmbeddingGemma, for the library, search and
     questions;
   - `serveurs-traduction.bat` (`./serveurs-traduction.sh`): TranslateGemma
     and EmbeddingGemma, for translation;
   - `serveurs-vl.bat` (`./serveurs-vl.sh`): Qwen3-VL-8B and EmbeddingGemma,
     to read difficult scans again (below).

   Their settings (where llama.cpp and the models are, memory options) are
   those of the reference laptop, in `server/local.hx370.bat`; on another
   computer copy it to `server/local.bat` and edit the copy
   ([SERVERS.md](SERVERS.md#starting-them)); on Linux and macOS, `server/local.sh`.
   IA-K alone is `ia-k.exe`, `./ia-k` on Linux, `IA-K.app` on macOS.

The folder then looks like this:

```
ia-k/
├── ia-k.exe  ia-k  IA-K.app the application (Windows, Linux, macOS)
├── backend/                 the engine (Python), started by the application
├── build/python/            portable Python, one folder per system (two for Macs)
├── config/servers.toml      where the model servers are, per computer
├── data/                    your library: database, copies of your files, backups
├── server/                  launchers for the model servers, their settings
├── serveurs-*.bat  *.sh     model servers + IA-K in one go
└── setup_python.*  iak.*    setup, and the command line (BACKEND.md)
```

The library can be moved elsewhere (a disk, a bigger key) from *Settings*.

## Using IA-K

- **Library**: import files or whole folders (a watched folder imports its
  new files by itself), group them in collections, correct the OCR text in
  the viewer.
- **Search and questions**: search by words or by meaning; ask questions
  and get answers citing the document, the page and the passage, exportable
  as Markdown.
- **Analyses**: a page described or transcribed, summaries, comparisons.
- **Full text**: a scanned PDF or EPUB turned into a light book of real
  text (paragraphs, notes, illustrations kept), finished book-wide (French
  typography, cut words, long s, modern spelling), as EPUB, PDF, HTML, Word,
  RTF, Markdown or for Project Gutenberg.
- **Difficult scans**: the vision model reads pages again, or only the
  doubtful words, as proposals you accept or not.
- **Translation**: a text, a document page by page, or a whole book with
  its layout, resumable after a pause.

Everything, step by step: [FEATURES.md](FEATURES.md).

## When something goes wrong

| What you see | What to do |
|---|---|
| "This library is already open in another IA-K window" | Two IA-K were started (two `serveurs-…` files, for instance): use the window already open, or close it and click *Try again*. |
| "The library is busy (database is locked)" | Another program writes in the library: another IA-K on it (see above), or a long command line import. Wait a moment and do it again. |
| "The IA-K engine could not start" | Read `data/iak-backend.log`; if `build/python/` has no folder for this system (an Intel Mac, a folder built from the sources), run `setup_python` once. |
| Chat, Meaning or Translation shown in red at the top | The model server is not running or not at that address: start it, check *Settings*. |
| An error mentioning `ErrorOutOfDeviceMemory` or "graphics memory" | The model server ran out of graphics memory: see [SERVERS.md, Memory](SERVERS.md#memory). |
| "The documents do not allow an answer" | Nothing relevant was found: rephrase, check the document is indexed, or try *Search* first. |
| Scans give no text | Tesseract is missing or lacks the language: *Settings* shows which Tesseract is used and its languages (on the key: `fra`, `eng`, `frm`, `lat`). |
| Linux key: "cannot run, mounted noexec" | Remount the key with the `exec` option. |
| macOS: "IA-K cannot be opened" or "is damaged" | The download is quarantined: `xattr -dr com.apple.quarantine` on the IA-K folder, then open it again. |

## Licences

IA-K's own code is under the MIT licence. It uses PyMuPDF, under AGPL-3.0:
a distributed build of IA-K that includes it falls under the AGPL terms,
while the source stays MIT. Language models are not part of IA-K and keep
their own licences (Gemma terms of use, Apache 2.0 for Qwen).
