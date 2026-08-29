"""LLM 없이 검증 가능한 순수 로직 테스트.

LLM이 필요한 경로는 monkeypatch로 llm.generate를 canned 응답으로 바꿔 검증한다.
CI에는 claude/codex 로그인이 없으므로 실제 CLI 호출 테스트는 여기 두지 않는다.
"""

import json

import pytest

import apply as apply_mod
import audit
import evolve
import scoring


# ---------- scoring: 파싱 ----------

def test_parse_qa_extracts_valid_items():
    text = 'blah [{"q":"Q1","a":"A1"},{"q":"Q2","a":"A2"},{"bad":1}] tail'
    qa = scoring._parse_qa(text)
    assert qa == [{"q": "Q1", "a": "A1"}, {"q": "Q2", "a": "A2"}]


def test_parse_qa_malformed_returns_empty():
    assert scoring._parse_qa("no json here") == []
    assert scoring._parse_qa("[{broken") == []


def test_parse_str_list_pads_and_truncates():
    assert scoring._parse_str_list('["a","b"]', 3) == ["a", "b", "모름"]
    assert scoring._parse_str_list('["a","b","c","d"]', 2) == ["a", "b"]
    assert scoring._parse_str_list("garbage", 2) == ["모름", "모름"]


# ---------- scoring: 효율 ----------

def test_efficiency_basic_and_floor():
    raw = "x" * 1000
    eff, ratio = scoring.efficiency(raw, "x" * 500)
    assert ratio == 0.5 and eff == 0.5
    # floor(0.10)보다 짧아도 효율 상한은 0.9에서 고정
    eff_short, _ = scoring.efficiency(raw, "x" * 10)
    assert eff_short == pytest.approx(0.9)
    # 원본보다 길면 0
    eff_long, _ = scoring.efficiency(raw, "x" * 2000)
    assert eff_long == 0.0


def test_score_combines_multiplicatively(monkeypatch):
    qs = [{"q": "Q1", "a": "A1"}, {"q": "Q2", "a": "A2"}]
    responses = iter(['["A1","wrong"]', "[1,0]"])
    monkeypatch.setattr(scoring.llm, "generate", lambda *a, **k: next(responses))
    result = scoring.score("x" * 1000, "x" * 500, qs)
    assert result["accuracy"] == 0.5
    assert result["efficiency"] == 0.5
    assert result["total"] == 0.25


# ---------- evolve: held-out 분할 ----------

def test_split_questions_deterministic_and_disjoint():
    qs = [{"q": f"q{i}", "a": f"a{i}"} for i in range(8)]
    t1, h1 = evolve.split_questions(qs, "docname")
    t2, h2 = evolve.split_questions(qs, "docname")
    assert t1 == t2 and h1 == h2  # 같은 문서명 = 같은 분할
    keys = lambda xs: {x["q"] for x in xs}
    assert keys(t1) & keys(h1) == set()  # 겹치지 않음
    assert len(h1) >= 2 and len(t1) + len(h1) == 8


def test_split_questions_tiny_set_falls_back():
    qs = [{"q": "q0", "a": "a0"}, {"q": "q1", "a": "a1"}]
    train, holdout = evolve.split_questions(qs, "doc")
    # 너무 적으면 분리 포기 (전부 양쪽 겸용)
    assert {x["q"] for x in train} == {x["q"] for x in holdout} == {"q0", "q1"}


# ---------- audit: raw<->wiki 짝 맞추기 ----------

def test_find_pairs_matches_by_name(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "raw" / "a.md").write_text("raw a")
    (tmp_path / "raw" / "b.md").write_text("raw b")
    (tmp_path / "raw" / "README.md").write_text("skip me")
    (tmp_path / "wiki" / "a.md").write_text("wiki a")
    pairs = audit.find_pairs(str(tmp_path))
    assert [p["name"] for p in pairs] == ["a", "b"]
    assert pairs[0]["wiki"] is not None and pairs[1]["wiki"] is None


def test_find_pairs_without_raw_subdir(tmp_path):
    (tmp_path / "x.md").write_text("doc")
    pairs = audit.find_pairs(str(tmp_path))
    assert [p["name"] for p in pairs] == ["x"]
    assert pairs[0]["wiki"] is None


# ---------- apply: best 전략 선택 ----------

