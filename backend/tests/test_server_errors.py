"""Failures of the model server must not look like "the documents do not answer"."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from iak import answer, search, servers
from iak.library import Library

OOM = "decode() failed: vk::Device::allocateMemory: ErrorOutOfDeviceMemory"


def stream_server(lines):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for line in lines:
                self.wfile.write(line.encode() + b"\n\n")
            self.close_connection = True

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data": [{"id": "m"}]}')

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def hits(tmp_path, native_pdf):
    with Library(tmp_path / "lib") as lib:
        lib.import_file(native_pdf)
        yield search.search(lib.con, "relieur atlas")


@pytest.mark.parametrize("lines", [
    ['data: {"choices":[{"delta":{"content":"L"}}]}',
     'data: {"error": {"code": 500, "message": "%s"}}' % OOM],
    ['error: {"code": 500, "message": "%s"}' % OOM],
])
def test_stream_error_is_raised_with_hint(hits, lines):
    srv = stream_server(lines)
    try:
        client = servers.Client(f"http://127.0.0.1:{srv.server_port}")
        with pytest.raises(servers.ServerError) as err:
            list(answer.ask_stream("Où ?", hits, client))
        assert "ErrorOutOfDeviceMemory" in str(err.value)
        assert "graphics memory" in str(err.value)
    finally:
        srv.shutdown()


def test_empty_stream_is_an_error_not_no_answer(hits):
    srv = stream_server(['data: {"choices":[{"delta":{}}]}', "data: [DONE]"])
    try:
        client = servers.Client(f"http://127.0.0.1:{srv.server_port}")
        with pytest.raises(servers.ServerError, match="empty answer"):
            list(answer.ask_stream("Où ?", hits, client))
    finally:
        srv.shutdown()


def test_empty_plain_answer_is_an_error(hits):
    class Empty:
        def chat(self, messages, max_tokens=700):
            return "  "
    with pytest.raises(servers.ServerError, match="empty answer"):
        answer.ask("Où ?", hits, Empty())
    # a real NO_ANSWER from the model is still a "no answer"
    class No:
        def chat(self, messages, max_tokens=700):
            return "NO_ANSWER"
    assert answer.ask("Où ?", hits, No()).status == "no_answer"


def test_explain():
    assert "graphics memory" in servers.explain(OOM)
    assert "context" in servers.explain("request (9000 tokens) exceeds the available context size")
    assert servers.explain("something else") == "something else"


def test_stream_error_over_api(tmp_path, native_pdf):
    """The interface receives an "error" event with the server's message."""
    import http.client
    from iak import api
    srv = stream_server(['data: {"error": {"message": "%s"}}' % OOM])
    app = api.App(tmp_path / "lib", servers.Host("t", chat=f"http://127.0.0.1:{srv.server_port}/v1"),
                  token="t")
    with app.library() as lib:
        lib.import_file(native_pdf)
    web = api.make_server(app)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", web.server_address[1], timeout=30)
        conn.request("POST", "/api/ask/stream", json.dumps({"question": "atlas relieur", "mode": "kw"}),
                     {"Authorization": "Bearer t", "Content-Type": "application/json"})
        body = conn.getresponse().read().decode()
        assert "event: error" in body and "ErrorOutOfDeviceMemory" in body
        assert "event: done" not in body
    finally:
        web.shutdown()
        srv.shutdown()
