"""Stage 0 (구조 제안) 단위 테스트 — LLM 호출 없음, 전부 오프라인.

repo_map은 순수 로직이라 직접, LLM 경로(task_questions/proposal)는
monkeypatch로 llm.generate를 canned 응답으로 바꿔 검증한다.
"""

import json
import os

import proposal
import repo_map
import skeleton
import task_questions


# ---------- repo_map ----------

def _mk_repo(tmp_path):
    root = tmp_path / "myrepo"
    (root / "docs").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text(
        "# 가이드\n\n소개 문장.\n\n## 채점 기준\n\n항목별 기준.\n\n## 배점\n\n배점표.\n"
    )
    (root / "src" / "score.py").write_text(
        '"""채점 모듈."""\n\ndef compute(x):\n    return x\n\nclass Scorer:\n    pass\n'
    )
    (root / "node_modules" / "x" / "junk.md").write_text("# junk")
    (root / "package-lock.json").write_text("{}")
    return str(root)


def test_build_map_kinds_and_excludes(tmp_path):
    entries = repo_map.build_map([_mk_repo(tmp_path)], use_cache=False)
    rels = [e["rel"] for e in entries]
    assert "myrepo/docs/guide.md" in rels
    assert "myrepo/src/score.py" in rels
    assert not any("node_modules" in r or "package-lock" in r for r in rels)
    doc = next(e for e in entries if e["rel"].endswith("guide.md"))
    assert [h["text"] for h in doc["headings"]] == ["가이드", "채점 기준", "배점"]
    code = next(e for e in entries if e["rel"].endswith("score.py"))
    assert code["names"] == ["compute", "Scorer"]


def test_read_source_anchor_hit_miss_and_unknown(tmp_path):
    entries = repo_map.build_map([_mk_repo(tmp_path)], use_cache=False)
    m = repo_map.by_rel(entries)
    sec, hit = repo_map.read_source(m, "myrepo/docs/guide.md#채점 기준")
    assert hit and "항목별 기준" in sec and "배점표" not in sec
    # 정규화된 앵커(공백→하이픈)도 적중
    _, hit2 = repo_map.read_source(m, "myrepo/docs/guide.md#채점-기준")
    assert hit2
    # 없는 앵커 → 파일 전체 폴백 + miss
    full, hit3 = repo_map.read_source(m, "myrepo/docs/guide.md#없는헤딩")
    assert not hit3 and "배점표" in full
    # 지도에 없는 rel
    text, hit4 = repo_map.read_source(m, "elsewhere/x.md")
    assert text == "" and not hit4


def test_fair_cut_spreads_across_dirs(tmp_path):
    root = tmp_path / "r"
    for d in ("a", "b"):
        (root / d).mkdir(parents=True)
        for i in range(10):
            (root / d / f"f{i}.md").write_text(f"# {d}{i}")
    entries = repo_map.build_map([str(root)], max_files=6, use_cache=False)
    tops = {e["rel"].split("/")[1] for e in entries}
    assert len(entries) == 6 and tops == {"a", "b"}


def test_build_map_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_map, "CACHE_DIR", str(tmp_path / "cache"))
    root = _mk_repo(tmp_path)
    first = repo_map.build_map([root])
    assert os.listdir(tmp_path / "cache")
    assert repo_map.build_map([root]) == first


# ---------- proposal.validate ----------

def _entries(tmp_path):
    return repo_map.build_map([_mk_repo(tmp_path)], use_cache=False)


