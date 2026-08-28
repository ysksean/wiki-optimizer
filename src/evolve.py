"""self-evolving 요약 최적화 루프.

진화 대상은 '요약 전략(프롬프트)'이다. 사람이 프롬프트를 튜닝하는 대신,
시스템이 스스로 세대를 거치며 전략을 개선한다:

  세대 g:
    1. 현재 전략으로 요약 생성 (Summarizer)
    2. Verifier로 채점 (요약만으로 Q&A 재구성 정확도 + 압축 효율)
    3. 지금까지 최고 점수면 best 갱신
    4. Reflector: 채점 세부(틀린 Q&A, 길이 비율)를 보고 전략을 수정
    5. 개선된 전략으로 다음 세대

Q&A 벤치마크는 문서당 한 번 생성해 전 세대에서 고정 사용(공정 비교).

사용법:
  python3 src/evolve.py data/raw/karpathy-llm-wiki-pattern.md --generations 4
"""

import argparse
import json
import os
import time
from datetime import datetime

import llm
import verifier


SEED_STRATEGY = (
    "다음 문서를 요약하라. 핵심 개념과 사실을 빠짐없이 담되 간결하게 써라."
)


def summarize(raw_text, strategy):
    """현재 전략(프롬프트)으로 요약을 생성한다."""
    prompt = f"{strategy}\n\n문서:\n{raw_text}\n\n요약:"
    return llm.generate(prompt, num_predict=400, temperature=0.3)


def reflect(strategy, score_result):
    """채점 결과를 보고 요약 전략을 개선한다 (Reflector).

    틀린 Q&A와 길이 비율을 근거로 '무엇을 고칠지'를 LLM이 스스로 판단해
    새 전략 프롬프트를 만든다.
    """
    missed = [d["q"] for d in score_result["qa_details"] if d["score"] < 1]
    missed_str = "\n".join(f"- {q}" for q in missed) if missed else "(없음)"
    ratio = score_result["length_ratio"]

    length_hint = ""
    if ratio > 0.35:
        length_hint = "요약이 너무 길다. 더 압축해라."
    elif ratio < 0.15:
        length_hint = "요약이 너무 짧아 정보가 누락됐다. 핵심을 더 담아라."

    prompt = (
        "너는 요약 '전략 프롬프트'를 개선하는 최적화기다.\n"
        "아래는 현재 전략과 그 성적이다. 요약만 보고도 다음 질문들에 답할 수 있도록 "
        "전략을 개선하라. 개선된 '전략 프롬프트' 한 문단만 출력하라(설명 금지).\n\n"
        f"[현재 전략]\n{strategy}\n\n"
        f"[요약만으로 답 못한 질문들]\n{missed_str}\n\n"
        f"[길이 비율] {ratio} (목표 0.15~0.35). {length_hint}\n\n"
        "[개선된 전략 프롬프트]:"
    )
    new_strategy = llm.generate(prompt, num_predict=300, temperature=0.5)
    return new_strategy.strip() or strategy


def evolve(raw_path, generations=4, n_qa=4, out_dir="runs"):
    raw_text = open(raw_path).read()
    doc_name = os.path.splitext(os.path.basename(raw_path))[0]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(out_dir, f"{doc_name}-{stamp}")
    os.makedirs(run_dir, exist_ok=True)

    print(f"[setup] 문서: {doc_name} ({len(raw_text)} chars)")
    print(f"[setup] Q&A 벤치마크 생성 중...")
    qa_set = verifier.build_qa_set(raw_text, n=n_qa)
    print(f"[setup] Q&A {len(qa_set)}개 고정 완료\n")
    if not qa_set:
        print("[error] Q&A 추출 실패. 중단.")
        return

    strategy = SEED_STRATEGY
    best = {"total": -1.0, "generation": -1, "strategy": None, "summary": None}
    history = []

    for g in range(generations):
        t = time.time()
        summary = summarize(raw_text, strategy)
        result = verifier.score_summary(raw_text, summary, qa_set)
        dt = time.time() - t

        improved = result["total"] > best["total"]
        marker = "  <- best" if improved else ""
        print(
            f"[gen {g}] total={result['total']} "
            f"faith={result['faithfulness']} comp={result['compression']} "
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

        # 마지막 세대가 아니면 반성해서 전략 개선.
        # 퇴화 방지: 개선 실패 시 지금까지 최고 전략에서 다시 반성.
        if g < generations - 1:
            base_strategy = strategy if improved else best["strategy"]
            base_result = result
            strategy = reflect(base_strategy, base_result)

    print(f"\n[done] best gen={best['generation']} total={best['total']}")
    print(f"[done] best summary:\n{best['summary']}\n")

    summary_report = {
        "doc": doc_name,
        "generations": generations,
        "qa_set": qa_set,
        "best": best,
        "history": history,
    }
    with open(os.path.join(run_dir, "report.json"), "w") as f:
        json.dump(summary_report, f, ensure_ascii=False, indent=2)
    with open(os.path.join(run_dir, "best_summary.md"), "w") as f:
        f.write(best["summary"] or "")
    print(f"[done] 결과 저장: {run_dir}/")

    # 점수 추이 한 줄 요약
    trail = " -> ".join(str(h["score"]["total"]) for h in history)
    print(f"[done] 점수 추이: {trail}")
    return summary_report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_path", help="요약할 raw 문서 경로")
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--n-qa", type=int, default=4)
    args = ap.parse_args()
    evolve(args.raw_path, generations=args.generations, n_qa=args.n_qa)
