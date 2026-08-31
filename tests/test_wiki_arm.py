"""evolve-wiki arm (구조화 패턴 위키) 테스트 (SEA-19).

LLM 호출 0회 — llm.generate를 프롬프트 종류로 분기하는 fake로 바꾼다.

커버 대상:
- wiki._parse_ops: JSON patch 파싱 (실패 None vs 빈 ops 구분)
- wiki._apply_ops: create/append/replace + 페이지 상한 + index 재생성
- wiki.maintain: 파싱 실패 시 위키 무변경 (SEA-13 교훈)
- evolve(history_mode="wiki"): arm 명명, maintainer 호출, reflect에
  flat 이력 대신 위키 주입
- batch.resolve_arms / aggregate 3-arm 보고 (evolve-wiki net + 유의성)
"""

import json
import os
import re

import pytest

import batch
import evolve
import wiki


@pytest.fixture
def wiki_env(tmp_path, monkeypatch):
    """flat 이력·run 출력을 tmp로 격리하고 문서 파일을 만든다.

    구조화 위키는 전역 경로가 없다 — evolve가 <out_dir>/wiki/<doc>로
    스코프하므로 out만 tmp면 실전 경로가 오염되지 않는다.
    "wdir"은 wiki 함수 직접 호출 테스트용 임의 위키 경로.
    """
    monkeypatch.setattr(evolve, "WIKI_DIR", str(tmp_path / "flat"))
    monkeypatch.setattr(evolve, "IMPACT_PATH", str(tmp_path / "flat" / "impact.jsonl"))
    doc = tmp_path / "doc.md"
    doc.write_text("x" * 1000)
    qs = [{"q": f"q{i}", "a": f"a{i}"} for i in range(6)]
    return {"doc": str(doc), "out": str(tmp_path / "runs"), "qs": qs,
            "wdir": str(tmp_path / "wiki")}


# ---------- _parse_ops ----------

def test_parse_ops_valid_and_empty():
    out = '설명 텍스트 {"ops": [{"op": "create", "slug": "over-length", "content": "# over-length\\n요약"}]}'
    ops = wiki._parse_ops(out)
    assert ops == [{"op": "create", "slug": "over-length", "content": "# over-length\n요약"}]
    # 빈 ops는 "수정할 것 없음" — 파싱 실패(None)와 다르다
    assert wiki._parse_ops('{"ops": []}') == []


@pytest.mark.parametrize("out", [
    "JSON 아님",
    '{"ops": "not-a-list"}',
    '{"ops": [{"op": "delete", "slug": "a", "content": "x"}]}',   # 미지원 op
    '{"ops": [{"op": "create", "slug": "한글슬러그", "content": "x"}]}',  # slug 형식 위반
    '{"ops": [{"op": "create", "slug": "a-b"}]}',                 # content 누락
    '{"ops": [{"op": "replace", "slug": "a-b", "old": "x"}]}',    # new 누락
])
def test_parse_ops_rejects_invalid(out):
    assert wiki._parse_ops(out) is None


# ---------- _apply_ops / index ----------

def test_apply_ops_create_append_replace_and_index(wiki_env):
    w = wiki_env["wdir"]
    n = wiki._apply_ops(w, [
        {"op": "create", "slug": "too-long", "content": "# too-long\n요약이 길면 효율이 떨어진다 — 압축하라\n본문1"},
    ])
    assert n == 1
    assert wiki._apply_ops(w, [{"op": "append", "slug": "too-long", "content": "본문2"}]) == 1
    assert wiki._apply_ops(w, [{"op": "replace", "slug": "too-long", "old": "본문1", "new": "본문1개정"}]) == 1

    page = wiki._read_page(w, "too-long")
    assert "본문1개정" in page and "본문2" in page
    index = open(os.path.join(w, "index.md")).read()
    assert "**too-long**: 요약이 길면 효율이 떨어진다 — 압축하라" in index


