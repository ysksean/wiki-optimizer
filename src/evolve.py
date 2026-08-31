"""self-evolving 요약 최적화 루프 (Query 기반).

진화 대상(손잡이) = 요약 전략(프롬프트).
채점 = Query 응답 정확도 x 컨텍스트 효율 (scoring.py).

과적합 방지 — 질문 세트를 train/held-out으로 나눈다:
  - train:    Reflector가 보는 피드백(틀린 질문). 전략은 이쪽에 맞춰 진화한다.
  - held-out: best 판정과 최종 보고에만 쓴다. Reflector에게 절대 노출하지 않는다.
  → held-out 점수가 올라야 "그 질문들에 암기"가 아닌 일반화된 개선이다.

  세대 g:
    1. 현재 전략으로 요약 생성 (Summarizer)
    2. train/held-out 각각 채점 (요약만 보고 답 -> 원본근거 정답과 대조) + 효율
    3. held-out 최고 점수면 best 갱신 (train 채점결과도 함께 저장)
    4. Reflector: *best 전략의 train 채점결과*를 근거로 전략 개선
    5. 개선된 전략으로 다음 세대

control 모드(no_evolve=True): reflect를 건너뛰고 seed 전략을 세대 수만큼
재샘플링만 한다. 진화 없이도 노이즈 max로 생기는 "가짜 향상폭"의 기준선이 된다.

질문 세트는 문서당 한 번 만들어 고정(공정 비교).
- 수동: data/questions/<docname>.json 이 있으면 그걸 사용 [{"q","a"}, ...]
- 자동: 없으면 원본에서 자동 생성 (raw 근거)

사용법:
  python3 src/evolve.py data/raw/karpathy-llm-wiki-pattern.md --generations 4
"""

import argparse
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import llm
import scoring


SEED_STRATEGY = (
    "다음 문서를 요약하라. 핵심 개념과 사실을 빠짐없이 담되 간결하게 써라."
)

HOLDOUT_RATIO = 0.4  # 질문 중 held-out 비율 (최소 2개 보장)

# 영속 지식층 (WikiSkill 스타일): 전략 채택/기각 이력을 run 경계를 넘어 축적한다.
# 전략(산출물)은 세대마다 교체되지만 이 이력은 절대 지우지 않는다 — reflect가
# 과거에 기각된 방향을 다시 제안하지 않도록 근거를 제공한다.
WIKI_DIR = os.path.join("runs", "wiki")
IMPACT_PATH = os.path.join(WIKI_DIR, "strategy-impact.jsonl")

# 이력 파일은 모든 문서/arm이 공유한다 — batch --parallel 시 append/read 경합 방지
_IMPACT_LOCK = threading.Lock()


