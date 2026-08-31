"""evolve 루프 불변식 + batch 집계 산식 테스트 (SEA-14).

LLM 호출 0회 — llm.generate를 프롬프트 종류로 분기하는 스크립트 fake로 바꾼다.
세대별 요약 마커(SUM-g<n>)를 답변/판정 단계까지 흘려보내, 판정 fake가
시나리오 테이블(acc_by_gen)대로 정확도를 결정론적으로 만든다.

커버 대상:
- evolve.evolve() best 갱신: best가 held-out 최고 세대인가
- "점수 안 오르면 best 전략으로 되돌려 reflect" 불변식 (evolve.py:264)
- no_evolve control arm: 전 세대 seed 전략 고정 + reflect 미호출
- batch._arm_stats / aggregate: net 효과 산식 (이 프로젝트의 결론식)
- batch.paired_bootstrap_net: 문서 단위 유의성 검정 (SEA-18)
"""

import json
import re
import statistics

import pytest

import batch
import evolve


# ---------- evolve 루프용 fake LLM ----------

def _install_fake_llm(monkeypatch, acc_by_gen):
    """llm.generate를 시나리오 기반 fake로 교체한다.

    acc_by_gen: {세대: held-out/train 공통 정답 비율}
    요약은 "SUM-g<n>" 고정 길이 → efficiency는 전 세대 동일(floor 고정 0.9).
    따라서 held-out total 순위는 acc_by_gen 순위와 같다.
    """
    state = {"gen": -1}

    def fake_generate(prompt, **kw):
        if prompt.endswith("요약:"):
            state["gen"] += 1
            return f"SUM-g{state['gen']}"
        if prompt.endswith("답변 배열:"):
            g = re.search(r"SUM-g(\d+)", prompt).group(1)
            n = len(re.findall(r"^\d+\. ", prompt.split("질문들:\n")[1], re.M))
            return json.dumps([f"pred-g{g}"] * n)
        if prompt.endswith("판정 배열:"):
            g = int(re.search(r"예측:pred-g(\d+)", prompt).group(1))
            n = len(re.findall(r"\| 예측:", prompt))
            k = round(acc_by_gen[g] * n)
            return json.dumps([1] * k + [0] * (n - k))
        raise AssertionError(f"예상 밖 LLM 호출: ...{prompt[-40:]}")

    # llm 모듈 속성 자체를 바꾸므로 scoring.llm 경로도 함께 대체된다
    monkeypatch.setattr(evolve.llm, "generate", fake_generate)


@pytest.fixture
def evolve_env(tmp_path, monkeypatch):
    """영속 이력·run 출력이 tmp로 가도록 격리하고 문서 파일을 만든다."""
    monkeypatch.setattr(evolve, "WIKI_DIR", str(tmp_path / "wiki"))
    monkeypatch.setattr(evolve, "IMPACT_PATH", str(tmp_path / "wiki" / "impact.jsonl"))
    doc = tmp_path / "doc.md"
    doc.write_text("x" * 1000)
    qs = [{"q": f"q{i}", "a": f"a{i}"} for i in range(6)]
    return {"doc": str(doc), "out": str(tmp_path / "runs"), "qs": qs}


def _capture_reflect(monkeypatch):
    """reflect를 인자 기록 + 세대별 새 전략 반환 스텁으로 교체한다."""
    calls = []

    def fake_reflect(strategy, train_result, doc=None):
        calls.append(strategy)
        return f"S{len(calls)}"

    monkeypatch.setattr(evolve, "reflect", fake_reflect)
    return calls


def test_evolve_best_tracks_holdout_and_reverts_on_regression(
        evolve_env, monkeypatch):
    # 시나리오: g1이 held-out 최고, g2·g3은 하락 → best는 g1에 고정돼야 한다
    _install_fake_llm(monkeypatch, acc_by_gen={0: 0.5, 1: 1.0, 2: 0.0, 3: 0.0})
    reflect_calls = _capture_reflect(monkeypatch)

    report = evolve.evolve(evolve_env["doc"], generations=4,
                           out_dir=evolve_env["out"],
                           question_set=evolve_env["qs"])

    # best = held-out 최고 세대
    assert report["best"]["generation"] == 1
    totals = [h["score"]["total"] for h in report["history"]]
    assert report["best"]["total"] == max(totals)
    assert len(report["history"]) == 4

    # reflect 인자 불변식: g0 후 SEED, g1 후 g1의 전략(S1),
    # g2에서 점수가 안 올랐으므로 g2 후에도 best인 S1으로 되돌려 reflect
    assert reflect_calls == [evolve.SEED_STRATEGY, "S1", "S1"]
    # 실제로 g1·g2 세대는 reflect가 돌려준 전략을 썼다
    assert [h["strategy"] for h in report["history"]] == [
        evolve.SEED_STRATEGY, "S1", "S2", "S3"]


