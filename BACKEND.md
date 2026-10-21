# The IA-K engine (backend)

The engine is the Python package `iak` in `backend/`. The desktop
application starts it by itself; it can also be used from the command line,
for scripts, batch imports or a computer without a screen. This page
describes the command line, how documents are handled, and the local API the
interface uses.

[Back to the README](README.md)

## Command line

`iak.bat` on Windows, `./iak.sh` on Linux, at the root of the IA-K folder
(they use the portable Python installed by `setup_python`):

```
./iak.sh import ~/docs/*.pdf scans/page12.png     # copied into the library
./iak.sh import --reference big-archive.pdf       # only its path is recorded
./iak.sh list                                     # documents, state, pages, OCR, passages
./iak.sh status                                   # counts, semantic index, servers
./iak.sh show 3                                   # pages of document 3 with their text
./iak.sh search "atlas relieur"                   # hybrid search
./iak.sh search "..." --mode kw|sem|hybrid --doc 3 --kind pdf --collection "Archives"
./iak.sh collection create "Archives" 3 7         # also: list, show, add, remove, rename, delete
./iak.sh fulltext 3 --format epub -o livre.epub   # a scanned document as text, illustrations kept
./iak.sh fulltext scans.epub [--vision]           # an EPUB of scanned pages, its pictures of text read
./iak.sh folder add ~/Documents/Archives          # its documents, in a collection "Archives"
./iak.sh folder scan 1                            # also: list, watch, unwatch, remove; --copy
./iak.sh ask "Où l'atlas a-t-il été envoyé ?"     # sourced answer
./iak.sh edit 42 corrected.txt                    # replace the text of unit 42 (OCR correction)
./iak.sh reindex 3 --extract                      # extract and OCR again
./iak.sh embed [--rebuild]                        # vectors for search by meaning
./iak.sh look 42 --task describe|transcribe|error|extract   # page analysis
./iak.sh look 42 --ask "Quel est le montant total ?"
./iak.sh reread 3 [--pages low|ocr|all] [--method correct|transcribe] [--replace]
./iak.sh summarize 3 [--refresh]                  # summary with [p. N] labels
./iak.sh compare 3 7 --question "dates"           # common points, differences
./iak.sh analyses 3                               # stored analyses
./iak.sh export 12 -o resume.md                   # analysis 12 as Markdown
./iak.sh ask "…" --md reponse.md                  # an answer and its sources as Markdown
./iak.sh translate 3 --to en --out doc3.en.md     # document, page by page (kept)
./iak.sh translate --unit 42 --to la              # one page
./iak.sh translate --text "Bonjour" --from fr --to de   # or --file text.txt
./iak.sh book candide.epub --to en [--passes 1|2|3] [--glossary g.txt] [--limit 3] [-o out.epub]
./iak.sh book --doc 3 --to en                     # a library document; resumable
./iak.sh book scan.pdf --to fr --format epub       # pdf (default for a PDF), epub, html, docx, rtf, md, txt
./iak.sh delete 3                                 # database backed up first
./iak.sh serve                                    # local API (below)
```

Common options: `--library` (else `IAK_LIBRARY`, else `data/`),
`--ocr-langs` (`IAK_OCR_LANGS`, default `fra+eng`), `--tesseract`
(`IAK_TESSERACT`), `--lang fr|en` (`IAK_LANG`) for summaries and analyses,
`--host` (a section of `config/servers.toml`), `--server`, `--embed-server`,
`--vision-server` and `--translate-server` to give URLs directly.

## The library folder

`data/` (or the folder chosen in *Settings*) holds `iak.sqlite`, the copies
of imported files under `originals/` (named by their content hash), database
backups under `backups/` and the engine's log `iak-backend.log`. It may be
on the key or on a disk. SQLite uses the rollback journal (not WAL), which
suits exFAT keys that may be unplugged.

## How documents are handled

- **Units and citations.** A document is cut into units, the parts an answer
  can cite: pages for PDF and images (`p. 12`, using the PDF's own page
  labels), sections for DOCX, Markdown, HTML and EPUB (the heading, or `§ 3`,
  `chap. 3`), blocks of lines for TXT (`l. 120`).
- **Text and OCR.** PDF pages with fewer than 40 characters of text are
  rendered at 300 dpi and read by Tesseract; images always are, small
  screenshots enlarged twice first. Word boxes and confidence are kept, for
  highlighting passages and spotting doubtful pages. Tesseract is looked for
  in `build/tesseract/`, `IAK_TESSERACT`, the `PATH`, then
  `C:\Program Files\Tesseract-OCR`.
