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
  turns: [],                   // the same answers with their sources, for export
  collections: [],             // [{id, name, n_docs}]
  collection: null,            // id of the collection shown in the library, or null
  folders: [],                 // followed folders
  seenJobs: new Set(),         // finished jobs already taken into account
  doubts: {},                  // unit id -> {doubtful, ratio, doubt} of the open document
  unitDoubts: null,            // the doubtful words of the page shown
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
/** Error text for the reader: known engine messages in the interface language. */
function errorText(msg) {
  msg = String(msg || "");
  if (/database is locked/.test(msg)) return t("error.locked");
  return msg;
}
const fail = (e) => toast(t("error.generic", { msg: errorText(e.message || e) }), true);

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
  for (const name of ["library", "search", "ask", "translate", "viewer", "settings"]) {
    $(`#view-${name}`).hidden = name !== view;
  }
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  if (view === "search") $("#search-input").focus();
  if (view === "ask") $("#ask-input").focus();
  if (view === "settings") loadSettings().catch(fail);
  if (view === "translate") return enterTranslate().catch(fail);
  return Promise.resolve();
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
    const finished = ["done", "error", "cancelled"].includes(job.status);
    if (finished && state.waiters.has(job.id)) {
      state.waiters.get(job.id)(job);
      state.waiters.delete(job.id);
    }
    // the library changes with these jobs, whoever started them
    if (finished && !state.seenJobs.has(job.id)) {
      state.seenJobs.add(job.id);
      if (state.started && ["import", "reindex", "embed", "scan"].includes(job.kind)) libraryChanged = true;
    }
  }
  state.started = true;
  if (libraryChanged) loadLibrary().catch(() => {});
  renderJobs();
  const active = state.jobs.some((j) => j.status === "queued" || j.status === "running");
  pollJobs.timer = setTimeout(pollJobs, active || state.waiters.size ? 1000 : 6000);
}

// jobs that stop when asked while running (the others only while queued)
const CANCELLABLE = new Set(["read", "fulltext", "scan"]);

function renderJobs() {
  renderActivity();
  renderFulltextProgress();
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
    if (j.error) li.append(el("span", { class: "status-error" }, errorText(j.error)));
    if ((j.status === "queued" || (j.status === "running" && CANCELLABLE.has(j.kind))) && !j.cancel) {
      li.append(el("button", { class: "link", onclick: () =>
        post(`/api/jobs/${j.id}/cancel`).then(pollJobs, fail) }, t("jobs.cancel")));
    }
    if (j.status === "done" && j.kind === "import" && j.result) {
      const c = { imported: 0, duplicate: 0, error: 0 };
      j.result.files.forEach((f) => { c[f.status] = (c[f.status] || 0) + 1; });
      (j.result.folders || []).forEach((f) => {
        c.imported += f.imported; c.duplicate += f.duplicates; c.error += f.errors; });
      li.append(el("span", { class: "muted" }, t("import.result", c)));
    }
    if (j.status === "done" && j.kind === "scan" && j.result) {
      li.append(el("span", { class: "muted" }, scanSummary(j.result)));
    }
    ul.append(li);
  }
}

/** The jobs still to do, in the top bar: seen from every tab. */
function renderActivity() {
  const active = state.jobs.filter((j) => j.status === "queued" || j.status === "running");
  const b = $("#activity");
  b.hidden = !active.length;
  if (!active.length) return;
  const run = active.find((j) => j.status === "running") || active[0];
  const p = run.progress || {};
  let text = t(`jobs.kind.${run.kind}`);
  if (run.status === "running" && p.total > 1) text += ` ${p.done || 0}/${p.total}`;
  if (active.length > 1) text += ` ${t("jobs.more", { n: active.length - 1 })}`;
  b.textContent = text;
}

// ------------------------------------------------------------------ library

async function loadLibrary() {
  [state.documents, state.collections, state.folders] = await Promise.all([
    get("/api/documents"), get("/api/collections"), get("/api/folders")]);
  renderFolders();
  if (state.collection && !state.collections.some((c) => c.id === state.collection)) {
    state.collection = null;
  }
  renderCollections();
  renderLibrary();
  renderFilters();
  renderTranslateDocs();
  renderBookDocs();
}

/** Type and document lists of the search and question filters. */
function renderFilters() {
  for (const id of ["#search-coll", "#ask-coll"]) {
    const sel = $(id);
    const keep = sel.value;
    sel.replaceChildren(el("option", { value: "" }, t("filter.all_colls")),
      ...state.collections.map((c) => el("option", { value: String(c.id) }, c.name)));
    sel.value = state.collections.some((c) => String(c.id) === keep) ? keep : "";
    sel.hidden = !state.collections.length;
  }
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
  const coll = $(`#${prefix}-coll`).value;
  return { kinds: kind ? [kind] : null, doc_ids: doc ? [Number(doc)] : null,
           collections: coll ? [Number(coll)] : null,
           label: [coll && $(`#${prefix}-coll`).selectedOptions[0].text, kind && kindLabel(kind),
                   doc && $(`#${prefix}-doc`).selectedOptions[0].text]
             .filter(Boolean).join(", ") };
}

// ------------------------------------------------------------------ collections

function renderCollections() {
  const chip = (id, label, n) => el("button", {
    class: `coll-chip${state.collection === id ? " active" : ""}`,
    onclick: () => { state.collection = id; renderCollections(); renderLibrary(); } },
    label, el("span", { class: "n" }, String(n)));
  $("#coll-chips").replaceChildren(chip(null, t("coll.all"), state.documents.length),
    ...state.collections.map((c) => chip(c.id, c.name, c.n_docs)));
  $("#coll-tools").hidden = !state.collection;
  // where the ticked documents go: another collection than the one shown
  const target = $("#coll-target");
  const keep = target.value;
  const others = state.collections.filter((c) => c.id !== state.collection);
  target.replaceChildren(...others.map((c) => el("option", { value: String(c.id) }, c.name)));
  if (others.some((c) => String(c.id) === keep)) target.value = keep;
  updateSelection();
}

function currentCollection() {
  return state.collections.find((c) => c.id === state.collection) || null;
}

/** The tools for the documents ticked in the list. */
function updateSelection() {
  const ids = [...state.selected].filter((id) => state.documents.some((d) => d.id === id));
  $("#coll-selection").hidden = !ids.length;
  $("#coll-selected").textContent = t("coll.selected", { n: ids.length });
  const cur = currentCollection();
  const hasTargets = state.collections.some((c) => c.id !== state.collection);
  $("#coll-target").hidden = $("#coll-add").hidden = !hasTargets;
  $("#coll-move").hidden = !(cur && hasTargets);
  $("#coll-move").textContent = t("coll.move");
  $("#coll-remove").hidden = !cur;
  if (cur) $("#coll-remove").textContent = t("coll.remove", { name: cur.name });
  if (!state.collections.length) $("#coll-selected").textContent += ` — ${t("coll.none_yet")}`;
}

async function changeMembers(collectionId, add, remove) {
  const r = await post(`/api/collections/${collectionId}/documents`, { add, remove });
  const name = (state.collections.find((c) => c.id === collectionId) || {}).name || "";
  toast(add.length ? t("coll.added", { n: r.added, name }) : t("coll.removed", { n: r.removed, name }));
  state.selected.clear();
  await loadLibrary();
}

async function moveMembers(fromId, toId, ids) {
  const r = await post(`/api/collections/${fromId}/move`, { to: toId, doc_ids: ids });
  const name = (state.collections.find((c) => c.id === toId) || {}).name || "";
  toast(t("coll.moved", { n: r.moved, name }));
  state.selected.clear();
  await loadLibrary();
}

