"""Stage 0 진입점 — 레포/데이터 + 태스크 → 위키 구조 제안.

1단계(현재): 단발 파이프라인.
  지도(repo_map) → 태스크 질문 + oracle 정답/gap(task_questions)
  → 구조 제안(proposal) → 골격 미리보기/생성(skeleton) + report.json

2단계(예정): score_proposal + 진화 루프 + control arm.

사용법:
  python3 src/evolve_proposal.py --source ~/aipmop --task-file task.txt
  python3 src/evolve_proposal.py --source <dir> --task "..." --write out/wiki
"""

import argparse
import json
import os
from datetime import datetime

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


def run_propose(sources, task, n_qa=8, strategy=None, out_dir="runs",
                write_dir=None, max_files=400, use_cache=True):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(out_dir, f"proposal-{stamp}")
    os.makedirs(run_dir, exist_ok=True)
    strategy = strategy or SEED_STRATEGY

    entries = repo_map.build_map(sources, max_files=max_files, use_cache=use_cache)
    n_doc = sum(1 for e in entries if e["kind"] == "doc")
    print(f"[map] 파일 {len(entries)}개 (doc {n_doc}, code {len(entries) - n_doc})")
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
        mark = "gap" if x["a"] is None else "ok "
        print(f"   [{mark}] {x['q']}")
    print(f"[qa] grounded {len(qa) - len(gaps)} / gap {len(gaps)}")

    print("[propose] 구조 제안 생성 중...")
    pages, stats = proposal.propose(task, entries, strategy, gap_questions=gaps)
    if not pages:
        print("[error] 제안 파싱 실패. 중단.")
        return None
    sk = skeleton.write_skeleton(pages, write_dir or "(dry-run)",
                                 write=bool(write_dir), run_id=stamp)
    print(f"\n[tree]\n{sk['tree']}\n")
    if stats["anchor_miss"] or stats["dropped_sources"] or stats["dropped_pages"]:
        print(f"[warn] anchor_miss={stats['anchor_miss']} "
              f"dropped_sources={stats['dropped_sources']} "
              f"dropped_pages={stats['dropped_pages']}")
    if write_dir:
        print(f"[write] 생성 {len(sk['written'])}개, 기존 파일 skip {len(sk['skipped'])}개"
              f" → {write_dir}")

    report = {
        "mode": "proposal",
        "sources": [os.path.abspath(os.path.expanduser(s)) for s in sources],
        "task": task,
        "strategy": strategy,
        "provenance": provenance.collect(
            question_set=[{"q": x["q"], "a": x["a"] or ""} for x in qa],
            params={"n_qa": n_qa, "max_files": max_files},
        ),
        "n_files_mapped": len(entries),
        "question_set": qa,
        "gap_questions": gaps,
        "pages": pages,
        "validate_stats": stats,
        "skeleton": {"written": sk["written"], "skipped": sk["skipped"],
                     "write_dir": write_dir},
    }
    with open(os.path.join(run_dir, "report.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[done] 결과 저장: {run_dir}/report.json")
    return report


def main():
    ap = argparse.ArgumentParser(description="Stage 0 — 위키 구조 제안")
    ap.add_argument("--source", action="append", required=True,
                    help="레포/데이터 폴더 (여러 번 지정 가능)")
    ap.add_argument("--task", help="태스크 설명 텍스트")
    ap.add_argument("--task-file", help="태스크 설명 파일")
    ap.add_argument("--n-qa", type=int, default=8)
    ap.add_argument("--max-files", type=int, default=400)
    ap.add_argument("--out-dir", default="runs")
    ap.add_argument("--write", metavar="DIR", help="골격을 실제로 쓸 위치 (기본 dry-run)")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    task = args.task
    if args.task_file:
        with open(args.task_file) as f:
            task = f.read().strip()
    if not task:
        ap.error("--task 또는 --task-file이 필요합니다")
    run_propose(args.source, task, n_qa=args.n_qa, out_dir=args.out_dir,
                write_dir=args.write, max_files=args.max_files,
                use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
