"""Follow-up questions and filters on questions."""
from iak import answer, search
from iak.library import Library

from .test_api import call, stack, wait  # noqa: F401


def test_clean_history_limits_and_strips_citations():
    h = [{"question": f"q{i}", "answer": f"a{i} [S1][S2, S3]."} for i in range(6)]
    h.append("not a turn")
    out = answer.clean_history(h)
    assert [t["question"] for t in out] == ["q3", "q4", "q5"]   # last 3 dicts
    assert out[-1]["answer"] == "a5 ."
    assert answer.clean_history([{"question": " ", "answer": "x"}]) == []
    long = answer.clean_history([{"question": "q", "answer": "x" * 5000}])
    assert len(long[0]["answer"]) == answer.HISTORY_ANSWER_CHARS
    assert answer.clean_history([{"question": "q", "answer": ""}])[0]["answer"] == answer.NO_ANSWER


def test_messages_with_history():
    src = [answer.Source("S1", 1, 1, "d.pdf", "p. 1", "texte")]
    msgs = answer.build_messages("Et quand ?", src, [{"question": "Qui ?", "answer": "Le relieur [S1]."}])
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "Qui ?" and msgs[2]["content"] == "Le relieur ."
    assert "cite only the passages above" in msgs[-1]["content"]
    assert len(answer.build_messages("x", src)) == 2


def test_followup_retrieval_finds_earlier_subject(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("# Atelier\n\nLe relieur répare l'atlas abîmé.\n\n"
                 "# Calendrier\n\nIl le rendit au mois de mars.\n", encoding="utf-8")
    with Library(tmp_path / "lib") as lib:
        lib.import_file(p)
        q = answer.retrieval_query("Et le relieur ?", [{"question": "Où est l'atlas ?", "answer": "x"}])
        assert q == "Où est l'atlas ? Et le relieur ?"
        alone = search.search(lib.con, "Et le relieur ?")
        both = answer.fuse(alone, search.search(lib.con, q))
        assert {h.label for h in both} >= {h.label for h in alone}
        assert answer.retrieval_query("x", []) is None


def test_fuse_orders_by_rank():
    from iak.search import Hit
    h = lambda i: Hit(i, 1, i, "d", "p", "t", "", 0.0)
    out = answer.fuse([h(1), h(2), h(3)], [h(3), h(4)])
    assert [x.passage_id for x in out][:2] == [3, 1]       # found by both first


def test_api_history_and_filters(stack, native_pdf, docx_file):  # noqa: F811
    app, port, chat = stack
    job = wait(port, call(port, "POST", "/api/import",
                          {"paths": [str(native_pdf), str(docx_file)]})[1]["job"])
    pdf, docx = [f["doc_id"] for f in job["result"]["files"]]
    s, res, _ = call(port, "POST", "/api/ask", {
        "question": "Et quand ?", "doc_ids": [pdf],
        "history": [{"question": "Où est l'atlas ?", "answer": "Chez le relieur [S1]."}]})
    assert s == 200
    req = chat.requests[-1]
    roles = [m["role"] for m in req["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert {x["doc_id"] for x in res["sent"]} == {pdf}           # filter applied
    s, res, _ = call(port, "POST", "/api/ask", {"question": "atlas", "kinds": ["docx"]})
    assert {x["doc_id"] for x in res["sent"]} == {docx}
    assert call(port, "POST", "/api/ask", {"question": "x", "doc_ids": ["1; drop"]})[0] == 400
