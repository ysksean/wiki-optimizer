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
import structure


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
    assert result["parse_failed"] is False


# ---------- scoring: judge 파싱 — 실패와 오답을 구분 ----------

@pytest.mark.parametrize("text,n,expected", [
    ("[1,0,1]", 3, [1.0, 0.0, 1.0]),
    ("판정 배열: [1, 0, 1]", 3, [1.0, 0.0, 1.0]),
    ("[1.0, 0.0, 1.0]", 3, [1.0, 0.0, 1.0]),            # JSON float 0/1은 허용
    ("[1, 0, 1] 설명: 2번은 [참고]와 다름 ]", 3, [1.0, 0.0, 1.0]),  # greedy `\[.*\]`면 뒤 `]`까지 번짐
    ("항목 [1]만 봄. 판정: [0,1,1]", 3, [0.0, 1.0, 1.0]),  # 첫 대괄호가 아니어도 개수 맞는 블록을 찾는다
    ("[1 0 1]", 3, [1.0, 0.0, 1.0]),                    # JSON은 아니지만 토큰이 딱 맞음
])
def test_parse_judgement_accepts_clean_arrays(text, n, expected):
    assert scoring.parse_judgement(text, n) == expected


@pytest.mark.parametrize("text,n", [
    ("[10, 0.5, 1]", 3),      # `10`·`0.5` 안의 0/1 문자를 긁으면 안 됨
    ("[1,0]", 3),             # 항목 부족 — 0으로 메우지 않는다
    ("[1,0,1,1]", 3),         # 항목 초과
    ("판정 불가", 3),
    ("", 3),
    ("[true,false,true]", 3),
])
def test_parse_judgement_rejects_noise(text, n):
    assert scoring.parse_judgement(text, n) is None


def test_judge_all_retries_once_then_succeeds(monkeypatch):
    qs = [{"q": "Q1", "a": "A1"}, {"q": "Q2", "a": "A2"}]
    responses = iter(["판정: 어렵네요", "[1,0]"])
    calls = []
    monkeypatch.setattr(scoring.llm, "generate",
                        lambda p, **k: (calls.append(p), next(responses))[1])
    scores, failed = scoring.judge_all(qs, ["A1", "wrong"])
    assert (scores, failed) == ([1.0, 0.0], False)
    assert len(calls) == 2 and calls[0] == calls[1]  # 같은 프롬프트로 1회 재요청


def test_judge_all_marks_parse_failed_after_retry(monkeypatch):
    qs = [{"q": "Q1", "a": "A1"}, {"q": "Q2", "a": "A2"}]
    responses = iter(["garbage", "[10, 0.5]"])
    monkeypatch.setattr(scoring.llm, "generate", lambda *a, **k: next(responses))
    scores, failed = scoring.judge_all(qs, ["A1", "wrong"])
    assert failed is True
    assert scores == [0.0, 0.0]  # 자리만 채움 — 호출자가 parse_failed로 걸러야 한다


def test_score_propagates_parse_failed(monkeypatch):
    qs = [{"q": "Q1", "a": "A1"}, {"q": "Q2", "a": "A2"}]
    responses = iter(['["A1","A2"]', "no array", "still no array"])
    monkeypatch.setattr(scoring.llm, "generate", lambda *a, **k: next(responses))
    result = scoring.score("x" * 1000, "x" * 500, qs)
    assert result["parse_failed"] is True
    assert result["accuracy"] == 0.0  # 0점이지만 parse_failed로 구분된다


# ---------- structure: 공용 judge 사용 + parse_failed 전파 ----------

def test_score_structure_uses_shared_judge_and_flags_parse_failure(monkeypatch):
    struct = {"files": [{"title": "T", "content": "c" * 50}],
              "index": [{"title": "T", "desc": "c"}]}
    qs = [{"q": "q1", "a": "a1"}, {"q": "q2", "a": "a2"}]
    monkeypatch.setattr(structure, "route", lambda q, idx: [0])
    monkeypatch.setattr(structure, "_answer", lambda ctx, q: "pred")
    monkeypatch.setattr(scoring, "judge_all", lambda qs_, preds: ([0.0, 0.0], True))
    result = structure.score_structure(struct, qs, total_raw_chars=100)
    assert result["parse_failed"] is True and result["accuracy"] == 0.0

    monkeypatch.setattr(scoring, "judge_all", lambda qs_, preds: ([1.0, 0.0], False))
    result = structure.score_structure(struct, qs, total_raw_chars=100)
    assert result["parse_failed"] is False and result["accuracy"] == 0.5


# ---------- evolve: parse_failed 세대는 best·영속 이력에서 제외 ----------

