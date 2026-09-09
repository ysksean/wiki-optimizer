"""근거 탐색(evidence.py) + evolve-informed arm — LLM 호출 없음.

설계 문서(2026-09-04 agent-reflector) 2단계: Reflector에 원본 근거 문단·실패 유형·구조 결함을
준다. 기대 답은 어디에도 노출하지 않는다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import batch  # noqa: E402
import evidence  # noqa: E402
import evolve_structure  # noqa: E402
import structure  # noqa: E402


DOCS = {
    "redis": "# Redis 통신\n\n요청 큐와 응답 큐를 분리한다.\n\n## 자료구조\n\n유실되면 안 되는 작업은 Stream을 쓴다. List는 단순 큐.",
    "claude": "# CLAUDE.md\n\n프로젝트 루트의 CLAUDE.md는 그 프로젝트에만 적용된다.\n\n## Skills\n\nSkills는 설명만 올라가고 쓸 때만 전체가 로드된다.",
}
STRUCT = {"files": [
    {"title": "인프라", "content": "...", "sources": ["redis"]},
    {"title": "도구", "content": "...", "sources": ["claude"]},
]}


def test_split_paragraphs_keeps_heading_context():
    paras = evidence.split_paragraphs(DOCS["redis"])
    assert [p["heading"] for p in paras] == ["Redis 통신", "자료구조"]
    assert paras[1]["text"].startswith("유실되면")


def test_find_evidence_ranks_paragraph_matching_the_answer_and_hides_it():
    hits = evidence.find_evidence(DOCS, "유실되면 안 되는 작업엔 어떤 자료구조?", "Stream을 사용한다")
    assert hits and hits[0]["doc"] == "redis" and hits[0]["heading"] == "자료구조"
    assert "Stream" in hits[0]["snippet"]
    assert all("expected" not in h for h in hits)      # 기대 답 원문은 결과에 없다
    assert evidence.find_evidence(DOCS, "", "") == []


def test_classify_failure_types():
    hit = [{"doc": "redis", "heading": "", "snippet": "", "score": 3}]
    assert evidence.classify_failure({"picked": ["도구"]}, STRUCT, hit) == ("routing_miss", ["인프라"])
    assert evidence.classify_failure({"picked": ["인프라"]}, STRUCT, hit) == ("content_lost", ["인프라"])
    dropped = {"files": [{"title": "도구", "content": "x", "sources": ["claude"]}]}
    assert evidence.classify_failure({"picked": ["도구"]}, dropped, hit) == ("doc_dropped", [])
    assert evidence.classify_failure({"picked": ["도구"]}, STRUCT, []) == ("no_evidence", [])


def test_structure_warnings_flag_dropped_docs_and_bad_files():
    struct = {"files": [{"title": "A", "content": "", "sources": []},
                        {"title": "A", "content": "x", "sources": ["redis"]}]}
    warnings = evidence.structure_warnings(DOCS, struct)
    joined = "\n".join(warnings)
    assert "'claude'" in joined and "본문이 비어" in joined and "sources가 없음" in joined and "중복" in joined
    assert evidence.structure_warnings(DOCS, STRUCT) == []


def test_enrich_only_failed_questions_and_render_has_no_answer():
    qs = [{"q": "유실되면 안 되는 작업엔 어떤 자료구조?", "a": "Stream을 사용한다"},
          {"q": "CLAUDE.md는 어디에?", "a": "프로젝트 루트"}]
    details = [{"q": qs[0]["q"], "picked": ["도구"], "score": 0},
               {"q": qs[1]["q"], "picked": ["도구"], "score": 1}]
    enriched = evidence.enrich(details, qs, DOCS, STRUCT)
    assert len(enriched) == 1 and enriched[0]["type"] == "routing_miss" and enriched[0]["holders"] == ["인프라"]
    block, warn = evidence.render_for_reflector(enriched, ["문서 'x'가 어느 파일의 sources에도 없음"])
    assert "Router가 엉뚱한 파일" in block and "[redis › 자료구조]" in block
    assert "Stream을 사용한다" not in block       # 기대 답 문장은 프롬프트에 없다
    assert warn.startswith("- 문서 'x'")


@pytest.fixture
def docs_env(tmp_path):
    files = []
    for name, text in DOCS.items():
        p = tmp_path / f"{name}.md"
        p.write_text(text)
        files.append(str(p))
    return {"files": files, "out": str(tmp_path / "runs")}


def _score(qs):
    return {"total": 0.5, "accuracy": 0.5, "efficiency": 1.0, "avg_read": 10, "n_files": 2,
            "parse_failed": False,
            "details": [{"q": qa["q"], "picked": ["도구"], "read_chars": 10, "pred": "p", "score": 0}
                        for qa in qs]}


def test_informed_arm_passes_evidence_to_reflect_and_records_it(docs_env, monkeypatch):
    qs = [{"q": f"q{i} 유실 작업 자료구조", "a": "Stream을 사용한다"} for i in range(6)]
    monkeypatch.setattr(structure, "organize", lambda docs, strategy: dict(STRUCT, index=[]))
    monkeypatch.setattr(structure, "score_structure", lambda struct, q, total_raw: _score(q))
    seen = {}

    def fake_reflect(strategy, result, enriched=None, warnings=None):
        seen["enriched"], seen["warnings"] = enriched, warnings
        return "S1"

    monkeypatch.setattr(evolve_structure, "reflect", fake_reflect)
    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=2, out_dir=docs_env["out"],
        question_set=qs, informed=True)
    assert report["arm"] == "evolve-informed"
    assert report["provenance"]["params"]["informed"] is True
    assert seen["enriched"] and seen["enriched"][0]["type"] == "routing_miss"
    h0 = report["history"][0]
    assert h0["evidence"] and h0["heldout_evidence"] and h0["warnings"] == []


def test_plain_evolve_arm_records_evidence_but_reflect_does_not_see_it(docs_env, monkeypatch):
    qs = [{"q": f"q{i} 유실 작업 자료구조", "a": "Stream을 사용한다"} for i in range(6)]
    monkeypatch.setattr(structure, "organize", lambda docs, strategy: dict(STRUCT, index=[]))
    monkeypatch.setattr(structure, "score_structure", lambda struct, q, total_raw: _score(q))
    calls = []
    monkeypatch.setattr(evolve_structure, "reflect", lambda s, r: calls.append(r) or "S1")
    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=2, out_dir=docs_env["out"], question_set=qs)
    assert report["arm"] == "evolve" and len(calls) == 1
    assert report["history"][0]["evidence"]         # 기록은 남긴다 (UI·비교용)


def test_batch_structure_arms_accept_informed():
    assert batch.resolve_arms(["control", "evolve", "evolve-informed"], stage="structure") == \
        ["control", "evolve", "evolve-informed"]
    with pytest.raises(ValueError):
        batch.resolve_arms(["evolve-wiki"], stage="structure")
    with pytest.raises(ValueError):
        batch.resolve_arms(["evolve-informed"], stage="summary")
