"""self-evolving 폴더 구조 최적화 루프 (B단계).

진화 손잡이 = 분할 전략(자연어 지시).
채점 = 구조에 대한 Query 성능 (정확도 x 효율).

  세대 g:
    1. Organizer: 현재 분할 전략으로 문서들을 구조화
    2. 채점: 질문 세트로 Router가 파일 선택 -> 답 -> 정확도 + 효율(읽은 글자수)
    3. 최고면 best 갱신
    4. Reflector: 못 맞춘 질문 / 많이 읽은 질문을 근거로 분할 전략 수정
    5. 다음 세대에 재조직

사용법:
  python3 src/evolve_structure.py --docs 3 --generations 2 --n-qa 4
"""

import argparse
import glob
import json
import os
import time
from datetime import datetime

import llm
import provenance
import structure


SEED_STRATEGY = (
    "문서들을 주제별로 3~4개 파일로 나눠라. 관련된 개념은 한 파일에 모으고, "
    "각 파일은 하나의 주제에 집중하게 하라."
)


def load_docs(n, raw_dir="data/raw", files=None):
    """files를 직접 주면 그걸 쓰고, 없으면 raw_dir에서 작은 순으로 n개."""
    if files is None:
        files = [
            f for f in glob.glob(os.path.join(raw_dir, "*.md"))
            if os.path.basename(f).lower() != "readme.md"
        ]
        files.sort(key=lambda f: os.path.getsize(f))
        files = files[:n]
    return {os.path.splitext(os.path.basename(f))[0]: open(f).read() for f in files}


def reflect(strategy, result):
    """채점 결과를 보고 분할 전략을 개선한다."""
    missed = [d for d in result["details"] if d.get("score", 0) < 1]
    heavy = sorted(result["details"], key=lambda d: d["read_chars"], reverse=True)[:2]

    missed_str = "\n".join(
        f"- Q:{d['q']} (고른 파일:{d['picked']})" for d in missed
    ) or "(없음)"
    heavy_str = "\n".join(
        f"- Q:{d['q']} 읽은글자:{d['read_chars']} (파일:{d['picked']})" for d in heavy
    )

    prompt = (
        "너는 지식베이스 '분할 전략'을 개선하는 최적화기다.\n"
        "목표: 질문에 답할 정보가 적절한 파일에 모여 있어서 (1) 정확히 답하고 "
        "(2) 적게 읽도록 구조를 개선하는 것.\n"
        "개선된 '분할 전략' 한 문단만 출력(설명 금지).\n\n"
        f"[현재 전략]\n{strategy}\n\n"
        f"[답 못한 질문들 — 관련 정보가 흩어졌거나 엉뚱한 파일 선택]\n{missed_str}\n\n"
        f"[많이 읽은 질문들 — 파일이 너무 크거나 관련없는 내용 섞임]\n{heavy_str}\n\n"
        f"[현재 파일 수] {result['n_files']}, 정확도 {result['accuracy']}, 효율 {result['efficiency']}\n\n"
        "[개선된 분할 전략]:"
    )
    new_strategy = llm.generate(prompt, num_predict=300, temperature=0.5)
    return new_strategy.strip() or strategy


def evolve_structure(n_docs=3, generations=2, n_qa=4, out_dir="runs", files=None):
    docs = load_docs(n_docs, files=files)
    total_raw = sum(len(t) for t in docs.values())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(out_dir, f"structure-{stamp}")
    os.makedirs(run_dir, exist_ok=True)

    print(f"[setup] 문서 {len(docs)}개, 총 {total_raw} chars: {list(docs.keys())}")
    print("[setup] 문서 전체에 걸친 질문 세트 생성 중...")
    question_set = structure.build_cross_question_set(docs, n=n_qa)
    print(f"[setup] 질문 {len(question_set)}개 고정")
    for qa in question_set:
        print(f"   - {qa['q']}")
    print()
    if not question_set:
        print("[error] 질문 세트 실패. 중단.")
        return

    strategy = SEED_STRATEGY
    # reflect에 넘길 채점 결과는 반드시 best 전략과 짝이어야 한다 → 함께 저장
    best = {"total": -1.0, "generation": -1, "strategy": None, "struct": None,
            "result": None}
    history = []
    parse_failed_gens = []

    for g in range(generations):
        t = time.time()
        struct = structure.organize(docs, strategy)
        result = structure.score_structure(struct, question_set, total_raw)
        dt = time.time() - t

        # 판정 파싱 실패 세대는 점수가 자리만 채운 0이다 — best 후보에서 제외
        parse_failed = bool(result.get("parse_failed"))
        if parse_failed:
            parse_failed_gens.append(g)
        improved = (not parse_failed) and result["total"] > best["total"]
        marker = "  <- best" if improved else ("  (judge 파싱 실패 — 제외)" if parse_failed else "")
        print(
            f"[gen {g}] total={result['total']} acc={result['accuracy']} "
            f"eff={result['efficiency']} files={result['n_files']} "
            f"avg_read={result['avg_read']}  ({dt:.0f}s){marker}"
        )

        history.append({
            "generation": g,
            "strategy": strategy,
            "n_files": result["n_files"],
            "file_titles": [f["title"] for f in struct.get("files", [])],
            "score": {k: v for k, v in result.items() if k != "details"},
            "elapsed_sec": round(dt, 1),
        })

        if improved:
            best = {"total": result["total"], "generation": g, "strategy": strategy,
                    "struct": struct, "result": result}

        with open(os.path.join(run_dir, "progress.json"), "w") as pf:
            json.dump({
                "mode": "structure", "docs": list(docs.keys()),
                "generations": generations, "done_generations": g + 1,
                "best_gen": best["generation"], "best_total": best["total"],
                "history": history,
            }, pf, ensure_ascii=False)

        if g < generations - 1 and best["strategy"] is not None:
            # 유효한 best가 없으면(전 세대 판정 실패) 현재 전략을 그대로 재시도
            strategy = reflect(best["strategy"], best["result"])

    print(f"\n[done] best gen={best['generation']} total={best['total']}")
    best_files = best["struct"]["files"] if best["struct"] else []
    print(f"[done] best 구조 파일: {[f['title'] for f in best_files]}")
    if parse_failed_gens:
        print(f"[warn] judge 파싱 실패 세대: {parse_failed_gens} — 이 run의 점수는 신뢰 불가")

    report = {
        "docs": list(docs.keys()),
        "provenance": provenance.collect(
            question_set=question_set,
            params={"generations": generations, "n_qa": n_qa, "n_docs": n_docs},
        ),
        "total_raw_chars": total_raw,
        "generations": generations,
        "question_set": question_set,
        "parse_failed": bool(parse_failed_gens),
        "parse_failed_generations": parse_failed_gens,
        "best": best,
        "history": history,
    }
    with open(os.path.join(run_dir, "report.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    trail = " -> ".join(str(h["score"]["total"]) for h in history)
    print(f"[done] 점수 추이: {trail}")
    print(f"[done] 결과 저장: {run_dir}/")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=3)
    ap.add_argument("--generations", type=int, default=2)
    ap.add_argument("--n-qa", type=int, default=4)
    args = ap.parse_args()
    evolve_structure(n_docs=args.docs, generations=args.generations, n_qa=args.n_qa)