/** The collections of the open document, as chips to switch on and off. */
function renderDocCollections() {
  const box = $("#doc-colls-box");
  box.hidden = !state.doc || !state.collections.length;
  if (box.hidden) return;
  const d = state.documents.find((x) => x.id === state.doc.id);
  const mine = new Set((d && d.collections) || []);
  $("#doc-colls").replaceChildren(...state.collections.map((c) => el("button", {
    class: `coll-chip${mine.has(c.id) ? " active" : ""}`, type: "button",
    title: t(mine.has(c.id) ? "viewer.coll_out" : "viewer.coll_in", { name: c.name }),
    onclick: () => setDocCollections(state.doc.id, mine.has(c.id)
      ? [...mine].filter((x) => x !== c.id) : [...mine, c.id]).catch(fail) }, c.name)));
}

async function setDocCollections(docId, ids) {
  await request("PUT", `/api/documents/${docId}/collections`, { collections: ids });
  await loadLibrary();
  renderDocCollections();
}

async function createCollection(ev) {
  ev.preventDefault();
  const f = $("#coll-new");
  const name = f.name.value.trim();
  if (!name) { f.name.focus(); return; }
  const ids = [...state.selected].filter((id) => state.documents.some((d) => d.id === id));
  try {
    const c = await post("/api/collections", { name, doc_ids: ids });
    f.hidden = true;
    $("#coll-new-btn").hidden = false;
    f.name.value = "";
    state.selected.clear();
    state.collection = c.id;
    toast(t("coll.created", { name: c.name, n: c.doc_ids.length }));
    await loadLibrary();
  } catch (e) { fail(e); }
}

async function renameCollection() {
  const cur = currentCollection();
  if (!cur) return;
  const f = $("#coll-new");
  f.hidden = false;
  $("#coll-new-btn").hidden = true;
  f.name.value = cur.name;
  f.dataset.rename = String(cur.id);
  f.querySelector("button.primary").textContent = t("coll.rename_ok");
  f.name.focus();
  f.name.select();
}

async function submitCollectionForm(ev) {
  const f = $("#coll-new");
  if (!f.dataset.rename) return createCollection(ev);
  ev.preventDefault();
  try {
    await request("PUT", `/api/collections/${f.dataset.rename}`, { name: f.name.value.trim() });
    closeCollectionForm();
    await loadLibrary();
  } catch (e) { fail(e); }
}

function closeCollectionForm() {
  const f = $("#coll-new");
  f.hidden = true;
  delete f.dataset.rename;
  f.querySelector("button.primary").textContent = t("coll.create");
  $("#coll-new-btn").hidden = false;
}

async function deleteCollection() {
  const cur = currentCollection();
  if (!cur) return;
  const msg = t("coll.delete_confirm", { name: cur.name });
  const ok = TAURI && TAURI.dialog.ask ? await TAURI.dialog.ask(msg, { kind: "warning" })
                                       : window.confirm(msg);
  if (!ok) return;
  try {
    await request("DELETE", `/api/collections/${cur.id}`);
    state.collection = null;
    await loadLibrary();
  } catch (e) { fail(e); }
}