- **Corrections.** A corrected unit is reindexed alone; the extracted text is
  kept beside it, and extracting again keeps the correction.
- **Duplicates.** A file whose content is already in the library is not
  imported again; two different files with the same name are both kept. A
  failed import can be retried.
- **Passages.** Each unit is cut into passages of about 1,200 characters
  overlapping by 200, on paragraph or sentence boundaries, indexed with SQLite
  FTS5 (accents ignored) and, with an embedding server, as vectors.

## Search

- **Hybrid by default.** Keyword (BM25) and meaning results are fused by
  reciprocal rank; a passage found both ways comes first. Keywords catch
  names, numbers and exact terms; meaning catches reformulations and old
  spellings. Without an embedding server, search falls back to keywords.
- **EmbeddingGemma prefixes.** Passages are embedded as
  `title: <document> | text: …`, queries as `task: search result | query: …`.
- **Cut-off.** Meaning search always has neighbours: hits under a cosine of
  0.10 or under half the best score are dropped.
- **Filters.** A collection, a type or a document limit both searches. Meaning
  search then scores only the passages the filters keep, so a small
  collection in a big library is searched as well as the whole library.
- **Collections.** `collections` and `collection_docs` (schema 6): names
  unique whatever their case; a document can be in several; deleting a
  collection leaves its documents, deleting a document takes it out of its
  collections.
- **One model per index.** The index records its embedding model (file name,
  dimension, vector of a probe sentence); another model is refused until
  `embed --rebuild`, another quantisation of the same model is accepted.
- **Size and speed.** About 3 KB per passage (100,000 passages ≈ 70,000
  pages ≈ 400 MB); 0.7 s to load 100,000 vectors once, then 14 ms a query.

## Answers

- The best passages (8 at most, 10,000 characters) go to the chat server
  inside `<passage>` tags, with an instruction to treat them as data, never
  as instructions. Only citations of passages actually sent are kept; an
  answer citing nothing is flagged.
- No passage found, or a model reply of `NO_ANSWER`, gives "the documents do
  not allow an answer". A failure of the model server (an error during
  generation, an empty reply) is reported as an error, with a hint when it is
  a memory shortage.
- Follow-up questions: the last three exchanges (shortened, citations
  removed) are sent before the new passages, and the previous question is
  used for a second search fused with the first.
- Thinking is turned off; the server's prompt cache is used.

## Analyses

- `look` sends a PDF page or an image rendered at up to 1,600 pixels to the
  vision server (or to the chat server when it has vision), with the page's
  OCR text as a reading aid (except for `transcribe`).
- `summarize` works by parts of about 9,000 characters and merges them; page
  labels are kept. A summary is stored per language and reused.
- `compare` works from the documents' summaries; documents are named D1, D2…
- Results are stored with their task, language and model, and deleted with
  their document.

## Exports

`iak/export.py` writes Markdown in the interface language. An answer keeps
its `[S1]` marks and gets a *Sources* list of the passages it cites
(document, label, the passage cut to 300 characters as a quotation; an
answer citing nothing is marked as such); a conversation is a
title, the date and one section per question. An analysis gets its title
(task, document, page), date and model, its text, and its sources: the
document and the pages a summary cites, the documents D1, D2… of a
comparison, the agreement of a page reading. Names and passages are escaped
so that they cannot become Markdown markup; files are written through a
temporary file.

## Folders

`iak/folders.py`, tables `folders` and `folder_files` (schema 7).

- **Following a folder.** Its documents (subfolders included, unless
  `recursive` is off) are imported, referenced by default or copied, into a
  collection named after the folder (an existing collection of that name is
  used). Hidden files, Office temporary files (`~$…`), partial downloads,
  AppleDouble files (`._…`) and system folders (`$RECYCLE.BIN`, `__MACOSX`…)
  are left out.
- **Scans.** Each file is known by its relative path, size and date: a scan
  imports only what is new or changed. A file changed in the last 5 seconds
  is left for the next scan (still being written). A changed file comes in as
  a new document; the old one is deleted (database backed up first) when it
  came from this file and has no correction by hand. A deleted file leaves
  its document; the folder forgets it.
- **Watching.** While the engine runs, a thread looks at the watched folders
  every `IAK_WATCH_SECONDS` (60; 0 turns it off): a cheap fingerprint (number
  of files, total size, latest date) decides whether a scan job is queued.
  Scans are jobs like imports; their vectors are computed after them.
