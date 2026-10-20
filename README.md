# IA-K

![IA-K](assets/ia-k.png)

IA-K is a personal, offline desktop assistant for your documents. Give it
PDF files, scans, screenshots, EPUB, HTML, DOCX, TXT or Markdown: it reads
them (with OCR when needed), lets you search them by words or by meaning, and
answers questions about them with **sourced answers** that point to the
document, the page and the passage. It also translates texts and whole
documents with TranslateGemma.

- **Offline and private.** Everything runs on your computer with local
  language models; no document is sent anywhere.
- **Portable.** IA-K lives on a USB key or a disk folder and runs on Windows
  and Linux, from the same exFAT key if you like.
- **French and English** interface.
- Open source, MIT licence.

Other guides: [SERVERS.md](SERVERS.md) (running the language models with
llama.cpp), [BACKEND.md](BACKEND.md) (command line, how documents are
handled, local API), [DEVELOPERS.md](DEVELOPERS.md) (building, tests,
architecture).

## What you need

- A Windows 10/11 or Linux computer (x86-64). The reference machine is a
  laptop with a Ryzen AI 9 HX 370, its Radeon 890M and 32 GB of memory.
- **Language model servers** on that computer (or on your network):
  [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` with
  Gemma 4 for answers and EmbeddingGemma for search by meaning. IA-K does
  not start them; [SERVERS.md](SERVERS.md) explains how, with ready-made
  launchers.
- **Tesseract** for scans and images: on Windows the
  [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki)
  (tick French), on Linux `sudo apt install tesseract-ocr tesseract-ocr-fra`.
  Without it, documents with real text still work.
- About 300 MB on the key for each system, plus your documents.

## Installing on a key or a disk

1. **Get IA-K.** Download `ia-k-windows.zip` or `ia-k-linux.zip` (GitHub,
   *Actions* tab, last successful *CI* run, *Artifacts*), or build it
   ([DEVELOPERS.md](DEVELOPERS.md)). Unzip it at the root of the key, for
   instance `E:\ia-k` or `/media/me/KEY/ia-k`. Both zips can be unzipped in
   the same folder: Windows and Linux then share the library.
2. **Install its Python** (once per system, needs internet):
   `setup_python.bat` on Windows, `./setup_python.sh` on Linux. It puts a
   complete Python in `build\python\` without touching the computer; it can
   be run again safely.
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
     and EmbeddingGemma, for translation.

   Their settings (where llama.cpp and the models are, memory options) are
   those of the reference laptop, in `server/local.hx370.bat`; on another
   computer copy it to `server/local.bat` and edit the copy
   ([SERVERS.md](SERVERS.md#starting-them)). IA-K alone is `ia-k.exe`, or
   `./ia-k` on Linux.

The folder then looks like this:

```
ia-k/
├── ia-k.exe  ia-k           the application (Windows, Linux)
├── backend/                 the engine (Python), started by the application
├── build/python/            portable Python, one folder per system
├── config/servers.toml      where the model servers are, per computer
├── data/                    your library: database, copies of your files, backups
├── server/                  launchers for the model servers, their settings
├── serveurs-*.bat  *.sh     model servers + IA-K in one go
└── setup_python.*  iak.*    setup, and the command line (BACKEND.md)
```

The library can be moved elsewhere (a disk, a bigger key) from *Settings*.

## Using IA-K

**Library.** *Import files…* or drop files on the window. They are copied
into the library (or only referenced, if you tick the box), read, cut into
passages and indexed; scanned pages go through OCR. The list shows each
document's state, pages, OCR pages and passages; the *Jobs* panel shows
imports and analyses in progress. From the list you can open, summarise or
delete a document, and compare the documents you tick.

**Document.** The page on the left, its text on the right. OCR text can be
corrected and saved; the correction is kept even if the document is read
again. *Analyse the page* asks the model to describe it, transcribe it,
explain an error on a screenshot, extract names, dates and amounts, or answer
a question of yours. When you arrive from a search or a citation, the passage
is highlighted in yellow on the page.

**Search.** Type words or a question. *Automatic* combines words and meaning:
"who repaired the damaged map?" finds "the binder mended the atlas spoilt by
damp" without any common word. Results can be limited to a type of document
or to one document; click one to open its page.

**Questions.** Ask in plain language. The answer is written as you watch,
with citations `[S1]` you can click; *Sources* lists them. You can follow up
("and when did he return it?"): the last exchanges are taken into account
until *New conversation*. When the documents do not contain the answer, IA-K
says so instead of inventing one. Questions can be limited to a type or a
document.

**Translation.** *Text*: paste a text, choose the languages (or let IA-K
detect the source language), *Translate*. *Library document*: choose a
document, *Translate the document*; it is translated page by page (or
section by section) and the translations are kept, so a long document can be
translated in several sittings, a page re-translated on its own, and the
pages whose text you corrected since are marked. *Export…* writes the
translation to a Markdown or text file, with the original if you like. In
the viewer, *Translate the page* does it for the page shown. Translation uses
TranslateGemma when its server runs, and otherwise the chat model.

*Book* translates a whole book and keeps its layout: an EPUB comes back as
an EPUB with its italics, notes, links and table of contents; a PDF as
Markdown (headings found from the font sizes, running heads and page numbers
removed, paragraphs joined across pages; pages read by OCR in the library
are taken from there); DOCX, HTML, text and Markdown files in their own
format. Choose a library document or a file, the languages and the quality
(1 pass, 2 with a revision, 3 with a final proofreading), and optionally a
glossary (`term = translation` per line). Paragraphs are sent in groups so
the model keeps the context. Everything translated is saved at once: *Pause*,
close IA-K, shut the computer down, and *Resume* another day where it
stopped. A *Trial* translates the first three blocks to check the result
first. When it is done, *Save as…*, or *Add to the library* to search the
translation. A long book takes hours, a night with two passes: the log
shows the time left.
**Settings.** The model servers of this computer, an API key if your servers
need one, the OCR languages (`fra+eng`, `fra+frm` for Middle French…), the
library folder.

Good to know:

- **Speed.** On the reference laptop with Gemma 4 26B-A4B, an answer starts
  after about 10 to 20 seconds and is written at 15 to 19 words per second;
  an image analysis takes 15 to 30 seconds. Importing a scanned book takes
  about a second per page for OCR.
- **One big model at a time** on a 32 GB laptop: the 26B (answers) and
  TranslateGemma 12B (translation) do not fit in graphics memory together;
  switch with the `serveurs-*` files.
- **Remote servers.** If the chat server is not on this computer, a banner
  says that passages of your documents leave it.
- **Backups.** The database is backed up in `data/backups/` before each
  deletion or upgrade. Your original files are never modified.

## When something goes wrong

| What you see | What to do |
|---|---|
| "The IA-K engine could not start" | Run `setup_python` on this system; read `data/iak-backend.log`. |
| Chat, Meaning or Translation shown in red at the top | The model server is not running or not at that address: start it, check *Settings*. |
| An error mentioning `ErrorOutOfDeviceMemory` or "graphics memory" | The model server ran out of graphics memory: see [SERVERS.md, Memory](SERVERS.md#memory). |
| "The documents do not allow an answer" | Nothing relevant was found: rephrase, check the document is indexed, or try *Search* first. |
| Scans give no text | Tesseract is missing or lacks the language: *Settings* lists the installed languages. |
| Linux key: "cannot run, mounted noexec" | Remount the key with the `exec` option. |

## Licences

IA-K's own code is under the MIT licence. It uses PyMuPDF, under AGPL-3.0:
a distributed build of IA-K that includes it falls under the AGPL terms,
while the source stays MIT. Language models are not part of IA-K and keep
their own licences (Gemma terms of use, Apache 2.0 for Qwen).

## Roadmap

Done:

1. Feasibility study, model servers and their configuration
2. Backend: import, text extraction, OCR, storage, passages, citations by page
3. Full-text search and sourced answers
4. Search by meaning (EmbeddingGemma) and hybrid search
5. Document chat, follow-up questions, image analysis, summaries, comparisons
6. Desktop interface (Tauri 2): library, viewer with highlights, search,
   questions, settings
7. Windows and Linux builds by CI, portable zip, Linux and Windows on the
   same exFAT key
8. Translation tab with [TranslateGemma](https://huggingface.co/google/translategemma-12b-it):
   a text, a page or a whole document of the library, kept and exportable;
   launchers that start the servers of a use and IA-K together
9. Translation of whole books (EPUB, PDF, DOCX, HTML, TXT, Markdown) as in
   [truchement](https://github.com/fhoudebert/transcribe): paragraph groups
   sized to the server's context, revision passes, layout, notes and table
   of contents kept, glossary, resumable after a pause or a shutdown

Next:

10. **[Qwen3-VL-8B](https://huggingface.co/lmstudio-community/Qwen3-VL-8B-Instruct-GGUF)
    as dedicated vision model** (`serveurs-vl.bat`, with EmbeddingGemma):
    transcription of difficult scans and tables to correct or replace
    Tesseract's text, page by page or for a whole document.

Later:

- **macOS**: an `ia-k-mac` zip downloadable from the *Actions* tab
  (*Artifacts*) like the Windows and Linux ones, built by CI on a macOS
  runner, with a `setup_python` for macOS.
- Other launchers by use, as needed.
- Export of answers, summaries and comparisons (Markdown, with their sources).
- Collections (named groups of documents) to limit search and questions.
- Watched folders: documents added to a folder imported by themselves.
- The engine frozen in the application (no `setup_python` step).
