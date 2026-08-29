"""배치 러너 + 집계 (evolve arm vs control arm).

여러 raw 문서 x 여러 run으로 self-evolving을 자동 실행하고, 문서별
gen0(베이스라인) vs best 향상폭을 집계한다.

핵심: --with-control이면 문서/run마다 두 arm을 돌린다.
  - evolve:  reflect로 전략을 진화시킴
  - control: seed 전략을 세대 수만큼 재샘플링만 함 (진화 없음)
best는 노이즈 N개의 최댓값이라 진화가 없어도 best-gen0 > 0으로 치우친다.
따라서 "진화의 진짜 효과" = evolve arm 향상폭 - control arm 향상폭.
두 arm은 문서당 같은 질문 세트(train/held-out 분할 포함)를 공유한다.
향상폭은 전부 held-out 점수 기준이다 (train은 reflect 전용).

특징:
- 크래시 대비: run 하나 끝날 때마다 batch_state.json에 전체 기록 저장
  (자동 resume은 없음 — 재실행하면 새 배치 디렉터리가 생긴다)
- 결과: CSV(runs/batch-<stamp>/results.csv) + 요약 리포트(summary.md)

사용법:
  python3 src/batch.py --docs 5 --runs 2 --generations 3 --with-control
  python3 src/batch.py --files a.md b.md --runs 3 --generations 4
"""

import argparse
import csv
import glob
import json
import os
import statistics
import time
from datetime import datetime

import evolve


def select_docs(n, raw_dir="data/raw"):
    """raw 문서를 크기순으로 정렬해 골고루 n개 선택 (README 제외)."""
    files = [
        f for f in glob.glob(os.path.join(raw_dir, "*.md"))
        if os.path.basename(f).lower() != "readme.md"
    ]
    files.sort(key=lambda f: os.path.getsize(f))
    if n >= len(files):
        return files
    # 크기 분포를 골고루 뽑기 위해 균등 간격 샘플링
    step = len(files) / n
    return [files[int(i * step)] for i in range(n)]


def run_batch(files, runs, generations, n_qa, with_control=False, ablation=False,
              out_dir="runs"):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir = os.path.join(out_dir, f"batch-{stamp}")
    os.makedirs(batch_dir, exist_ok=True)
    state_path = os.path.join(batch_dir, "batch_state.json")

    if ablation:
        # 영속 이력 ablation: 둘 다 진화하되 이력 참조만 켜고/끈다
        arms = ["evolve", "evolve-nohist"]
    else:
        arms = ["evolve", "control"] if with_control else ["evolve"]
    records = []
    total = len(files) * runs * len(arms)
    done = 0
    t_batch = time.time()

    print(f"[batch] 문서 {len(files)}개 x run {runs} x arm {arms} x gen {generations} = {total} runs")
    for f in files:
        doc = os.path.splitext(os.path.basename(f))[0]
        size = os.path.getsize(f)
        # 질문 세트는 문서당 1회 생성해 모든 arm/run이 공유 (공정 비교)
        raw_text = open(f).read()
        question_set = evolve.load_question_set(f, raw_text, n_qa)
        if not question_set:
            print(f"[batch] {doc}: 질문 세트 실패, 건너뜀")
            done += runs * len(arms)
            continue

        for r in range(runs):
            for arm in arms:
                done += 1
                print(f"\n[batch {done}/{total}] {doc} (size={size}) run={r} arm={arm}")
                t = time.time()
                try:
                    report = evolve.evolve(
                        f, generations=generations, n_qa=n_qa, out_dir=batch_dir,
                        no_evolve=(arm == "control"), question_set=question_set,
                        use_history=(arm != "evolve-nohist"),
                    )
                except Exception as e:  # 한 run 실패해도 배치는 계속
                    print(f"[batch] run 실패: {e}")
                    continue
                dt = time.time() - t
                if not report:
                    continue

                hist = report["history"]
                gen0 = hist[0]["score"]["total"]
                best = report["best"]["total"]
                rec = {
                    "doc": doc,
                    "size": size,
                    "run": r,
                    "arm": arm,
                    "gen0_total": gen0,
                    "best_total": best,
                    "best_gen": report["best"]["generation"],
                    "improvement": round(best - gen0, 3),
                    "improved": best > gen0,
                    "gen0_acc": hist[0]["score"]["accuracy"],
                    "best_acc": hist[report["best"]["generation"]]["score"]["accuracy"],
                    "elapsed_sec": round(dt, 1),
                }
                records.append(rec)
                # 크래시 대비 중간저장
                with open(state_path, "w") as sf:
                    json.dump(records, sf, ensure_ascii=False, indent=2)
                print(f"[batch] gen0={gen0} best={best} improvement={rec['improvement']} ({dt:.0f}s)")

    batch_elapsed = time.time() - t_batch
    aggregate(records, batch_dir, generations, runs, batch_elapsed)
    return records, batch_dir


def _arm_stats(rs):
    imps = [r["improvement"] for r in rs]
    return {
        "n": len(rs),
        "mean_imp": statistics.mean(imps) if imps else 0.0,
        "stdev_imp": statistics.stdev(imps) if len(imps) > 1 else 0.0,
        "improved_ratio": sum(1 for r in rs if r["improved"]) / len(rs) if rs else 0.0,
    }


