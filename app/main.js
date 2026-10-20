// IA-K interface. Talks to the Python backend's local API (see BACKEND.md,
// "Local API"); inside Tauri the URL and token come from the Rust side.
// Text from documents is always inserted as text, never as HTML.
import { t, getLang, setLang, translatePage } from "./i18n.js";

const TAURI = window.__TAURI__;
const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...children) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "dataset") Object.assign(e.dataset, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
};

let backend = null;            // {url, token}
const state = {
  view: "library",
  documents: [],
  selected: new Set(),         // documents chosen for a comparison
  doc: null, units: [], unit: null,
  jobs: [],
  waiters: new Map(),          // job id -> resolve
  history: [],                 // [{question, answer}] of the current conversation
  seenJobs: new Set(),         // finished jobs already taken into account
  started: false,              // first jobs poll done
  imageUrl: null,
};

// ------------------------------------------------------------------ API

async function request(method, path, body) {
  const headers = { Authorization: `Bearer ${backend.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(backend.url + path, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = (r.headers.get("Content-Type") || "").includes("json") ? await r.json() : null;
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}
const get = (p) => request("GET", p);
const post = (p, b = {}) => request("POST", p, b);

async function blobUrl(path) {
  const r = await fetch(backend.url + path, { headers: { Authorization: `Bearer ${backend.token}` } });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).error || msg; } catch (_) { /* not JSON */ }
    throw new Error(msg);
  }
  return URL.createObjectURL(await r.blob());
}

function toast(message, error = false) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", error);
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, error ? 8000 : 4000);
}
const fail = (e) => toast(t("error.generic", { msg: e.message || e }), true);

// ------------------------------------------------------------------ text rendering

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function inline(s) {
  // escape first: the model's text cannot add markup of its own
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[(S\d+(?:\s*[,;]\s*S\d+)*)\]/g, (_, refs) =>
      refs.split(/\s*[,;]\s*/).map((r) => `<button class="cite" data-ref="${r}">${r}</button>`).join(""));
}

/** Small Markdown subset (paragraphs, headings, lists, bold, code) to DOM. */
function markdown(text) {
  const out = [];
  let list = null;
  let para = [];
  const flush = () => {
    if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; }
  };
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of (text || "").split("\n")) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { flush(); close(); continue; }
    if ((m = line.match(/^\s*#{1,4}\s+(.*)$/))) {
      flush(); close(); out.push(`<h4>${inline(m[1])}</h4>`);
    } else if ((m = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/))) {
      flush();
      const kind = /^\s*\d/.test(line) ? "ol" : "ul";
      if (list !== kind) { close(); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else {
      close(); para.push(line.trim());
    }
  }
  flush(); close();
  const div = el("div", { class: "prose" });
  div.innerHTML = out.join("");
  return div;
}

/** Snippet with the matched words (between [ and ] from the backend) marked. */
function snippet(text) {
  const span = el("span");
  (text || "").split(/(\[[^\]]*\])/).forEach((part) => {
    if (/^\[[^\]]*\]$/.test(part)) span.append(el("mark", {}, part.slice(1, -1)));
    else span.append(part);
  });
  return span;
}

const kindLabel = (k) => ({ pdf: "PDF", image: "Image", docx: "DOCX", txt: "TXT",
  md: "Markdown", html: "HTML", epub: "EPUB" }[k] || k);

// ------------------------------------------------------------------ views

function show(view) {
  state.view = view;
  for (const name of ["library", "search", "ask", "viewer", "settings"]) {
    $(`#view-${name}`).hidden = name !== view;
  }
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  if (view === "search") $("#search-input").focus();
  if (view === "ask") $("#ask-input").focus();
  if (view === "settings") loadSettings().catch(fail);
}

// ------------------------------------------------------------------ jobs

function waitJob(id) {
  return new Promise((resolve) => { state.waiters.set(id, resolve); pollJobs(); });
}

