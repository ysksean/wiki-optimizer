"""B 구조 모드 train/held-out 분리 — LLM 호출 없음.

설계 문서(2026-09-04 agent-reflector) 1단계: best 판정·리포트 점수는 held-out,
Reflector는 train 판정만 본다. A 모드의 split_questions 규칙을 그대로 쓴다.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import evolve  # noqa: E402
import evolve_structure  # noqa: E402
import structure  # noqa: E402


QS6 = [{"q": f"q{i}", "a": f"a{i}"} for i in range(6)]


def _fake_struct(docs, strategy):
    return {"files": [{"title": "f", "content": "c", "sources": list(docs)}],
            "index": [{"title": "f", "desc": "c"}]}


def _scorer(train_total, test_total, train_qs, test_qs):
    """train/held-out 질문 목록으로 어느 채점인지 구분해 다른 점수를 준다.
    (병렬 채점이라 호출 순서에 의존하면 안 된다.)"""
    def score(struct, qs, total_raw):
        is_test = qs == test_qs
        total = test_total if is_test else train_total
        return {"total": total, "accuracy": total, "efficiency": 1.0, "avg_read": 10,
                "n_files": 1, "parse_failed": False,
                "details": [{"q": qa["q"], "picked": ["f"], "read_chars": 10, "pred": "p",
                             "score": 1.0 if is_test else 0.0} for qa in qs]}
    return score


@pytest.fixture
def docs_env(tmp_path):
    files = []
    for name in ("a", "b"):
        p = tmp_path / f"{name}.md"
        p.write_text(f"{name} " * 50)
        files.append(str(p))
    return {"files": files, "out": str(tmp_path / "runs")}


def _split_for(files):
    bundle = "+".join(Path(f).stem for f in files)
    return evolve.split_questions(QS6, bundle)


def test_split_is_recorded_and_reflect_sees_only_train(docs_env, monkeypatch):
    train_qs, test_qs = _split_for(docs_env["files"])
    assert len(test_qs) == 2 and len(train_qs) == 4
    monkeypatch.setattr(structure, "organize", _fake_struct)
    monkeypatch.setattr(structure, "score_structure", _scorer(0.9, 0.4, train_qs, test_qs))
    seen = []

    def fake_reflect(strategy, result):
        seen.append(result)
        return "S1"

    monkeypatch.setattr(evolve_structure, "reflect", fake_reflect)

    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=2, out_dir=docs_env["out"], question_set=QS6)

    assert report["question_split"] == {
        "train": 4, "heldout": 2, "degenerate": False,
        "heldout_questions": [qa["q"] for qa in test_qs]}
    # 리포트 점수 = held-out
    h0 = report["history"][0]
    assert h0["score"]["total"] == 0.4 and h0["train_score"]["total"] == 0.9
    assert {d["q"] for d in h0["details"]} == {qa["q"] for qa in test_qs}
    assert {d["q"] for d in h0["train_details"]} == {qa["q"] for qa in train_qs}
    assert report["best"]["total"] == 0.4
    # reflect는 train 결과만 받았다
    assert len(seen) == 1 and {d["q"] for d in seen[0]["details"]} == {qa["q"] for qa in train_qs}


def test_control_arm_scores_heldout_only(docs_env, monkeypatch):
    train_qs, test_qs = _split_for(docs_env["files"])
    calls = []
    monkeypatch.setattr(structure, "organize", _fake_struct)

    def score(struct, qs, total_raw):
        calls.append(list(qs))
        return {"total": 0.5, "accuracy": 0.5, "efficiency": 1.0, "avg_read": 10,
                "n_files": 1, "parse_failed": False, "details": []}

    monkeypatch.setattr(structure, "score_structure", score)
    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=2, out_dir=docs_env["out"],
        no_evolve=True, question_set=QS6)
    assert calls == [test_qs, test_qs]           # 세대당 held-out 1회, train 없음
    assert report["history"][0]["train_score"] is None
    assert report["history"][0]["train_details"] == []


def test_small_question_set_is_degenerate_and_scored_once(docs_env, monkeypatch):
    qs2 = QS6[:2]
    calls = []
    monkeypatch.setattr(structure, "organize", _fake_struct)

    def score(struct, qs, total_raw):
        calls.append(list(qs))
        return {"total": 0.5, "accuracy": 0.5, "efficiency": 1.0, "avg_read": 10,
                "n_files": 1, "parse_failed": False, "details": [{"q": "q0", "score": 1}]}

    monkeypatch.setattr(structure, "score_structure", score)
    monkeypatch.setattr(evolve_structure, "reflect", lambda s, r: "S1")
    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=1, out_dir=docs_env["out"], question_set=qs2)
    assert report["question_split"]["degenerate"] is True
    assert len(calls) == 1                        # 분리 포기 → 한 번만 채점해 둘 다로 쓴다
    assert report["history"][0]["train_score"]["total"] == 0.5


def test_train_parse_failure_also_excludes_generation(docs_env, monkeypatch):
    train_qs, test_qs = _split_for(docs_env["files"])
    monkeypatch.setattr(structure, "organize", _fake_struct)

    def score(struct, qs, total_raw):
        failed = qs != test_qs   # train 판정만 실패
        return {"total": 0.0 if failed else 0.7, "accuracy": 0.0, "efficiency": 1.0, "avg_read": 10,
                "n_files": 1, "parse_failed": failed, "details": []}

    monkeypatch.setattr(structure, "score_structure", score)
    monkeypatch.setattr(evolve_structure, "reflect", lambda s, r: "S1")
    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=1, out_dir=docs_env["out"], question_set=QS6)
    assert report["parse_failed_generations"] == [0] and report["best"]["generation"] == -1
    progress = json.loads(next(Path(docs_env["out"]).glob("structure-*/progress.json")).read_text())
    assert progress["question_split"]["heldout"] == 2