def test_apply_ops_skips_unsafe_ops(wiki_env):
    w = wiki_env["wdir"]
    wiki._apply_ops(w, [{"op": "create", "slug": "p1", "content": "# p1\n요약\nAA\nAA"}])
    applied = wiki._apply_ops(w, [
        {"op": "create", "slug": "p1", "content": "# p1\n덮어쓰기 시도"},  # 기존 페이지 덮기 금지
        {"op": "append", "slug": "no-such", "content": "x"},               # 없는 페이지 append 금지
        {"op": "replace", "slug": "p1", "old": "AA", "new": "BB"},         # 2회 일치 — 오적용 방지
    ])
    assert applied == 0
    assert "덮어쓰기" not in wiki._read_page(w, "p1")


def test_page_clamped_to_max_lines(wiki_env):
    w = wiki_env["wdir"]
    body = "\n".join(f"line{i}" for i in range(100))
    wiki._apply_ops(w, [{"op": "create", "slug": "big", "content": f"# big\n요약\n{body}"}])
    lines = wiki._read_page(w, "big").splitlines()
    assert len(lines) <= wiki.MAX_PAGE_LINES
    assert lines[0] == "# big" and lines[1] == "요약"  # 요약 줄은 지킨다
    assert "line99" in lines[-1]  # 오래된 본문부터 버린다


def test_wiki_block_budget_skips_oversized_page_not_rest(wiki_env, monkeypatch):
    """예산 초과 페이지는 건너뛰되(continue) 뒤의 작은 페이지는 실려야 한다."""
    w = wiki_env["wdir"]
    wiki._apply_ops(w, [
        {"op": "create", "slug": "huge", "content": "# huge\n요약\n" + "본문 " * 3000},
        {"op": "create", "slug": "tiny", "content": "# tiny\n작은 패턴 요약"},
    ])
    # huge를 최신으로 만들어 예산 판정을 먼저 받게 한다 (mtime 해상도 무관하게)
    future = os.path.getmtime(wiki._page_path(w, "tiny")) + 10
    os.utime(wiki._page_path(w, "huge"), (future, future))
    block = wiki.wiki_block(w)
    assert "[패턴: tiny]" in block
    assert "[패턴: huge]" not in block
    assert "**huge**" in block  # index에는 남는다


# ---------- maintain: 파싱 실패 시 무변경 ----------

def test_maintain_parse_failure_leaves_wiki_untouched(wiki_env, monkeypatch):
    w = wiki_env["wdir"]
    wiki._apply_ops(w, [{"op": "create", "slug": "keep", "content": "# keep\n요약"}])
    before = wiki._read_page(w, "keep")
    monkeypatch.setattr(wiki.llm, "generate", lambda *a, **k: "JSON이 아닌 잡담")

    ok = wiki.maintain(w, "doc", 0, "전략", {"qa_details": []},
                       {"total": 0.5, "length_ratio": 0.3}, True)

    assert ok is False
    assert wiki._read_page(w, "keep") == before
    assert wiki._list_pages(w)[0][0] == "keep" and len(wiki._list_pages(w)) == 1


# ---------- evolve(history_mode="wiki") e2e ----------

def _install_fake_llm(monkeypatch, acc_by_gen, maintainer_ops, reflect_prompts,
                      maintainer_prompts=None):
    """요약/채점 fake(test_evolve_batch 패턴) + maintainer/reflect 분기.

    acc_by_gen: {전역 요약 호출 순번: 정답 비율}. 배치처럼 evolve()가 여러 번
    불리면 순번이 이어지므로, 범위 밖 순번은 주기 반복으로 처리한다.
    """
    state = {"gen": -1, "maintain_calls": 0}

    def acc_of(g):
        return acc_by_gen.get(g, acc_by_gen[g % len(acc_by_gen)])

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
            k = round(acc_of(g) * n)
            return json.dumps([1] * k + [0] * (n - k))
        if prompt.endswith("JSON:"):  # Wiki Maintainer
            state["maintain_calls"] += 1
            if maintainer_prompts is not None:
                maintainer_prompts.append(prompt)
            return json.dumps({"ops": maintainer_ops})
        if prompt.endswith("[개선된 전략 프롬프트]:"):  # reflect
            reflect_prompts.append(prompt)
            return f"S{len(reflect_prompts)}"
        raise AssertionError(f"예상 밖 LLM 호출: ...{prompt[-40:]}")

    monkeypatch.setattr(evolve.llm, "generate", fake_generate)
    return state