async function pollJobs() {
  clearTimeout(pollJobs.timer);
  try {
    state.jobs = await get("/api/jobs");
  } catch (_) {
    pollJobs.timer = setTimeout(pollJobs, 5000);
    return;
  }
  let libraryChanged = false;
  for (const job of state.jobs) {
    const finished = job.status === "done" || job.status === "error";
    if (finished && state.waiters.has(job.id)) {
      state.waiters.get(job.id)(job);
      state.waiters.delete(job.id);
    }
    // the library changes with these jobs, whoever started them
    if (finished && !state.seenJobs.has(job.id)) {
      state.seenJobs.add(job.id);
      if (state.started && ["import", "reindex", "embed"].includes(job.kind)) libraryChanged = true;
    }
  }
  state.started = true;
  if (libraryChanged) loadLibrary().catch(() => {});
  renderJobs();
  const active = state.jobs.some((j) => j.status === "queued" || j.status === "running");
  pollJobs.timer = setTimeout(pollJobs, active || state.waiters.size ? 1000 : 6000);
}

function renderJobs() {
  const ul = $("#jobs-list");
  ul.replaceChildren();
  const recent = state.jobs.slice(0, 8);
  if (!recent.length) { ul.append(el("li", { class: "muted" }, t("jobs.none"))); return; }
  for (const j of recent) {
    const p = j.progress || {};
    const li = el("li", {},
      el("strong", {}, t(`jobs.kind.${j.kind}`)),
      el("span", { class: `status-${j.status}` }, t(`jobs.status.${j.status}`)));
    if (j.status === "running" && p.total) {
      li.append(el("progress", { max: p.total, value: p.done || 0 }), `${p.done || 0}/${p.total}`);
    }
    if (p.file && j.status === "running") li.append(el("span", { class: "muted" }, p.file.split(/[\\/]/).pop()));
    if (j.error) li.append(el("span", { class: "status-error" }, j.error));
    if (j.status === "done" && j.kind === "import" && j.result) {
      const c = { imported: 0, duplicate: 0, error: 0 };
      j.result.files.forEach((f) => { c[f.status] = (c[f.status] || 0) + 1; });
      li.append(el("span", { class: "muted" }, t("import.result", c)));
    }
    ul.append(li);
  }
}

// ------------------------------------------------------------------ library

async function loadLibrary() {
  state.documents = await get("/api/documents");
  renderLibrary();
  renderFilters();
}

/** Type and document lists of the search and question filters. */
function renderFilters() {
  const kinds = [...new Set(state.documents.map((d) => d.kind))].sort();
  for (const id of ["#search-kind", "#ask-kind"]) {
    const sel = $(id);
    const keep = sel.value;
    sel.replaceChildren(el("option", { value: "" }, t("filter.all_kinds")),
      ...kinds.map((k) => el("option", { value: k }, kindLabel(k))));
    sel.value = kinds.includes(keep) ? keep : "";
  }
  for (const id of ["#search-doc", "#ask-doc"]) {
    const sel = $(id);
    const keep = sel.value;
    const docs = [...state.documents].sort((a, b) => a.name.localeCompare(b.name, getLang()));
    sel.replaceChildren(el("option", { value: "" }, t("filter.all_docs")),
      ...docs.map((d) => el("option", { value: String(d.id) }, d.name)));
    sel.value = docs.some((d) => String(d.id) === keep) ? keep : "";
  }
}

function filterOf(prefix) {
  const kind = $(`#${prefix}-kind`).value;
  const doc = $(`#${prefix}-doc`).value;
  return { kinds: kind ? [kind] : null, doc_ids: doc ? [Number(doc)] : null,
           label: [kind && kindLabel(kind), doc && $(`#${prefix}-doc`).selectedOptions[0].text]
             .filter(Boolean).join(", ") };
}

