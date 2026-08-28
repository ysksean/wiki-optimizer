"""배치 러너 + 집계.

여러 raw 문서 x 여러 run x 세대로 self-evolving을 자동 실행하고,
문서별 gen0(베이스라인) vs best 향상폭을 집계한다.
목적: "self-evolving이 일관되게 개선하는가"를 단일 run 일화가 아닌 증거로 만든다.

특징:
- 크래시 대비: run 하나 끝날 때마다 batch_state.json에 append (재실행 시 이어서)
- 소요시간 로깅: run별 elapsed 기록
- 집계: 문서별 gen0 vs best 향상폭, run 간 평균/표준편차, 개선된 run 비율
- 결과: CSV(runs/batch-<stamp>/results.csv) + 요약 리포트(summary.md)

사용법:
  python3 src/batch.py --docs 5 --runs 2 --generations 3
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


def run_batch(files, runs, generations, n_qa, out_dir="runs"):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir = os.path.join(out_dir, f"batch-{stamp}")
    os.makedirs(batch_dir, exist_ok=True)
    state_path = os.path.join(batch_dir, "batch_state.json")

    records = []  # 각 run 결과
    total = len(files) * runs
    done = 0
    t_batch = time.time()

    print(f"[batch] 문서 {len(files)}개 x run {runs} x gen {generations} = {total} runs")
    for f in files:
        doc = os.path.splitext(os.path.basename(f))[0]
        size = os.path.getsize(f)
        for r in range(runs):
            done += 1
            print(f"\n[batch {done}/{total}] {doc} (size={size}) run={r}")
            t = time.time()
            try:
                report = evolve.evolve(
                    f, generations=generations, n_qa=n_qa, out_dir=batch_dir
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
                "gen0_total": gen0,
                "best_total": best,
                "best_gen": report["best"]["generation"],
                "improvement": round(best - gen0, 3),
                "improved": best > gen0,
                "gen0_acc": hist[0]["score"]["accuracy"],
                "best_acc": report["history"][report["best"]["generation"]]["score"]["accuracy"],
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


def aggregate(records, batch_dir, generations, runs, batch_elapsed):
    """문서별/전체 집계 → CSV + summary.md."""
    # CSV (raw records)
    csv_path = os.path.join(batch_dir, "results.csv")
    if records:
        with open(csv_path, "w", newline="") as cf:
            w = csv.DictWriter(cf, fieldnames=list(records[0].keys()))
            w.writeheader()
            w.writerows(records)

    # 집계
    lines = []
    lines.append(f"# 배치 결과 요약\n")
    lines.append(f"- 실행: 문서 {len({r['doc'] for r in records})}개 x run {runs} x gen {generations}")
    lines.append(f"- 총 run: {len(records)}개")
    lines.append(f"- 총 소요: {batch_elapsed/60:.1f}분\n")

    if not records:
        lines.append("(성공한 run 없음)")
        _write(batch_dir, lines)
        return

    improvements = [r["improvement"] for r in records]
    improved_ratio = sum(1 for r in records if r["improved"]) / len(records)

    lines.append("## 전체 지표")
    lines.append(f"- 평균 향상폭(best-gen0): {statistics.mean(improvements):+.3f}")
    if len(improvements) > 1:
        lines.append(f"- 향상폭 표준편차: {statistics.stdev(improvements):.3f}")
    lines.append(f"- 개선된 run 비율: {improved_ratio*100:.0f}% ({sum(1 for r in records if r['improved'])}/{len(records)})")
    lines.append(f"- 평균 run 소요: {statistics.mean(r['elapsed_sec'] for r in records):.0f}s\n")

    # 문서별 집계 (run 평균)
    lines.append("## 문서별 (run 평균)")
    lines.append("| 문서 | size | gen0(평균) | best(평균) | 향상폭 | 개선run |")
    lines.append("|---|---|---|---|---|---|")
    docs = {}
    for r in records:
        docs.setdefault(r["doc"], []).append(r)
    for doc, rs in sorted(docs.items(), key=lambda kv: kv[1][0]["size"]):
        g0 = statistics.mean(r["gen0_total"] for r in rs)
        bs = statistics.mean(r["best_total"] for r in rs)
        imp = bs - g0
        n_imp = sum(1 for r in rs if r["improved"])
        lines.append(f"| {doc} | {rs[0]['size']} | {g0:.3f} | {bs:.3f} | {imp:+.3f} | {n_imp}/{len(rs)} |")

    lines.append("\n## 해석")
    mean_imp = statistics.mean(improvements)
    if mean_imp > 0.02 and improved_ratio >= 0.6:
        lines.append(f"- self-evolving이 **일관되게 개선**하는 경향 (평균 {mean_imp:+.3f}, {improved_ratio*100:.0f}% 개선).")
    elif mean_imp > 0:
        lines.append(f"- 평균적으로 개선되나 **분산이 큼**. run 반복/세대 증가 필요.")
    else:
        lines.append(f"- 현 설정에선 **개선 근거 약함**. 지표/전략/세대수 재검토 필요.")

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
    ap.add_argument("--n-qa", type=int, default=5)
    args = ap.parse_args()

    files = args.files if args.files else select_docs(args.docs)
    print("[batch] 대상 문서:")
    for f in files:
        print(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")
    run_batch(files, args.runs, args.generations, args.n_qa)
