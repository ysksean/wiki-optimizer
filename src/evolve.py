"""self-evolving 요약 최적화 루프 (Query 기반).

진화 대상(손잡이) = 요약 전략(프롬프트).
채점 = Query 응답 정확도 x 컨텍스트 효율 (scoring.py).

  세대 g:
    1. 현재 전략으로 요약 생성 (Summarizer)
    2. 질문 세트로 채점 (요약만 보고 답 -> 원본근거 정답과 대조) + 효율
    3. 최고 점수면 best 갱신
    4. Reflector: 틀린 질문 + 효율을 근거로 요약 전략을 개선
    5. 개선된 전략으로 다음 세대

질문 세트는 문서당 한 번 만들어 고정(공정 비교).
- 수동: data/questions/<docname>.json 이 있으면 그걸 사용 [{"q","a"}, ...]
- 자동: 없으면 원본에서 자동 생성 (raw 근거)

사용법:
  python3 src/evolve.py data/raw/karpathy-llm-wiki-pattern.md --generations 4
"""

import argparse
import json
import os
import time
from datetime import datetime

import llm
import scoring


SEED_STRATEGY = (
    "다음 문서를 요약하라. 핵심 개념과 사실을 빠짐없이 담되 간결하게 써라."
)


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


def summarize(raw_text, strategy):
    prompt = f"{strategy}\n\n문서:\n{raw_text}\n\n요약:"
    return llm.generate(prompt, num_predict=400, temperature=0.3)


def reflect(strategy, result):
    """채점 결과(틀린 질문 + 효율)를 근거로 요약 전략을 개선."""
    missed = [d["q"] for d in result["qa_details"] if d["score"] < 1]
    missed_str = "\n".join(f"- {q}" for q in missed) if missed else "(없음)"
    ratio = result["length_ratio"]

    hint = ""
    if result["accuracy"] < 1.0:
        hint += "요약이 일부 질문에 답할 정보를 누락했다. 그 정보를 담아라. "
    if ratio > 0.5:
        hint += "요약이 너무 길어 효율이 낮다. 더 압축하라. "

    prompt = (
        "너는 요약 '전략 프롬프트'를 개선하는 최적화기다.\n"
        "목표: 요약만 보고도 아래 질문들에 답할 수 있으면서(정확도), "
        "동시에 최대한 짧게(효율). 둘 다 만족하도록 전략을 개선하라.\n"
        "개선된 '전략 프롬프트' 한 문단만 출력(설명 금지).\n\n"
        f"[현재 전략]\n{strategy}\n\n"
        f"[요약만으로 답 못한 질문들]\n{missed_str}\n\n"
        f"[길이 비율] {ratio} (작을수록 효율↑). {hint}\n\n"
        "[개선된 전략 프롬프트]:"
    )
    new_strategy = llm.generate(prompt, num_predict=300, temperature=0.5)
    return new_strategy.strip() or strategy


def evolve(raw_path, generations=4, n_qa=6, out_dir="runs"):
    raw_text = open(raw_path).read()
    doc = os.path.splitext(os.path.basename(raw_path))[0]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(out_dir, f"{doc}-{stamp}")
    os.makedirs(run_dir, exist_ok=True)

    print(f"[setup] 문서: {doc} ({len(raw_text)} chars)")
    question_set = load_question_set(raw_path, raw_text, n_qa)
    print(f"[setup] 질문 {len(question_set)}개 고정\n")
    if not question_set:
        print("[error] 질문 세트 확보 실패. 중단.")
        return

    strategy = SEED_STRATEGY
    best = {"total": -1.0, "generation": -1, "strategy": None, "summary": None}
    history = []

    for g in range(generations):
        t = time.time()
        summary = summarize(raw_text, strategy)
        result = scoring.score(raw_text, summary, question_set)
        dt = time.time() - t

        improved = result["total"] > best["total"]
        marker = "  <- best" if improved else ""
        print(
            f"[gen {g}] total={result['total']} "
            f"acc={result['accuracy']} eff={result['efficiency']} "
            f"ratio={result['length_ratio']}  ({dt:.0f}s){marker}"
        )

        history.append(
            {
                "generation": g,
                "strategy": strategy,
                "summary": summary,
                "score": {k: v for k, v in result.items() if k != "qa_details"},
                "elapsed_sec": round(dt, 1),
            }
        )

        if improved:
            best = {
                "total": result["total"],
                "generation": g,
                "strategy": strategy,
                "summary": summary,
            }

        if g < generations - 1:
            base = strategy if improved else best["strategy"]
            strategy = reflect(base, result)

    print(f"\n[done] best gen={best['generation']} total={best['total']}")
    print(f"[done] best summary ({len(best['summary'] or '')} chars):\n{best['summary']}\n")

    report = {
        "doc": doc,
        "generations": generations,
        "question_set": question_set,
        "best": best,
        "history": history,
    }
    with open(os.path.join(run_dir, "report.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(os.path.join(run_dir, "best_summary.md"), "w") as f:
        f.write(best["summary"] or "")

    trail = " -> ".join(str(h["score"]["total"]) for h in history)
    print(f"[done] 점수 추이: {trail}")
    print(f"[done] 결과 저장: {run_dir}/")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_path")
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--n-qa", type=int, default=6)
    args = ap.parse_args()
    evolve(args.raw_path, generations=args.generations, n_qa=args.n_qa)
