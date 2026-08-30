"""batch.run_batch 견고성 — 문서 1개의 실패가 배치 전체를 죽이지 않아야 한다.

evolve.load_question_set / evolve.evolve를 스텁으로 바꿔 LLM 없이 돈다.
"""

import os

import pytest

import batch
import evolve


def _fake_report(gen0=0.2, best=0.5):
    return {
        "history": [
            {"score": {"total": gen0, "accuracy": 0.4}},
            {"score": {"total": best, "accuracy": 0.8}},
        ],
        "best": {"total": best, "generation": 1},
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