function renderLibrary() {
  const docs = state.documents;
  $("#library-empty").hidden = docs.length > 0;
  $("#library-table").hidden = docs.length === 0;
  const tbody = $("#library-rows");
  tbody.replaceChildren();
  for (const d of [...docs].reverse()) {
    const added = new Date(d.added_at * 1000).toLocaleString(getLang(), {
      dateStyle: "short", timeStyle: "short" });
    const box = el("input", { type: "checkbox", title: t("lib.compare") });
    box.checked = state.selected.has(d.id);
    box.addEventListener("change", () => {
      if (box.checked) state.selected.add(d.id); else state.selected.delete(d.id);
      updateCompare();
    });
    const stateCell = el("td", { class: d.status === "error" ? "state-error" : "" },
      t(`lib.state.${d.status}`));
    if (d.error) stateCell.title = d.error;
    tbody.append(el("tr", {},
      el("td", {}, box),
      el("td", { class: "name", onclick: () => openDoc(d.id) }, d.name),
      el("td", {}, kindLabel(d.kind)),
      el("td", {}, added),
      stateCell,
      el("td", { class: "num" }, String(d.n_units)),
      el("td", { class: "num" }, d.n_ocr ? String(d.n_ocr) : ""),
      el("td", { class: "num" }, String(d.n_passages)),
      el("td", { class: "actions" },
        el("button", { class: "link", onclick: () => openDoc(d.id) }, t("lib.open")),
        el("button", { class: "link", onclick: () => summarize(d.id, d.name) }, t("lib.summary")),
        el("button", { class: "link danger", onclick: () => removeDoc(d) }, t("lib.delete")))));
  }
  updateCompare();
}

function updateCompare() {
  const n = [...state.selected].filter((id) => state.documents.some((d) => d.id === id)).length;
  $("#compare-btn").hidden = n < 2;
  $("#compare-question").hidden = n < 2;
}

async function chooseFiles() {
  if (!TAURI || !TAURI.dialog) { toast("File dialog only available in the application", true); return; }
  const picked = await TAURI.dialog.open({
    multiple: true,
    filters: [{ name: t("lib.filetypes"), extensions: ["pdf", "png", "jpg", "jpeg", "tif", "tiff",
      "bmp", "webp", "docx", "txt", "md", "markdown", "html", "htm", "xhtml", "epub"] }],
  });
  if (picked) importPaths(Array.isArray(picked) ? picked : [picked]);
}

async function importPaths(paths) {
  if (!paths.length) return;
  try {
    const { job } = await post("/api/import", { paths, reference: $("#import-reference").checked });
    const done = await waitJob(job);
    if (done.status === "error") fail(done.error);
    else {
      const c = { imported: 0, duplicate: 0, error: 0 };
      done.result.files.forEach((f) => { c[f.status] = (c[f.status] || 0) + 1; });
      toast(t("import.result", c), c.error > 0);
      if (done.result.warning) toast(done.result.warning, true);
    }
    await loadLibrary();
  } catch (e) { fail(e); }
}

async function removeDoc(d) {
  const message = t("lib.confirm_delete", { name: d.name });
  const ok = TAURI && TAURI.dialog ? await TAURI.dialog.ask(message, { title: "IA-K", kind: "warning" })
                                    : window.confirm(message);
  if (!ok) return;
  try {
    await request("DELETE", `/api/documents/${d.id}`);
    state.selected.delete(d.id);
    if (state.doc && state.doc.id === d.id) { state.doc = null; $("#tab-viewer").hidden = true; }
    await loadLibrary();
  } catch (e) { fail(e); }
}

function showResult(title, text) {
  $("#result-title").textContent = title;
  $("#result-body").replaceChildren(markdown(text));
  $("#result-panel").hidden = false;
}

async function summarize(docId, name, refresh = false) {
  try {
    const { job } = await post("/api/summarize", { doc_id: docId, refresh, lang: getLang() });
    toast(t("analysis.running"));
    const done = await waitJob(job);
    if (done.status === "error") { fail(done.error); return; }
    if (state.view === "viewer" && state.doc && state.doc.id === docId) await loadAnalyses();
    else showResult(`${t("lib.summary")} — ${name}`, done.result.text);
  } catch (e) { fail(e); }
}

