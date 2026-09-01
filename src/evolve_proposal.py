"""Stage 0 진입점 — 레포/데이터 + 태스크 → 위키 구조 제안 + self-evolving.

진화 손잡이 = 분할 전략(자연어 지시). 채점 = 제안 구조에 대한 Query 성능
(grounded 질문 정확도 x 읽기 효율). gap 질문(소스에 정답 근거가 없는 것)은
채점에서 빼되 구조 제안의 status:gap 페이지 근거로 쓴다.

control 모드(--control): reflect를 건너뛰고 seed 전략을 세대 수만큼
재샘플링만 한다 — evolve_structure와 같은 의미의 노이즈 max 기준선.

grounded 질문이 0개면(순수 백지 — 소스에 정답이 하나도 없음) 채점이
불가능하므로 1세대 단발 제안으로 강등하고 리포트에 이유를 남긴다.

warm start: --seed-from-runs가 runs/**/report.json에서 이전 proposal run의
best 전략을 찾아 seed로 쓴다 (apply.best_strategy_from_runs와 같은 관례).

사용법:
  python3 src/evolve_proposal.py --source ~/aipmop --task-file task.txt
  python3 src/evolve_proposal.py --source <dir> --task "..." --generations 3
  python3 src/evolve_proposal.py --source <dir> --task "..." --control
"""

import argparse
import glob
import json
import os
import time
from datetime import datetime

import llm
import proposal
import provenance
import repo_map
import skeleton
import task_questions

SEED_STRATEGY = (
    "태스크의 소비자가 실제로 던질 질문 단위로 페이지를 나눠라. 한 페이지는 "
    "하나의 질문 축에 집중하고, 관련 축은 같은 폴더로 묶어라. 근거 소스가 "
    "없는 축도 필요하면 gap 페이지로 남겨라."
)


def best_proposal_strategy_from_runs(runs_dir="runs"):
    """이전 proposal run 전체에서 best total이 가장 높은 분할 전략."""
    best = None
    for p in glob.glob(os.path.join(runs_dir, "**", "report.json"), recursive=True):
        try:
            with open(p) as f:
                r = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if r.get("mode") != "proposal":
            continue
        b = r.get("best") or {}
        strategy, total = b.get("strategy"), b.get("total")
        if not strategy or not isinstance(total, (int, float)):
            continue
        if best is None or total > best["total"]:
            best = {"strategy": strategy, "total": total, "source": p}
    return best


def reflect(strategy, result, gap_questions):
    """채점 결과 + gap을 보고 분할 전략을 개선한다."""
    missed = [d for d in result["details"] if d.get("score", 0) < 1]
    heavy = sorted(result["details"], key=lambda d: d["read_chars"], reverse=True)[:2]
    missed_str = "\n".join(
        f"- Q:{d['q']} (고른 페이지:{d['picked']}, 읽은글자:{d['read_chars']})"
        for d in missed
    ) or "(없음)"
    heavy_str = "\n".join(
        f"- Q:{d['q']} 읽은글자:{d['read_chars']} (페이지:{d['picked']})"
        for d in heavy
    )
    gap_str = "\n".join(f"- {q}" for q in gap_questions) or "(없음)"
    prompt = (
        "너는 위키 구조의 '분할 전략'을 개선하는 최적화기다.\n"
        "목표: 질문이 옳은 페이지로 라우팅되고, 그 페이지의 sources가 정답이 "
        "있는 원본 구간을 정확히(좁게) 가리키도록 전략을 개선하는 것.\n"
        "못 맞춘 질문은 (a) purpose가 모호해 엉뚱한 페이지를 골랐거나 "
        "(b) sources에 정답 구간이 없는 것이다 — 읽은 글자수가 크면서 틀렸으면 "
        "(a)보다 (b)를 의심하라. 많이 읽은 질문은 sources가 너무 넓다 — "
        "문서는 '경로#헤딩'으로 섹션까지 좁히게 하라.\n"
        "개선된 '분할 전략' 한 문단만 출력(설명 금지).\n\n"
        f"[현재 전략]\n{strategy}\n\n"
        f"[못 맞춘 질문들]\n{missed_str}\n\n"
        f"[많이 읽은 질문들]\n{heavy_str}\n\n"
        f"[근거 없는 질문들 — gap 페이지가 감당해야 할 축]\n{gap_str}\n\n"
        f"[현재 페이지 수] {result['n_pages']}, 정확도 {result['accuracy']}, "
        f"효율 {result['efficiency']}\n\n"
        "[개선된 분할 전략]:"
    )
    new_strategy = llm.generate(prompt, num_predict=300, temperature=0.5)
    return new_strategy.strip() or strategy