def test_evolve_wiki_arm_maintains_and_injects_wiki(wiki_env, monkeypatch):
    reflect_prompts = []
    ops = [{"op": "create", "slug": "missing-facts",
            "content": "# missing-facts\n핵심 사실 누락이 문제 — 수치를 명시하라"}]
    state = _install_fake_llm(monkeypatch, {0: 0.5, 1: 1.0, 2: 1.0}, ops, reflect_prompts)
    # flat 이력이 이미 있어도 wiki arm의 reflect에는 새어들면 안 된다
    evolve.record_strategy_impact("doc", "evolve", 0, "옛 flat 전략",
                                  {"total": 0.1, "length_ratio": 0.5}, True)

    report = evolve.evolve(wiki_env["doc"], generations=3, out_dir=wiki_env["out"],
                           question_set=wiki_env["qs"], history_mode="wiki")

    assert report["arm"] == "evolve-wiki"
    assert state["maintain_calls"] == 3  # 유효 세대마다 1회
    wdir = wiki.wiki_dir_for(wiki_env["out"], "doc")
    assert wiki._read_page(wdir, "missing-facts") is not None
    # reflect(gen0 이후)에는 위키가 들어가고 flat 이력 블록은 없다
    assert len(reflect_prompts) == 2
    for p in reflect_prompts:
        assert "[전략 패턴 위키" in p
        assert "missing-facts" in p
        assert "[과거 채택된 전략들" not in p and "옛 flat 전략" not in p
    # flat 이력에는 evolve-wiki arm으로 기록된다 (evolve arm 이력 오염 없음)
    accepted, _ = evolve.load_strategy_history("doc")
    assert [e["strategy"] for e in accepted] == ["옛 flat 전략"]


def test_evolve_flat_arm_never_touches_wiki(wiki_env, monkeypatch):
    reflect_prompts = []
    state = _install_fake_llm(monkeypatch, {0: 0.5, 1: 1.0}, [], reflect_prompts)

    report = evolve.evolve(wiki_env["doc"], generations=2, out_dir=wiki_env["out"],
                           question_set=wiki_env["qs"])

    assert report["arm"] == "evolve"
    assert state["maintain_calls"] == 0
    assert not os.path.isdir(os.path.join(wiki_env["out"], "wiki"))  # 위키 자체가 안 생긴다


def test_run_batch_three_arms_end_to_end(wiki_env, monkeypatch, tmp_path):
    """run_batch 전체 경로 — _run_doc → evolve(3 arm) → wiki → aggregate.

    실 LLM 배치를 대신하는 오프라인 e2e: 3-arm summary.md가 실제로
    생성되고 두 net + 유의성 문구가 함께 실리는지 확인한다.
    """
    reflect_prompts, maintainer_prompts = [], []
    ops = [{"op": "create", "slug": "missing-facts",
            "content": "# missing-facts\n핵심 사실 누락 — 수치를 명시하라"}]
    # 짝수 순번 0.5, 홀수 순번 1.0 → 모든 run에서 gen1이 best
    _install_fake_llm(monkeypatch, {0: 0.5, 1: 1.0}, ops, reflect_prompts,
                      maintainer_prompts)
    monkeypatch.setattr(evolve, "load_question_set",
                        lambda path, text, n: wiki_env["qs"])
    doc2 = tmp_path / "doc2.md"
    doc2.write_text("y" * 800)

    records, batch_dir = batch.run_batch(
        [wiki_env["doc"], str(doc2)], runs=1, generations=2, n_qa=6,
        out_dir=str(tmp_path / "batch-out"),
        arms=["evolve", "evolve-wiki", "control"])

    assert sorted({r["arm"] for r in records}) == ["control", "evolve", "evolve-wiki"]
    assert len(records) == 6  # 문서 2 x run 1 x arm 3
    summary = open(os.path.join(batch_dir, "summary.md")).read()
    assert "구조화 위키 효과(net)" in summary
    assert "진화 효과(net)" in summary
    assert summary.count("문서 단위 paired bootstrap") + summary.count("판정 불가") == 2
    assert "한계(교란 요인)" in summary  # 위키 net에는 교란 요인 한계가 같이 실린다
    # 위키는 배치 안에서 문서별로 격리된다 — 문서 간 패턴 전이 없음
    for d in ("doc", "doc2"):
        assert wiki._read_page(wiki.wiki_dir_for(batch_dir, d), "missing-facts") is not None
    assert len(maintainer_prompts) == 4  # 문서 2 x gen 2
    # doc2의 첫 maintainer 호출 시점에 index가 비어 있어야 한다
    # (doc1이 이미 missing-facts를 만든 뒤다 — 새어들면 격리 실패)
    assert "(비어 있음)" in maintainer_prompts[2]
    assert "missing-facts" not in maintainer_prompts[2].split("[이번 세대")[0]


