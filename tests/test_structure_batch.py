"""B단계 control arm + batch --stage structure 테스트 (SEA-15).

LLM 호출 0회 — organize/score_structure/reflect를 함수 단위 스텁으로 바꾼다.

커버 대상:
- evolve_structure no_evolve: 전 세대 seed 전략 고정 + reflect 미호출
- evolve arm: reflect가 best 전략을 받아 다음 세대 전략을 만든다
- 주입한 question_set이 재생성 없이 그대로 쓰인다
- batch --stage structure: 두 arm이 돌고 summary.md에 net 효과가 나온다
"""

import pytest

import batch
import evolve_structure
import structure


QS = [{"q": "q1", "a": "a1"}, {"q": "q2", "a": "a2"}]


def _score(total, acc=0.5):
    return {"total": total, "accuracy": acc, "efficiency": 0.9,
            "avg_read": 100, "n_files": 2, "parse_failed": False,
            "details": [{"q": "q1", "picked": ["f"], "read_chars": 100, "score": 1}]}


def _install_fakes(monkeypatch, totals):
    """organize/score_structure를 스텁으로, reflect를 기록 스텁으로 바꾼다.

    totals: score_structure가 호출 순서대로 돌려줄 total 점수 목록.
    """
    monkeypatch.setattr(
        structure, "organize",
        lambda docs, strategy: {"files": [{"title": "f", "content": "c"}],
                                "index": [{"title": "f", "desc": "c"}]})
    queue = list(totals)
    monkeypatch.setattr(
        structure, "score_structure",
        lambda struct, qs, total_raw: _score(queue.pop(0)))

    reflect_calls = []

    def fake_reflect(strategy, result):
        reflect_calls.append(strategy)
        return f"S{len(reflect_calls)}"

    monkeypatch.setattr(evolve_structure, "reflect", fake_reflect)
    return reflect_calls


@pytest.fixture
def docs_env(tmp_path):
    files = []
    for name in ("a", "b"):
        p = tmp_path / f"{name}.md"
        p.write_text(f"{name} " * 100)
        files.append(str(p))
    return {"files": files, "out": str(tmp_path / "runs")}


def test_control_arm_keeps_seed_and_never_reflects(docs_env, monkeypatch):
    reflect_calls = _install_fakes(monkeypatch, totals=[0.2, 0.5, 0.3])

    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=3, out_dir=docs_env["out"],
        no_evolve=True, question_set=QS)

    assert reflect_calls == []
    assert all(h["strategy"] == evolve_structure.SEED_STRATEGY
               for h in report["history"])
    assert report["arm"] == "control"
    # 전략이 고정돼도 best 판정은 여전히 최고 세대를 따른다
    assert report["best"]["generation"] == 1


def test_evolve_arm_reflects_from_best_strategy(docs_env, monkeypatch):
    # g1이 최고, g2는 하락 → g2 후에도 best인 g1의 전략(S1)으로 되돌려 reflect
    reflect_calls = _install_fakes(monkeypatch, totals=[0.2, 0.5, 0.3, 0.4])

    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=4, out_dir=docs_env["out"],
        question_set=QS)

    assert report["arm"] == "evolve"
    assert reflect_calls == [evolve_structure.SEED_STRATEGY, "S1", "S1"]
    assert [h["strategy"] for h in report["history"]] == [
        evolve_structure.SEED_STRATEGY, "S1", "S2", "S3"]
    assert report["best"]["generation"] == 1


def test_injected_question_set_is_used_without_regeneration(docs_env, monkeypatch):
    _install_fakes(monkeypatch, totals=[0.2])
    monkeypatch.setattr(
        structure, "build_cross_question_set",
        lambda docs, n=4: pytest.fail("주입한 question_set이 있는데 재생성했다"))

    report = evolve_structure.evolve_structure(
        files=docs_env["files"], generations=1, out_dir=docs_env["out"],
        question_set=QS)
    assert report["question_set"] == QS


def test_batch_stage_structure_runs_both_arms_and_reports_net(
        docs_env, monkeypatch):
    # run 1회 x [evolve, control] x gen 2 → score 4회
    # evolve: 0.2→0.5 (imp +0.3), control: 0.2→0.3 (imp +0.1) → net +0.200
    reflect_calls = _install_fakes(monkeypatch, totals=[0.2, 0.5, 0.2, 0.3])
    monkeypatch.setattr(
        structure, "build_cross_question_set", lambda docs, n=4: QS)

    records, batch_dir = batch.run_batch(
        docs_env["files"], runs=1, generations=2, n_qa=2,
        with_control=True, out_dir=docs_env["out"], stage="structure")

    assert [r["arm"] for r in records] == ["evolve", "control"]
    assert records[0]["improvement"] == pytest.approx(0.3)
    assert records[1]["improvement"] == pytest.approx(0.1)
    # reflect는 evolve arm의 세대 전환 1회뿐 (control은 0회)
    assert reflect_calls == [evolve_structure.SEED_STRATEGY]

    import pathlib
    summary = (pathlib.Path(batch_dir) / "summary.md").read_text()
    assert "진화 효과(net) = evolve +0.300 - control +0.100 = **+0.200**" in summary
    assert (pathlib.Path(batch_dir) / "results.csv").exists()


def test_batch_stage_structure_rejects_ablation(docs_env):
    with pytest.raises(ValueError):
        batch.run_batch(docs_env["files"], runs=1, generations=1, n_qa=2,
                        ablation=True, out_dir=docs_env["out"], stage="structure")


def test_batch_stage_structure_skips_when_question_set_fails(
        docs_env, monkeypatch, capsys):
    _install_fakes(monkeypatch, totals=[])
    monkeypatch.setattr(structure, "build_cross_question_set",
                        lambda docs, n=4: [])

    records, _ = batch.run_batch(
        docs_env["files"], runs=1, generations=1, n_qa=2,
        with_control=True, out_dir=docs_env["out"], stage="structure")

    assert records == []
    assert "질문 세트 실패" in capsys.readouterr().out