def test_control_arm_keeps_seed_strategy_and_never_reflects(
        evolve_env, monkeypatch):
    _install_fake_llm(monkeypatch, acc_by_gen={0: 0.5, 1: 0.0, 2: 1.0})
    reflect_calls = _capture_reflect(monkeypatch)

    report = evolve.evolve(evolve_env["doc"], generations=3,
                           out_dir=evolve_env["out"],
                           question_set=evolve_env["qs"], no_evolve=True)

    assert reflect_calls == []
    assert all(h["strategy"] == evolve.SEED_STRATEGY for h in report["history"])
    assert report["arm"] == "control"
    # 전략이 고정돼도 best 판정은 여전히 held-out 최고 세대를 따른다
    assert report["best"]["generation"] == 2


# ---------- batch: _arm_stats / aggregate (net 산식) ----------

def _rec(doc, arm, imp, run=0, gen0=0.2):
    return {
        "doc": doc, "size": 100, "run": run, "arm": arm,
        "gen0_total": gen0, "best_total": round(gen0 + imp, 3),
        "best_gen": 1, "improvement": imp, "improved": imp > 0,
        "gen0_acc": 0.5, "best_acc": 0.8, "elapsed_sec": 1.0,
    }


def test_arm_stats_known_values():
    rs = [_rec("d", "evolve", 0.1), _rec("d", "evolve", 0.3, run=1),
          _rec("d", "evolve", -0.1, run=2)]
    s = batch._arm_stats(rs)
    assert s["n"] == 3
    assert s["mean_imp"] == pytest.approx(0.1)
    assert s["stdev_imp"] == pytest.approx(statistics.stdev([0.1, 0.3, -0.1]))
    assert s["improved_ratio"] == pytest.approx(2 / 3)


def test_arm_stats_edge_cases():
    assert batch._arm_stats([]) == {
        "n": 0, "mean_imp": 0.0, "stdev_imp": 0.0, "improved_ratio": 0.0}
    single = batch._arm_stats([_rec("d", "evolve", 0.2)])
    assert single["stdev_imp"] == 0.0  # run 1개는 표준편차 정의 불가 → 0
    assert single["improved_ratio"] == 1.0


def test_aggregate_net_formula_evolve_vs_control(tmp_path):
    # evolve 평균 +0.20, control 평균 +0.05 → net = +0.150
    records = [
        _rec("d1", "evolve", 0.1), _rec("d1", "evolve", 0.3, run=1),
        _rec("d1", "control", 0.0), _rec("d1", "control", 0.1, run=1),
    ]
    batch.aggregate(records, str(tmp_path), generations=3, runs=2,
                    batch_elapsed=60.0)

    summary = (tmp_path / "summary.md").read_text()
    assert "진화 효과(net) = evolve +0.200 - control +0.050 = **+0.150**" in summary
    # 문서가 1개뿐이라 유의성은 판정할 수 없다 — 조용히 단언하지 않는다
    assert "판정 불가" in summary
    assert "진화가 실제로 개선" not in summary
    # CSV도 함께 남는다
    assert (tmp_path / "results.csv").exists()


def test_aggregate_net_formula_history_ablation(tmp_path):
    # ablation: evolve vs evolve-nohist, net이 0 이하 → 노이즈 해석 분기
    records = [
        _rec("d1", "evolve", 0.1),
        _rec("d1", "evolve-nohist", 0.2),
    ]
    batch.aggregate(records, str(tmp_path), generations=3, runs=1,
                    batch_elapsed=60.0)

    summary = (tmp_path / "summary.md").read_text()
    assert "영속 이력 효과(net) = 이력 있음 +0.100 - 이력 없음 +0.200 = **-0.100**" in summary
    assert "판정 불가" in summary


def test_aggregate_empty_records_writes_stub_summary(tmp_path):
    batch.aggregate([], str(tmp_path), generations=3, runs=1, batch_elapsed=1.0)
    summary = (tmp_path / "summary.md").read_text()
    assert "(성공한 run 없음)" in summary
    assert not (tmp_path / "results.csv").exists()


# ---------- perf: control arm train 채점 생략 + patience 조기 종료 ----------

def test_control_arm_skips_train_scoring(evolve_env, monkeypatch):
    """control은 reflect가 없으므로 train 채점(LLM 2회/세대)을 생략한다."""
    _install_fake_llm(monkeypatch, acc_by_gen={0: 0.5, 1: 1.0})
    score_calls = []
    real_score = evolve.scoring.score

    def spy_score(raw_text, summary, qs):
        score_calls.append(len(qs))
        return real_score(raw_text, summary, qs)

    monkeypatch.setattr(evolve.scoring, "score", spy_score)
    report = evolve.evolve(evolve_env["doc"], generations=2,
                           out_dir=evolve_env["out"],
                           question_set=evolve_env["qs"], no_evolve=True)

    assert len(score_calls) == 2  # 세대당 held-out 1회만 (train 없음)
    assert all(h["train_score"] is None for h in report["history"])
    assert report["best"]["generation"] == 1  # held-out 판정은 그대로