# ---------- batch: resolve_arms / aggregate ----------

def test_resolve_arms_explicit_and_fallback():
    assert batch.resolve_arms(["evolve", "evolve-wiki", "control"]) == \
        ["evolve", "evolve-wiki", "control"]
    assert batch.resolve_arms(with_control=True) == ["evolve", "control"]
    assert batch.resolve_arms(ablation=True) == ["evolve", "evolve-nohist"]
    assert batch.resolve_arms() == ["evolve"]


@pytest.mark.parametrize("kwargs", [
    {"arms": ["evolve", "no-such-arm"]},
    {"arms": ["evolve", "evolve"]},
    {"arms": ["evolve-wiki"], "stage": "structure"},
    {"ablation": True, "stage": "structure"},
])
def test_resolve_arms_rejects_invalid(kwargs):
    with pytest.raises(ValueError):
        batch.resolve_arms(**kwargs)


def _rec(doc, arm, imp, run=0, gen0=0.2):
    return {
        "doc": doc, "size": 100, "run": run, "arm": arm,
        "gen0_total": gen0, "best_total": round(gen0 + imp, 3),
        "best_gen": 1, "improvement": imp, "improved": imp > 0,
        "gen0_acc": 0.5, "best_acc": 0.8, "elapsed_sec": 1.0,
    }


def test_aggregate_three_arms_reports_both_nets(tmp_path):
    records = []
    for d in ["d1", "d2", "d3", "d4", "d5"]:
        records += [_rec(d, "evolve", 0.1), _rec(d, "evolve-wiki", 0.3),
                    _rec(d, "control", 0.0)]
    batch.aggregate(records, str(tmp_path), generations=3, runs=1, batch_elapsed=60.0)

    summary = (tmp_path / "summary.md").read_text()
    assert "구조화 위키 효과(net) = evolve-wiki +0.300 - evolve +0.100 = **+0.200**" in summary
    assert "진화 효과(net) = evolve +0.100 - control +0.000 = **+0.100**" in summary
    assert "구조화 위키가 실제로 개선" in summary
    assert summary.count("문서 단위 paired bootstrap") == 2  # 두 비교 모두 유의성 판정


def test_aggregate_wiki_without_evolve_baseline_is_inconclusive(tmp_path):
    records = [_rec("d1", "evolve-wiki", 0.3), _rec("d2", "evolve-wiki", 0.2)]
    batch.aggregate(records, str(tmp_path), generations=3, runs=1, batch_elapsed=60.0)
    summary = (tmp_path / "summary.md").read_text()
    assert "비교 기준(evolve arm)이 없다" in summary
    assert "실제로 개선" not in summary


def test_aggregate_control_without_evolve_prints_no_ghost_net(tmp_path):
    """--arms evolve-wiki,control: 돌지도 않은 evolve의 0.0으로 net을 지어내면 안 된다."""
    records = [_rec("d1", "evolve-wiki", 0.3), _rec("d1", "control", 0.1),
               _rec("d2", "evolve-wiki", 0.2), _rec("d2", "control", 0.0)]
    batch.aggregate(records, str(tmp_path), generations=3, runs=1, batch_elapsed=60.0)
    summary = (tmp_path / "summary.md").read_text()
    assert "진화 효과(net)" not in summary  # 유령 net 없음
    assert "진화 효과는 판정 불가" in summary


def test_aggregate_nohist_without_evolve_prints_no_ghost_net(tmp_path):
    records = [_rec("d1", "evolve-nohist", 0.1), _rec("d2", "evolve-nohist", 0.2)]
    batch.aggregate(records, str(tmp_path), generations=3, runs=1, batch_elapsed=60.0)
    summary = (tmp_path / "summary.md").read_text()
    assert "영속 이력 효과(net)" not in summary
    assert "영속 이력 효과는 판정 불가" in summary
