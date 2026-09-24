"""문서 없이 풀리는 질문 거르기 + 기준선(question_filter) — LLM 호출 없음.

llm.generate를 프롬프트 모양으로 나눠 흉내 낸다.
  - 문서 없이 답하기: KNOWN에 든 질문만 정답(a{i}), 나머지는 '모름'
  - 컨텍스트로 답하기: CONTEXT_MISS에 든 질문만 '모름', 나머지는 정답
  - 판정: 예측 == 정답이면 1
"""

import json
import re

import pytest

import audit
import batch
import evolve_structure
import llm
import question_filter as qf
import scoring
import structure


def _pool(k):
    return [{"q": f"q{i}", "a": f"a{i}"} for i in range(k)]


class FakeLLM:
    def __init__(self, known=(), context_miss=(), closed_book_reply=None, context_reply=None):
        self.known, self.context_miss = set(known), set(context_miss)
        self.closed_book_reply, self.context_reply = closed_book_reply, context_reply
        self.calls = []

    @staticmethod
    def _questions(prompt):
        block = prompt.split("질문들:\n", 1)[1].split("\n\n", 1)[0]
        return [re.sub(r"^\d+\.\s*", "", line) for line in block.splitlines()]

    def __call__(self, prompt, **kw):
        if prompt.startswith("아래 질문들에 문서 없이"):
            self.calls.append("closed_book")
            if self.closed_book_reply is not None:
                return self.closed_book_reply
            return json.dumps([f"a{q[1:]}" if q in self.known else "모름" for q in self._questions(prompt)])
        if prompt.startswith("아래 '컨텍스트'에 근거해서만 각 질문에"):
            self.calls.append("context")
            if self.context_reply is not None:
                return self.context_reply
            return json.dumps(["모름" if q in self.context_miss else f"a{q[1:]}" for q in self._questions(prompt)])
        if prompt.startswith("각 항목에서 '예측'이"):
            self.calls.append("judge")
            rows = re.findall(r"질문:(.*?) \| 정답:(.*?) \| 예측:(.*)", prompt)
            return json.dumps([1 if gold == pred else 0 for _, gold, pred in rows])
        raise AssertionError(f"예상 못 한 프롬프트: {prompt[:60]}")


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(qf, "FILTER", True)
    monkeypatch.setattr(qf, "BASELINES", True)


def _use(monkeypatch, fake):
    monkeypatch.setattr(llm, "generate", fake)
    return fake


def test_filter_moves_closed_book_questions_out(on, monkeypatch):
    _use(monkeypatch, FakeLLM(known={"q1", "q3"}))
    asked = []
    qs, info = qf.filter_questions(lambda k: asked.append(k) or _pool(k), 4)
    assert asked == [6]                                    # 1.5배로 만든다
    assert [qa["q"] for qa in qs] == ["q0", "q2", "q4", "q5"]
    assert all(qa["closed_book"] is False for qa in qs)
    assert info["checked"] and info["dropped"] == 2 and info["dropped_questions"] == ["q1", "q3"]
    assert info["kept_closed_book"] == 0


def test_filter_fills_with_marked_questions_when_short(on, monkeypatch):
    _use(monkeypatch, FakeLLM(known={"q0", "q1", "q2"}))
    qs, info = qf.filter_questions(_pool, 4)
    assert [qa["q"] for qa in qs] == ["q3", "q4", "q5", "q0"]
    assert qs[-1]["closed_book"] is True
    assert info["dropped"] == 2 and info["kept_closed_book"] == 1


def test_filter_keeps_first_n_when_check_is_unreadable(on, monkeypatch):
    _use(monkeypatch, FakeLLM(closed_book_reply="잘 모르겠습니다"))
    qs, info = qf.filter_questions(_pool, 4)
    assert [qa["q"] for qa in qs] == ["q0", "q1", "q2", "q3"]
    assert not info["checked"] and all("closed_book" not in qa for qa in qs)