def test_best_strategy_excludes_structure_reports(tmp_path):
    a = tmp_path / "run-a"
    b = tmp_path / "run-b"
    a.mkdir()
    b.mkdir()
    (a / "report.json").write_text(json.dumps(
        {"best": {"strategy": "summary strategy", "total": 0.3}}))
    (b / "report.json").write_text(json.dumps(
        {"best": {"strategy": "split strategy", "total": 0.9, "struct": {"files": []}}}))
    best = apply_mod.best_strategy_from_runs(runs_dir=str(tmp_path))
    # B단계(struct) 결과는 요약 전략이 아니므로 점수가 높아도 제외
    assert best["strategy"] == "summary strategy"


def test_best_strategy_none_when_empty(tmp_path):
    assert apply_mod.best_strategy_from_runs(runs_dir=str(tmp_path)) is None


# ---------- evolve: 영속 전략 이력 (WikiSkill 스타일) ----------

def _fake_result(total, ratio):
    return {"total": total, "length_ratio": ratio}


def test_strategy_impact_roundtrip_filters_doc_and_arm(tmp_path, monkeypatch):
    monkeypatch.setattr(evolve, "WIKI_DIR", str(tmp_path))
    monkeypatch.setattr(evolve, "IMPACT_PATH", str(tmp_path / "strategy-impact.jsonl"))
    evolve.record_strategy_impact("docA", "evolve", 0, "s-accepted", _fake_result(0.3, 0.5), True)
    evolve.record_strategy_impact("docA", "evolve", 1, "s-rejected", _fake_result(0.0, 1.1), False)
    evolve.record_strategy_impact("docA", "control", 0, "s-control", _fake_result(0.2, 0.5), True)
    evolve.record_strategy_impact("docB", "evolve", 0, "s-other-doc", _fake_result(0.4, 0.5), True)

    accepted, rejected = evolve.load_strategy_history("docA")
    assert [e["strategy"] for e in accepted] == ["s-accepted"]  # control/타문서 제외
    assert [e["strategy"] for e in rejected] == ["s-rejected"]


def test_history_block_warns_runaway_ratio(tmp_path, monkeypatch):
    monkeypatch.setattr(evolve, "WIKI_DIR", str(tmp_path))
    monkeypatch.setattr(evolve, "IMPACT_PATH", str(tmp_path / "strategy-impact.jsonl"))
    evolve.record_strategy_impact("doc", "evolve", 1, "길게 쓰는 전략", _fake_result(0.0, 1.1), False)
    block = evolve._history_block("doc")
    assert "기각된 전략" in block and "길게 쓰는 전략" in block
    assert "ratio ≥ 1.0" in block  # 폭주 경고
    # 이력이 없으면 빈 문자열
    assert evolve._history_block("unknown-doc") == ""


def test_nohist_arm_does_not_pollute_history(tmp_path, monkeypatch):
    monkeypatch.setattr(evolve, "WIKI_DIR", str(tmp_path))
    monkeypatch.setattr(evolve, "IMPACT_PATH", str(tmp_path / "strategy-impact.jsonl"))
    evolve.record_strategy_impact("doc", "evolve-nohist", 0, "s-nohist", _fake_result(0.5, 0.5), True)
    accepted, rejected = evolve.load_strategy_history("doc")
    assert accepted == [] and rejected == []  # nohist 기록은 이력 조회에서 제외


def test_reflect_prompt_includes_history(tmp_path, monkeypatch):
    monkeypatch.setattr(evolve, "WIKI_DIR", str(tmp_path))
    monkeypatch.setattr(evolve, "IMPACT_PATH", str(tmp_path / "strategy-impact.jsonl"))
    evolve.record_strategy_impact("doc", "evolve", 1, "REJECTED-DIRECTION", _fake_result(0.0, 1.2), False)

    captured = {}
    def fake_generate(prompt, **kw):
        captured["prompt"] = prompt
        return "새 전략"
    monkeypatch.setattr(evolve.llm, "generate", fake_generate)

    train_result = {"qa_details": [], "accuracy": 1.0, "length_ratio": 0.4}
    out = evolve.reflect("현재 전략", train_result, doc="doc")
    assert out == "새 전략"
    assert "REJECTED-DIRECTION" in captured["prompt"]
    # doc 없이 부르면 이력 미포함 (하위 호환)
    evolve.reflect("현재 전략", train_result)
    assert "REJECTED-DIRECTION" not in captured["prompt"]
