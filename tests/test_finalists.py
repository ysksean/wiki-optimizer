"""최종 후보 재채점 — 단일 점수 최고값(노이즈 최대값) 대신 상위 후보를 다시 재서 고른다. LLM 호출 없음."""

import json
from pathlib import Path

import pytest

import batch
import evolve_structure
import structure

QS6 = [{"q": f"q{i}", "a": f"a{i}"} for i in range(6)]
SINGLE = [0.90, 0.70, 0.80]            # 세대별 단일 held-out 점수
RESCORED = {0: 0.60, 2: 0.85}          # 재채점하면 gen0는 운이 좋았던 것, gen2가 실제로 낫다


@pytest.fixture
def env(tmp_path, monkeypatch):
    files = []
    for name in ("a", "b"):
        p = tmp_path / f"{name}.md"
        p.write_text(name * 50)
        files.append(str(p))
    gen = {"n": -1}

    def organize(docs, strategy, previous=None):
        gen["n"] += 1
        return {"files": [{"title": f"g{gen['n']}", "content": "c", "sources": list(docs)}],
                "index": [{"title": f"g{gen['n']}", "desc": "c"}]}

    calls = []

    def score(struct, qs, total_raw, repeats=None, answer_mode=None):
        g = int(struct["files"][0]["title"][1:])
        rescoring = repeats is not None
        calls.append((g, repeats, answer_mode))
        total = RESCORED[g] if rescoring else SINGLE[g]
        return {"total": total, "accuracy": total, "efficiency": 1.0, "avg_read": 10, "n_files": 1,
                "parse_failed": False, "details": [{"q": qa["q"], "picked": ["x"], "read_chars": 1, "score": 1}
                                                    for qa in qs]}

    monkeypatch.setattr(structure, "organize", organize)
    monkeypatch.setattr(structure, "score_structure", score)
    monkeypatch.setattr(evolve_structure, "reflect", lambda s, r: "S")
    return {"files": files, "out": str(tmp_path / "runs"), "calls": calls}


def test_finalists_rescored_and_best_replaced(env):
    report = evolve_structure.evolve_structure(files=env["files"], generations=3, out_dir=env["out"],
                                               question_set=QS6, no_evolve=True, finalists=2, finalist_repeats=3)
    sel = report["selection"]
    assert [(e["generation"], e["single_total"], e["total"]) for e in sel["entries"]] == [(0, 0.9, 0.6), (2, 0.8, 0.85)]
    assert sel["winner"] == 2 and sel["changed"] is True and sel["single_best_total"] == 0.9
    assert report["best"]["generation"] == 2 and report["best"]["total"] == 0.85
    assert report["best"]["single_total"] == 0.8 and report["best"]["rescored"] is True
    assert [c for c in env["calls"] if c[1] is not None] == [(0, 3, "extractive"), (2, 3, "extractive")]
    progress = json.loads(next(Path(env["out"]).glob("structure-*/progress.json")).read_text())
    assert progress["best_gen"] == 2 and progress["best_total"] == 0.85 and progress["selection"]["winner"] == 2


def test_no_finalists_keeps_single_best(env):
    report = evolve_structure.evolve_structure(files=env["files"], generations=3, out_dir=env["out"],
                                               question_set=QS6, no_evolve=True)
    assert report["selection"] is None and report["best"]["generation"] == 0
    assert all(c[1] is None for c in env["calls"])


def test_batch_record_and_absolute_comparison_use_rescored_best(env):
    report = evolve_structure.evolve_structure(files=env["files"], generations=3, out_dir=env["out"],
                                               question_set=QS6, no_evolve=True, finalists=2)
    rec = batch._record(report, "d", 1, 0, "control", 1.0)
    assert rec["best_total"] == 0.8 and rec["best_rescored"] == 0.85     # 고른 구조의 단일 점수 / 다시 잰 값
    lines = batch._absolute_score_lines([dict(rec), dict(rec, doc="e")], {"control": [rec]})
    assert "최종 후보 재채점 값" in lines[0] and "| control | 1 | 0.850 |" in "\n".join(lines)


def test_structure_card_shows_rescored_selection():
    js = (Path(__file__).parents[1] / "src" / "static" / "app.js").read_text()
    css = (Path(__file__).parents[1] / "src" / "static" / "app.css").read_text()
    assert "const selection = rep?.selection || p.selection;" in js and 'class="finalists"' in js
    assert js.count("selection_hero:") == 3 and js.count("selection_note:") == 3
    assert ".finalists {" in css
