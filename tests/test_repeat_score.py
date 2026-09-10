"""채점기 반복성 측정(repeat_score.py) — LLM 호출 없음."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import repeat_score  # noqa: E402
import scoring  # noqa: E402
import structure  # noqa: E402


QS = [{"q": "q1", "a": "a1"}, {"q": "q2", "a": "a2"}]


def _result(total, picks, scores):
    return {"total": total, "accuracy": sum(scores) / len(scores), "efficiency": 0.9, "avg_read": 10,
            "n_files": 2, "parse_failed": False,
            "details": [{"q": qa["q"], "picked": p, "pred": "p" + qa["q"], "score": s}
                        for qa, p, s in zip(QS, picks, scores)]}


def test_summarize_runs_measures_spread_pick_agreement_and_flips(monkeypatch):
    runs = [_result(0.9, [["A"], ["B"]], [1, 1]),
            _result(0.5, [["A"], ["C"]], [1, 0]),
            _result(0.9, [["A"], ["B"]], [1, 1])]
    s = repeat_score.summarize_runs(runs, QS)
    assert s["total"]["mean"] == 0.767 and s["total"]["sd"] > 0.1
    assert s["per_question"][0] == {"q": "q1", "pick_agreement": 1.0, "n_distinct_picks": 1, "correct_rate": 1.0, "flips": False}
    assert s["per_question"][1]["pick_agreement"] == 0.67 and s["per_question"][1]["flips"] is True
    assert s["flip_rate"] == 0.5 and s["mean_pick_agreement"] == 0.835


def test_repeat_score_calls_scorer_n_times_without_changing_struct(monkeypatch):
    calls = []
    monkeypatch.setattr(structure, "score_structure",
                        lambda struct, qs, raw: calls.append(struct) or _result(0.9, [["A"], ["B"]], [1, 1]))
    struct = {"files": [{"title": "A", "content": "x"}], "index": []}
    res = repeat_score.repeat_score(struct, QS, 100, repeats=4)
    assert len(calls) == 4 and all(c is struct for c in calls)
    assert res["summary"]["repeats"] == 4 and res["summary"]["total"]["sd"] == 0.0


def test_rejudge_flip_rate(monkeypatch):
    seq = iter([([1, 1], False), ([1, 0], False), ([0, 0], True), ([1, 1], False)])
    monkeypatch.setattr(scoring, "judge_all", lambda qs, preds, retries=1: next(seq))
    r = repeat_score.rejudge(QS, ["p1", "p2"], repeats=4)
    assert r["repeats"] == 3                       # 파싱 실패 1회는 뺀다
    assert r["per_question"][0]["flips"] is False and r["per_question"][1]["flips"] is True
    assert r["flip_rate"] == 0.5


def test_render_mentions_flips():
    runs = [_result(0.9, [["A"], ["B"]], [1, 1]), _result(0.5, [["A"], ["C"]], [1, 0])]
    lines = repeat_score.render(repeat_score.summarize_runs(runs, QS))
    assert any("뒤집힘" in l for l in lines) and lines[0].startswith("- 반복 2회")


def test_extractive_answer_mode_changes_prompt(monkeypatch):
    prompts = []
    monkeypatch.setattr(structure.llm, "generate", lambda prompt, **kw: prompts.append(prompt) or "x")
    structure._answer("ctx", "q?", mode="extractive")
    structure._answer("ctx", "q?", mode="free")
    assert "그대로 인용" in prompts[0] and "그대로 인용" not in prompts[1]


def test_score_structure_repeats_average_answer_and_judge_rounds(monkeypatch):
    struct = {"files": [{"title": "A", "content": "aaa"}, {"title": "B", "content": "bbb"}],
              "index": [{"title": "A", "desc": "a"}, {"title": "B", "desc": "b"}]}
    monkeypatch.setattr(structure, "route", lambda q, index: [0])
    answers = iter(["p1", "p2", "p1'", "p2'", "p1''", "p2''"])
    monkeypatch.setattr(structure, "_answer", lambda ctx, q, mode=None: next(answers))
    judged = iter([([1, 0], False), ([1, 1], False), ([0, 1], False)])
    monkeypatch.setattr(scoring, "judge_all", lambda qs, preds, retries=1: next(judged))
    r = structure.score_structure(struct, QS, 100, repeats=3)
    assert r["details"][0]["score_rounds"] == [1, 1, 0] and r["details"][0]["score"] == 0.667
    assert r["details"][1]["score_rounds"] == [0, 1, 1]
    assert r["accuracy"] == 0.667 and r["parse_failed"] is False
    assert "_context" not in r["details"][0] and r["details"][0]["pred"] == "p1"   # 첫 회차 답만 남긴다