def test_filter_off_asks_for_exactly_n(monkeypatch):
    fake = _use(monkeypatch, FakeLLM())
    asked = []
    qs, info = qf.filter_questions(lambda k: asked.append(k) or _pool(k), 4)
    assert asked == [4] and len(qs) == 4 and info["enabled"] is False and fake.calls == []


def test_baselines_use_filter_marks_and_memoize(on, monkeypatch):
    fake = _use(monkeypatch, FakeLLM(context_miss={"q2"}))
    qs = [dict(qa, closed_book=(qa["q"] == "q3")) for qa in _pool(4)]
    b = qf.baselines(qs, "원본")
    assert b["floor"] == {"accuracy": 0.25, "n": 4, "hits": ["q3"], "source": "filter"}
    assert b["ceiling"] == {"accuracy": 0.75, "n": 4, "misses": ["q2"]}
    assert fake.calls == ["context", "judge"]              # floor는 표시로 — 추가 호출 없음
    assert qf.baselines(qs, "원본") == b and fake.calls == ["context", "judge"]


def test_baselines_measure_floor_without_marks(on, monkeypatch):
    fake = _use(monkeypatch, FakeLLM(known={"q0"}))
    b = qf.baselines(_pool(2), "원본")
    assert b["floor"]["accuracy"] == 0.5 and b["floor"]["source"] == "measured"
    assert fake.calls.count("closed_book") == 1


def test_unreadable_ceiling_is_missing_not_zero(on, monkeypatch):
    _use(monkeypatch, FakeLLM(context_reply='["a0"]'))      # 답 개수가 모자람
    b = qf.baselines([dict(qa, closed_book=False) for qa in _pool(3)], "원본")
    assert b["ceiling"] is None and b["floor"]["accuracy"] == 0.0


def _fake_struct(docs, strategy):
    return {"files": [{"title": "f", "content": "c", "sources": list(docs)}],
            "index": [{"title": "f", "desc": "c"}]}


def _score(struct, qs, total_raw, **kw):
    return {"total": 0.5, "accuracy": 0.5, "efficiency": 1.0, "avg_read": 10, "n_files": 1,
            "parse_failed": False,
            "details": [{"q": qa["q"], "picked": ["f"], "read_chars": 10, "pred": "p", "score": 0.5} for qa in qs]}


def test_structure_run_records_filter_and_heldout_baselines(on, tmp_path, monkeypatch):
    files = []
    for name in ("a", "b"):
        p = tmp_path / f"{name}.md"
        p.write_text(f"{name} " * 50)
        files.append(str(p))
    _use(monkeypatch, FakeLLM(known={"q2"}, context_miss={"q0"}))
    monkeypatch.setattr(structure, "build_cross_question_set", lambda docs, n=4: _pool(n))
    monkeypatch.setattr(structure, "organize", _fake_struct)
    monkeypatch.setattr(structure, "score_structure", _score)
    monkeypatch.setattr(evolve_structure, "reflect", lambda strategy, result: strategy)

    report = evolve_structure.evolve_structure(files=files, generations=1, n_qa=6, out_dir=str(tmp_path / "runs"))

    assert report["question_filter"]["dropped"] == 1 and len(report["question_set"]) == 6
    assert "q2" not in [qa["q"] for qa in report["question_set"]]
    heldout = report["question_split"]["heldout_questions"]
    b = report["baselines"]
    assert b["n"] == len(heldout) and b["floor"]["accuracy"] == 0.0
    expected_miss = ["q0"] if "q0" in heldout else []
    assert b["ceiling"]["misses"] == expected_miss
    progress = json.loads((next((tmp_path / "runs").glob("structure-*")) / "progress.json").read_text())
    assert progress["question_filter"]["dropped"] == 1


