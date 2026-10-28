# IA-K features

What IA-K does and how to use it. In French: [FONCTIONNALITES.md](FONCTIONNALITES.md).
Installing: [README.md](README.md).

## Library

- **Importing.** *Import files…* or drop files on the window: PDF, scans,
  images, screenshots, EPUB, DOCX, HTML, TXT, Markdown. They are copied into
  the library (or only referenced, if you tick the box), read, cut into
  passages and indexed; scanned pages go through OCR (Tesseract, on the key
  with French, English, Middle French and Latin). The list shows each
  document's state, pages, OCR pages and passages; the *Jobs* panel shows
  imports and analyses in progress. From the list you can open, summarise or
  delete a document, and compare the documents you tick.
- **Folders.** *Import a folder…* (or drop a folder on the window) brings in
  all the documents of a folder and its subfolders, in a collection named
  after it; by default the files are only referenced and stay where they
  are. A *watched* folder is looked at every minute while IA-K runs: files
  added or changed there are imported by themselves (a changed file
  replaces its document, unless you corrected it); a deleted file leaves
  its document in the library. A folder on the key is found again on
  another computer, whatever its drive letter. *Look now*, *Stop watching*
  and *Stop following* are in the list of folders.
- **Collections.** Tick documents, then *+ New collection* (or *Add to the
  collection*). A document can be in several collections. The chips above
  the list show one collection at a time, where documents can be removed or
  moved (*Move to the collection*), and the collection renamed or deleted
  (its documents stay). In the viewer, the *Collections* chips put the
  document in or out of each one. A collection limits *Search* and
  *Questions*.
- **Viewer.** The page on the left, its text on the right. OCR text can be
  corrected and saved; the correction is kept even if the document is read
  again. Arriving from a search or a citation, the passage is highlighted in
  yellow on the page.
- **Settings.** The model servers of this computer, an API key if they need
  one, the OCR languages (`fra+eng`, `fra+frm` for Middle French…), the
  library folder (it can be moved to a disk or a bigger key).

## Search and questions

- **Search.** Type words or a question. *Automatic* combines words and
  meaning: "who repaired the damaged map?" finds "the binder mended the
  atlas spoilt by damp" without any common word. Results can be limited to a
  collection, a type of document or one document; click one to open its
  page.
- **Questions.** Ask in plain language. The answer is written as you watch,
  with citations `[S1]` you can click; *Sources* lists them. Follow-up
  questions ("and when did he return it?") take the last exchanges into
  account until *New conversation*. When the documents do not contain the
  answer, IA-K says so instead of inventing one.
- **Exports.** *Copy as Markdown* on every answer, and *Export the
  conversation…* for all of it: each question, its answer with its `[S1]`
  marks, and under it the sources (document, page, the passage quoted).

## Analyses

- *Analyse the page* asks the model to describe it, transcribe it, explain
  an error on a screenshot, extract names, dates and amounts, or answer a
  question of yours.
- **Summaries** of a document citing its pages, **comparisons** of several
  documents (common points, differences, contradictions).
- Summaries, comparisons and page analyses have *Copy* and *Export…* as
  Markdown, which opens in any editor, Obsidian, Typora, or converts to Word
  or PDF (Pandoc).

## Full text

*Full text…* in the viewer.

