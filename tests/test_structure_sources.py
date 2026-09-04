"""Organizer 출처(sources) 기록 — LLM 호출 없음.

B 구조 결과 카드의 문서→파일 연결선은 이 데이터에 기대므로,
파싱·검증·history 전파를 여기서 고정한다.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import evolve_structure  # noqa: E402
import structure  # noqa: E402


DOCS = {"alpha": "a text", "beta": "b text"}


def test_parse_struct_keeps_only_real_source_names():
    out = json.dumps({"files": [
        {"title": "T1", "content": "c1", "sources": ["alpha", "ghost", "alpha", " beta "]},
        {"title": "T2", "content": "c2"},
        {"title": "T3", "content": "c3", "sources": "alpha"},
    ]})
    files = structure._parse_struct(out, DOCS)["files"]
    assert files[0]["sources"] == ["alpha", "beta"]   # 지어낸 이름 제거, 중복 제거, 공백 정리
    assert files[1]["sources"] == []                  # 누락은 빈 목록
    assert files[2]["sources"] == []                  # 배열이 아니면 무시


def test_parse_struct_without_docs_keeps_sources_unverified():
    out = json.dumps({"files": [{"title": "T", "content": "c", "sources": ["whatever"]}]})
    assert structure._parse_struct(out)["files"][0]["sources"] == ["whatever"]


def test_organize_prompt_asks_for_sources_and_fallback_maps_each_doc_to_itself(monkeypatch):
    prompts = []

    def fake_generate(prompt, **kw):
        prompts.append(prompt)
        return "not json"

    monkeypatch.setattr(structure.llm, "generate", fake_generate)
    struct = structure.organize(DOCS, "strategy")
    assert '"sources"' in prompts[0]
    assert [f["sources"] for f in struct["files"]] == [["alpha"], ["beta"]]


def test_history_entries_carry_per_file_sources(tmp_path, monkeypatch):
    files = []
    for name in ("a", "b"):
        p = tmp_path / f"{name}.md"
        p.write_text(f"{name} " * 50)
        files.append(str(p))
    monkeypatch.setattr(structure, "organize", lambda docs, strategy: {
        "files": [{"title": "merged", "content": "xyz", "sources": ["a", "b"]}],
        "index": [{"title": "merged", "desc": "xyz"}]})
    monkeypatch.setattr(structure, "score_structure", lambda struct, qs, total_raw: {
        "total": 0.5, "accuracy": 0.5, "efficiency": 1.0, "avg_read": 3, "n_files": 1,
        "parse_failed": False, "details": []})
    evolve_structure.evolve_structure(generations=1, files=files, out_dir=str(tmp_path / "runs"),
                                      question_set=[{"q": "q", "a": "a"}])
    report = json.loads(next((tmp_path / "runs").glob("structure-*/report.json")).read_text())
    entry = report["history"][0]
    assert entry["files"] == [{"title": "merged", "sources": ["a", "b"], "n_chars": 3}]
    assert entry["file_titles"] == ["merged"]   # 구버전 필드 유지
    assert entry["details"] == []                # 질문별 판정도 시도마다 남긴다
    progress = json.loads(next((tmp_path / "runs").glob("structure-*/progress.json")).read_text())
    assert progress["total_raw_chars"] > 0 and progress["question_set"] == [{"q": "q", "a": "a"}]
    assert report["best"]["struct"]["files"][0]["sources"] == ["a", "b"]