def test_evolve_excludes_parse_failed_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(evolve, "WIKI_DIR", str(tmp_path / "wiki"))
    monkeypatch.setattr(evolve, "IMPACT_PATH", str(tmp_path / "wiki" / "impact.jsonl"))
    raw = tmp_path / "doc.md"
    raw.write_text("x" * 100)
    qs = [{"q": f"q{i}", "a": f"a{i}"} for i in range(4)]  # train 2 / held-out 2

    # gen0: held-out 판정 파싱 실패인데 total은 (오염 상황을 흉내내) 높게.
    # gen1: 정상 채점, 낮은 점수. → best는 gen1이어야 한다.
    plan = iter([
        {"total": 0.4, "accuracy": 0.5, "efficiency": 0.8, "length_ratio": 0.2,
         "parse_failed": False, "qa_details": []},                      # gen0 train
        {"total": 0.9, "accuracy": 1.0, "efficiency": 0.9, "length_ratio": 0.1,
         "parse_failed": True, "qa_details": []},                       # gen0 held-out (실패)
        {"total": 0.4, "accuracy": 0.5, "efficiency": 0.8, "length_ratio": 0.2,
         "parse_failed": False, "qa_details": []},                      # gen1 train
        {"total": 0.5, "accuracy": 0.5, "efficiency": 1.0, "length_ratio": 0.1,
         "parse_failed": False, "qa_details": []},                      # gen1 held-out
    ])
    monkeypatch.setattr(evolve.scoring, "score", lambda *a, **k: next(plan))
    monkeypatch.setattr(evolve, "summarize", lambda raw_text, strategy: "요약")
    reflected = []
    def fake_reflect(strategy, train_result, doc=None):
        reflected.append(strategy)
        return strategy + "+"
    monkeypatch.setattr(evolve, "reflect", fake_reflect)

    report = evolve.evolve(str(raw), generations=2, out_dir=str(tmp_path / "runs"),
                           question_set=qs)

    assert report["best"]["generation"] == 1 and report["best"]["total"] == 0.5
    assert report["parse_failed"] is True
    assert report["parse_failed_generations"] == [0]
    assert report["history"][0]["score"]["parse_failed"] is True
    # gen0에는 유효한 best가 없으므로 reflect 없이 seed 전략을 그대로 재시도
    assert reflected == []
    # 영속 이력에는 실패 세대가 기록되지 않는다 (reflect 오염 방지)
    accepted, rejected = evolve.load_strategy_history("doc")
    assert [e["generation"] for e in accepted] == [1] and rejected == []


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


def test_score_structure_parallel_preserves_question_order(monkeypatch):
    """질문별 route→answer가 병렬이어도 details/preds는 질문 순서를 지킨다."""
    import time as _time
    struct = {"files": [{"title": "T", "content": "c" * 50}],
              "index": [{"title": "T", "desc": "c"}]}
    qs = [{"q": f"q{i}", "a": f"a{i}"} for i in range(6)]
    monkeypatch.setattr(structure, "route", lambda q, idx: [0])

    def slow_first_answer(ctx, q):
        # 앞 질문일수록 늦게 끝나게 해 완료 순서를 뒤집는다
        _time.sleep((6 - int(q[1:])) * 0.01)
        return f"pred-{q}"

    monkeypatch.setattr(structure, "_answer", slow_first_answer)
    captured = {}

    def spy_judge(qs_, preds):
        captured["preds"] = list(preds)
        return [1.0] * len(qs_), False

    monkeypatch.setattr(scoring, "judge_all", spy_judge)
    result = structure.score_structure(struct, qs, total_raw_chars=100)
    assert [d["q"] for d in result["details"]] == [f"q{i}" for i in range(6)]
    assert captured["preds"] == [f"pred-q{i}" for i in range(6)]


# ---------- audit: 백링크 그래프 + Router 진단 ----------

def _make_wiki(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "projects").mkdir()
    (tmp_path / "raw" / "doc1.md").write_text("원본 doc1")
    (tmp_path / "wiki" / "hub.md").write_text("# Hub\n[[leaf]] 그리고 [[leaf|별칭]] [[missing]]")
    (tmp_path / "wiki" / "leaf.md").write_text("# Leaf\n[[hub#섹션]] 내용")
    (tmp_path / "wiki" / "orphan.md").write_text("# Orphan\n링크 없음")
    (tmp_path / "wiki" / "projects" / "secret.md").write_text("고객 정보 [[hub]]")
    return tmp_path


def test_parse_links_normalizes_alias_and_anchor():
    links = audit.parse_links("[[a]] [[a|label]] [[b#sec]] [[ c ]]")
    assert links == ["a", "b", "c"]  # 중복 제거, 별칭/앵커 제거


def test_wiki_pages_excludes_projects(tmp_path):
    _make_wiki(tmp_path)
    names = [p["name"] for p in audit.wiki_pages(str(tmp_path))]
    assert "secret" not in names and set(names) == {"hub", "leaf", "orphan"}


def test_build_graph_counts_inlinks_and_drops_missing(tmp_path):
    _make_wiki(tmp_path)
    g = audit.build_graph(audit.wiki_pages(str(tmp_path)))
    edges = {(e["source"], e["target"]) for e in g["edges"]}
    assert edges == {("hub", "leaf"), ("leaf", "hub")}  # missing/자기링크 제외
    inl = {n["id"]: n["inlinks"] for n in g["nodes"]}
    assert inl == {"hub": 1, "leaf": 1, "orphan": 0}


def test_route_batch_parses_and_falls_back(monkeypatch):
    index = [{"title": "a", "desc": ""}, {"title": "b", "desc": ""}]
    monkeypatch.setattr(audit.llm, "generate", lambda *a, **k: "[[2],[1,2]]")
    assert audit.route_batch(["q1", "q2"], index) == [[1], [0, 1]]
    # 파싱 실패 → 질문별 structure.route 폴백
    monkeypatch.setattr(audit.llm, "generate", lambda *a, **k: "garbage")
    monkeypatch.setattr(audit.structure, "route", lambda q, idx: [0])
    assert audit.route_batch(["q1", "q2"], index) == [[0], [0]]


def test_audit_auto_selects_router_for_concept_wiki(tmp_path, monkeypatch):
    _make_wiki(tmp_path)  # raw=doc1, wiki 이름 불일치 → 커버리지 0
    calls = {}
    def fake_router(base_dir, **kw):
        calls["router"] = True
        return {"variant": "router", "n_docs": 1}
    monkeypatch.setattr(audit, "router_audit", fake_router)
    res = audit.audit(str(tmp_path))
    assert calls.get("router") and res["variant"] == "router"
