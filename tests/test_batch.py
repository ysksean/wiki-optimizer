"""batch.run_batch 견고성 — 문서 1개의 실패가 배치 전체를 죽이지 않아야 한다.

evolve.load_question_set / evolve.evolve를 스텁으로 바꿔 LLM 없이 돈다.
"""

import os

import pytest

import batch
import evolve


def _fake_report(gen0=0.2, best=0.5, parse_failed=False):
    return {
        "history": [
            {"score": {"total": gen0, "accuracy": 0.4}},
            {"score": {"total": best, "accuracy": 0.8}},
        ],
        "best": {"total": best, "generation": 1},
        "parse_failed": parse_failed,
    }


def _make_docs(tmp_path, names):
    files = []
    for n in names:
        p = tmp_path / f"{n}.md"
        p.write_text(f"doc {n}")
        files.append(str(p))
    return files


@pytest.fixture
def stub_evolve(monkeypatch):
    calls = []

    def fake_evolve(raw_path, **kw):
        calls.append(os.path.basename(raw_path))
        return _fake_report()

    monkeypatch.setattr(evolve, "evolve", fake_evolve)
    return calls


def test_question_set_exception_skips_doc_and_batch_continues(tmp_path, monkeypatch, stub_evolve):
    files = _make_docs(tmp_path, ["bad", "good"])

    def fake_qs(raw_path, raw_text, n_qa):
        if "bad" in raw_path:
            raise RuntimeError("LLM 타임아웃")  # 질문 세트 생성 중 예외 주입
        return [{"q": "Q", "a": "A"}]

    monkeypatch.setattr(evolve, "load_question_set", fake_qs)

    records, batch_dir = batch.run_batch(
        files, runs=1, generations=2, n_qa=1, out_dir=str(tmp_path / "runs"))

    # bad 문서는 건너뛰고 good 문서는 정상 처리
    assert stub_evolve == ["good.md"]
    assert [r["doc"] for r in records] == ["good"]
    # 최종 집계 파일이 생성됨
    assert os.path.exists(os.path.join(batch_dir, "results.csv"))
    assert os.path.exists(os.path.join(batch_dir, "summary.md"))


def test_unreadable_doc_is_skipped(tmp_path, monkeypatch, stub_evolve):
    files = _make_docs(tmp_path, ["good"])
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff\xfe invalid utf-8 \x80")  # UnicodeDecodeError 유발
    files.insert(0, str(bad))

    monkeypatch.setattr(evolve, "load_question_set",
                        lambda p, t, n: [{"q": "Q", "a": "A"}])

    records, batch_dir = batch.run_batch(
        files, runs=1, generations=2, n_qa=1, out_dir=str(tmp_path / "runs"))

    assert stub_evolve == ["good.md"]
    assert [r["doc"] for r in records] == ["good"]
    assert os.path.exists(os.path.join(batch_dir, "summary.md"))


def test_aggregate_runs_even_when_batch_is_interrupted(tmp_path, monkeypatch):
    files = _make_docs(tmp_path, ["a", "b"])
    monkeypatch.setattr(evolve, "load_question_set",
                        lambda p, t, n: [{"q": "Q", "a": "A"}])

    def fake_evolve(raw_path, **kw):
        if raw_path.endswith("b.md"):
            raise KeyboardInterrupt  # 사람이 중단 (Exception 아님 → run 루프 except를 통과)
        return _fake_report()

    monkeypatch.setattr(evolve, "evolve", fake_evolve)

    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(files, runs=1, generations=2, n_qa=1, out_dir=str(tmp_path / "runs"))

    # 중단돼도 그때까지의 기록(a)으로 집계가 남는다
    batch_dirs = os.listdir(tmp_path / "runs")
    assert len(batch_dirs) == 1
    summary = (tmp_path / "runs" / batch_dirs[0] / "summary.md").read_text()
    assert "총 run: 1개" in summary


# ---------- judge 파싱 실패 run은 net 산식에서 제외 ----------