def test_validate_paths_sources_anchors(tmp_path):
    entries = _entries(tmp_path)
    pages = [
        {"path": "/abs/평가.md", "title": "t", "purpose": "p"},          # 절대경로 거부
        {"path": "../탈출.md", "title": "t", "purpose": "p"},            # .. 거부
        {"path": "prerm/기준", "title": "채점", "purpose": "기준은?",     # .md 자동 부여
         "outline": ["## 기준"],
         "sources": ["myrepo/docs/guide.md#채점 기준",                    # 앵커 적중
                     "myrepo/docs/guide.md#환각헤딩",                     # miss → 앵커 제거
                     "없는파일.md"]},                                     # drop
        {"path": "cases/리스크.md", "title": "사례", "purpose": "사례는?",
         "sources": []},                                                 # gap
    ]
    clean, stats = proposal.validate(pages, entries)
    assert stats == {"anchor_miss": 1, "dropped_sources": 1, "dropped_pages": 2}
    assert [p["path"] for p in clean] == ["prerm/기준.md", "cases/리스크.md"]
    assert clean[0]["status"] == "grounded"
    assert clean[0]["sources"] == ["myrepo/docs/guide.md#채점 기준",
                                   "myrepo/docs/guide.md"]
    assert clean[1]["status"] == "gap"


def test_propose_retries_then_gives_up(tmp_path, monkeypatch):
    entries = _entries(tmp_path)
    calls = []
    monkeypatch.setattr(proposal.llm, "generate",
                        lambda *a, **k: calls.append(1) or "잡담")
    pages, _ = proposal.propose("태스크", entries, "전략")
    assert pages == [] and len(calls) == 2


# ---------- task_questions ----------