def record_strategy_impact(doc, arm, generation, strategy, r_test, accepted):
    """세대 하나의 전략과 결과를 영속 이력에 append한다."""
    os.makedirs(WIKI_DIR, exist_ok=True)
    rec = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "doc": doc,
        "arm": arm,
        "generation": generation,
        "strategy": strategy,
        "held_out_total": r_test["total"],
        "length_ratio": r_test["length_ratio"],
        "accepted": accepted,
    }
    with _IMPACT_LOCK, open(IMPACT_PATH, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_strategy_history(doc, n_rejected=5, n_accepted=3):
    """이 문서의 과거 전략 이력 (evolve arm만). 반환: (accepted, rejected) 최근순."""
    if not os.path.exists(IMPACT_PATH):
        return [], []
    entries = []
    with _IMPACT_LOCK, open(IMPACT_PATH) as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("doc") == doc and e.get("arm") == "evolve":
                entries.append(e)
    accepted = [e for e in entries if e.get("accepted")][-n_accepted:]
    rejected = [e for e in entries if not e.get("accepted")][-n_rejected:]
    return accepted, rejected


def _history_block(doc):
    """reflect 프롬프트에 넣을 과거 이력 요약. 이력이 없으면 빈 문자열."""
    accepted, rejected = load_strategy_history(doc)
    if not accepted and not rejected:
        return ""
    fmt = lambda e: (
        f"- (held-out {e['held_out_total']}, ratio {e['length_ratio']}) "
        f"{e['strategy'][:200]}"
    )
    lines = []
    if accepted:
        lines.append("[과거 채택된 전략들 — 이 방향이 효과가 있었다]")
        lines += [fmt(e) for e in accepted]
    if rejected:
        lines.append("[과거 기각된 전략들 — 같은 방향을 다시 제안하지 마라]")
        lines += [fmt(e) for e in rejected]
        if any(e["length_ratio"] >= 1.0 for e in rejected):
            lines.append(
                "(주의: ratio ≥ 1.0은 요약이 원본보다 길어진 폭주다. "
                "길이를 늘리는 방향은 금지.)"
            )
    return "\n".join(lines) + "\n\n"


def load_question_set(raw_path, raw_text, n_qa):
    """수동 질문 파일이 있으면 로드, 없으면 자동 생성."""
    doc = os.path.splitext(os.path.basename(raw_path))[0]
    manual = os.path.join("data", "questions", f"{doc}.json")
    if os.path.exists(manual):
        with open(manual) as f:
            qs = json.load(f)
        qs = [x for x in qs if "q" in x and "a" in x]
        if qs:
            print(f"[setup] 수동 질문 세트 사용: {manual} ({len(qs)}개)")
            return qs
    print("[setup] 자동 질문 세트 생성 중...")
    return scoring.build_question_set(raw_text, n=n_qa)


def split_questions(question_set, doc, holdout_ratio=HOLDOUT_RATIO):
    """질문을 train/held-out으로 나눈다. 문서명 기반 고정 시드(재현 가능)."""
    qs = list(question_set)
    rng = random.Random(doc)  # 같은 문서 = 같은 분할
    rng.shuffle(qs)
    n_test = max(2, round(len(qs) * holdout_ratio))
    if n_test >= len(qs):  # 질문이 너무 적으면 분리 포기(전부 train 겸용)
        return qs, qs
    return qs[n_test:], qs[:n_test]


def summarize(raw_text, strategy):
    prompt = f"{strategy}\n\n문서:\n{raw_text}\n\n요약:"
    return llm.generate(prompt, num_predict=400, temperature=0.3)


def reflect(strategy, train_result, doc=None):
    """train 채점 결과(틀린 질문 + 효율)를 근거로 요약 전략을 개선.

    train_result는 반드시 *strategy로 만든 요약*의 채점 결과여야 한다.
    (held-out 질문은 여기 절대 노출하지 않는다.)
    doc을 주면 영속 이력(채택/기각 전략)을 함께 제공해 실패 방향의
    재제안을 막는다.
    """
    missed = [d["q"] for d in train_result["qa_details"] if d["score"] < 1]
    missed_str = "\n".join(f"- {q}" for q in missed) if missed else "(없음)"
    ratio = train_result["length_ratio"]

    hint = ""
    if train_result["accuracy"] < 1.0:
        hint += "요약이 일부 질문에 답할 정보를 누락했다. 그 정보를 담아라. "
    if ratio > 0.5:
        hint += "요약이 너무 길어 효율이 낮다. 더 압축하라. "

    prompt = (
        "너는 요약 '전략 프롬프트'를 개선하는 최적화기다.\n"
        "목표: 요약만 보고도 아래 질문들에 답할 수 있으면서(정확도), "
        "동시에 최대한 짧게(효율). 둘 다 만족하도록 전략을 개선하라.\n"
        "주의: 아래 질문들은 예시일 뿐이다. 그 질문들만 노리는 전략이 아니라, "
        "문서의 어떤 질문에도 통할 일반적인 요약 전략을 써라.\n"
        "개선된 '전략 프롬프트' 한 문단만 출력(설명 금지).\n\n"
        + (_history_block(doc) if doc else "")
        + f"[현재 전략]\n{strategy}\n\n"
        f"[요약만으로 답 못한 질문들]\n{missed_str}\n\n"
        f"[길이 비율] {ratio} (작을수록 효율↑). {hint}\n\n"
        "[개선된 전략 프롬프트]:"
    )
    new_strategy = llm.generate(prompt, num_predict=300, temperature=0.5)
    return new_strategy.strip() or strategy


def _flush_progress(run_dir, payload):
    """세대마다 진행 상태를 남긴다 (웹 대시보드 폴링용)."""
    with open(os.path.join(run_dir, "progress.json"), "w") as f:
        json.dump(payload, f, ensure_ascii=False)


def evolve(raw_path, generations=4, n_qa=8, out_dir="runs", no_evolve=False,
           question_set=None, use_history=True):
    """question_set을 넘기면 그걸 쓴다 (배치에서 arm/run 간 동일 세트 보장).

    use_history=False면 reflect가 영속 이력을 읽지 않고, 기록도
    'evolve-nohist' arm으로 남겨 이력을 쓰는 run을 오염시키지 않는다
    (ablation 용도).
    """
    raw_text = open(raw_path).read()
    doc = os.path.splitext(os.path.basename(raw_path))[0]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    arm = "control" if no_evolve else ("evolve" if use_history else "evolve-nohist")
    run_dir = os.path.join(out_dir, f"{doc}-{arm}-{stamp}")
    os.makedirs(run_dir, exist_ok=True)

    print(f"[setup] 문서: {doc} ({len(raw_text)} chars)  arm={arm}")
    if question_set is None:
        question_set = load_question_set(raw_path, raw_text, n_qa)
    if not question_set:
        print("[error] 질문 세트 확보 실패. 중단.")
        return
    train_qs, test_qs = split_questions(question_set, doc)
    print(f"[setup] 질문 {len(question_set)}개 → train {len(train_qs)} / held-out {len(test_qs)}\n")

    strategy = SEED_STRATEGY
    # best 판정은 held-out total. reflect용으로 train_result도 함께 보관한다.
    best = {"total": -1.0, "generation": -1, "strategy": None, "summary": None,
            "train_result": None}
    history = []
    parse_failed_gens = []

    for g in range(generations):
        t = time.time()
        summary = summarize(raw_text, strategy)
        # train/held-out 채점은 서로 독립 (공유 상태 없음) — 병렬로 세대당
        # LLM 4회(답변+판정 x2)를 2회 폭으로 접는다. 순서·결과는 동일하다.
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_train = ex.submit(scoring.score, raw_text, summary, train_qs)
            fut_test = ex.submit(scoring.score, raw_text, summary, test_qs)
            r_train = fut_train.result()
            r_test = fut_test.result()
        dt = time.time() - t

        # 판정 파싱 실패 세대는 점수가 자리만 채운 0이다 — best 후보·영속 이력에서 제외
        parse_failed = bool(r_train.get("parse_failed") or r_test.get("parse_failed"))
        if parse_failed:
            parse_failed_gens.append(g)
        improved = (not parse_failed) and r_test["total"] > best["total"]
        marker = "  <- best" if improved else ("  (judge 파싱 실패 — 제외)" if parse_failed else "")
        print(
            f"[gen {g}] held-out={r_test['total']} (acc={r_test['accuracy']}) "
            f"train={r_train['total']} (acc={r_train['accuracy']}) "
            f"eff={r_test['efficiency']} ratio={r_test['length_ratio']}  ({dt:.0f}s){marker}"
        )

        history.append(
            {
                "generation": g,
                "strategy": strategy,
                "summary": summary,
                "score": {k: v for k, v in r_test.items() if k != "qa_details"},
                "train_score": {k: v for k, v in r_train.items() if k != "qa_details"},
                "elapsed_sec": round(dt, 1),
            }
        )

        if improved:
            best = {
                "total": r_test["total"],
                "generation": g,
                "strategy": strategy,
                "summary": summary,
                "train_result": r_train,
            }

        if not parse_failed:
            record_strategy_impact(doc, arm, g, strategy, r_test, improved)

        _flush_progress(run_dir, {
            "mode": "summary", "doc": doc, "arm": arm,
            "generations": generations, "done_generations": g + 1,
            "best_gen": best["generation"], "best_total": best["total"],
            "history": [
                {k: h[k] for k in ("generation", "strategy", "score", "train_score", "elapsed_sec")}
                for h in history
            ],
        })

        if not no_evolve and g < generations - 1 and best["strategy"] is not None:
            # 점수가 안 올랐으면 best 전략으로 되돌리되, 피드백도
            # *그 best 전략의* train 채점 결과를 쓴다 (전략-결과 짝 유지).
            # (아직 유효한 best가 없으면 — 전 세대 판정 실패 — 현재 전략을 그대로 재시도)
            strategy = reflect(best["strategy"], best["train_result"],
                               doc=doc if use_history else None)

    print(f"\n[done] best gen={best['generation']} held-out total={best['total']}")
    print(f"[done] best summary ({len(best['summary'] or '')} chars):\n{best['summary']}\n")
    if parse_failed_gens:
        print(f"[warn] judge 파싱 실패 세대: {parse_failed_gens} — 이 run의 점수는 집계에서 제외해야 한다")

    report = {
        "doc": doc,
        "arm": arm,
        "generations": generations,
        "train_questions": train_qs,
        "holdout_questions": test_qs,
        "parse_failed": bool(parse_failed_gens),
        "parse_failed_generations": parse_failed_gens,
        "best": {k: v for k, v in best.items() if k != "train_result"},
        "history": history,
    }
    with open(os.path.join(run_dir, "report.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(os.path.join(run_dir, "best_summary.md"), "w") as f:
        f.write(best["summary"] or "")

    trail = " -> ".join(str(h["score"]["total"]) for h in history)
    print(f"[done] held-out 점수 추이: {trail}")
    print(f"[done] 결과 저장: {run_dir}/")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_path")
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--n-qa", type=int, default=8)
    ap.add_argument("--control", action="store_true", help="진화 없이 seed 재샘플링(대조군)")
    args = ap.parse_args()
    evolve(args.raw_path, generations=args.generations, n_qa=args.n_qa,
           no_evolve=args.control)