- **Paths.** A folder on the drive or key that holds IA-K is stored relative
  to the IA-K folder, so the key's folders are found again under another
  drive letter or mount point; a referenced document whose recorded path is
  gone is looked for through its folder. A folder absent from this computer
  is skipped.

## Full text

`iak/fulltext.py`; formats from `iak/bookformats.py` (pictures included).

- **A PDF or an image of the library.** Each page gives text and pictures in
  reading order. A native page: its text blocks (with their font size) and
  the images it holds, cut out of the page at 150 dpi. A page read by OCR (or
  corrected, or read again by the vision model): the library's text as
  paragraphs, a size for each from its words' boxes, and the illustrations
  found on the rendered page; a picture covering most of a page is the scan
  itself and is searched the same way. Then, as for books: headings from the
  sizes, running heads and page numbers removed, paragraphs joined across
  pages.
- **Finding illustrations.** The page is rendered in grey at 100 dpi; the
  words' rectangles are blanked (except "words" of low confidence or great
  size: Tesseract reads hatching that way); a grid of cells marks those with
  enough ink or grey; neighbouring cells make regions; small, thin, sparse
  regions, a scan's dark border and the whole page are dropped. The rest is
  cut out with a margin, as JPEG.
- **An EPUB of pictures.** Every picture of the reading order (not the
  cover) is read by Tesseract; it is text with at least 12 words read with a
  mean confidence of 55, covering a tenth of it (or 40 words covering less:
  a page with a large picture). Its paragraphs (headings from the boxes'
  heights, running heads and page numbers removed) and the illustrations cut
  out of it replace the picture's wrapper (`<p>`, `<div>`, `<svg>`); with the
  vision model, the text is its reading. Pictures no longer used leave the
  book and its manifest; the new ones join it.
- **Results.** In `<library>/fulltext/<document>/`, deleted with the
  document.

## Reading pages again

`iak/transcription.py`, for PDF pages and images.

- **Methods.** `correct`: the vision model gets the page image (1,600 pixels
  on its longest side) and the page's text in `<ocr>` tags, and corrects it;
  `transcribe`: it reads the image alone. Both ask for the original spelling
  kept, line breaks and paragraphs, columns in order, tables in Markdown,
  `[?]` for unreadable words; up to 3,000 tokens per page (when correcting,
  about one and a half times the OCR text). Repetition is discouraged
  (presence penalty 1.5, as Qwen recommends for Qwen3-VL) and a line repeated
  at the end is dropped: small vision models can loop.
- **Readings.** Stored as analyses (task `correct` or `transcribe`) with
  their model, their agreement with the page's text at the time (share of
  words in common, whatever their order, 0-1: `score`) and whether the answer was cut by
  the token limit. *Apply* makes a reading the page's text (`applied`, the
  page reindexed, its vectors computed in the background); the extracted
  text stays in `text_extracted` and *restore* puts it back. The units list
  says which pages have a model's text (`reader`); a page corrected by hand
  afterwards no longer counts as one.
- **A document.** Pages: `low` (OCR pages below the confidence threshold, 80
  by default, or nearly empty), `ocr`, `all`. Pages already read by the same
  model with the same method are skipped (unless `refresh`), so a cancelled
  job goes on where it stopped; with `replace`, pages corrected by hand are
  kept.
- **Server.** The vision server when it answers, else the chat server when
  it has a vision projector.

## Translation

