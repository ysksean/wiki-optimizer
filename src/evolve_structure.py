"""self-evolving 폴더 구조 최적화 루프 (B단계).

진화 손잡이 = 분할 전략(자연어 지시).
채점 = 구조에 대한 Query 성능 (정확도 x 효율).

control 모드(no_evolve=True): reflect를 건너뛰고 seed 전략을 세대 수만큼
재샘플링만 한다. A단계(evolve.py)와 같은 의미의 노이즈 max 기준선이다.

  세대 g:
    1. Organizer: 현재 분할 전략으로 문서들을 구조화
    2. 채점: 질문 세트로 Router가 파일 선택 -> 답 -> 정확도 + 효율(읽은 글자수)
       질문은 train/held-out으로 나눈다(evolve.split_questions, 묶음 이름 시드).
       held-out 점수가 best 판정·리포트 점수, train 점수는 Reflector 전용.
    3. 최고면 best 갱신 (held-out 기준)
    4. Reflector: train에서 못 맞춘 질문 / 많이 읽은 질문을 근거로 분할 전략 수정
    5. 다음 세대에 재조직

사용법:
  python3 src/evolve_structure.py --docs 3 --generations 2 --n-qa 4
  python3 src/evolve_structure.py --docs 3 --generations 2 --control
"""

import argparse
import glob
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import llm
import provenance
import structure
import evolve


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