def test_audit_question_cache_keeps_filtered_set(on, tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "QCACHE_DIR", str(tmp_path))
    _use(monkeypatch, FakeLLM(known={"q0"}))
    built = []
    monkeypatch.setattr(audit.scoring, "build_question_set", lambda raw, n: built.append(n) or _pool(n))
    qs, info = audit.question_record("doc", "원본", n=4)
    again, info2 = audit.question_record("doc", "원본", n=4)
    assert built == [6] and again == qs and info2 == info
    assert [qa["q"] for qa in qs] == ["q1", "q2", "q3", "q4"] and info["dropped"] == 1
    cached = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert set(cached) == {"questions", "filter"}
    assert audit.get_questions("doc", "원본", n=4) == qs


def test_router_audit_reports_raw_ceiling_and_floor(on, tmp_path, monkeypatch):
    for rel, text in [("raw/a.md", "원본 a"), ("wiki/page.md", "# page\n내용")]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    monkeypatch.setattr(audit, "QCACHE_DIR", str(tmp_path / "qcache"))
    _use(monkeypatch, FakeLLM(known={"q0", "q1", "q2", "q3"}, context_miss={"q5"}))
    monkeypatch.setattr(audit.scoring, "build_question_set", lambda raw, n: _pool(n))
    monkeypatch.setattr(audit, "route_batch", lambda qs, index: [[0] for _ in qs])
    monkeypatch.setattr(audit.structure, "_answer", lambda context, q: "모름")

    res = audit.router_audit(str(tmp_path), n_qa=4)

    # 6개 중 4개가 문서 없이 풀림 → 2개만 깨끗 + 2개는 표시하고 채움, 2개 제외
    b = res["baselines"]
    assert b["dropped"] == 2
    assert b["floor"]["accuracy"] == 0.5 and b["floor"]["n"] == 4
    assert b["ceiling"]["accuracy"] == 0.75 and b["ceiling"]["misses"] == [{"doc": "a", "q": "q5"}]
    assert {d["q"]: d["ceiling"] for d in res["questions"]} == {"q4": 1.0, "q5": 0.0, "q0": 1.0, "q1": 1.0}


def test_batch_summary_places_best_accuracy_between_baselines():
    recs = [{"doc": "d1", "arm": "control", "best_acc": 0.8, "ceiling_acc": 0.9, "floor_acc": 0.0},
            {"doc": "d1", "arm": "evolve", "best_acc": 0.85, "ceiling_acc": 0.9, "floor_acc": 0.0},
            {"doc": "d2", "arm": "control", "best_acc": 0.7, "ceiling_acc": 1.0, "floor_acc": 0.2},
            {"doc": "d2", "arm": "evolve", "best_acc": 0.75, "ceiling_acc": 1.0, "floor_acc": 0.2}]
    by_arm = {"control": recs[0::2], "evolve": recs[1::2]}
    [line] = batch._baseline_lines(recs, by_arm)
    assert "원본 전체를 읽으면 0.950" in line and "문서 없이 0.100" in line
    assert "control 0.750" in line and "evolve 0.800" in line
    assert batch._baseline_lines([{"doc": "d", "arm": "control", "best_acc": 1}], {}) == []


def test_answer_prompt_is_shared_with_a_mode_scoring():
    qs = _pool(2)
    assert scoring.answer_all_prompt("ctx", qs).startswith("아래 '컨텍스트'에 근거해서만 각 질문에")


def test_heldout_prefers_questions_that_need_the_documents():
    """문서 없이 풀리는 질문(closed_book)은 held-out에서 뒤로 — 보고 점수가 문서 의존 질문을 잰다.
    표시가 없는 세트는 예전과 똑같이 나뉜다."""
    import evolve
    plain = _pool(6)
    train, test = evolve.split_questions(plain, "bundle")
    import random
    expected = list(plain)
    random.Random("bundle").shuffle(expected)
    assert test == expected[:2] and train == expected[2:]

    flagged = [dict(qa, closed_book=qa["q"] not in ("q4", "q5")) for qa in _pool(6)]
    train, test = evolve.split_questions(flagged, "bundle")
    assert sorted(qa["q"] for qa in test) == ["q4", "q5"]
    assert all(qa["closed_book"] for qa in train)