def run_evolve(sources, task, generations=3, n_qa=8, strategy=None,
               out_dir="runs", write_dir=None, max_files=400, use_cache=True,
               no_evolve=False):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    arm = "control" if no_evolve else "evolve"
    run_dir = os.path.join(out_dir, f"proposal-{arm}-{stamp}")
    os.makedirs(run_dir, exist_ok=True)
    strategy = strategy or SEED_STRATEGY

    entries = repo_map.build_map(sources, max_files=max_files, use_cache=use_cache)
    n_doc = sum(1 for e in entries if e["kind"] == "doc")
    print(f"[map] 파일 {len(entries)}개 (doc {n_doc}, code {len(entries) - n_doc})"
          f"  arm={arm}")
    if not entries:
        print("[error] 소스에서 파일을 찾지 못함. 중단.")
        return None

    print(f"[qa] 태스크 질문 {n_qa}개 생성 + oracle 정답 확인 중...")
    qa, gaps = task_questions.get_question_set(task, entries, n=n_qa,
                                              use_cache=use_cache)
    if not qa:
        print("[error] 질문 세트 실패. 중단.")
        return None
    for x in qa:
        print(f"   [{'gap' if x['a'] is None else 'ok '}] {x['q']}")
    n_grounded = len(qa) - len(gaps)
    print(f"[qa] grounded {n_grounded} / gap {len(gaps)}")

    scoreable = n_grounded > 0
    if not scoreable:
        print("[warn] grounded 질문 0개 — 채점 불가. 1세대 단발 제안으로 강등.")
        generations = 1

    best = {"total": -1.0, "generation": -1, "strategy": None, "pages": None,
            "result": None}
    history, parse_failed_gens = [], []

    for g in range(generations):
        t = time.time()
        pages, vstats = proposal.propose(task, entries, strategy,
                                         gap_questions=gaps)
        if not pages:
            print(f"[gen {g}] 제안 파싱 실패 — 건너뜀")
            parse_failed_gens.append(g)
            history.append({"generation": g, "strategy": strategy,
                            "propose_failed": True})
            continue
        result = proposal.score_proposal(pages, qa, entries)
        dt = time.time() - t

        parse_failed = bool(result.get("parse_failed"))
        if parse_failed:
            parse_failed_gens.append(g)
        total = result["total"] if result["total"] is not None else -1.0
        improved = (not parse_failed) and (
            total > best["total"] or best["pages"] is None)
        marker = "  <- best" if improved else (
            "  (judge 파싱 실패 — 제외)" if parse_failed else "")
        print(f"[gen {g}] total={result['total']} acc={result['accuracy']} "
              f"eff={result['efficiency']} pages={result['n_pages']} "
              f"gap_pages={sum(1 for p in pages if p['status'] == 'gap')} "
              f"avg_read={result['avg_read']}  ({dt:.0f}s){marker}")

        history.append({
            "generation": g, "strategy": strategy,
            "page_paths": [p["path"] for p in pages],
            "validate_stats": vstats,
            "score": {k: v for k, v in result.items() if k != "details"},
            "elapsed_sec": round(dt, 1),
        })
        if improved:
            best = {"total": total, "generation": g, "strategy": strategy,
                    "pages": pages, "result": result}

        with open(os.path.join(run_dir, "progress.json"), "w") as pf:
            json.dump({"mode": "proposal", "arm": arm,
                       "generations": generations, "done_generations": g + 1,
                       "best_gen": best["generation"], "best_total": best["total"],
                       "history": history}, pf, ensure_ascii=False)

        if scoreable and not no_evolve and g < generations - 1 \
                and best["pages"] is not None and best["result"]["total"] is not None:
            strategy = reflect(best["strategy"], best["result"], gaps)

    if best["pages"] is None:
        print("[error] 유효한 제안이 한 세대도 없음.")
        return None

    sk = skeleton.write_skeleton(best["pages"], write_dir or "(dry-run)",
                                 write=bool(write_dir), run_id=stamp)
    print(f"\n[best gen={best['generation']}] tree:\n{sk['tree']}\n")
    if write_dir:
        print(f"[write] 생성 {len(sk['written'])}개, 기존 파일 skip "
              f"{len(sk['skipped'])}개 → {write_dir}")
    if parse_failed_gens:
        print(f"[warn] 파싱 실패 세대: {parse_failed_gens} — 이 run의 점수는 신뢰 불가")

    report = {
        "mode": "proposal",
        "arm": arm,
        "sources": [os.path.abspath(os.path.expanduser(s)) for s in sources],
        "task": task,
        "scoreable": scoreable,
        "provenance": provenance.collect(
            question_set=[{"q": x["q"], "a": x["a"] or ""} for x in qa],
            params={"n_qa": n_qa, "generations": generations,
                    "max_files": max_files, "no_evolve": no_evolve},
        ),
        "n_files_mapped": len(entries),
        "question_set": qa,
        "gap_questions": gaps,
        "generations": generations,
        "parse_failed": bool(parse_failed_gens),
        "parse_failed_generations": parse_failed_gens,
        "best": {"total": best["total"] if scoreable else None,
                 "generation": best["generation"],
                 "strategy": best["strategy"],
                 "pages": best["pages"],
                 "score": {k: v for k, v in best["result"].items()
                           if k != "details"} if best["result"] else None},
        "history": history,
        "skeleton": {"written": sk["written"], "skipped": sk["skipped"],
                     "write_dir": write_dir},
    }
    with open(os.path.join(run_dir, "report.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    trail = " -> ".join(str(h.get("score", {}).get("total")) for h in history)
    print(f"[done] 점수 추이: {trail}")
    print(f"[done] 결과 저장: {run_dir}/report.json")
    return report


def main():
    ap = argparse.ArgumentParser(description="Stage 0 — 위키 구조 제안 + 진화")
    ap.add_argument("--source", action="append", required=True,
                    help="레포/데이터 폴더 (여러 번 지정 가능)")
    ap.add_argument("--task", help="태스크 설명 텍스트")
    ap.add_argument("--task-file", help="태스크 설명 파일")
    ap.add_argument("--n-qa", type=int, default=8)
    ap.add_argument("--generations", type=int, default=3)
    ap.add_argument("--max-files", type=int, default=400)
    ap.add_argument("--out-dir", default="runs")
    ap.add_argument("--write", metavar="DIR", help="best 골격을 실제로 쓸 위치 (기본 dry-run)")
    ap.add_argument("--control", action="store_true",
                    help="진화 없이 seed 재샘플링(대조군)")
    ap.add_argument("--seed-from-runs", action="store_true",
                    help="이전 proposal run의 best 전략을 seed로 (warm start)")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    task = args.task
    if args.task_file:
        with open(args.task_file) as f:
            task = f.read().strip()
    if not task:
        ap.error("--task 또는 --task-file이 필요합니다")
    strategy = None
    if args.seed_from_runs:
        prev = best_proposal_strategy_from_runs(args.out_dir)
        if prev:
            strategy = prev["strategy"]
            print(f"[seed] 이전 best 전략 재사용 (total={prev['total']}, "
                  f"{prev['source']})")
        else:
            print("[seed] 이전 proposal run 없음 — 기본 seed 사용")
    run_evolve(args.source, task, generations=args.generations, n_qa=args.n_qa,
               strategy=strategy, out_dir=args.out_dir, write_dir=args.write,
               max_files=args.max_files, use_cache=not args.no_cache,
               no_evolve=args.control)


if __name__ == "__main__":
    main()
