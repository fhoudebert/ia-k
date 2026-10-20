"""The local API, through real HTTP requests."""
import http.client
import json
import threading
import time

import pytest

from iak import api, servers

from .conftest import needs_tesseract
from .test_analyze import FakeChat
from .test_semantic import FakeEmbeddingServer

TOKEN = "test-token"


@pytest.fixture
def stack(tmp_path):
    chat = FakeChat(reply=lambda req: "L'atlas est chez le relieur [S1].")
    emb = FakeEmbeddingServer()
    host = servers.Host("test", chat=chat.url, embedding=emb.url)
    app = api.App(tmp_path / "lib", host, token=TOKEN,
                  origins={"http://localhost:1420"})
    srv = api.make_server(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, srv.server_address[1], chat
    srv.shutdown()
    srv.server_close()
    chat.close()
    emb.close()


def call(port, method, path, body=None, token=TOKEN, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    conn.request(method, path, json.dumps(body) if body is not None else None, h)
    r = conn.getresponse()
    data = r.read()
    ctype = r.getheader("Content-Type") or ""
    hdrs = dict(r.getheaders())
    conn.close()
    return r.status, (json.loads(data) if "json" in ctype and data else data), hdrs


def wait(port, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, j, _ = call(port, "GET", f"/api/jobs/{job_id}")
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_security(stack):
    _, port, _ = stack
    assert call(port, "GET", "/api/health", token=None)[0] == 401
    assert call(port, "GET", "/api/health", token="wrong")[0] == 401
    assert call(port, "GET", "/api/health")[0] == 200
    assert call(port, "GET", "/api/health", headers={"Host": "evil.example:80"})[0] == 403
    assert call(port, "GET", "/api/health",
                headers={"Origin": "https://evil.example"})[0] == 403
    status, _, h = call(port, "GET", "/api/health", headers={"Origin": "tauri://localhost"})
    assert status == 200 and h["Access-Control-Allow-Origin"] == "tauri://localhost"
    # preflight: answered for the interface only
    s, _, h = call(port, "OPTIONS", "/api/ask", token=None,
                   headers={"Origin": "http://localhost:1420"})
    assert s == 204 and "Authorization" in h["Access-Control-Allow-Headers"]
    assert call(port, "OPTIONS", "/api/ask", token=None,
                headers={"Origin": "https://evil.example"})[0] == 403
    assert call(port, "GET", "/api/nothing")[0] == 404
    assert call(port, "DELETE", "/api/status")[0] == 405


def test_import_job_search_ask(stack, native_pdf, docx_file):
    app, port, chat = stack
    s, r, _ = call(port, "POST", "/api/import", {"paths": [str(native_pdf), str(docx_file)]})
    assert s == 202
    job = wait(port, r["job"])
    assert job["status"] == "done", job
    files = job["result"]["files"]
    assert [f["status"] for f in files] == ["imported", "imported"]
    assert job["result"]["vectors"] > 0

    _, docs, _ = call(port, "GET", "/api/documents")
    assert {d["name"] for d in docs} == {"registre.pdf", "notes.docx"}
    pdf = next(d for d in docs if d["kind"] == "pdf")
    assert pdf["meta"]["pages"] == 3 and pdf["n_passages"] == 3

    _, res, _ = call(port, "GET", "/api/search?q=relieur%20atlas&n=5")
    assert res["mode"] == "hybrid"
    # both documents speak of the binder and the atlas
    assert {h["label"] for h in res["hits"][:2]} == {"p. 2", "Chapitre second"}
    assert res["hits"][0]["via"] == "kw+sem"
    _, res, _ = call(port, "GET", f"/api/search?q=atlas&mode=kw&doc={pdf['id']}")
    assert res["mode"] == "kw" and {h["doc_id"] for h in res["hits"]} == {pdf["id"]}

    s, ans, _ = call(port, "POST", "/api/ask", {"question": "Où est l'atlas ?"})
    assert s == 200 and ans["status"] == "answered"
    assert ans["sources"][0]["ref"] == "S1" and ans["remote"] is False
    assert ans["sources"][0]["unit_id"] > 0
    assert call(port, "POST", "/api/ask", {"question": " "})[0] == 400

    _, st, _ = call(port, "GET", "/api/status")
    assert st["documents"] == 2 and st["vectors"] == st["passages"]


def test_document_views_edit_delete(stack, native_pdf):
    _, port, _ = stack
    job = wait(port, call(port, "POST", "/api/import", {"paths": [str(native_pdf)]})[1]["job"])
    doc_id = job["result"]["files"][0]["doc_id"]
    s, d, _ = call(port, "GET", f"/api/documents/{doc_id}")
    assert s == 200 and d["original_available"] is True
    s, units, _ = call(port, "GET", f"/api/documents/{doc_id}/units")
    assert len(units) == 3 and "boxes" not in units[0] and units[0]["locator"] == {"page": 1}
    s, u, _ = call(port, "GET", f"/api/units/{units[0]['id']}")
    assert u["boxes"] and u["width"] == 595.0
    s, raw, h = call(port, "GET", f"/api/documents/{doc_id}/original")
    assert s == 200 and raw[:4] == b"%PDF" and h["Content-Type"] == "application/pdf"
    s, png, h = call(port, "GET", f"/api/units/{units[1]['id']}/image?max=400")
    assert s == 200 and png[:4] == b"\x89PNG"

    s, r, _ = call(port, "PUT", f"/api/units/{units[2]['id']}/text",
                   {"text": "Le cabinet ouest garde les cartes."})
    assert s == 200
    wait(port, r["job"])
    _, res, _ = call(port, "GET", "/api/search?q=ouest&mode=kw")
    assert res["hits"][0]["unit_id"] == units[2]["id"]

    s, _, _ = call(port, "DELETE", f"/api/documents/{doc_id}")
    assert s == 200
    assert call(port, "GET", f"/api/documents/{doc_id}")[0] == 404


def test_analysis_jobs(stack, native_pdf, docx_file):
    _, port, chat = stack
    job = wait(port, call(port, "POST", "/api/import",
                          {"paths": [str(native_pdf), str(docx_file)]})[1]["job"])
    a, b = [f["doc_id"] for f in job["result"]["files"]]
    _, units, _ = call(port, "GET", f"/api/documents/{a}/units")
    j = wait(port, call(port, "POST", "/api/look",
                        {"unit_id": units[0]["id"], "task": "describe"})[1]["job"])
    assert j["status"] == "done" and j["result"]["text"]
    j = wait(port, call(port, "POST", "/api/summarize", {"doc_id": a})[1]["job"])
    assert j["status"] == "done"
    j = wait(port, call(port, "POST", "/api/compare", {"doc_ids": [a, b]})[1]["job"])
    assert j["status"] == "done"
    _, rows, _ = call(port, "GET", "/api/analyses")
    assert [r["task"] for r in rows] == ["describe", "summary", "summary", "compare"]
    assert rows[-1]["sources"] == [a, b]
    # a look on a text document fails as a job error, not a crash
    _, units_b, _ = call(port, "GET", f"/api/documents/{b}/units")
    j = wait(port, call(port, "POST", "/api/look", {"unit_id": units_b[0]["id"]})[1]["job"])
    assert j["status"] == "error" and "only PDF pages" in j["error"]
    _, jobs, _ = call(port, "GET", "/api/jobs")
    assert jobs[0]["id"] > jobs[-1]["id"]


def test_bad_requests(stack, tmp_path):
    _, port, _ = stack
    assert call(port, "POST", "/api/import", {"paths": []})[0] == 400
    s, r, _ = call(port, "POST", "/api/import", {"paths": [str(tmp_path / "missing.pdf")]})
    assert s == 400 and "not found" in r["error"]
    assert call(port, "POST", "/api/look", {})[0] == 400
    assert call(port, "POST", "/api/compare", {"doc_ids": [1]})[0] == 400
    assert call(port, "GET", "/api/search")[0] == 400
    assert call(port, "GET", "/api/jobs/999")[0] == 404
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("POST", "/api/ask", "not json",
                  {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    assert conn.getresponse().status == 400


def test_servers_endpoint(stack):
    _, port, _ = stack
    _, info, _ = call(port, "GET", "/api/servers")
    assert info["chat"]["ok"] and info["chat"]["local"] is True
    assert info["chat"]["props"]["vision"] is True
    assert info["embedding"]["ok"] and info["vision"]["url"] is None


def test_announce_line(tmp_path, capsys):
    import io
    app = api.App(tmp_path / "lib", servers.Host("x"))
    out = io.StringIO()
    srv_thread = threading.Thread(target=api.serve, args=(app, 0, out), daemon=True)
    srv_thread.start()
    for _ in range(100):
        if out.getvalue():
            break
        time.sleep(0.02)
    line = out.getvalue().splitlines()[0]
    assert line.startswith("IAK_API ")
    info = json.loads(line[len("IAK_API "):])
    assert info["url"].startswith("http://127.0.0.1:") and len(info["token"]) >= 30
    port = int(info["url"].rsplit(":", 1)[1])
    assert call(port, "GET", "/api/health", token=info["token"])[0] == 200


def test_serve_stops_when_stdin_closes(tmp_path):
    """The desktop application keeps the backend's stdin open; when it dies,
    stdin closes and the backend must stop."""
    import os
    import subprocess
    import sys
    env = dict(os.environ, PYTHONPATH=str(__import__("pathlib").Path(api.__file__).parents[1]),
               IAK_LIBRARY=str(tmp_path / "lib"))
    proc = subprocess.Popen([sys.executable, "-m", "iak", "serve", "--watch-stdin"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    try:
        line = proc.stdout.readline().decode()
        assert line.startswith("IAK_API ")
        assert proc.poll() is None
        proc.stdin.close()
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_keep_alive_after_unread_bodies(stack):
    """A body the route ignores, or a refused request's body, must not be
    taken for the next request on the same connection (the browser reuses it)."""
    _, port, _ = stack
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    for method, path, headers in (("POST", "/api/books/none/pause", auth),   # 404, body unread
                                  ("POST", "/api/import", {"Content-Type": "application/json"}),
                                  ("GET", "/api/health", auth)):
        conn.request(method, path, '{"paths": []}' if method == "POST" else None, headers)
        r = conn.getresponse()
        r.read()
        assert r.status in (404, 401, 200)
    assert r.status == 200
    conn.request("GET", "/api/status", None, auth)
    r = conn.getresponse()
    assert r.status == 200 and json.loads(r.read())["documents"] == 0
    conn.close()