def evolve_structure(n_docs=3, generations=2, n_qa=4, out_dir="runs", files=None,
                     no_evolve=False, question_set=None):
    """question_set을 넘기면 그걸 쓴다 (배치에서 arm/run 간 동일 세트 보장)."""
    docs = load_docs(n_docs, files=files)
    total_raw = sum(len(t) for t in docs.values())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    arm = "control" if no_evolve else "evolve"
    run_dir = os.path.join(out_dir, f"structure-{arm}-{stamp}")
    os.makedirs(run_dir, exist_ok=True)

    print(f"[setup] 문서 {len(docs)}개, 총 {total_raw} chars: {list(docs.keys())}  arm={arm}")
    if question_set is None:
        print("[setup] 문서 전체에 걸친 질문 세트 생성 중...")
        question_set = structure.build_cross_question_set(docs, n=n_qa)
    if not question_set:
        print("[error] 질문 세트 실패. 중단.")
        return
    # train/held-out 분리 — A 모드(evolve.split_questions)와 같은 규칙, 시드는 문서 묶음 이름.
    # reflect는 train 판정만 보고, best 판정과 리포트 점수는 held-out으로만 낸다.
    bundle = "+".join(docs.keys())
    train_qs, test_qs = evolve.split_questions(question_set, bundle)
    degenerate = train_qs is test_qs or len(train_qs) == len(question_set)
    question_split = {"train": len(train_qs), "heldout": len(test_qs), "degenerate": degenerate,
                      "heldout_questions": [qa["q"] for qa in test_qs]}
    print(f"[setup] 질문 {len(question_set)}개 고정 → train {len(train_qs)} / held-out {len(test_qs)}"
          + ("  (질문이 적어 분리 포기 — 전부 겸용)" if degenerate else ""))
    for qa in question_set:
        tag = "H" if qa in test_qs and not degenerate else "T"
        print(f"   - [{tag}] {qa['q']}")
    print()

    strategy = SEED_STRATEGY
    # reflect에 넘길 채점 결과(train)는 반드시 best 전략과 짝이어야 한다 → 함께 저장.
    # best["result"]는 held-out 결과(리포트·UI용), best["train_result"]는 reflect용.
    best = {"total": -1.0, "generation": -1, "strategy": None, "struct": None,
            "result": None, "train_result": None}
    history = []
    parse_failed_gens = []

    for g in range(generations):
        t = time.time()
        struct = structure.organize(docs, strategy)
        # held-out은 항상, train은 reflect가 필요한 arm(evolve)에서만 — control은 채점 절약.
        # 질문이 적어 분리를 포기한 경우(degenerate) 한 번만 채점해 둘 다로 쓴다.
        if no_evolve or degenerate:
            result = structure.score_structure(struct, test_qs, total_raw)
            r_train = result if degenerate and not no_evolve else None
        else:
            with ThreadPoolExecutor(max_workers=2) as ex:
                fut_train = ex.submit(structure.score_structure, struct, train_qs, total_raw)
                fut_test = ex.submit(structure.score_structure, struct, test_qs, total_raw)
                r_train = fut_train.result()
                result = fut_test.result()
        dt = time.time() - t

        # 판정 파싱 실패 세대는 점수가 자리만 채운 0이다 — best 후보에서 제외
        parse_failed = bool(result.get("parse_failed")
                            or (r_train is not None and r_train.get("parse_failed")))
        if parse_failed:
            parse_failed_gens.append(g)
        improved = (not parse_failed) and result["total"] > best["total"]
        marker = "  <- best" if improved else ("  (judge 파싱 실패 — 제외)" if parse_failed else "")
        train_part = f"train={r_train['total']} (acc={r_train['accuracy']}) " if r_train is not None else ""
        print(
            f"[gen {g}] held-out={result['total']} acc={result['accuracy']} "
            + train_part
            + f"eff={result['efficiency']} files={result['n_files']} "
            f"avg_read={result['avg_read']}  ({dt:.0f}s){marker}"
        )

        history.append({
            "generation": g,
            "strategy": strategy,
            "n_files": result["n_files"],
            "file_titles": [f["title"] for f in struct.get("files", [])],
            # 시도별 구조 요약 — 제목·출처·글자수 (본문은 best만 struct에 남긴다)
            "files": [{"title": f["title"], "sources": f.get("sources", []), "n_chars": len(f.get("content", ""))}
                      for f in struct.get("files", [])],
            # score/details = held-out (채택 판단 기준). train_*는 reflect가 본 것.
            "score": {k: v for k, v in result.items() if k != "details"},
            "details": result.get("details", []),
            "train_score": ({k: v for k, v in r_train.items() if k != "details"}
                            if r_train is not None else None),
            "train_details": r_train.get("details", []) if r_train is not None else [],
            "elapsed_sec": round(dt, 1),
        })

        if improved:
            best = {"total": result["total"], "generation": g, "strategy": strategy,
                    "struct": struct, "result": result, "train_result": r_train}

        with open(os.path.join(run_dir, "progress.json"), "w") as pf:
            json.dump({
                "mode": "structure", "arm": arm, "docs": list(docs.keys()),
                "total_raw_chars": total_raw, "question_set": question_set,
                "question_split": question_split,
                "generations": generations, "done_generations": g + 1,
                "best_gen": best["generation"], "best_total": best["total"],
                "history": history,
            }, pf, ensure_ascii=False)

        if not no_evolve and g < generations - 1 and best["strategy"] is not None:
            # 유효한 best가 없으면(전 세대 판정 실패) 현재 전략을 그대로 재시도.
            # reflect는 train 판정만 본다 — held-out 실패를 보여주면 채택 점수가 오염된다.
            strategy = reflect(best["strategy"], best["train_result"] or best["result"])

    print(f"\n[done] best gen={best['generation']} total={best['total']}")
    best_files = best["struct"]["files"] if best["struct"] else []
    print(f"[done] best 구조 파일: {[f['title'] for f in best_files]}")
    if parse_failed_gens:
        print(f"[warn] judge 파싱 실패 세대: {parse_failed_gens} — 이 run의 점수는 신뢰 불가")

    report = {
        "docs": list(docs.keys()),
        "arm": arm,
        "provenance": provenance.collect(
            question_set=question_set,
            params={"generations": generations, "n_qa": n_qa, "n_docs": n_docs,
                    "no_evolve": no_evolve},
        ),
        "total_raw_chars": total_raw,
        "generations": generations,
        "question_set": question_set,
        "question_split": question_split,
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
    ap.add_argument("--control", action="store_true", help="진화 없이 seed 재샘플링(대조군)")
    args = ap.parse_args()
    evolve_structure(n_docs=args.docs, generations=args.generations, n_qa=args.n_qa,
                     no_evolve=args.control)