async function compareSelected() {
  const ids = [...state.selected];
  try {
    const { job } = await post("/api/compare", {
      doc_ids: ids, question: $("#compare-question").value, lang: getLang() });
    toast(t("analysis.running"));
    const done = await waitJob(job);
    if (done.status === "error") { fail(done.error); return; }
    const names = ids.map((id, i) => `D${i + 1} ${(state.documents.find((d) => d.id === id) || {}).name || id}`);
    showResult(`${t("lib.compare")} — ${names.join(", ")}`, done.result.text);
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ viewer

async function openDoc(docId, unitId = null, passageId = null) {
  try {
    const [doc, units] = await Promise.all([get(`/api/documents/${docId}`),
                                            get(`/api/documents/${docId}/units`)]);
    state.doc = doc;
    state.units = units;
    $("#tab-viewer").hidden = false;
    $("#viewer-title").textContent = doc.name;
    const ul = $("#unit-list");
    ul.replaceChildren(...units.map((u) => el("li", {
      class: u.source === "ocr" ? "ocr" : "", dataset: { id: u.id }, title: u.label,
      onclick: () => selectUnit(u.id) }, u.label)));
    const hasPages = doc.kind === "pdf" || doc.kind === "image";
    $("#analyse-box").hidden = !hasPages;
    show("viewer");
    await selectUnit(unitId || (units[0] && units[0].id), passageId);
    await loadAnalyses();
  } catch (e) { fail(e); }
}

async function selectUnit(unitId, passageId = null) {
  const u = state.units.find((x) => x.id === unitId);
  if (!u) return;
  state.unit = u;
  document.querySelectorAll("#unit-list li").forEach((li) =>
    li.classList.toggle("active", Number(li.dataset.id) === unitId));
  const active = document.querySelector("#unit-list li.active");
  if (active) active.scrollIntoView({ block: "nearest" });
  $("#unit-text").value = u.text;
  const badges = [el("span", { class: "badge" }, t(`viewer.source.${u.source}`))];
  if (u.ocr_conf !== null && u.ocr_conf !== undefined) {
    badges.push(el("span", { class: u.ocr_conf < 70 ? "badge warn" : "badge" },
      t("viewer.conf", { conf: Math.round(u.ocr_conf) })));
  }
  if (u.edited) badges.push(el("span", { class: "badge" }, t("viewer.edited")));
  $("#unit-badges").replaceChildren(...badges);
  const img = $("#page-image");
  $("#page-marks").replaceChildren();
  if (state.imageUrl) { URL.revokeObjectURL(state.imageUrl); state.imageUrl = null; }
  const hasPages = state.doc.kind === "pdf" || state.doc.kind === "image";
  $("#page-no-image").hidden = hasPages;
  img.hidden = !hasPages;
  img.removeAttribute("src");
  if (hasPages && state.doc.original_available) {
    try {
      state.imageUrl = await blobUrl(`/api/units/${unitId}/image?max=1400`);
      if (state.unit && state.unit.id === unitId) img.src = state.imageUrl;
    } catch (e) { fail(e); }
    if (passageId) markPassage(passageId, unitId).catch(() => {});
  }
}

/** Yellow rectangles over the lines of a passage on the page image. */
async function markPassage(passageId, unitId) {
  const box = await get(`/api/passages/${passageId}/boxes`);
  if (!state.unit || state.unit.id !== unitId || box.unit_id !== unitId || !box.width) return;
  const marks = box.rects.map(([x0, y0, x1, y1]) => el("div", {
    style: `left:${(x0 / box.width) * 100}%;top:${(y0 / box.height) * 100}%;` +
           `width:${((x1 - x0) / box.width) * 100}%;height:${((y1 - y0) / box.height) * 100}%`,
  }));
  $("#page-marks").replaceChildren(...marks);
  if (marks.length) {
    // bring the first line into view once the image has its size
    const img = $("#page-image");
    const scroll = () => marks[0].scrollIntoView({ block: "center", behavior: "smooth" });
    if (img.complete) scroll(); else img.addEventListener("load", scroll, { once: true });
  }
}

async function saveUnit() {
  const u = state.unit;
  if (!u) return;
  try {
    await request("PUT", `/api/units/${u.id}/text`, { text: $("#unit-text").value });
    u.text = $("#unit-text").value;
    u.edited = 1;
    toast(t("viewer.saved"));
    await selectUnit(u.id);
  } catch (e) { fail(e); }
}

async function loadAnalyses() {
  if (!state.doc) return;
  const rows = await get(`/api/analyses?doc=${state.doc.id}`);
  const box = $("#analyses");
  box.replaceChildren();
  if (!rows.length) { box.append(el("p", { class: "muted" }, t("viewer.no_analyses"))); return; }
  for (const a of [...rows].reverse()) {
    const unit = state.units.find((u) => u.id === a.unit_id);
    const when = new Date(a.created_at * 1000).toLocaleString(getLang(), {
      dateStyle: "short", timeStyle: "short" });
    const what = a.task === "summary" ? t("lib.summary")
      : (t(`viewer.task.${a.task}`) + (a.question ? ` : ${a.question}` : ""));
    box.append(el("div", { class: "analysis" },
      el("div", { class: "meta" }, [what, unit ? unit.label : null, when].filter(Boolean).join(" · ")),
      markdown(a.text)));
  }
}

async function runLook() {
  const u = state.unit;
  if (!u) return;
  const task = $("#look-task").value;
  const question = task === "ask" ? $("#look-question").value.trim() : "";
  if (task === "ask" && !question) { $("#look-question").focus(); return; }
  const button = $("#look-run");
  button.disabled = true;
  const pending = el("p", { class: "thinking" }, t("analysis.running"));
  $("#analyses").prepend(pending);
  try {
    const { job } = await post("/api/look", { unit_id: u.id, task, question, lang: getLang() });
    const done = await waitJob(job);
    if (done.status === "error") fail(done.error);
    await loadAnalyses();
  } catch (e) { fail(e); }
  finally { button.disabled = false; pending.remove(); }
}

async function reextract() {
  if (!state.doc) return;
  try {
    const { job } = await post(`/api/documents/${state.doc.id}/reindex`, { extract: true });
    const done = await waitJob(job);
    if (done.status === "error") fail(done.error);
    await openDoc(state.doc.id, state.unit && state.unit.id);
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ search

async function runSearch(ev) {
  ev.preventDefault();
  const q = $("#search-input").value.trim();
  if (!q) return;
  const mode = $("#search-mode").value;
  const f = filterOf("search");
  let url = `/api/search?q=${encodeURIComponent(q)}&mode=${mode}&n=30`;
  if (f.kinds) url += `&kind=${encodeURIComponent(f.kinds[0])}`;
  if (f.doc_ids) url += `&doc=${f.doc_ids[0]}`;
  try {
    const res = await get(url);
    const modeName = t(`search.mode.${res.mode === "kw" ? "kw" : res.mode}`).toLowerCase();
    $("#search-count").textContent = res.hits.length
      ? t("search.count", { n: res.hits.length, mode: modeName }) : t("search.none");
    $("#search-results").replaceChildren(...res.hits.map((h) => el("li", {
      onclick: () => openDoc(h.doc_id, h.unit_id, h.passage_id) },
      el("div", { class: "cite-head" },
        el("span", { class: "doc" }, h.doc_name), el("span", {}, h.label),
        el("span", { class: "badge" }, t(`via.${h.via}`))),
      snippet(h.snippet))));
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ questions

async function runAsk(ev) {
  ev.preventDefault();
  const input = $("#ask-input");
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  const chat = $("#chat");
  $("#chat-empty").hidden = true;
  const f = filterOf("ask");
  chat.append(el("div", { class: "msg user" }, question,
    f.label ? el("span", { class: "scope" }, t("filter.scope", { what: f.label })) : null));
  const live = el("div", { class: "msg bot thinking" }, t("ask.thinking"));
  chat.append(live);
  live.scrollIntoView({ block: "end" });
  let text = "";
  let frame = 0;
  const paint = () => {
    frame = 0;
    live.className = "msg bot streaming";
    live.replaceChildren(markdown(text));
    live.scrollIntoView({ block: "end" });
  };
  try {
    const body = { question, history: state.history.slice(-3),
                   doc_ids: f.doc_ids, kinds: f.kinds };
    await streamAsk(body, {
      sources: (d) => { live.textContent = t("ask.found", { n: d.sent.length }); },
      token: (d) => { text += d.text; if (!frame) frame = requestAnimationFrame(paint); },
      done: (d) => {
        if (frame) cancelAnimationFrame(frame);
        live.replaceWith(answerMessage(d));
        state.history.push({ question, answer: d.status === "no_answer" ? "" : d.text });
      },
      error: (d) => { throw new Error(d.error); },
    });
  } catch (e) {
    if (live.isConnected) live.replaceWith(errorMessage(e.message));
  }
  chat.lastElementChild.scrollIntoView({ block: "end" });
}

/** An error in the conversation, explained when its cause is known. */
function errorMessage(msg) {
  const box = el("div", { class: "msg bot" });
  if (/OutOfDeviceMemory|out of memory|graphics memory|failed to allocate/i.test(msg)) {
    box.append(el("p", {}, t("error.memory")),
               el("div", { class: "meta" }, t("error.details", { msg: msg.split(" — ")[0] })));
  } else {
    box.append(t("error.generic", { msg }));
  }
  return box;
}

/** POST /api/ask/stream and dispatch its server-sent events. */
async function streamAsk(body, handlers) {
  const r = await fetch(`${backend.url}/api/ask/stream`, {
    method: "POST",
    headers: { Authorization: `Bearer ${backend.token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).error || msg; } catch (_) { /* not JSON */ }
    throw new Error(msg);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const event = (block.match(/^event: (.*)$/m) || [])[1];
      const data = (block.match(/^data: (.*)$/m) || [])[1];
      if (event && data && handlers[event]) {
        handlers[event](JSON.parse(data));
        if (event === "done") finished = true;
      }
    }
    if (done) break;
  }
  if (!finished) throw new Error("the answer was interrupted");
}

function answerMessage(res) {
  const msg = el("div", { class: "msg bot" });
  if (res.status === "no_answer") {
    msg.append(el("p", {}, t("ask.no_answer")));
  } else {
    if (res.status === "unsourced") msg.append(el("p", { class: "badge warn" }, t("ask.unsourced")));
    const body = markdown(res.text);
    const byRef = Object.fromEntries(res.sources.map((s) => [s.ref, s]));
    body.querySelectorAll("button.cite").forEach((b) => {
      const s = byRef[b.dataset.ref];
      if (s) {
        b.title = `${s.doc_name}, ${s.label}`;
        b.addEventListener("click", () => openDoc(s.doc_id, s.unit_id, s.passage_id));
      }
    });
    msg.append(body);
    if (res.sources.length) {
      msg.append(el("h4", {}, t("ask.sources")));
      msg.append(el("ul", { class: "sources" }, res.sources.map((s) => el("li", {
        onclick: () => openDoc(s.doc_id, s.unit_id, s.passage_id), title: s.text },
        el("button", { class: "cite" }, s.ref), " ",
        el("span", { class: "doc" }, s.doc_name), `, ${s.label}`))));
    }
  }
  const modeName = t(`search.mode.${res.search}`).toLowerCase();
  msg.append(el("div", { class: "meta" },
    t("ask.meta", { n: res.sent.length, mode: modeName, s: res.seconds })));
  return msg;
}

// ------------------------------------------------------------------ settings

async function loadSettings() {
  const cfg = await get("/api/settings");
  state.settings = cfg;
  $("#settings-computer").textContent = t("settings.computer", {
    name: cfg.computer, section: cfg.host.name, file: cfg.config || "—" });
  const f = $("#servers-form");
  f.chat.value = cfg.host.chat;
  f.embedding.value = cfg.host.embedding;
  f.vision.value = cfg.host.vision;
  f.api_key.value = "";
  f.api_key.placeholder = cfg.host.api_key_set ? t("settings.api_key_set") : "";
  $("#api-key-clear").hidden = !cfg.host.api_key_set;
  const o = $("#ocr-form");
  o.ocr_langs.value = cfg.ocr.langs;
  const avail = $("#ocr-available");
  if (cfg.ocr.available.length) {
    avail.replaceChildren(...cfg.ocr.available.filter((l) => l !== "osd").map((l) =>
      el("button", { type: "button", class: "chip", onclick: () => {
        const cur = o.ocr_langs.value.split("+").filter(Boolean);
        if (!cur.includes(l)) o.ocr_langs.value = [...cur, l].join("+");
      } }, l)));
  } else {
    avail.textContent = t("settings.ocr_missing", { detail: cfg.ocr.tesseract });
  }
  $("#settings-library").textContent = t("settings.library_now", { path: cfg.library });
  $("#library-choose").hidden = !TAURI;
  $("#library-default").hidden = !TAURI;
  $("#settings-about").textContent = t("settings.about", {
    version: backend.version || "", log: backend.log || "—" });
  renderServerStatus();
}

function renderServerStatus() {
  $("#servers-status").replaceChildren(...[...$("#servers").children].map((p) => p.cloneNode(true)));
}

async function saveServers(ev, clearKey = false) {
  if (ev) ev.preventDefault();
  const f = $("#servers-form");
  const servers = { chat: f.chat.value.trim(), embedding: f.embedding.value.trim(),
                    vision: f.vision.value.trim() };
  if (clearKey) servers.api_key = "";
  else if (f.api_key.value) servers.api_key = f.api_key.value;
  try {
    await request("PUT", "/api/settings", { servers });
    toast(t("settings.saved"));
    await loadServers();
    await loadSettings();
  } catch (e) { fail(e); }
}

async function saveOcr(ev) {
  ev.preventDefault();
  try {
    await request("PUT", "/api/settings", { ocr_langs: $("#ocr-form").ocr_langs.value.trim() });
    toast(t("settings.saved"));
    await loadSettings();
  } catch (e) { fail(e); }
}

async function chooseLibrary(useDefault) {
  let library = null;
  if (!useDefault) {
    library = await TAURI.dialog.open({ directory: true, multiple: false });
    if (!library) return;
  }
  try {
    await TAURI.core.invoke("set_library", { library, lang: getLang() });
  } catch (e) { fail(e); return; }
  // the engine restarts on the new folder: reconnect from scratch
  state.doc = null;
  state.selected.clear();
  $("#tab-viewer").hidden = true;
  $("#app").hidden = true;
  $("#boot").hidden = false;
  await boot();
  show("settings");
}

// ------------------------------------------------------------------ servers

async function loadServers() {
  try {
    const info = await get("/api/servers");
    const pills = [];
    for (const role of ["chat", "embedding", "vision"]) {
      const s = info[role] || {};
      const name = t(`server.${role}`);
      if (!s.url) { if (role !== "vision") pills.push(el("span", { class: "pill none" }, t("server.none", { role: name }))); continue; }
      const model = (s.model || "").split(/[\\/]/).pop().replace(/\.gguf$/i, "");
      pills.push(el("span", { class: `pill ${s.ok ? "ok" : "ko"}`, title: s.url + (s.error ? `\n${s.error}` : "") },
        s.ok ? t("server.ok", { role: name, model }) : t("server.ko", { role: name })));
    }
    $("#servers").replaceChildren(...pills);
    $("#remote-warning").hidden = !(info.chat && info.chat.url && info.chat.local === false);
    if (state.view === "settings") renderServerStatus();
  } catch (_) { /* shown at next refresh */ }
  clearTimeout(loadServers.timer);
  loadServers.timer = setTimeout(loadServers, 30000);
}

// ------------------------------------------------------------------ start

async function connect() {
  const params = new URLSearchParams(location.search);
  if (!TAURI) {
    // development in a browser: ?url=http://127.0.0.1:PORT&token=...
    // (start the backend with --allow-origin for this page's origin)
    if (params.get("url") && params.get("token")) return { url: params.get("url"), token: params.get("token") };
    throw new Error("no backend: open this page in IA-K, or add ?url=…&token=…");
  }
  for (;;) {
    const info = await TAURI.core.invoke("backend_info");
    if (info.status === "ready") return info;
    if (info.status === "error" || info.status === "stopped") {
      const err = new Error(info.error || t("app.stopped"));
      err.log = info.log;
      throw err;
    }
    await new Promise((r) => setTimeout(r, 300));
  }
}

async function boot() {
  setLang(getLang());
  translatePage();
  $("#boot-retry").hidden = true;
  $("#boot-message").className = "";
  $("#boot-message").textContent = t("app.starting");
  $("#boot-detail").textContent = "";
  try {
    backend = await connect();
    state.jobs = [];
    state.waiters.clear();
  } catch (e) {
    $("#boot-message").textContent = t("app.error");
    $("#boot-message").className = "error";
    $("#boot-detail").textContent = e.message + (e.log ? `\n${t("app.log", { path: e.log })}` : "");
    $("#boot-retry").hidden = !TAURI;
    return;
  }
  $("#boot").hidden = true;
  $("#app").hidden = false;
  show("library");
  await Promise.all([loadLibrary().catch(fail), loadServers()]);
  pollJobs();
}

function wire() {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.addEventListener("click", () => show(b.dataset.view)));
  $("#lang-switch").addEventListener("click", () => {
    setLang(getLang() === "fr" ? "en" : "fr");
    translatePage();
    renderLibrary();
    renderFilters();
    renderJobs();
    loadServers();
    if (state.doc) { loadAnalyses().catch(fail); if (state.unit) selectUnit(state.unit.id); }
    if (state.view === "settings") loadSettings().catch(fail);
  });
  $("#boot-retry").addEventListener("click", async () => {
    await TAURI.core.invoke("restart_backend", { lang: getLang() });
    boot();
  });
  $("#import-btn").addEventListener("click", () => chooseFiles().catch(fail));
  $("#compare-btn").addEventListener("click", compareSelected);
  $("#result-close").addEventListener("click", () => { $("#result-panel").hidden = true; });
  $("#search-form").addEventListener("submit", runSearch);
  $("#chat-new").addEventListener("click", () => {
    state.history = [];
    $("#chat").replaceChildren($("#chat-empty"));
    $("#chat-empty").hidden = false;
    $("#ask-input").focus();
  });
  $("#ask-form").addEventListener("submit", runAsk);
  $("#viewer-back").addEventListener("click", () => show("library"));
  $("#viewer-summary").addEventListener("click", () => state.doc && summarize(state.doc.id, state.doc.name));
  $("#viewer-reextract").addEventListener("click", reextract);
  $("#unit-save").addEventListener("click", saveUnit);
  $("#look-run").addEventListener("click", runLook);
  $("#servers-form").addEventListener("submit", saveServers);
  $("#api-key-clear").addEventListener("click", () => saveServers(null, true));
  $("#ocr-form").addEventListener("submit", saveOcr);
  $("#library-choose").addEventListener("click", () => chooseLibrary(false));
  $("#library-default").addEventListener("click", () => chooseLibrary(true));
  $("#look-task").addEventListener("change", () => {
    $("#look-question").hidden = $("#look-task").value !== "ask";
  });
  // keyboard: arrows move between pages in the viewer (outside the text box)
  document.addEventListener("keydown", (e) => {
    if (state.view !== "viewer" || !state.unit || e.target.matches("textarea, input, select")) return;
    const i = state.units.findIndex((u) => u.id === state.unit.id);
    if ((e.key === "ArrowDown" || e.key === "PageDown") && i < state.units.length - 1) selectUnit(state.units[i + 1].id);
    if ((e.key === "ArrowUp" || e.key === "PageUp") && i > 0) selectUnit(state.units[i - 1].id);
  });
  // files dropped on the window (Tauri gives their paths)
  if (TAURI && TAURI.webview) {
    TAURI.webview.getCurrentWebview().onDragDropEvent((event) => {
      const zone = $("#drop-zone");
      const type = event.payload.type;
      if (type === "over" || type === "enter") zone.classList.add("over");
      else zone.classList.remove("over");
      if (type === "drop" && backend) { show("library"); importPaths(event.payload.paths || []); }
    });
  }
}

wire();
boot();