function renderLibrary() {
  const docs = state.collection
    ? state.documents.filter((d) => (d.collections || []).includes(state.collection))
    : state.documents;
  $("#library-empty").hidden = docs.length > 0;
  $("#library-empty").textContent = state.collection ? t("coll.empty") : t("lib.empty");
  $("#library-table").hidden = docs.length === 0;
  const names = Object.fromEntries(state.collections.map((c) => [c.id, c.name]));
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
      updateSelection();
    });
    const stateCell = el("td", { class: d.status === "error" ? "state-error" : "" },
      t(`lib.state.${d.status}`));
    if (d.error) stateCell.title = d.error;
    tbody.append(el("tr", {},
      el("td", {}, box),
      el("td", { class: "name", onclick: () => openDoc(d.id) }, d.name,
        ...(d.collections || []).filter((c) => names[c] && c !== state.collection)
          .map((c) => el("span", { class: "coll-tag" }, names[c]))),
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

// ------------------------------------------------------------------ folders

function scanSummary(r) {
  if (!r.available) return t("folder.absent");
  const parts = [t("folder.scan_found", { n: r.found })];
  if (r.imported) parts.push(t("folder.scan_new", { n: r.imported }));
  if (r.changed) parts.push(t("folder.scan_changed", { n: r.changed }));
  if (r.errors) parts.push(t("folder.scan_errors", { n: r.errors }));
  if (r.waiting) parts.push(t("folder.scan_waiting", { n: r.waiting }));
  if (r.gone) parts.push(t("folder.scan_gone", { n: r.gone }));
  return parts.join(" · ");
}

function renderFolders() {
  const ul = $("#folders");
  ul.hidden = !state.folders.length;
  ul.replaceChildren(...state.folders.map((f) => {
    const when = f.last_scan ? new Date(f.last_scan * 1000).toLocaleString(getLang(), {
      dateStyle: "short", timeStyle: "short" }) : "—";
    const status = !f.available ? el("span", { class: "badge warn" }, t("folder.absent"))
      : el("span", { class: f.watch ? "badge ok" : "badge" }, t(f.watch ? "folder.watched" : "folder.once"));
    const coll = state.collections.find((c) => c.name === f.collection);
    return el("li", {},
      el("button", { class: "link coll-link", title: t("folder.show"), onclick: () => {
        if (coll) { state.collection = coll.id; renderCollections(); renderLibrary(); }
      } }, f.collection || "—"),
      el("span", { class: "path", title: f.full_path }, f.full_path),
      status,
      el("span", { class: "muted" }, t("folder.files", { n: f.files, when })),
      el("span", { class: "spacer" }),
      f.available ? el("button", { class: "link", onclick: () => scanFolder(f).catch(fail) },
        t("folder.scan")) : null,
      el("button", { class: "link", onclick: () => request("PUT", `/api/folders/${f.id}`,
        { watch: !f.watch }).then(loadLibrary, fail) },
        t(f.watch ? "folder.unwatch" : "folder.rewatch")),
      el("button", { class: "link danger", onclick: () => unfollow(f).catch(fail) },
        t("folder.remove")));
  }));
}

async function chooseFolder() {
  if (!TAURI || !TAURI.dialog) { toast(t("tr.export_desktop"), true); return; }
  const path = await TAURI.dialog.open({ directory: true, multiple: false });
  if (path) openFolderForm(Array.isArray(path) ? path[0] : path);
}

function openFolderForm(path) {
  const f = $("#folder-form");
  f.dataset.path = path;
  $("#folder-path").textContent = path;
  f.name.value = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
  f.copy.checked = false;
  f.hidden = false;
  f.name.focus();
}

async function followFolder(ev) {
  ev.preventDefault();
  const f = $("#folder-form");
  try {
    const r = await post("/api/folders", { path: f.dataset.path, name: f.name.value.trim(),
      recursive: f.recursive.checked, watch: f.watch.checked, copy: f.copy.checked });
    f.hidden = true;
    toast(t("folder.added", { name: r.collection }));
    await loadLibrary();
    const done = await waitJob(r.job);
    if (done.status === "error") fail(done.error);
    else toast(`${r.collection} : ${scanSummary(done.result)}`);
    const c = state.collections.find((x) => x.name === r.collection);
    if (c) { state.collection = c.id; renderCollections(); renderLibrary(); }
  } catch (e) { fail(e); }
}

async function scanFolder(f) {
  const { job } = await post(`/api/folders/${f.id}/scan`);
  toast(t("folder.scanning", { name: f.collection }));
  const done = await waitJob(job);
  if (done.status === "error") fail(done.error);
  else toast(`${f.collection} : ${scanSummary(done.result)}`);
}

async function unfollow(f) {
  const msg = t("folder.remove_confirm", { path: f.full_path, name: f.collection });
  const ok = TAURI && TAURI.dialog.ask ? await TAURI.dialog.ask(msg, { kind: "warning" })
                                       : window.confirm(msg);
  if (!ok) return;
  await request("DELETE", `/api/folders/${f.id}`);
  await loadLibrary();
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

function showResult(title, text, analysisId = null) {
  state.resultAnalysis = analysisId;
  $("#result-copy").hidden = $("#result-export").hidden = !analysisId;
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
    else showResult(`${t("analysis.summary")} — ${name}`, done.result.text, done.result.analysis);
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
    showResult(`${t("lib.compare")} — ${names.join(", ")}`, done.result.text, done.result.analysis);
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ exports

/** Copy the Markdown an export call returns. */
async function copyMarkdown(promise) {
  try {
    const r = await promise;
    await navigator.clipboard.writeText(r.markdown);
    toast(t("export.copied"));
  } catch (e) { fail(e); }
}

/** Ask where to save, then let `write(path)` produce the file. */
async function saveMarkdown(name, write) {
  if (!TAURI) { toast(t("tr.export_desktop"), true); return; }
  const path = await TAURI.dialog.save({ defaultPath: name,
                                         filters: [{ name: "Markdown", extensions: ["md"] }] });
  if (!path) return;
  try {
    const r = await write(path);
    toast(t("export.saved", { path: r.path }));
  } catch (e) { fail(e); }
}

async function exportAnalysis(id) {
  const lang = getLang();
  const r = await get(`/api/analyses/${id}/markdown?lang=${lang}`);
  await saveMarkdown(r.name, (path) => post(`/api/analyses/${id}/export`, { path, lang }));
}

function analysisExportButtons(id) {
  return el("span", { class: "export-links" },
    el("button", { class: "link inline", onclick: () =>
      copyMarkdown(get(`/api/analyses/${id}/markdown?lang=${getLang()}`)) }, t("export.copy")),
    el("button", { class: "link inline", onclick: () => exportAnalysis(id).catch(fail) },
      t("export.save")));
}

async function exportConversation() {
  if (!state.turns.length) return;
  const lang = getLang();
  const r = await post("/api/export/conversation", { turns: state.turns, lang });
  await saveMarkdown(r.name, (path) =>
    post("/api/export/conversation", { turns: state.turns, lang, path }));
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
    state.doubts = {};
    renderUnitList();
    loadDocDoubts(docId).catch(() => {});
    const hasPages = doc.kind === "pdf" || doc.kind === "image";
    $("#analyse-box").hidden = !hasPages;
    $("#viewer-reread").hidden = !hasPages;
    $("#reread-panel").hidden = true;
    $("#viewer-fulltext").hidden = !(hasPages || doc.kind === "epub");
    $("#fulltext-panel").hidden = true;
    renderDocCollections();
    show("viewer");
    await selectUnit(unitId || (units[0] && units[0].id), passageId);
    await loadAnalyses();
  } catch (e) { fail(e); }
}

function renderUnitList() {
  const current = state.unit ? state.unit.id : null;
  $("#unit-list").replaceChildren(...state.units.map((u) => el("li", {
    class: [u.source === "ocr" ? "ocr" : "", u.reader ? "vl" : "",
            state.doubts[u.id] && state.doubts[u.id].doubt ? "doubt" : "",
            u.id === current ? "active" : ""].filter(Boolean).join(" "),
    dataset: { id: u.id }, title: u.label, onclick: () => selectUnit(u.id) }, u.label)));
}

/** Units of the open document again (after a page text changed). */
async function refreshUnits() {
  if (!state.doc) return;
  const keep = state.unit ? state.unit.id : null;
  state.units = await get(`/api/documents/${state.doc.id}/units`);
  renderUnitList();
  loadDocDoubts(state.doc.id).catch(() => {});
  if (keep) await selectUnit(keep);
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
  if (u.reader) badges.push(el("span", { class: "badge" }, t("viewer.read_by", { model: u.reader.model || "?" })));
  else if (u.edited) badges.push(el("span", { class: "badge" }, t("viewer.edited")));
  $("#unit-restore").hidden = !u.edited;
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
  loadDoubts(u).catch(() => {});
}

// ------------------------------------------------------------------ doubtful words

/** Which pages of the open document have many doubtful words (unit list). */
async function loadDocDoubts(docId) {
  const rows = await get(`/api/documents/${docId}/doubts`);
  if (!state.doc || state.doc.id !== docId) return;
  state.doubts = Object.fromEntries(rows.map((r) => [r.unit_id, r]));
  renderUnitList();
}

/** The doubtful words of the page shown: chips that select the word in the
 * text and show it on the page image. */
async function loadDoubts(u) {
  const box = $("#doubts-box");
  box.hidden = true;
  state.unitDoubts = null;
  if (u.source !== "ocr" || u.edited) return;
  const d = await get(`/api/units/${u.id}/doubts`);
  if (!state.unit || state.unit.id !== u.id) return;
  state.unitDoubts = d;
  if (!d.doubtful) return;
  box.hidden = false;
  $("#doubts-title").textContent = t("doubt.title", { n: d.doubtful,
    pct: Math.round(d.ratio * 100) });
  $("#doubts-list").replaceChildren(...d.suspects.map((s) => el("button", {
    type: "button", class: `doubt-word ${s.reasons.join(" ")}`,
    title: [...s.reasons.map((r) => t(`doubt.why.${r}`)),
            s.conf === null || s.conf === undefined ? null : t("doubt.conf", { conf: Math.round(s.conf) })]
      .filter(Boolean).join(" · "),
    onclick: () => showDoubt(s) }, s.word)));
  renderDoubtMarks();
}

function doubtMark(s, d, cls = "") {
  const [x0, y0, x1, y1] = s.box;
  return el("div", { class: `doubt-mark ${cls}`,
    style: `left:${(x0 / d.width) * 100}%;top:${(y0 / d.height) * 100}%;` +
           `width:${((x1 - x0) / d.width) * 100}%;height:${((y1 - y0) / d.height) * 100}%` });
}

/** Every doubtful word framed on the page image, when asked. */
function renderDoubtMarks() {
  const d = state.unitDoubts;
  const layer = $("#page-marks");
  layer.querySelectorAll(".doubt-mark").forEach((m) => m.remove());
  if (!d || !d.width || !$("#doubts-marks").checked) return;
  layer.append(...d.suspects.filter((s) => s.box).map((s) => doubtMark(s, d)));
}

/** One doubtful word: selected in the text (found again near its place if
 * the text was edited since), and shown on the image. */
function showDoubt(s) {
  const ta = $("#unit-text");
  let i = ta.value.substr(s.start, s.word.length) === s.word ? s.start
    : ta.value.indexOf(s.word, Math.max(0, s.start - 80));
  if (i < 0) i = ta.value.indexOf(s.word);
  if (i >= 0) {
    ta.focus();
    ta.setSelectionRange(i, i + s.word.length);
    // bring the selection into view: put the caret line near the top
    const line = ta.value.slice(0, i).split("\n").length - 1;
    const lh = parseFloat(getComputedStyle(ta).lineHeight) || 18;
    ta.scrollTop = Math.max(0, line * lh - ta.clientHeight / 3);
  }
  const d = state.unitDoubts;
  const layer = $("#page-marks");
  layer.querySelectorAll(".doubt-mark.current").forEach((m) => m.remove());
  if (d && d.width && s.box) {
    const m = doubtMark(s, d, "current");
    layer.append(m);
    m.scrollIntoView({ block: "center", behavior: "smooth" });
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
    u.reader = null;                 // corrected by hand: no longer the model's text
    toast(t("viewer.saved"));
    renderUnitList();
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
    const what = a.task === "summary" ? t("analysis.summary")
      : (t(`viewer.task.${a.task}`) + (a.question ? ` : ${a.question}` : ""));
    const reading = ["transcribe", "correct", "fix", "focus"].includes(a.task);
    const model = a.model ? a.model.split(/[\\/]/).pop().replace(/\.gguf$/i, "") : null;
    const meta = [what, unit ? unit.label : null, model, when];
    if (reading && a.score !== null && a.score !== undefined) {
      meta.push(t("reading.score", { pct: Math.round(a.score * 100) }));
    }
    const item = el("div", { class: reading ? "analysis reading" : "analysis" },
      el("div", { class: "meta" }, meta.filter(Boolean).join(" · "), " · ",
         analysisExportButtons(a.id)));
    if (reading) {
      // a correction of doubtful words: the words first, the page after
      if (a.sources && a.sources.changes) item.append(wordChanges(a.sources));
      item.append(el("div", { class: "text" }, a.text));
      if (a.sources && a.sources.truncated) item.append(el("p", { class: "badge warn" }, t("reading.truncated")));
      if (unit) item.append(readingActions(a, unit, item));
    } else {
      item.append(markdown(a.text));
    }
    box.append(item);
  }
}

/** The words a correction of doubtful words changed, and those it refused. */
function wordChanges(src) {
  const box = el("div", { class: "word-changes" },
    el("div", { class: "meta" }, t("reading.changes", { kept: src.kept || 0, refused: src.refused || 0,
                                                       n: src.doubtful || 0 })));
  for (const c of src.changes) {
    box.append(el("span", { class: c.kept ? "change kept" : "change refused",
                             title: t(`reading.why.${c.why}`) },
      el("del", {}, c.word), " → ", c.kept ? el("ins", {}, c.new) : c.new));
  }
  return box;
}

/** Buttons of a page reading: use it as the page text, show the differences. */
function readingActions(a, unit, item) {
  const current = unit.reader && unit.reader.id === a.id;
  const bar = el("div", { class: "reading-actions" });
  if (current) bar.append(el("span", { class: "badge" }, t("reading.current")));
  else {
    bar.append(el("button", { class: "primary", onclick: async () => {
      try {
        await post(`/api/analyses/${a.id}/apply`);
        toast(t("reading.applied", { page: unit.label }));
        await refreshUnits();
        if (state.unit && state.unit.id !== unit.id) await selectUnit(unit.id);
        await loadAnalyses();
      } catch (e) { fail(e); }
    } }, t("reading.apply")));
  }
  bar.append(el("button", { onclick: async (ev) => {
    const old = item.querySelector(".diff");
    if (old) { old.remove(); ev.target.textContent = t("reading.diff"); return; }
    try {
      const d = await get(`/api/analyses/${a.id}/diff`);
      const box = el("div", { class: "diff" });
      if (d.same) box.append(t("reading.same"));
      else {
        box.append(el("div", { class: "meta" }, t("reading.diff_legend")));
        for (const [op, text] of d.ops) {
          box.append(op === "=" ? text : el(op === "+" ? "ins" : "del", {}, text));
        }
      }
      item.append(box);
      ev.target.textContent = t("reading.diff_hide");
    } catch (e) { fail(e); }
  } }, t("reading.diff")));
  if (state.unit && state.unit.id !== unit.id) {
    bar.append(el("button", { class: "link", onclick: () => selectUnit(unit.id) }, t("reading.show_page")));
  }
  return bar;
}

async function restoreUnit() {
  const u = state.unit;
  if (!u) return;
  const ok = TAURI && TAURI.dialog.ask
    ? await TAURI.dialog.ask(t("viewer.restore_confirm"), { kind: "warning" })
    : window.confirm(t("viewer.restore_confirm"));
  if (!ok) return;
  try {
    await post(`/api/units/${u.id}/restore`);
    toast(t("viewer.restored"));
    await refreshUnits();
    await loadAnalyses();
  } catch (e) { fail(e); }
}

// documents in full text

function sizeLabel(n) {
  return n >= 1e6
    ? `${(n / 1e6).toLocaleString(getLang(), { maximumFractionDigits: 1 })} ${t("ft.mb")}`
    : `${Math.max(1, Math.round(n / 1e3))} ${t("ft.kb")}`;
}

async function openFulltext() {
  const f = $("#fulltext-panel");
  if (!f.hidden) { f.hidden = true; return; }
  const epub = state.doc.kind === "epub";
  f.format.hidden = epub;
  f.method.hidden = !epub;
  $("#fulltext-notes").hidden = epub;
  $("#fulltext-note").textContent = t(epub ? "ft.note_epub" : "ft.note_pdf");
  $("#fulltext-files").replaceChildren();          // not the previous document's
  f.hidden = false;
  await loadFulltextFiles();
}

async function loadFulltextFiles() {
  if (!state.doc) return;
  const docId = state.doc.id;
  const files = await get(`/api/documents/${docId}/fulltext`);
  $("#fulltext-files").replaceChildren(...files.map((x) => el("li", {},
    el("strong", {}, x.name), el("span", { class: "muted" }, sizeLabel(x.size)),
    el("button", { type: "button", class: "link", onclick: () => saveFulltext(docId, x).catch(fail) }, t("book.save")),
    ["tsv", "zip"].includes(x.format) ? "" : el("button", { type: "button", class: "link", onclick: async () => {
      const { job } = await post(`/api/documents/${docId}/fulltext/import`, { name: x.name });
      toast(t("book.importing"));
      const done = await waitJob(job);
      if (done.status === "error") fail(done.error); else toast(t("ft.imported"));
    } }, t("book.import")),
    el("button", { type: "button", class: "link danger", onclick: async () => {
      await request("DELETE", `/api/documents/${docId}/fulltext?name=${encodeURIComponent(x.name)}`);
      await loadFulltextFiles();
    } }, t("book.delete")))));
}

async function saveFulltext(docId, x) {
  if (!TAURI) { toast(t("tr.export_desktop"), true); return; }
  const path = await TAURI.dialog.save({ defaultPath: x.name,
    filters: [{ name: x.format.toUpperCase(), extensions: [x.format] }] });
  if (!path) return;
  const r = await post(`/api/documents/${docId}/fulltext/save`, { name: x.name, path });
  toast(t("export.saved", { path: r.path }));
}

/** The full-text job of a document still to finish, if any. */
function fulltextJob(docId) {
  return state.jobs.find((j) => j.kind === "fulltext" && j.doc_id === docId &&
                         (j.status === "queued" || j.status === "running"));
}

/** Progress of the document's full-text job in its panel and on its button
 * (again when coming back to the document while it runs). */
function renderFulltextProgress() {
  const box = $("#fulltext-progress");
  const j = state.doc ? fulltextJob(state.doc.id) : null;
  $("#fulltext-panel button.primary").disabled = !!j;
  $("#viewer-fulltext").classList.toggle("busy", !!j);
  box.hidden = !j;
  if (!j) return;
  const p = j.progress || {};
  const bar = box.querySelector("progress");
  let text;
  if (j.cancel) text = t("ft.cancelling");
  else if (j.status === "queued") text = t("ft.queued");
  else if (p.total && p.done >= p.total) text = t("ft.writing");
  else if (p.total) text = t("ft.page", { done: (p.done || 0) + 1, total: p.total });
  else text = t("ft.running");
  if (p.total && j.status === "running" && p.done < p.total) {
    bar.max = p.total;
    bar.value = p.done || 0;
  } else {
    bar.removeAttribute("value");                 // moving bar: no measure yet
  }
  $("#fulltext-progress-text").textContent = text;
  $("#fulltext-cancel").hidden = j.cancel;
  $("#fulltext-cancel").onclick = () => post(`/api/jobs/${j.id}/cancel`).then(pollJobs, fail);
}

async function makeFulltext(ev) {
  ev.preventDefault();
  if (!state.doc || fulltextJob(state.doc.id)) return;
  const f = $("#fulltext-panel");
  const docId = state.doc.id;
  const name = state.doc.name;
  try {
    const { job } = await post(`/api/documents/${docId}/fulltext`, {
      format: f.format.value, method: f.method.value, illustrations: f.illustrations.checked,
      notes: f.notes.checked, typography: f.typography.checked ? null : false,
      cut_words: f.cut_words.checked, old_print: f.old_print.checked,
      modern: f.modern.checked });
    const done = waitJob(job);                    // starts the polling, shows the progress
    const r = await done;
    if (r.status === "cancelled") { toast(t("ft.cancelled")); return; }
    if (r.status === "error") { fail(r.error); return; }
    const x = r.result;
    toast(t("ft.done", { file: x.file, before: sizeLabel(x.size_before), after: sizeLabel(x.size_after),
                         n: x.illustrations })
          + (x.notes ? `, ${t("ft.notes_done", { n: x.notes })}` : "")
          + (x.corrected || x.refused ? `, ${t("ft.corrected", { n: x.corrected || 0, refused: x.refused || 0 })}` : "")
          + (x.finished ? `, ${t("ft.finished", { n: x.finished })}` : "")
          + (x.errors ? ` — ${t("ft.errors", { n: x.errors, error: x.error })}` : "")
          + (state.doc && state.doc.id === docId ? "" : ` (${name})`));
    if (state.doc && state.doc.id === docId) await loadFulltextFiles();
  } catch (e) { fail(e); }
}

async function startReread(ev) {
  ev.preventDefault();
  if (!state.doc) return;
  const f = $("#reread-panel");
  try {
    const { job } = await post("/api/read", {
      doc_id: state.doc.id, pages: f.pages.value, threshold: Number(f.threshold.value) || 80,
      method: f.method.value, replace: f.replace.checked });
    f.hidden = true;
    toast(t("reread.started"));
    const docId = state.doc.id;
    const done = await waitJob(job);
    if (done.status === "error") { fail(done.error); return; }
    const r = done.result || {};
    toast(t("reread.done", { read: r.read || 0, replaced: r.replaced || 0, kept: r.kept_edited || 0 })
          + (done.status === "cancelled" ? ` — ${t("jobs.status.cancelled")}` : ""));
    if (state.doc && state.doc.id === docId) { await refreshUnits(); await loadAnalyses(); }
  } catch (e) { fail(e); }
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
    // reading the page again: a proposal for its text
    const { job } = ["correct", "transcribe", "fix", "focus"].includes(task)
      ? await post("/api/read", { unit_id: u.id, method: task })
      : await post("/api/look", { unit_id: u.id, task, question, lang: getLang() });
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
  if (f.collections) url += `&collection=${f.collections[0]}`;
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
                   doc_ids: f.doc_ids, kinds: f.kinds, collections: f.collections };
    await streamAsk(body, {
      sources: (d) => { live.textContent = t("ask.found", { n: d.sent.length }); },
      token: (d) => { text += d.text; if (!frame) frame = requestAnimationFrame(paint); },
      done: (d) => {
        if (frame) cancelAnimationFrame(frame);
        const turn = { question, text: d.text, status: d.status, scope: f.label,
                       sources: d.sources };
        live.replaceWith(answerMessage(d, turn));
        state.history.push({ question, answer: d.status === "no_answer" ? "" : d.text });
        state.turns.push(turn);
        $("#chat-export").hidden = false;
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

function answerMessage(res, turn = null) {
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
  const meta = el("div", { class: "meta" },
    t("ask.meta", { n: res.sent.length, mode: modeName, s: res.seconds }));
  if (turn) {
    meta.append(" · ", el("button", { class: "link inline", onclick: () =>
      copyMarkdown(post("/api/export/conversation", { turns: [turn], single: true, lang: getLang() }))
    }, t("export.copy_answer")));
  }
  msg.append(meta);
  return msg;
}

// ------------------------------------------------------------------ translation

const tr = { langs: [], docId: null, rows: [], busy: false };

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (_) { /* storage unavailable */ }
  return null;
}

const trMode = () => document.querySelector('input[name="tr-mode"]:checked').value;

async function enterTranslate() {
  if (!tr.langs.length) {
    tr.langs = await get("/api/languages");
    const opts = () => tr.langs.map((l) => el("option", { value: l.code }, `${l.name} (${l.code})`));
    $("#tr-src").replaceChildren(el("option", { value: "auto" }, t("tr.auto")), ...opts());
    $("#tr-tgt").replaceChildren(...opts());
    $("#tr-src").value = store("iak.tr.src") || "auto";
    $("#tr-tgt").value = store("iak.tr.tgt") || getLang();
  }
  $("#tr-src").options[0].textContent = t("tr.auto");
  renderTranslateDocs();
  renderBookDocs();
  renderEngine();
  if (trMode() === "doc") await loadTranslations();
  else if (trMode() === "book") await loadBooks();
  else $("#tr-input").focus();
}

/** Which model translates: the translation server, or the chat server as a fallback. */
function renderEngine() {
  const info = state.servers || {};
  const tsrv = info.translate || {};
  const chat = info.chat || {};
  const name = (s) => (s.model || "").split(/[\\/]/).pop().replace(/\.gguf$/i, "");
  let text = t("tr.engine.none");
  if (tsrv.url && tsrv.ok) text = t("tr.engine.translate", { model: name(tsrv) });
  else if (chat.ok) text = t(tsrv.url ? "tr.engine.fallback" : "tr.engine.chat", { model: name(chat) });
  $("#tr-engine").textContent = text;
}

function renderTranslateDocs() {
  const sel = $("#tr-doc");
  if (!sel) return;
  const keep = tr.docId ? String(tr.docId) : sel.value;
  const docs = [...state.documents].sort((a, b) => a.name.localeCompare(b.name, getLang()));
  sel.replaceChildren(el("option", { value: "" }, t("tr.choose_doc")),
    ...docs.map((d) => el("option", { value: String(d.id) }, d.name)));
  sel.value = docs.some((d) => String(d.id) === keep) ? keep : "";
  tr.docId = sel.value ? Number(sel.value) : null;
}

function setMode(mode, load = true) {
  document.querySelector(`input[name="tr-mode"][value="${mode}"]`).checked = true;
  $("#tr-text-mode").hidden = mode !== "text";
  $("#tr-doc-mode").hidden = mode !== "doc";
  $("#tr-book-mode").hidden = mode !== "book";
  if (!load) return;
  if (mode === "doc") loadTranslations().catch(fail);
  if (mode === "book") { renderBookDocs(); loadBooks().catch(fail); }
}

function trLanguages() {
  const src = $("#tr-src").value;
  const tgt = $("#tr-tgt").value;
  if (src) store("iak.tr.src", src);              // empty before the languages load
  if (tgt) store("iak.tr.tgt", tgt);
  return { src, tgt };
}

/** Wait for a translation job, showing its progress in `info`. */
async function waitTranslation(job, info) {
  const timer = setInterval(() => {
    const j = state.jobs.find((x) => x.id === job);
    const p = (j && j.progress) || {};
    if (j && j.status === "queued") info.textContent = t("tr.queued");
    else if (p.total > 1) info.textContent = t("tr.progress", { done: p.done, total: p.total });
  }, 500);
  try { return await waitJob(job); } finally { clearInterval(timer); }
}

function langName(code) {
  const l = tr.langs.find((x) => x.code === code);
  return l ? l.name : code;
}

async function translateText() {
  const text = $("#tr-input").value;
  if (!text.trim() || tr.busy) { $("#tr-input").focus(); return; }
  const { src, tgt } = trLanguages();
  const info = $("#tr-info");
  tr.busy = true;
  $("#tr-run").disabled = true;
  info.textContent = t("tr.running");
  try {
    const { job } = await post("/api/translate", { text, src, tgt });
    const done = await waitTranslation(job, info);
    if (done.status === "error") { info.textContent = ""; fail(trError(done.error)); return; }
    const r = done.result;
    $("#tr-output").value = r.text;
    tr.detected = r.src;
    info.textContent = r.src === r.tgt ? t("tr.same", { lang: langName(r.src) })
      : t("tr.done", { src: langName(r.src), model: r.model, seconds: r.seconds });
  } catch (e) { info.textContent = ""; fail(e); }
  finally { tr.busy = false; $("#tr-run").disabled = false; }
}

function trError(msg) {
  if (/could not be detected/.test(msg)) return t("tr.detect_failed");
  if (/no translation or chat server/.test(msg)) return t("tr.no_server");
  if (/is already in .*: choose another target language/.test(msg)) return t("tr.same_lang_short");
  return msg;
}

async function loadTranslations() {
  const list = $("#tr-units");
  const info = $("#tr-doc-info");
  tr.docId = $("#tr-doc").value ? Number($("#tr-doc").value) : null;
  if (!tr.docId) { list.replaceChildren(); info.textContent = t("tr.choose_doc_hint"); return; }
  const { tgt } = trLanguages();
  if (!tgt) return;                               // the languages are not loaded yet
  const data = await get(`/api/documents/${tr.docId}/translations?tgt=${encodeURIComponent(tgt)}`);
  // the document or the language changed meanwhile: a newer call shows it
  if (Number($("#tr-doc").value) !== tr.docId || $("#tr-tgt").value !== tgt) return;
  tr.rows = data.units;
  tr.detected = data.src;
  const done = data.units.filter((u) => u.text !== null).length;
  const stale = data.units.filter((u) => u.stale).length;
  info.textContent = [
    t("tr.doc_state", { done, total: data.units.length, tgt: langName(tgt) }),
    stale ? t("tr.doc_stale", { n: stale }) : null,
    data.src ? t("tr.doc_lang", { lang: langName(data.src) }) : null,
    data.src && data.src === tgt ? t("tr.same_lang", { lang: langName(tgt) }) : null,
    data.languages.filter((l) => l !== tgt).length
      ? t("tr.doc_other", { langs: data.languages.filter((l) => l !== tgt).map(langName).join(", ") }) : null,
  ].filter(Boolean).join(" · ");
  list.replaceChildren(...data.units.map(unitRow));
}

function unitRow(u) {
  const head = el("div", { class: "tr-head" }, el("strong", {}, u.label));
  if (u.stale) head.append(el("span", { class: "badge warn" }, t("tr.stale")));
  const again = u.text === null ? t("tr.unit_run") : t("tr.unit_again");
  head.append(el("span", { class: "spacer" }),
    el("button", { class: "link", onclick: () => translateUnit(u.unit_id, u.text !== null && !u.stale) }, again),
    el("button", { class: "link", onclick: () => openDoc(tr.docId, u.unit_id) }, t("tr.open")));
  const out = u.text === null ? el("div", { class: "none" }, t("tr.not_yet"))
    : el("div", {}, u.text);
  return el("li", { dataset: { unit: u.unit_id } }, head,
    el("div", { class: "tr-pair" }, el("div", { class: "src" }, u.source), out));
}

async function translateUnit(unitId, refresh) {
  const { src, tgt } = trLanguages();
  const li = document.querySelector(`#tr-units li[data-unit="${unitId}"]`);
  if (li) li.querySelector(".tr-pair").lastChild.replaceWith(el("div", { class: "thinking" }, t("tr.running")));
  try {
    const { job } = await post("/api/translate/document", { unit_id: unitId, src, tgt, refresh });
    const done = await waitJob(job);
    if (done.status === "error") fail(trError(done.error));
    if (trMode() === "doc" && tr.docId) await loadTranslations();
    return done;
  } catch (e) { fail(e); return null; }
}

async function translateDocument() {
  if (!tr.docId || tr.busy) { $("#tr-doc").focus(); return; }
  const { src, tgt } = trLanguages();
  const info = $("#tr-doc-info");
  const docId = tr.docId;
  tr.busy = true;
  $("#tr-doc-run").disabled = true;
  info.textContent = t("tr.running");
  try {
    const { job } = await post("/api/translate/document", {
      doc_id: docId, src, tgt, refresh: $("#tr-refresh").checked });
    const done = await waitTranslation(job, info);
    if (done.status === "error") fail(trError(done.error));
    else toast(t("tr.doc_done", { n: done.result.translated, cached: done.result.cached,
                                  seconds: done.result.seconds }));
  } catch (e) { fail(e); }
  finally {
    tr.busy = false;
    $("#tr-doc-run").disabled = false;
    $("#tr-refresh").checked = false;
    if (tr.docId === docId) loadTranslations().catch(fail);
  }
}

async function exportTranslation() {
  if (!tr.docId) return;
  const { tgt } = trLanguages();
  const format = $("#tr-format").value;
  const doc = state.documents.find((d) => d.id === tr.docId);
  const base = (doc ? doc.name : "document").replace(/\.[^.]+$/, "");
  if (!TAURI) { toast(t("tr.export_desktop"), true); return; }
  const path = await TAURI.dialog.save({
    defaultPath: `${base}.${tgt}.${format}`,
    filters: [{ name: format === "md" ? "Markdown" : "Text", extensions: [format] }] });
  if (!path) return;
  try {
    const r = await post(`/api/documents/${tr.docId}/translations/export`, {
      tgt, path, format, bilingual: $("#tr-bilingual").checked });
    toast(t("tr.exported", { path: r.path }));
  } catch (e) { fail(e); }
}

/** From the viewer: the translation of the page shown, in the translation tab. */
async function translateFromViewer() {
  if (!state.doc || !state.unit) return;
  const unitId = state.unit.id;
  const docId = state.doc.id;
  tr.docId = docId;
  setMode("doc", false);
  // the tab fills its lists first (the languages on the first visit), keeping
  // this document, then loads its translations
  await show("translate");
  if ($("#tr-doc").value !== String(docId)) {
    $("#tr-doc").value = String(docId);
    await loadTranslations();
  }
  if (tr.docId !== docId) return;
  const li = document.querySelector(`#tr-units li[data-unit="${unitId}"]`);
  if (li) li.scrollIntoView({ block: "start" });
  const { tgt } = trLanguages();
  if (tr.detected && tr.detected === tgt) {      // French into French: choose the language
    $("#tr-tgt").focus();
    toast(t("tr.same_lang", { lang: langName(tgt) }), true);
    return;
  }
  const row = tr.rows.find((u) => u.unit_id === unitId);
  if (row && (row.text === null || row.stale)) {
    await translateUnit(unitId, false);
    const again = document.querySelector(`#tr-units li[data-unit="${unitId}"]`);
    if (again) again.scrollIntoView({ block: "start" });
  }
}

// ------------------------------------------------------------------ books

const books = { list: [], file: null, logs: new Set() };

function duration(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h} h ${String(m).padStart(2, "0")}`;
  return m ? `${m} min ${String(s).padStart(2, "0")}` : `${s} s`;
}

function renderBookDocs() {
  const sel = $("#book-doc");
  if (!sel) return;
  setTimeout(updateBookFormat);
  const keep = sel.value;
  const docs = [...state.documents].sort((a, b) => a.name.localeCompare(b.name, getLang()));
  sel.replaceChildren(el("option", { value: "" }, t("book.choose_doc")),
    ...docs.map((d) => el("option", { value: String(d.id) }, `${d.name} (${kindLabel(d.kind)})`)));
  sel.value = docs.some((d) => String(d.id) === keep) ? keep : "";
}

async function chooseBookFile() {
  if (!TAURI) { toast(t("tr.export_desktop"), true); return; }
  const path = await TAURI.dialog.open({ multiple: false, filters: [{
    name: t("book.formats"), extensions: ["epub", "pdf", "txt", "md", "html", "htm", "xhtml", "docx"] }] });
  if (!path) return;
  books.file = path;
  $("#book-doc").value = "";
  $("#book-file-name").textContent = path.split(/[\\/]/).pop();
  updateBookFormat();
}

/** EPUB, HTML and DOCX books keep their layout, so their format: the
 * choice is for books read as a flat text (PDF, scans, TXT, Markdown). */
function updateBookFormat() {
  const doc = state.documents.find((d) => String(d.id) === $("#book-doc").value);
  const kind = doc ? doc.kind : (books.file || "").split(".").pop().toLowerCase();
  const fixed = ["epub", "html", "htm", "xhtml", "docx"].includes(kind);
  $("#book-format-box").hidden = fixed;
  const auto = $("#book-format").options[0];
  auto.textContent = t("book.fmt_auto") + ({ pdf: " (PDF)", image: " (PDF)", txt: " (TXT)",
                                             md: " (Markdown)" }[kind] || "");
}

async function startBook() {
  const docId = $("#book-doc").value;
  if (!docId && !books.file) { $("#book-doc").focus(); return; }
  const { src, tgt } = trLanguages();
  const body = { src, tgt, passes: Number($("#book-passes").value),
                 glossary: $("#book-glossary").value, limit: $("#book-trial").checked ? 3 : 0,
                 format: $("#book-format-box").hidden ? "auto" : $("#book-format").value };
  if (docId) body.doc_id = Number(docId); else body.path = books.file;
  try {
    await post("/api/books", body);
    toast(t("book.queued"));
    await loadBooks();
  } catch (e) { fail(e); }
}

async function loadBooks() {
  clearTimeout(loadBooks.timer);
  try {
    books.list = await get("/api/books");
    const logs = {};
    await Promise.all([...books.logs].filter((id) => books.list.some((b) => b.id === id))
      .map(async (id) => { logs[id] = (await get(`/api/books/${id}?log=60`)).log; }));
    renderBooks(logs);
  } finally {
    // poll on, even after a failed request
    const active = books.list.some((b) => b.status === "queued" || b.status === "running");
    if (state.view === "translate" && trMode() === "book") {
      loadBooks.timer = setTimeout(() => loadBooks().catch(() => {}), active ? 2000 : 15000);
    }
  }
}

function bookAction(label, fn, cls = "") {
  return el("button", { class: cls, onclick: () => fn().catch(fail) }, label);
}

function renderBooks(logs = {}) {
  const ul = $("#book-list");
  $("#book-empty").hidden = books.list.length > 0;
  ul.replaceChildren(...books.list.map((b) => {
    const status = b.pausing ? "pausing" : b.status;
    const cls = { done: "badge ok", error: "badge err", interrupted: "badge warn",
                  paused: "badge warn" }[status] || "badge";
    const head = el("div", { class: "book-head" },
      el("strong", {}, b.name),
      el("span", { class: "muted" }, `${b.src && b.src !== "auto" ? langName(b.src) : t("tr.auto")} → ${langName(b.tgt)}`),
      el("span", { class: "muted" }, t(`book.passes${b.passes}`)),
      el("span", { class: cls }, t(`book.status.${status}`)));
    if (b.limit) head.append(el("span", { class: "badge warn" }, t("book.trial_badge")));
    const li = el("li", { dataset: { id: b.id } }, head);
    if (b.units) {
      const info = [t("book.progress", { done: b.done_units || 0, units: b.units, pct: b.pct || 0 })];
      if (b.status === "running" && b.eta) info.push(t("book.eta", { time: duration(b.eta) }));
      if (b.status === "done" && b.work_seconds) info.push(t("book.took", { time: duration(b.work_seconds) }));
      if (b.model) info.push(b.model);
      li.append(el("div", { class: "book-progress" },
        el("progress", { max: 100, value: b.pct || 0 }), info.join(" · ")));
    }
    if (b.status === "error" && b.error) li.append(el("div", { class: "status-error" }, trError(b.error)));
    const actions = el("div", { class: "book-actions" });
    if (b.status === "running" || b.status === "queued") {
      if (!b.pausing) actions.append(bookAction(t("book.pause"), async () => {
        await post(`/api/books/${b.id}/pause`); await loadBooks(); }));
    } else if (b.status !== "done") {
      actions.append(bookAction(t("book.resume"), async () => {
        await post(`/api/books/${b.id}/resume`); await loadBooks(); }, "primary"));
    }
    if (b.output_ready) {
      if ((b.formats || []).length > 1) actions.append(formatSelect(b));
      actions.append(bookAction(t("book.save"), () => saveBook(b, false), "primary"),
        bookAction(t("book.import"), async () => {
          const { job } = await post(`/api/books/${b.id}/import`);
          toast(t("book.importing"));
          const done = await waitJob(job);
          if (done.status === "error") fail(done.error); else toast(t("book.imported"));
        }));
      if (b.limit) actions.append(bookAction(t("book.full"), async () => {
        await post("/api/books", { ...(b.doc_id !== null && b.doc_id !== undefined ? { doc_id: b.doc_id } : { path: b.source }),
          src: b.src, tgt: b.tgt, passes: b.passes, limit: 0 });
        await loadBooks(); }));
    } else if (b.partial_ready) {
      actions.append(bookAction(t("book.save_partial"), () => saveBook(b, true)));
    }
    const open = books.logs.has(b.id);
    actions.append(bookAction(open ? t("book.log_hide") : t("book.log"), async () => {
      if (open) books.logs.delete(b.id); else books.logs.add(b.id);
      await loadBooks(); }, "link"));
    if (b.status !== "running" && b.status !== "queued") {
      actions.append(bookAction(t("book.delete"), async () => {
        const ok = TAURI && TAURI.dialog.ask
          ? await TAURI.dialog.ask(t("book.delete_confirm", { name: b.name }), { kind: "warning" })
          : window.confirm(t("book.delete_confirm", { name: b.name }));
        if (!ok) return;
        await request("DELETE", `/api/books/${b.id}`);
        books.logs.delete(b.id);
        await loadBooks(); }, "link danger"));
    }
    li.append(actions);
    if (open && logs[b.id]) {
      const pre = el("pre", { class: "book-log" }, logs[b.id].join("\n"));
      li.append(pre);
      requestAnimationFrame(() => { pre.scrollTop = pre.scrollHeight; });
    }
    return li;
  }));
}

/** Format to save a finished book in (written again from its translations). */
function formatSelect(b) {
  books.formats = books.formats || {};
  const sel = el("select", { class: "book-fmt", title: t("book.format"),
                             onchange: () => { books.formats[b.id] = sel.value; } },
    ...b.formats.map((f) => el("option", { value: f },
      { docx: "Word (DOCX)", md: "Markdown", txt: t("book.fmt_txt") }[f] || f.toUpperCase())));
  sel.value = books.formats[b.id] || b.out_format || b.formats[0];
  return sel;
}

async function saveBook(b, partial) {
  if (!TAURI) { toast(t("tr.export_desktop"), true); return; }
  const name = partial ? b.partial : b.output;
  const current = name.split(".").pop();
  const ext = (!partial && books.formats && books.formats[b.id]) || current;
  const path = await TAURI.dialog.save({ defaultPath: name.replace(/\.[^.]+$/, `.${ext}`),
                                         filters: [{ name: ext.toUpperCase(), extensions: [ext] }] });
  if (!path) return;
  const r = await post(`/api/books/${b.id}/save`, { path, partial, format: ext });
  toast(t("tr.exported", { path: r.path }));
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
  f.translate.value = cfg.host.translate || "";
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
                    vision: f.vision.value.trim(), translate: f.translate.value.trim() };
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

const OPTIONAL_DEFAULTS = { vision: "http://127.0.0.1:8082/v1", translate: "http://127.0.0.1:8083/v1" };

async function loadServers() {
  try {
    const info = await get("/api/servers");
    const pills = [];
    state.servers = info;
    for (const role of ["chat", "embedding", "vision", "translate"]) {
      const s = info[role] || {};
      const name = t(`server.${role}`);
      // a vision or translation server at its default port that does not
      // answer is simply not started: the chat server stands in, no red pill
      if (s.url && !s.ok && OPTIONAL_DEFAULTS[role] === s.url) continue;
      if (!s.url) { if (role === "chat" || role === "embedding") pills.push(el("span", { class: "pill none" }, t("server.none", { role: name }))); continue; }
      const model = (s.model || "").split(/[\\/]/).pop().replace(/\.gguf$/i, "");
      pills.push(el("span", { class: `pill ${s.ok ? "ok" : "ko"}`, title: s.url + (s.error ? `\n${s.error}` : "") },
        s.ok ? t("server.ok", { role: name, model }) : t("server.ko", { role: name })));
    }
    $("#servers").replaceChildren(...pills);
    $("#remote-warning").hidden = !(info.chat && info.chat.url && info.chat.local === false);
    if (state.view === "settings") renderServerStatus();
    if (state.view === "translate") renderEngine();
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
    const msg = /^library in use/.test(e.message) ? t("app.in_use") : e.message;
    $("#boot-detail").textContent = msg + (e.log ? `\n${t("app.log", { path: e.log })}` : "");
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
    renderCollections();
    renderFolders();
    renderLibrary();
    renderFilters();
    renderJobs();
    loadServers();
    if (state.doc) { loadAnalyses().catch(fail); if (state.unit) selectUnit(state.unit.id); }
    if (state.view === "settings") loadSettings().catch(fail);
    if (state.view === "translate") enterTranslate().catch(fail);
  });
  $("#boot-retry").addEventListener("click", async () => {
    await TAURI.core.invoke("restart_backend", { lang: getLang() });
    boot();
  });
  $("#import-btn").addEventListener("click", () => chooseFiles().catch(fail));
  $("#compare-btn").addEventListener("click", compareSelected);
  $("#folder-btn").addEventListener("click", () => chooseFolder().catch(fail));
  $("#folder-form").addEventListener("submit", followFolder);
  $("#folder-cancel").addEventListener("click", () => { $("#folder-form").hidden = true; });
  $("#coll-new-btn").addEventListener("click", () => {
    $("#coll-new").hidden = false; $("#coll-new-btn").hidden = true; $("#coll-new").name.focus(); });
  $("#coll-new-cancel").addEventListener("click", closeCollectionForm);
  $("#coll-new").addEventListener("submit", submitCollectionForm);
  $("#coll-rename").addEventListener("click", renameCollection);
  $("#coll-delete").addEventListener("click", deleteCollection);
  $("#coll-add").addEventListener("click", () => {
    const ids = [...state.selected].filter((id) => state.documents.some((d) => d.id === id));
    if (ids.length && $("#coll-target").value) {
      changeMembers(Number($("#coll-target").value), ids, []).catch(fail);
    }
  });
  $("#coll-move").addEventListener("click", () => {
    const ids = [...state.selected].filter((id) => state.documents.some((d) => d.id === id));
    if (ids.length && state.collection && $("#coll-target").value) {
      moveMembers(state.collection, Number($("#coll-target").value), ids).catch(fail);
    }
  });
  $("#coll-remove").addEventListener("click", () => {
    const ids = [...state.selected].filter((id) => state.documents.some((d) => d.id === id));
    if (ids.length && state.collection) changeMembers(state.collection, [], ids).catch(fail);
  });
  $("#result-close").addEventListener("click", () => { $("#result-panel").hidden = true; });
  $("#search-form").addEventListener("submit", runSearch);
  $("#chat-export").addEventListener("click", () => exportConversation().catch(fail));
  $("#result-copy").addEventListener("click", () => state.resultAnalysis &&
    copyMarkdown(get(`/api/analyses/${state.resultAnalysis}/markdown?lang=${getLang()}`)));
  $("#result-export").addEventListener("click", () => state.resultAnalysis &&
    exportAnalysis(state.resultAnalysis).catch(fail));
  $("#chat-new").addEventListener("click", () => {
    state.history = [];
    state.turns = [];
    $("#chat-export").hidden = true;
    $("#chat").replaceChildren($("#chat-empty"));
    $("#chat-empty").hidden = false;
    $("#ask-input").focus();
  });
  $("#ask-form").addEventListener("submit", runAsk);
  $("#viewer-back").addEventListener("click", () => show("library"));
  $("#viewer-summary").addEventListener("click", () => state.doc && summarize(state.doc.id, state.doc.name));
  $("#viewer-reextract").addEventListener("click", reextract);
  $("#unit-save").addEventListener("click", saveUnit);
  $("#unit-restore").addEventListener("click", restoreUnit);
  $("#viewer-reread").addEventListener("click", () => { $("#reread-panel").hidden = !$("#reread-panel").hidden; });
  $("#reread-close").addEventListener("click", () => { $("#reread-panel").hidden = true; });
  $("#reread-panel").addEventListener("submit", startReread);
  $("#viewer-fulltext").addEventListener("click", () => openFulltext().catch(fail));
  $("#doubts-marks").addEventListener("change", renderDoubtMarks);
  $("#activity").addEventListener("click", () => {
    show("library");
    $("#jobs-list").scrollIntoView({ block: "nearest" });
  });
  $("#fulltext-close").addEventListener("click", () => { $("#fulltext-panel").hidden = true; });
  $("#fulltext-panel").addEventListener("submit", makeFulltext);
  $("#reread-panel").pages.addEventListener("change", () => {
    $("#reread-threshold-box").hidden = $("#reread-panel").pages.value !== "low";
  });
  $("#look-run").addEventListener("click", runLook);
  $("#servers-form").addEventListener("submit", saveServers);
  $("#api-key-clear").addEventListener("click", () => saveServers(null, true));
  $("#ocr-form").addEventListener("submit", saveOcr);
  $("#library-choose").addEventListener("click", () => chooseLibrary(false));
  $("#library-default").addEventListener("click", () => chooseLibrary(true));
  document.querySelectorAll('input[name="tr-mode"]').forEach((r) =>
    r.addEventListener("change", () => setMode(r.value)));
  $("#tr-run").addEventListener("click", translateText);
  $("#tr-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); translateText(); }
  });
  $("#tr-copy").addEventListener("click", () => {
    const text = $("#tr-output").value;
    if (text) navigator.clipboard.writeText(text).then(() => toast(t("tr.copied")), fail);
  });
  $("#tr-swap").addEventListener("click", () => {
    const src = $("#tr-src").value === "auto" ? (tr.detected || "") : $("#tr-src").value;
    if (!src) return;
    const tgt = $("#tr-tgt").value;
    $("#tr-src").value = tgt;
    $("#tr-tgt").value = src;
    if ($("#tr-output").value) {
      $("#tr-input").value = $("#tr-output").value;
      $("#tr-output").value = "";
    }
    trLanguages();
    if (trMode() === "doc") loadTranslations().catch(fail);
  });
  for (const id of ["#tr-src", "#tr-tgt"]) {
    $(id).addEventListener("change", () => {
      trLanguages();
      if (trMode() === "doc") loadTranslations().catch(fail);
    });
  }
  $("#tr-doc").addEventListener("change", () => loadTranslations().catch(fail));
  $("#tr-doc-run").addEventListener("click", translateDocument);
  $("#tr-export").addEventListener("click", () => exportTranslation().catch(fail));
  $("#viewer-translate").addEventListener("click", () => translateFromViewer().catch(fail));
  $("#book-file").addEventListener("click", () => chooseBookFile().catch(fail));
  $("#book-doc").addEventListener("change", () => {
    if ($("#book-doc").value) { books.file = null; $("#book-file-name").textContent = ""; }
    updateBookFormat();
  });
  $("#book-start").addEventListener("click", startBook);
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