- **A scanned PDF** (or an image, or a PDF with an OCR layer) becomes a
  light document of real text an e-reader can reflow: the library's text
  (OCR, your corrections, the vision model's readings) replaces the pictures
  of the pages; headings are found from the size of the letters; running
  heads and page numbers go; paragraphs are rebuilt from the page as a
  reader sees them and joined across pages; the notes at the foot of the
  pages are linked to their calls (*link the notes*); the illustrations
  (photographs, engravings, maps) are cut out and kept in place. As EPUB, PDF
  on A5 pages, HTML, Word, RTF or Markdown.
- **A scanned PDF read again**: instead of *the library's text*, Tesseract
  reads its scanned pages again; when the PDF carries another OCR's text
  layer (ABBYY, Acrobat), each doubtful word is checked against it, and the
  words the two OCRs read differently go to the vision model (*targeted
  vision*) or the text model; pages corrected by hand keep their text.
- **An EPUB made of pictures of scanned pages**: each picture of text is
  read and replaced by its text; illustrations and the cover stay.
  *Tesseract, then targeted vision on the doubtful words* is the best
  compromise (`serveurs-vl`); *Tesseract, then the text model* uses the chat
  server (`serveurs-baseConnaissance`); Tesseract alone needs no server; the
  vision model can also read whole pages. The elisions OCR misses are put
  right (`quon`, `Lanxiété` → `qu'on`, `L'anxiété`).
- **Finishing the whole book** (from Prescel's scripts):
  - *French typography*: no-break spaces before `; : ! ?` and inside
    `« »`, `…`, dialogue dashes (books in French);
  - *join the cut words, split the glued ones*: `mar eschal` → `mareschal`
    when the book writes the whole word elsewhere;
  - *old print*: the long s read `f` (`eft`, `auffi` → `est`, `aussi`),
    never where the f form is a word too (`fait`, `font`);
  - *modern spelling* for French books of the 17th and 18th centuries
    (`estoit`, `luy`, `bontez`, `charmans` → `était`, `lui`, `bontés`,
    `charmants`): sure forms change, those that are modern words too
    (`teste`) are only listed.
- Every correction, made or refused, is listed in a `.corrections.tsv` file
  next to the book. The result can be saved, or added to the library.
- **Project Gutenberg**: the zip Project Gutenberg asks for (text in 72
  columns and one HTML page, notes at the end of each chapter, images), for
  a book in the public domain, proofread.

## Reading difficult scans again

- **Reading a page again.** When Tesseract reads a scan badly (old print,
  columns, tables, handwriting), in *Analyse the page*, *Correct the OCR
  text from the image* gives the vision model the page and Tesseract's text
  to correct; *Transcribe* has it read the image alone. The reading comes
  with its agreement with the page's text and its *Differences* word by
  word; nothing is replaced before *Use as the page's text*, and *Restore the
  extracted text* goes back.
- **Doubtful words.** Under the text of an OCR page, the words Tesseract was
  unsure of, that the library does not know (its own vocabulary, old French
  included, and word lists in `config/words/`), or whose letters look like
  an OCR slip (`at1as`). A click frames the word on the page; pages with
  many have an orange dot in the list. *Fix the doubtful words (text model)*
  gives each one, in its sentence, to the chat model; *Read the doubtful
  words again on the image (targeted vision)* has the vision model read only
  the lines that hold them. Each proposed word is checked, and the words
  corrected and the proposals refused are listed.
- **A whole document.** *Read pages again…*: the poor OCR pages (confidence
  below 80 %), the pages with doubtful words, every OCR page or every page,
  proposing readings or replacing the text directly, never on pages you
  corrected by hand. A cancelled run goes on where it stopped; pages whose
  text comes from the model are marked ·VL. Best with Qwen3-VL-8B
  (`serveurs-vl`).

## Translation

- **Text.** Paste a text, choose the languages (or let IA-K detect the
  source), *Translate*; 18 languages, Latin, Occitan and Breton among them.
- **Library document.** Translated page by page (or section by section),
  the translations kept: a long document can be translated in several
  sittings, a page re-translated on its own, and pages corrected since are
  marked. *Export…* as Markdown or text, with the original if you like. In
  the viewer, *Translate the page*.
- **Book.** A whole book with its layout: EPUB → EPUB (italics, notes,
  links, table of contents), DOCX → DOCX, HTML → HTML. A PDF (headings,
  paragraphs joined, running heads removed) comes back on A5 pages, or as
  EPUB, HTML, Word, RTF, Markdown or text; so do scans, text and Markdown
  files. A finished translation can be saved in another format without
  translating again.
  - 1 to 3 passes: translation, revision, final proofreading;
  - an optional glossary (`term = translation` per line);
  - a *Trial* on the first three blocks;
  - *Pause* and *Resume*, even after shutting the computer down; the log
    shows the time left (a long book: hours, a night with two passes);
  - *Save as…*, or *Add to the library* to search the translation.
- TranslateGemma when its server runs, otherwise the chat model.

## Good to know

- **Speed.** On the reference laptop with Gemma 4 26B-A4B, an answer starts
  after 10 to 20 seconds and is written at 15 to 19 words per second; an
  image analysis takes 15 to 30 seconds; OCR, about a second per page.
- **One big model at a time** on a 32 GB laptop: the 26B (answers) and
  TranslateGemma 12B (translation) do not fit in graphics memory together;
  switch with the `serveurs-*` files.
- **Remote servers.** If the chat server is not on this computer, a banner
  says that passages of your documents leave it.
- **Backups.** The database is backed up in `data/backups/` before each
  deletion or upgrade. Your original files are never modified.
- **Command line and API.** Everything can be done without the interface
  (`iak.bat`, `./iak.sh`): see [BACKEND.md](BACKEND.md).