def test_get_question_set_marks_gaps(tmp_path, monkeypatch):
    entries = _entries(tmp_path)
    monkeypatch.setattr(task_questions, "QCACHE_DIR", str(tmp_path / "qc"))
    monkeypatch.setattr(task_questions, "build_questions",
                        lambda *a, **k: ["채점 기준은?", "PRB 필드는?"])
    monkeypatch.setattr(task_questions.audit, "route_batch",
                        lambda qs, idx: [[0], [0]])
    answers = iter(["항목별 기준이다", "그런 내용은 나와 있지 않습니다"])
    monkeypatch.setattr(task_questions.structure, "_answer",
                        lambda ctx, q: next(answers))
    qa, gaps = task_questions.get_question_set("태스크", entries, n=2)
    assert qa[0]["a"] == "항목별 기준이다"
    assert qa[1]["a"] is None and gaps == ["PRB 필드는?"]
    # 캐시 적중 확인 (LLM 경로가 죽어도 같은 결과)
    monkeypatch.setattr(task_questions, "build_questions",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    qa2, _ = task_questions.get_question_set("태스크", entries, n=2)
    assert qa2 == qa


# ---------- skeleton ----------

def _pages():
    return [
        {"path": "prerm/기준.md", "title": "채점 기준", "purpose": "기준은?",
         "outline": ["## 기준", "## 배점"], "sources": ["myrepo/docs/guide.md"],
         "status": "grounded"},
        {"path": "cases/리스크.md", "title": "리스크 사례", "purpose": "사례는?",
         "outline": [], "sources": [], "status": "gap"},
    ]


def test_skeleton_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "wiki"
    res = skeleton.write_skeleton(_pages(), str(out), write=False)
    assert not out.exists()
    assert res["written"] == ["prerm/기준.md", "cases/리스크.md"]
    assert "[gap]" in res["tree"] and "채점 기준" in res["tree"]


def test_skeleton_write_and_never_overwrite(tmp_path):
    out = tmp_path / "wiki"
    (out / "prerm").mkdir(parents=True)
    (out / "prerm" / "기준.md").write_text("사람이 쓴 내용")
    res = skeleton.write_skeleton(_pages(), str(out), write=True, run_id="r1")
    assert res["skipped"] == ["prerm/기준.md"]
    assert (out / "prerm" / "기준.md").read_text() == "사람이 쓴 내용"
    stub = (out / "cases" / "리스크.md").read_text()
    assert "status: gap" in stub and "purpose: 사례는?" in stub


def test_report_shape_is_json_serializable(tmp_path):
    # report에 들어가는 조각들이 json 직렬화 가능해야 한다
    pages, stats = proposal.validate(_pages(), _entries(tmp_path))
    json.dumps({"pages": pages, "stats": stats}, ensure_ascii=False)


# ---------- score_proposal ----------

def test_score_proposal_no_grounded_returns_unscoreable(tmp_path):
    entries = _entries(tmp_path)
    qa = [{"q": "q1", "a": None, "evidence": []}]
    r = proposal.score_proposal(_pages(), qa, entries)
    assert r["total"] is None and r["reason"] == "no_grounded_questions"
    assert r["n_gap_questions"] == 1


def test_score_proposal_scores_and_denom(tmp_path, monkeypatch):
    entries = _entries(tmp_path)
    pages = [
        {"path": "prerm/기준.md", "title": "채점 기준", "purpose": "기준은?",
         "outline": [], "sources": ["myrepo/docs/guide.md#채점 기준"],
         "status": "grounded"},
        {"path": "cases/리스크.md", "title": "사례", "purpose": "사례는?",
         "outline": [], "sources": [], "status": "gap"},
    ]
    qa = [{"q": "채점 기준은?", "a": "항목별 기준", "evidence": []},
          {"q": "사례는?", "a": None, "evidence": []}]
    monkeypatch.setattr(proposal.audit, "route_batch", lambda qs, idx: [[0]])
    monkeypatch.setattr(proposal.structure, "_answer", lambda ctx, q: "항목별 기준")
    monkeypatch.setattr(proposal.scoring, "judge_all",
                        lambda qs, preds: ([1.0], False))
    r = proposal.score_proposal(pages, qa, entries)
    assert r["accuracy"] == 1.0 and r["n_grounded_questions"] == 1
    # 분모 = 구조가 참조하는 유일 source(섹션)의 실물 길이
    assert r["denom_chars"] == proposal.total_source_chars(pages, entries) > 0
    # 읽은 글자 = 그 섹션 + 헤더 라벨 → 효율은 (0,1) 사이가 아니라 0일 수도
    assert r["total"] == round(r["accuracy"] * r["efficiency"], 3)
    assert r["details"][0]["picked"] == ["prerm/기준.md"]


def test_score_proposal_propagates_judge_parse_failure(tmp_path, monkeypatch):
    entries = _entries(tmp_path)
    pages = [_pages()[0]]
    qa = [{"q": "q", "a": "a", "evidence": []}]
    monkeypatch.setattr(proposal.audit, "route_batch", lambda qs, idx: [[0]])
    monkeypatch.setattr(proposal.structure, "_answer", lambda ctx, q: "pred")
    monkeypatch.setattr(proposal.scoring, "judge_all",
                        lambda qs, preds: ([0.0], True))
    r = proposal.score_proposal(pages, qa, entries)
    assert r["parse_failed"] is True


# ---------- evolve loop ----------

def _setup_evolve(tmp_path, monkeypatch):
    import evolve_proposal
    entries = _entries(tmp_path)
    monkeypatch.setattr(evolve_proposal.repo_map, "build_map",
                        lambda *a, **k: entries)
    qa = [{"q": "채점 기준은?", "a": "항목별 기준", "evidence": []},
          {"q": "PRB 필드는?", "a": None, "evidence": []}]
    monkeypatch.setattr(evolve_proposal.task_questions, "get_question_set",
                        lambda *a, **k: (qa, ["PRB 필드는?"]))
    return evolve_proposal


def test_evolve_tracks_best_and_reflects_from_best(tmp_path, monkeypatch):
    ep = _setup_evolve(tmp_path, monkeypatch)
    pages = [_pages()[0]]
    monkeypatch.setattr(ep.proposal, "propose",
                        lambda *a, **k: (pages, {"anchor_miss": 0,
                                                 "dropped_sources": 0,
                                                 "dropped_pages": 0}))
    totals = iter([0.5, 0.3, 0.7])

    def fake_score(pgs, qa, entries):
        t = next(totals)
        return {"total": t, "accuracy": t, "efficiency": 1.0, "avg_read": 10,
                "denom_chars": 100, "n_pages": 1, "n_gap_questions": 1,
                "n_grounded_questions": 1, "parse_failed": False,
                "details": [{"q": "채점 기준은?", "picked": ["prerm/기준.md"],
                             "read_chars": 10, "pred": "p", "score": 1.0}]}

    monkeypatch.setattr(ep.proposal, "score_proposal", fake_score)
    reflected = []
    monkeypatch.setattr(ep, "reflect",
                        lambda s, r, g: reflected.append(r["total"]) or f"전략v{len(reflected)}")
    rep = ep.run_evolve(["src"], "태스크", generations=3,
                        out_dir=str(tmp_path / "runs"), use_cache=False)
    assert rep["best"]["total"] == 0.7 and rep["best"]["generation"] == 2
    # gen1 점수(0.3)가 아니라 best(0.5) 기준으로 reflect했는지
    assert reflected == [0.5, 0.5]


def test_evolve_control_never_reflects(tmp_path, monkeypatch):
    ep = _setup_evolve(tmp_path, monkeypatch)
    monkeypatch.setattr(ep.proposal, "propose",
                        lambda *a, **k: ([_pages()[0]], {"anchor_miss": 0,
                                                         "dropped_sources": 0,
                                                         "dropped_pages": 0}))
    monkeypatch.setattr(ep.proposal, "score_proposal",
                        lambda *a, **k: {"total": 0.5, "accuracy": 0.5,
                                         "efficiency": 1.0, "avg_read": 1,
                                         "denom_chars": 10, "n_pages": 1,
                                         "n_gap_questions": 1,
                                         "n_grounded_questions": 1,
                                         "parse_failed": False, "details": []})
    monkeypatch.setattr(ep, "reflect",
                        lambda *a: (_ for _ in ()).throw(AssertionError("reflect 호출됨")))
    rep = ep.run_evolve(["src"], "태스크", generations=2, no_evolve=True,
                        out_dir=str(tmp_path / "runs"), use_cache=False)
    assert rep["arm"] == "control"


def test_evolve_downgrades_when_no_grounded(tmp_path, monkeypatch):
    ep = _setup_evolve(tmp_path, monkeypatch)
    qa = [{"q": "q1", "a": None, "evidence": []}]
    monkeypatch.setattr(ep.task_questions, "get_question_set",
                        lambda *a, **k: (qa, ["q1"]))
    monkeypatch.setattr(ep.proposal, "propose",
                        lambda *a, **k: ([_pages()[1]], {"anchor_miss": 0,
                                                         "dropped_sources": 0,
                                                         "dropped_pages": 0}))
    rep = ep.run_evolve(["src"], "태스크", generations=5,
                        out_dir=str(tmp_path / "runs"), use_cache=False)
    assert rep["scoreable"] is False
    assert rep["generations"] == 1 and len(rep["history"]) == 1
    assert rep["best"]["pages"]  # 단발 제안은 유지


def test_seed_from_runs_ignores_other_modes(tmp_path):
    import evolve_proposal as ep
    runs = tmp_path / "runs"
    (runs / "a").mkdir(parents=True)
    (runs / "b").mkdir()
    json.dump({"mode": "proposal", "best": {"strategy": "제안 전략", "total": 0.4,
                                            "pages": []}},
              open(runs / "a" / "report.json", "w"))
    json.dump({"best": {"strategy": "요약 전략", "total": 0.9}},
              open(runs / "b" / "report.json", "w"))
    got = ep.best_proposal_strategy_from_runs(str(runs))
    assert got["strategy"] == "제안 전략"


def test_apply_best_ignores_proposal_strategies(tmp_path):
    import apply as apply_mod
    runs = tmp_path / "runs"
    (runs / "a").mkdir(parents=True)
    (runs / "b").mkdir()
    json.dump({"mode": "proposal", "best": {"strategy": "제안 전략", "total": 0.9,
                                            "pages": []}},
              open(runs / "a" / "report.json", "w"))
    json.dump({"best": {"strategy": "요약 전략", "total": 0.4}},
              open(runs / "b" / "report.json", "w"))
    got = apply_mod.best_strategy_from_runs(str(runs))
    assert got["strategy"] == "요약 전략"
