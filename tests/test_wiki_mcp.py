"""위키 MCP 서버(wiki_mcp) — stdio JSON-RPC, 검색·읽기, 질의 기록. LLM 없음."""

import io
import json

import pytest

import wiki_mcp


@pytest.fixture
def wiki(tmp_path):
    files = {
        "raw/h.md": "원본",
        "wiki/index.md": "---\ntitle: 색인\n---\n# 색인\n- [[harness-engineering]] — 에이전트를 둘러싼 실행 환경 설계\n"
                         "- [[overview]] — 겹치는 이름\n",
        "wiki/harness-engineering.md": "---\ntitle: 하네스 엔지니어링\nsources: [raw/h.md]\n---\n"
                                      "# 하네스 엔지니어링\n하네스는 모델 바깥의 도구·검증·기억 장치를 묶은 실행 환경이다.\n",
        "wiki/vibe-coding.md": "---\ntitle: 바이브 코딩\n---\n자연어로 지시하고 결과만 확인하는 개발 방식.\n",
        "wiki/projects/a/overview.md": "# A 개요\n프로젝트 A.\n",
        "wiki/projects/b/overview.md": "# B 개요\n프로젝트 B.\n",
        "wiki/.hidden/secret.md": "숨김",
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return wiki_mcp.Wiki(str(tmp_path), log_enabled=True)


def _call(w, name, **arguments):
    reply = wiki_mcp.handle(w, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                "params": {"name": name, "arguments": arguments}})
    return reply["result"]


def test_handshake_and_tool_list(wiki):
    init = wiki_mcp.handle(wiki, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "t", "version": "0"}}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert wiki_mcp.handle(wiki, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = wiki_mcp.handle(wiki, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["wiki_index", "wiki_search", "wiki_read"]
    assert wiki_mcp.handle(wiki, {"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601


def test_index_uses_reader_descriptions_and_skips_hidden(wiki):
    text = _call(wiki, "wiki_index")["content"][0]["text"]
    assert "harness-engineering.md — 하네스 엔지니어링: 에이전트를 둘러싼 실행 환경 설계" in text
    assert "vibe-coding.md — 바이브 코딩: 바이브 코딩" in text      # 색인에 없으면 title
    assert "projects/a/overview.md" in text and "secret" not in text


def test_search_matches_korean_with_particles(wiki):
    res = _call(wiki, "wiki_search", query="하네스를 어떻게 설계하나요?")
    text = res["content"][0]["text"]
    assert res["isError"] is False
    assert text.splitlines()[1].startswith("- harness-engineering.md")
    assert "모델 바깥의 도구" in text


def test_read_resolves_names_links_and_refuses_escape(wiki):
    assert "하네스는 모델 바깥" in _call(wiki, "wiki_read", path="harness-engineering")["content"][0]["text"]
    assert "프로젝트 A" in _call(wiki, "wiki_read", path="[[a/overview]]")["content"][0]["text"]
    assert "프로젝트 A" in _call(wiki, "wiki_read", path="wiki/projects/a/overview.md")["content"][0]["text"]
    amb = _call(wiki, "wiki_read", path="overview")
    assert amb["isError"] and "projects/a/overview" in amb["content"][0]["text"]
    for bad in ("../raw/h", "missing-page", ""):
        assert _call(wiki, "wiki_read", path=bad)["isError"]


def test_queries_are_logged_without_page_text(wiki):
    _call(wiki, "wiki_search", query="바이브 코딩이란")
    _call(wiki, "wiki_search", query="바이브 코딩이란")
    _call(wiki, "wiki_read", path="vibe-coding")
    _call(wiki, "wiki_read", path="missing-page")
    lines = [json.loads(x) for x in open(wiki.log_path, encoding="utf-8")]
    assert [x["tool"] for x in lines] == ["wiki_search", "wiki_search", "wiki_read", "wiki_read"]
    assert lines[0]["results"][0] == "vibe-coding" and {x["session"] for x in lines} == {wiki.session}
    assert lines[2] == {**lines[2], "path": "vibe-coding", "found": True} and lines[3]["found"] is False
    assert "자연어로 지시" not in open(wiki.log_path, encoding="utf-8").read()
    [row] = wiki_mcp.query_summary(wiki.log_path)
    assert row["query"] == "바이브 코딩이란" and row["count"] == 2


def test_logging_can_be_turned_off(tmp_path, wiki):
    quiet = wiki_mcp.Wiki(wiki.root, log_path=str(tmp_path / "q.jsonl"), log_enabled=False)
    _call(quiet, "wiki_search", query="하네스")
    assert not (tmp_path / "q.jsonl").exists()


def test_stdio_loop_replies_line_by_line(wiki):
    stdin = io.StringIO("\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "{not json",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        json.dumps([{"jsonrpc": "2.0", "id": 3, "method": "ping"}]),
    ]) + "\n")
    stdout = io.StringIO()
    wiki_mcp.serve(wiki, stdin, stdout)
    replies = [json.loads(x) for x in stdout.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2, None]
    assert replies[0]["result"]["protocolVersion"] == "2099-01-01"
    assert replies[1]["error"]["code"] == -32700 and replies[2]["result"] == {}
    assert replies[3]["error"]["code"] == -32600


def test_original_question_groups_rephrased_queries(wiki):
    _call(wiki, "wiki_search", query="하네스 실행 환경", question="하네스가 뭐야?")
    _call(wiki, "wiki_search", query="하네스 정의", question="하네스가 뭐야?")
    _call(wiki, "wiki_search", query="바이브 코딩")
    rows = wiki_mcp.query_summary(wiki.log_path)
    assert rows[0] == {**rows[0], "query": "하네스가 뭐야?", "count": 2,
                       "rephrased": ["하네스 실행 환경", "하네스 정의"]}
    assert rows[1]["query"] == "바이브 코딩" and rows[1]["rephrased"] == []