def aggregate(records, batch_dir, generations, runs, batch_elapsed):
    """arm별/문서별 집계 → CSV + summary.md."""
    csv_path = os.path.join(batch_dir, "results.csv")
    if records:
        with open(csv_path, "w", newline="") as cf:
            w = csv.DictWriter(cf, fieldnames=list(records[0].keys()))
            w.writeheader()
            w.writerows(records)

    lines = []
    lines.append("# 배치 결과 요약 (held-out 점수 기준)\n")
    lines.append(f"- 실행: 문서 {len({r['doc'] for r in records})}개 x run {runs} x gen {generations}")
    lines.append(f"- 총 run: {len(records)}개")
    lines.append(f"- 총 소요: {batch_elapsed/60:.1f}분\n")

    if not records:
        lines.append("(성공한 run 없음)")
        _write(batch_dir, lines)
        return

    by_arm = {}
    for r in records:
        by_arm.setdefault(r["arm"], []).append(r)

    lines.append("## Arm별 지표 (best - gen0, held-out)")
    lines.append("| arm | runs | 평균 향상폭 | 표준편차 | 개선run 비율 |")
    lines.append("|---|---|---|---|---|")
    for arm, rs in sorted(by_arm.items()):
        s = _arm_stats(rs)
        lines.append(
            f"| {arm} | {s['n']} | {s['mean_imp']:+.3f} | {s['stdev_imp']:.3f} "
            f"| {s['improved_ratio']*100:.0f}% |"
        )
    lines.append("")

    # 문서별 집계 (arm x run 평균)
    lines.append("## 문서별 (run 평균)")
    lines.append("| 문서 | size | arm | gen0(평균) | best(평균) | 향상폭 |")
    lines.append("|---|---|---|---|---|---|")
    docs = {}
    for r in records:
        docs.setdefault((r["doc"], r["arm"]), []).append(r)
    for (doc, arm), rs in sorted(docs.items(), key=lambda kv: (kv[1][0]["size"], kv[0][1])):
        g0 = statistics.mean(r["gen0_total"] for r in rs)
        bs = statistics.mean(r["best_total"] for r in rs)
        lines.append(f"| {doc} | {rs[0]['size']} | {arm} | {g0:.3f} | {bs:.3f} | {bs-g0:+.3f} |")

    lines.append("\n## 해석")
    ev = _arm_stats(by_arm.get("evolve", []))
    if "evolve-nohist" in by_arm:
        nh = _arm_stats(by_arm["evolve-nohist"])
        net = ev["mean_imp"] - nh["mean_imp"]
        lines.append(
            f"- 영속 이력 효과(net) = 이력 있음 {ev['mean_imp']:+.3f} - 이력 없음 "
            f"{nh['mean_imp']:+.3f} = **{net:+.3f}**"
        )
        if net > 0.02:
            lines.append("- 이력 없는 진화 대비 **영속 이력이 실제로 개선**하는 경향.")
        elif net > 0:
            lines.append("- 이력 우위가 미미. run/세대 수를 늘려 재확인 필요.")
        else:
            lines.append("- 이력 유무 차이 없음 — 관측된 효과는 노이즈로 설명 가능.")
    elif "control" in by_arm:
        ct = _arm_stats(by_arm["control"])
        net = ev["mean_imp"] - ct["mean_imp"]
        lines.append(
            f"- 진화 효과(net) = evolve {ev['mean_imp']:+.3f} - control {ct['mean_imp']:+.3f} "
            f"= **{net:+.3f}**"
        )
        if net > 0.02:
            lines.append("- 노이즈 기준선(control) 대비 **진화가 실제로 개선**하는 경향.")
        elif net > 0:
            lines.append("- control 대비 우위가 미미. run/세대 수를 늘려 재확인 필요.")
        else:
            lines.append("- control과 구분 안 됨 — 관측된 향상폭은 **선택 노이즈**로 설명 가능.")
    else:
        lines.append(
            f"- evolve 평균 향상폭 {ev['mean_imp']:+.3f}. "
            "단, control arm 없이는 노이즈 max 편향과 구분 불가 (--with-control 권장)."
        )

    _write(batch_dir, lines)
    print("\n" + "\n".join(lines))


def _write(batch_dir, lines):
    with open(os.path.join(batch_dir, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n[batch] 집계 저장: {batch_dir}/ (results.csv, summary.md)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=5, help="크기순 골고루 뽑을 문서 수")
    ap.add_argument("--files", nargs="*", help="특정 파일 지정 (지정 시 --docs 무시)")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--generations", type=int, default=3)
    ap.add_argument("--n-qa", type=int, default=8)
    ap.add_argument("--with-control", action="store_true", help="무진화 대조군 arm 추가")
    ap.add_argument("--ablation", action="store_true",
                    help="영속 이력 ablation: evolve vs evolve-nohist 두 arm")
    args = ap.parse_args()

    files = args.files if args.files else select_docs(args.docs)
    print("[batch] 대상 문서:")
    for f in files:
        print(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")
    run_batch(files, args.runs, args.generations, args.n_qa,
              with_control=args.with_control, ablation=args.ablation)
