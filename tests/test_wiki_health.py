"""위키 상태 리포트(wiki_health) — LLM 호출 없음."""

import json
import os
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import web
import wiki_health

OLD = time.mktime((2026, 6, 1, 12, 0, 0, 0, 0, -1))
NEW = time.mktime((2026, 8, 30, 12, 0, 0, 0, 0, -1))


def _wiki(tmp_path: Path) -> Path:
    raw, wiki = tmp_path / "raw", tmp_path / "wiki"
    (raw / "sub").mkdir(parents=True)
    (wiki / "projects" / "a").mkdir(parents=True)
    (wiki / "projects" / "b").mkdir(parents=True)
    (raw / "covered.md").write_text("원본 1")
    (raw / "uncovered.md").write_text("원본 2")
    (raw / "sub" / "mentioned.md").write_text("원본 3")
    (raw / "README.md").write_text("무시")
    (wiki / "index.md").write_text("---\ntitle: 색인\n---\n- [[p1]] — 첫 페이지\n- [[overview]] — 모호\n"
                                   "- [[b/overview]] — 경로 끝 일치\n")
    (wiki / "p1.md").write_text("---\ntitle: P1\nsources:\n  - raw/covered.md\nupdated: 2026-07-01\n---\n"
                                "[[p2]] [[nope]] [[p1]]")
    (wiki / "p2.md").write_text("---\ntitle: P2\n---\n본문 근거는 raw/sub/mentioned.md 참고")
    (wiki / "p3.md").write_text("---\ntitle: P3\nsources: [covered]\n---\n아무도 링크하지 않음")
    (wiki / "projects" / "a" / "overview.md").write_text("a")
    (wiki / "projects" / "b" / "overview.md").write_text("b [[p1]]")
    os.utime(raw / "covered.md", (NEW, NEW))                 # p1 updated(07-01)보다 나중에 바뀐 원본
    os.utime(raw / "uncovered.md", (OLD, OLD))
    return tmp_path


def test_health_report_flags_coverage_freshness_and_links(tmp_path):
    r = wiki_health.health(str(_wiki(tmp_path)))
    assert r["layout"] is True and r["raw_total"] == 3 and r["page_total"] == 3
    assert [u["rel"] for u in r["uncovered"]] == ["raw/uncovered.md"]            # sources·본문 언급은 반영으로
    assert r["stale"] == [{"page": "p1", "updated": "2026-07-01",
                           "newer_sources": [{"rel": "raw/covered.md", "modified": "2026-08-30"}]}]
    assert r["broken_links"] == [{"page": "p1", "target": "nope"}]
    assert r["ambiguous_links"] == [{"page": "index", "target": "overview",
                                     "candidates": ["projects/a/overview", "projects/b/overview"]}]
    assert r["orphans"] == ["p3"]                                                # 자기 링크는 inbound가 아니다
    assert r["not_in_index"] == ["p2", "p3"]
    assert r["no_sources"] == ["p2"]
    assert r["counts"]["uncovered"] == 1 and r["versioned"] is False


def test_health_on_non_wiki_folder_and_render(tmp_path):
    assert wiki_health.health(str(tmp_path)) == {"layout": False, "root": str(tmp_path)}
    lines = wiki_health.render(wiki_health.health(str(_wiki(tmp_path))))
    assert lines[0].startswith("위키 상태 · 원본 3개") and "git 미관리" in lines[0]
    assert any("raw/uncovered.md" in line for line in lines)


def test_health_skips_symlinks_hidden_and_generated_dirs(tmp_path):
    base = _wiki(tmp_path)
    (base / "raw" / ".cache").mkdir()
    (base / "raw" / ".cache" / "x.md").write_text("숨김")
    (base / "raw" / "node_modules").mkdir()
    (base / "raw" / "node_modules" / "y.md").write_text("생성")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("밖")
    (base / "raw" / "linked.md").symlink_to(outside)
    assert wiki_health.health(str(base))["raw_total"] == 3


def test_health_api_route(tmp_path):
    base = _wiki(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/api/health?dir={urllib.request.quote(str(base))}"
        with urllib.request.urlopen(url) as resp:
            body = json.loads(resp.read())
        assert body["counts"]["uncovered"] == 1
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/api/health")
            raise AssertionError("dir 없이 200")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown()


def test_home_renders_health_and_hands_off_to_incremental_update():
    static = Path(__file__).parents[1] / "src" / "static"
    js, html, css = ((static / n).read_text() for n in ("home.js", "index.html", "home.css"))
    assert 'id="homeHealth"' in html and "if (data.has_wiki) loadHomeHealth(data.root);" in js
    # 예전 대시보드로 넘기지 않고 시작 화면 안에서 증분 갱신을 시작·표시한다
    assert "function startHomeUpdate(source)" in js and "startHomeJob('incremental', {source_file: source" in js
    assert "incrementalView(job.result, job.id)" in js and "wikiopt:update-committed" in js
    assert "showView('opt')" not in js.split("function startHomeUpdate")[1].split("document.addEventListener")[0]
    inc = (static / "incremental.js").read_text()
    assert 'new CustomEvent("wikiopt:update-committed"' in inc
    assert "esc(r.rel)" in js and "esc(r.path)" in js                              # 파일명은 이스케이프
    assert ".home-health {" in css and "#" not in css.split("/* 위키 점검")[1].split("@media")[0].replace("#view-home", "")