def _rec(arm, gen0, best, parse_failed=False):
    return {"doc": "d", "size": 10, "run": 0, "arm": arm, "gen0_total": gen0,
            "best_total": best, "best_gen": 1, "improvement": round(best - gen0, 3),
            "improved": best > gen0, "gen0_acc": 0.0, "best_acc": 0.0,
            "parse_failed": parse_failed, "elapsed_sec": 1.0}


def test_aggregate_excludes_parse_failed_runs_from_net(tmp_path):
    # control arm의 gen0가 파싱 실패로 0점이면 control 향상폭이 부풀어 net이 왜곡된다.
    records = [
        _rec("evolve", 0.2, 0.5),
        _rec("control", 0.2, 0.3),
        _rec("control", 0.0, 0.6, parse_failed=True),  # 오염된 run
    ]
    batch.aggregate(records, str(tmp_path), generations=2, runs=1, batch_elapsed=1.0)
    summary = (tmp_path / "summary.md").read_text()

    assert "judge 파싱 실패 run: 1개" in summary
    # control 평균 향상폭은 유효 run(+0.1)만 — 오염 run(+0.6)을 넣으면 +0.35
    assert "| control | 1 | +0.100 |" in summary
    assert "| 1 |" in summary.split("| control |")[1].split("\n")[0]  # 파싱실패(제외) 열
    assert "= **+0.200**" in summary  # net = 0.3 - 0.1
    # CSV에는 실패 run도 남긴다 (증거 보존)
    assert (tmp_path / "results.csv").read_text().count("\n") == 4


def test_aggregate_all_parse_failed_reports_no_valid_runs(tmp_path):
    records = [_rec("evolve", 0.0, 0.0, parse_failed=True)]
    batch.aggregate(records, str(tmp_path), generations=2, runs=1, batch_elapsed=1.0)
    summary = (tmp_path / "summary.md").read_text()
    assert "유효한 run 없음" in summary


def test_record_flags_parse_failed_and_no_valid_best():
    report = _fake_report(parse_failed=True)
    rec = batch._record(report, "d", 10, 0, "evolve", 1.0)
    assert rec["parse_failed"] is True

    # 전 세대 실패면 best.generation == -1 → 마지막 세대를 best로 오독하지 않는다
    report = {"history": [{"score": {"total": 0.0, "accuracy": 0.0}},
                          {"score": {"total": 0.0, "accuracy": 0.0}}],
              "best": {"total": -1.0, "generation": -1}, "parse_failed": True}
    rec = batch._record(report, "d", 10, 0, "evolve", 1.0)
    assert rec["parse_failed"] is True and rec["best_gen"] == 0 and rec["improvement"] == 0.0


def test_parallel_batch_matches_sequential(tmp_path, monkeypatch, stub_evolve):
    """--parallel 경로: 결과 레코드 집합이 순차 실행과 동일해야 한다."""
    files = _make_docs(tmp_path, ["a", "b", "c"])
    monkeypatch.setattr(evolve, "load_question_set",
                        lambda *a, **k: [{"q": "Q", "a": "A"}])

    records_seq, _ = batch.run_batch(
        files, runs=2, generations=2, n_qa=1,
        out_dir=str(tmp_path / "runs-seq"), parallel=1)
    records_par, _ = batch.run_batch(
        files, runs=2, generations=2, n_qa=1,
        out_dir=str(tmp_path / "runs-par"), parallel=3)

    def key(recs):
        return sorted((r["doc"], r["run"], r["arm"], r["best_total"]) for r in recs)

    assert len(records_par) == len(records_seq) == 6
    assert key(records_par) == key(records_seq)


def test_parallel_batch_survives_one_doc_failure(tmp_path, monkeypatch, stub_evolve):
    files = _make_docs(tmp_path, ["bad", "good", "also"])

    def fake_qs(raw_path, raw_text, n_qa):
        if "bad" in raw_path:
            raise RuntimeError("boom")
        return [{"q": "Q", "a": "A"}]

    monkeypatch.setattr(evolve, "load_question_set", fake_qs)
    records, batch_dir = batch.run_batch(
        files, runs=1, generations=2, n_qa=1,
        out_dir=str(tmp_path / "runs"), parallel=2)
    assert sorted({r["doc"] for r in records}) == ["also", "good"]
    assert os.path.exists(os.path.join(batch_dir, "results.csv"))
