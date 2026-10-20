"""Settings, streamed answers and passage highlights."""
import json
import tomllib

import pytest

from iak import answer, search, servers
from iak.library import Library

from .test_analyze import FakeChat
from .test_api import call, stack, wait  # noqa: F401  (fixture and helpers)


def test_save_host_keeps_other_computers(tmp_path, monkeypatch):
    cfg = tmp_path / "config" / "servers.toml"
    cfg.parent.mkdir()
    cfg.write_text('[host.default]\nchat = "http://127.0.0.1:8080/v1"\n'
                   '[host.bureau]\nmatch = ["BUREAU-01"]\nchat = "http://10.0.0.2:8080/v1"\n'
                   'api_key = "secret"\n', encoding="utf-8")
    monkeypatch.setenv("COMPUTERNAME", "PC-HX370")
    h = servers.save_host(cfg, "http://127.0.0.1:9000/v1", "http://127.0.0.1:9001/v1", "")
    assert h.name == "pc-hx370" and h.chat.endswith(":9000/v1") and h.vision is None
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))["host"]
    assert data["bureau"]["api_key"] == "secret" and data["bureau"]["match"] == ["BUREAU-01"]
    assert data["pc-hx370"]["match"] == ["PC-HX370"]
    assert (cfg.parent / "servers.toml.bak").exists()
    # this computer is now matched; the key is kept when not given, removed with ""
    servers.save_host(cfg, "http://127.0.0.1:9000/v1", "", "", api_key="k")
    assert servers.load_host(cfg).api_key == "k"
    servers.save_host(cfg, "http://127.0.0.1:9000/v1", "", "")
    assert servers.load_host(cfg).api_key == "k"
    servers.save_host(cfg, "http://127.0.0.1:9000/v1", "", "", api_key="")
    assert servers.load_host(cfg).api_key == ""
    # an existing matching section is updated, not duplicated
    monkeypatch.setenv("COMPUTERNAME", "bureau-01")
    h = servers.save_host(cfg, "http://10.0.0.3:8080/v1", "", "")
    assert h.name == "bureau" and h.api_key == "secret"
    assert set(tomllib.loads(cfg.read_text(encoding="utf-8"))["host"]) == {
        "default", "bureau", "pc-hx370"}


def test_settings_api(stack, tmp_path, monkeypatch):  # noqa: F811
    app, port, _ = stack
    app.config = tmp_path / "servers.toml"
    s, cfg, _ = call(port, "GET", "/api/settings")
    assert s == 200 and cfg["host"]["chat"] == app.host.chat and cfg["ocr"]["langs"] == "fra+eng"
    assert "fra" in cfg["ocr"]["available"] or cfg["ocr"]["available"] == []
    new = {"servers": {"chat": app.host.chat, "embedding": app.host.embedding,
                       "vision": "", "api_key": "abc"},
           "ocr_langs": "fra+frm", "lang": "en"}
    s, cfg, _ = call(port, "PUT", "/api/settings", new)
    assert s == 200 and cfg["ocr"]["langs"] == "fra+frm" and cfg["lang"] == "en"
    assert cfg["host"]["api_key_set"] is True
    assert "api_key" not in json.dumps(cfg["host"]).replace("api_key_set", "")
    assert app.host.api_key == "abc"
    assert call(port, "PUT", "/api/settings", {"ocr_langs": "fra; rm -rf"})[0] == 400
    assert call(port, "PUT", "/api/settings", {"lang": "de"})[0] == 400
    assert call(port, "PUT", "/api/settings",
                {"servers": {"chat": "javascript:alert(1)"}})[0] == 400


def test_ocr_langs_setting_survives_restart(tmp_path):
    from iak import api
    a = api.App(tmp_path / "lib", servers.Host("x"))
    with a.library() as lib:
        from iak import db
        db.set_setting(lib.con, "ocr_langs", "fra+frm")
    b = api.App(tmp_path / "lib", servers.Host("x"))
    assert b.ocr_langs == "fra+frm"


def test_ask_stream_events(tmp_path, native_pdf):
    chat = FakeChat(reply=lambda req: "L'atlas est chez le relieur [S1][S9].")
    try:
        with Library(tmp_path / "lib") as lib:
            lib.import_file(native_pdf)
            hits = search.search(lib.con, "relieur atlas")
            events = list(answer.ask_stream("Où ?", hits, chat.client()))
    finally:
        chat.close()
    kinds = [k for k, _ in events]
    assert kinds[0] == "sources" and kinds[-1] == "done" and kinds.count("token") > 2
    assert "".join(v for k, v in events if k == "token") == "L'atlas est chez le relieur [S1][S9]."
    final = events[-1][1]
    assert final.status == "answered" and "[S9]" not in final.text
    assert list(answer.ask_stream("x", [], None)) [0][0] == "done"


def test_ask_stream_over_http(stack, native_pdf):  # noqa: F811
    _, port, _ = stack
    wait(port, call(port, "POST", "/api/import", {"paths": [str(native_pdf)]})[1]["job"])
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.request("POST", "/api/ask/stream", json.dumps({"question": "Où est l'atlas ?"}),
                 {"Authorization": "Bearer test-token", "Content-Type": "application/json"})
    r = conn.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    events = []
    for block in r.read().decode("utf-8").split("\n\n"):
        if block.strip():
            name = block.split("\n")[0].removeprefix("event: ")
            data = json.loads(block.split("\n")[1].removeprefix("data: "))
            events.append((name, data))
    conn.close()
    assert events[0][0] == "sources" and events[0][1]["sent"]
    assert events[-1][0] == "done" and events[-1][1]["status"] == "answered"
    assert events[-1][1]["sources"][0]["unit_id"] > 0
    text = "".join(d["text"] for n, d in events if n == "token")
    assert text == "L'atlas est chez le relieur [S1]."


def test_passage_boxes(tmp_path, native_pdf):
    with Library(tmp_path / "lib") as lib:
        r = lib.import_file(native_pdf)
        hit = search.search(lib.con, "relieur")[0]
        box = lib.passage_boxes(hit.passage_id)
        assert box["unit_id"] == hit.unit_id and box["width"] == 595.0
        assert box["rects"]
        for x0, y0, x1, y1 in box["rects"]:
            assert 0 <= x0 < x1 <= 595 and 0 <= y0 < y1 <= 842
        # an edited unit keeps whatever words still match
        lib.set_unit_text(hit.unit_id, "Texte entièrement réécrit.")
        new = search.search(lib.con, "réécrit")[0]
        assert lib.passage_boxes(new.passage_id)["rects"] == []
        with pytest.raises(KeyError):
            lib.passage_boxes(99999)


def test_passage_boxes_api(stack, native_pdf):  # noqa: F811
    _, port, _ = stack
    wait(port, call(port, "POST", "/api/import", {"paths": [str(native_pdf)]})[1]["job"])
    _, res, _ = call(port, "GET", "/api/search?q=relieur&mode=kw")
    s, box, _ = call(port, "GET", f"/api/passages/{res['hits'][0]['passage_id']}/boxes")
    assert s == 200 and box["rects"]
    assert call(port, "GET", "/api/passages/99999/boxes")[0] == 404