- **Which model.** The translation server when it answers, otherwise the
  chat server. TranslateGemma is recognised by its chat template (it wants
  language codes) and gets, through raw completion (`/completion`), the exact
  prompt it was trained on inside a Gemma turn, as in
  [truchement](https://github.com/fhoudebert/transcribe); any other model
  gets a plain instruction through the chat API.
- **Pieces.** A text is cut on paragraphs, then sentences, into pieces of
  about 3,000 characters (less with a small context); the paragraph
  structure is kept. A translation much shorter than its piece is asked
  again once, at a higher temperature.
- **Languages.** Codes such as `fr`, `en`, `la`, `oc`; the interface lists
  18 of them, TranslateGemma knows many more. `auto` detects the usual
  European languages from frequent words; a document also uses its
  metadata.
- **Documents.** Translated unit by unit (page or section) into the
  `translations` table, one per unit and target language, with a hash of the
  source text: a unit corrected since is marked stale and translated again
  by the next pass; the others are reused. Deleted with their document.
  Export writes Markdown (`# title`, `## p. N`) or text, optionally with the
  original before each translation.

## Books

`iak/book.py`, ported from truchement's `traduire-livre.py`.

- **Reading.** EPUB (every XHTML document in reading order, and the NCX),
  HTML, DOCX (paragraphs and table cells), Markdown (headings, lists and
  quotes keep their marks, code blocks are left alone), TXT, PDF. A PDF is
  read with its font sizes (headings), without its running heads and page
  numbers, with paragraphs joined across pages and hyphenation removed; for a
  library PDF, pages read by OCR or corrected are taken from the library. An
  image or a document whose original is gone is translated from the
  library's text; so is an EPUB, DOCX or HTML whose text was corrected in
  IA-K.
- **Formats.** EPUB, HTML and DOCX books are written back in their format,
  layout kept. Books read as a flat text (PDF, library text, TXT, Markdown)
  are written by `iak/bookformats.py` as PDF (the default for a PDF: A5
  pages laid out by MuPDF from HTML, fonts embedded for Latin, Greek,
  Cyrillic, Hebrew and CJK scripts, page numbers; Arabic is not shaped well),
  EPUB 3 (chapters at the headings, navigation and NCX), HTML, DOCX, RTF,
  Markdown or text. `render` writes a translated book again in another
  format from its cache, without the model.
- **Segments and units.** Each paragraph, heading, list item or cell is a
  segment; consecutive segments are sent together, as
  `<seg n="1">…</seg>` (up to 2,500 characters, less with a small context),
  so the model keeps the context. Inside a paragraph, formatting becomes
  neutral tags `<g1>…</g1>`, `<x2/>` that the model must keep; the original
  tags are put back around the translation. A group whose tags come back
  damaged is translated again paragraph by paragraph, a paragraph as plain
  text as a last resort; after a few failures in a row IA-K stops sending
  tags to that model. Everything is counted in the log.
- **Passes.** 1 translation (TranslateGemma's own prompt, or an instruction
  for another model); 2 a bilingual revision; 3 a final proofreading. A
  revision that loses tags or changes the length too much is rejected. A
  glossary is put in the instruction when a term occurs (TranslateGemma: in
  the revisions only, its own prompt cannot carry it).
- **Resuming.** Results are appended to `cache.jsonl` (synced every 20 s),
  per unit and pass and per segment: resuming does not depend on the cutting,
  the context or the model. A pause is taken between two units and writes the
  partial book (translated beginning, original rest).
- **Jobs.** Books have their own thread, one at a time, so imports and
  questions are not held up. Each lives in `<library>/books/<id>/`
  (`job.json`, `cache.jsonl`, `book.log`, the translated book). A book left
  running when IA-K closed shows as *interrupted* and is resumed by hand.

## Local API

The interface talks to the engine through an HTTP API:

```
./iak.sh serve            # first line: IAK_API {"url": "http://127.0.0.1:PORT", "token": "..."}
```

- **Access.** 127.0.0.1 only, a free port (`--port` to fix one). Every
  request needs `Authorization: Bearer <token>`; the token is random at each
  start (`--token` or `IAK_TOKEN` to fix it). Requests whose `Host` is not
  127.0.0.1 or localhost are refused (DNS rebinding); only the Tauri origins
  get CORS answers (`--allow-origin` adds one).
- **Lifetime.** With `--watch-stdin` (used by the application) the engine
  stops when its standard input closes, that is when the application ends,
  even abruptly.
- **Jobs.** Import, embedding, re-extraction, page analysis and readings,
  summaries, comparisons and translations run one at a time: the call returns
  `{"job": id}` (202) and `GET /api/jobs/<id>` gives status, progress and
  result. A queued job can be cancelled; a reading of several pages stops
  between two pages.
- **Errors.** `{"error": "..."}` with 400, 401 (token), 403 (host or origin),
  404, 409 (not configured, wrong embedding model, impossible task), 502
  (model server).

| Method and path | Purpose |
|---|---|
| `GET /api/health`, `/api/status`, `/api/servers` | version; counts; model servers and their state |
| `GET /api/documents[?kind=pdf]`, `GET /api/documents/<id>` | library, one document |
| `GET /api/documents/<id>/units[?boxes=1]` | pages or sections with their text |
| `GET /api/documents/<id>/original` | the original file |
| `DELETE /api/documents/<id>` | remove (database backed up first) |
| `POST /api/import` `{"paths": [...], "reference": false}` | job; a folder among the paths is followed (not watched) |
| `POST /api/documents/<id>/reindex` `{"extract": true}` | job |
| `GET /api/units/<id>`, `GET /api/units/<id>/image?max=1600` | text and word boxes; page as PNG |
| `PUT /api/units/<id>/text` `{"text": "..."}` | OCR correction, reindexed |
| `GET /api/passages/<id>/boxes` | rectangles of a passage's lines on its page |
| `GET /api/search?q=&mode=auto\|hybrid\|sem\|kw&n=&doc=&kind=&collection=` | passages with citations |
| `POST /api/ask` `{"question", "doc_ids", "kinds", "collections", "history"}` | sourced answer |
| `GET /api/collections`, `POST /api/collections` `{"name", "doc_ids"}` | collections with their counts; create one |
| `GET`, `POST /api/documents/<id>/fulltext` `{"format", "illustrations", "method": "ocr"\|"vision"}` | full-text versions of a document; make one (job) |
| `POST /api/documents/<id>/fulltext/save` `{"name", "path"}`, `/import` `{"name"}`, `DELETE …/fulltext?name=` | save a copy, add it to the library (job), delete it |
| `GET /api/folders`, `POST /api/folders` `{"path", "copy", "recursive", "watch", "name"}` | followed folders; follow one (its first scan is a job) |
| `PUT /api/folders/<id>` `{"watch", "copy", "recursive"}`, `POST /api/folders/<id>/scan`, `DELETE /api/folders/<id>` | settings; scan now (job); stop following |
| `GET`, `PUT` `{"name"}`, `DELETE /api/collections/<id>` | one collection: its documents, rename, delete |
| `POST /api/collections/<id>/documents` `{"add": [ids], "remove": [ids]}` | put documents in it or take them out |
| `POST /api/ask/stream` (same body) | server-sent events: `sources`, `token`…, `done` or `error` |
| `POST /api/look` `{"unit_id": 42, "task": "error"}` or `"question"` | job |
| `POST /api/summarize` `{"doc_id": 3}`, `POST /api/compare` `{"doc_ids": [3, 7]}` | jobs |
| `GET /api/analyses[?doc=3]`, `GET /api/jobs`, `GET /api/jobs/<id>` | stored results, jobs |
| `POST /api/read` `{"unit_id"` or `"doc_id", "pages", "threshold", "method", "replace", "refresh"}` | job: page readings by the vision model |
| `GET /api/analyses/<id>/diff`, `POST /api/analyses/<id>/apply` | word differences with the page's text; use a reading as the text |
| `POST /api/units/<id>/restore` | back to the extracted text |
| `POST /api/jobs/<id>/cancel` | cancel a queued job, or stop a reading between two pages |
| `POST /api/export/conversation` `{"turns": [{"question", "text", "status", "sources", "scope"}], "lang", "single", "path"}` | Markdown of an answer or a conversation (returned, or written to `path`) |
| `GET /api/analyses/<id>/markdown?lang=fr`, `POST /api/analyses/<id>/export` `{"path", "lang"}` | an analysis as Markdown |
| `GET /api/languages` | languages offered for translation |
| `POST /api/translate` `{"text", "src": "auto", "tgt": "en"}` | job: translated text, detected language |
| `POST /api/translate/document` `{"doc_id"` or `"unit_id", "src", "tgt", "refresh"}` | job |
| `GET /api/documents/<id>/translations?tgt=en` | each unit with its translation (or null) and whether it is stale |
| `POST /api/documents/<id>/translations/export` `{"tgt", "path", "format": "md"\|"txt", "bilingual"}` | write the file |
| `GET /api/books`, `GET /api/books/<id>?log=40` | book translations, one with the end of its log |
| `POST /api/books` `{"doc_id"` or `"path", "src", "tgt", "passes", "glossary", "limit"}` | start (or take up) a book |
| `POST /api/books/<id>/pause`, `/resume` `{"fresh": false}`, `DELETE /api/books/<id>` | pause between two units, resume, delete |
| `POST /api/books/<id>/save` `{"path", "partial": false}`, `POST /api/books/<id>/import` | copy the translated (or partial) book; import it (job) |
| `POST /api/embed` `{"rebuild": false}` | job |
| `GET /api/settings`, `PUT /api/settings` `{"servers", "ocr_langs", "lang"}` | servers of this computer, OCR languages |
