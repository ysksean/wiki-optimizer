"""provenance 메타데이터 테스트 (LLM 호출 없음)."""

import provenance


def test_collect_shape():
    p = provenance.collect(
        doc_text="hello",
        question_set=[{"q": "a", "a": "b"}],
        params={"generations": 3},
    )
    for key in ("timestamp", "code_sha", "backend", "model", "language",
                "python", "doc_sha", "question_set_sha", "params"):
        assert key in p
    assert p["params"]["generations"] == 3
    assert p["backend"] in ("claude", "codex")
    assert len(p["doc_sha"]) == 16


def test_collect_without_optional_inputs():
    p = provenance.collect()
    assert p["doc_sha"] is None
    assert p["question_set_sha"] is None
    assert p["params"] == {}


def test_question_set_sha_order_invariant():
    qs1 = [{"q": "a", "a": "1"}, {"q": "b", "a": "2"}]
    qs2 = [{"q": "b", "a": "2"}, {"q": "a", "a": "1"}]
    assert provenance.question_set_sha(qs1) == provenance.question_set_sha(qs2)


def test_question_set_sha_content_sensitive():
    qs1 = [{"q": "a", "a": "1"}]
    qs2 = [{"q": "a", "a": "changed"}]
    assert provenance.question_set_sha(qs1) != provenance.question_set_sha(qs2)
