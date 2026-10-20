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
./iak.sh search "..." --mode kw|sem|hybrid --doc 3 --kind pdf
./iak.sh ask "Où l'atlas a-t-il été envoyé ?"     # sourced answer
./iak.sh edit 42 corrected.txt                    # replace the text of unit 42 (OCR correction)
./iak.sh reindex 3 --extract                      # extract and OCR again
./iak.sh embed [--rebuild]                        # vectors for search by meaning
./iak.sh look 42 --task describe|transcribe|error|extract   # page analysis
./iak.sh look 42 --ask "Quel est le montant total ?"
./iak.sh summarize 3 [--refresh]                  # summary with [p. N] labels
./iak.sh compare 3 7 --question "dates"           # common points, differences
./iak.sh analyses 3                               # stored analyses
./iak.sh translate 3 --to en --out doc3.en.md     # document, page by page (kept)
./iak.sh translate --unit 42 --to la              # one page
./iak.sh translate --text "Bonjour" --from fr --to de   # or --file text.txt
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
- **Jobs.** Import, embedding, re-extraction, page analysis, summaries,
  comparisons and translations run one at a time: the call returns `{"job": id}` (202) and
  `GET /api/jobs/<id>` gives status, progress and result.
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
| `POST /api/import` `{"paths": [...], "reference": false}` | job |
| `POST /api/documents/<id>/reindex` `{"extract": true}` | job |
| `GET /api/units/<id>`, `GET /api/units/<id>/image?max=1600` | text and word boxes; page as PNG |
| `PUT /api/units/<id>/text` `{"text": "..."}` | OCR correction, reindexed |
| `GET /api/passages/<id>/boxes` | rectangles of a passage's lines on its page |
| `GET /api/search?q=&mode=auto\|hybrid\|sem\|kw&n=&doc=&kind=` | passages with citations |
| `POST /api/ask` `{"question", "doc_ids", "kinds", "history"}` | sourced answer |
| `POST /api/ask/stream` (same body) | server-sent events: `sources`, `token`…, `done` or `error` |
| `POST /api/look` `{"unit_id": 42, "task": "error"}` or `"question"` | job |
| `POST /api/summarize` `{"doc_id": 3}`, `POST /api/compare` `{"doc_ids": [3, 7]}` | jobs |
| `GET /api/analyses[?doc=3]`, `GET /api/jobs`, `GET /api/jobs/<id>` | stored results, jobs |
| `GET /api/languages` | languages offered for translation |
| `POST /api/translate` `{"text", "src": "auto", "tgt": "en"}` | job: translated text, detected language |
| `POST /api/translate/document` `{"doc_id"` or `"unit_id", "src", "tgt", "refresh"}` | job |
| `GET /api/documents/<id>/translations?tgt=en` | each unit with its translation (or null) and whether it is stale |
| `POST /api/documents/<id>/translations/export` `{"tgt", "path", "format": "md"\|"txt", "bilingual"}` | write the file |
| `POST /api/embed` `{"rebuild": false}` | job |
| `GET /api/settings`, `PUT /api/settings` `{"servers", "ocr_langs", "lang"}` | servers of this computer, OCR languages |