def test_patience_stops_after_plateau(evolve_env, monkeypatch):
    _install_fake_llm(monkeypatch, acc_by_gen={0: 1.0, 1: 0.5, 2: 0.5, 3: 0.5})
    _capture_reflect(monkeypatch)
    report = evolve.evolve(evolve_env["doc"], generations=4,
                           out_dir=evolve_env["out"],
                           question_set=evolve_env["qs"], patience=2)
    # gen0 best → gen1·gen2 연속 미갱신 → gen2에서 종료 (gen3 실행 안 함)
    assert len(report["history"]) == 3
    assert report["best"]["generation"] == 0


def test_no_patience_runs_all_generations(evolve_env, monkeypatch):
    _install_fake_llm(monkeypatch, acc_by_gen={0: 1.0, 1: 0.5, 2: 0.5, 3: 0.5})
    _capture_reflect(monkeypatch)
    report = evolve.evolve(evolve_env["doc"], generations=4,
                           out_dir=evolve_env["out"],
                           question_set=evolve_env["qs"])
    assert len(report["history"]) == 4


# ---------- batch: paired bootstrap 유의성 검정 (SEA-18) ----------

def _paired(docs, treat_imp, base_imp, treat="evolve", base="control"):
    """문서마다 treat/base 각각 1 run씩 — deltas = treat_imp - base_imp."""
    out = []
    for d, ti, bi in zip(docs, treat_imp, base_imp):
        out.append(_rec(d, treat, ti))
        out.append(_rec(d, base, bi))
    return out


def test_bootstrap_needs_two_paired_docs():
    # 문서 1개 → 판정 불가 (None)
    one = _paired(["d1"], [0.3], [0.0])
    assert batch.paired_bootstrap_net(one, "evolve", "control") is None
    # 두 문서지만 한쪽은 evolve만 있어 짝이 안 됨 → 여전히 판정 불가
    unpaired = one + [_rec("d2", "evolve", 0.3)]
    assert batch.paired_bootstrap_net(unpaired, "evolve", "control") is None


def test_bootstrap_run_count_does_not_inflate_sample():
    """표본 단위는 run이 아니라 문서 — 같은 문서를 여러 번 돌려도 n_docs는 안 는다."""
    recs = _paired(["d1", "d2"], [0.3, 0.3], [0.0, 0.0])
    recs += [_rec("d1", "evolve", 0.3, run=1), _rec("d1", "control", 0.0, run=1)]
    b = batch.paired_bootstrap_net(recs, "evolve", "control")
    assert b["n_docs"] == 2


def test_bootstrap_detects_clear_positive_effect():
    docs = ["d1", "d2", "d3", "d4", "d5"]
    recs = _paired(docs, [0.3] * 5, [0.0] * 5)
    b = batch.paired_bootstrap_net(recs, "evolve", "control")
    assert b["n_docs"] == 5
    assert b["net"] == pytest.approx(0.3)
    assert b["p"] < 0.05
    assert b["ci_low"] > 0


def test_bootstrap_reports_null_when_effect_straddles_zero():
    docs = ["d1", "d2", "d3", "d4"]
    recs = _paired(docs, [0.3, 0.0, 0.2, 0.0], [0.0, 0.3, 0.0, 0.2])
    b = batch.paired_bootstrap_net(recs, "evolve", "control")
    assert b["net"] == pytest.approx(0.0)
    assert b["p"] >= 0.05          # 노이즈와 구분되지 않는다
    assert b["ci_low"] < 0 < b["ci_high"]


def test_bootstrap_is_deterministic():
    """시드 고정 — 같은 입력에 같은 CI/p가 나와야 결론이 재현된다."""
    recs = _paired(["d1", "d2", "d3"], [0.3, 0.1, 0.2], [0.0, 0.05, 0.1])
    assert batch.paired_bootstrap_net(recs, "evolve", "control") == \
        batch.paired_bootstrap_net(recs, "evolve", "control")


def test_aggregate_reports_significance_for_multi_doc_batch(tmp_path):
    docs = ["d1", "d2", "d3", "d4", "d5"]
    batch.aggregate(_paired(docs, [0.3] * 5, [0.0] * 5), str(tmp_path),
                    generations=3, runs=1, batch_elapsed=60.0)

    summary = (tmp_path / "summary.md").read_text()
    assert "문서 단위 paired bootstrap" in summary
    assert "95% CI" in summary
    assert "진화가 실제로 개선" in summary
    assert "판정 불가" not in summary


def test_aggregate_calls_null_result_not_an_improvement(tmp_path):
    docs = ["d1", "d2", "d3", "d4"]
    batch.aggregate(_paired(docs, [0.3, 0.0, 0.2, 0.0], [0.0, 0.3, 0.0, 0.2]),
                    str(tmp_path), generations=3, runs=1, batch_elapsed=60.0)

    summary = (tmp_path / "summary.md").read_text()
    assert "구분되지 않는다" in summary
    assert "선택 노이즈" in summary
    assert "실제로 개선" not in summary
