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
- judge 파싱 실패(report.parse_failed) run은 CSV에는 남기되 arm 통계/net에서 제외

사용법:
  python3 src/batch.py --docs 5 --runs 2 --generations 3 --with-control
  python3 src/batch.py --files a.md b.md --runs 3 --generations 4
"""

import argparse
import csv
import threading
from concurrent.futures import ThreadPoolExecutor
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
              out_dir="runs", parallel=1):
    """parallel > 1이면 문서 단위로 동시에 돈다 (문서 안의 run x arm 순서는 유지).

    문서끼리는 질문 세트·run 디렉터리가 독립이라 병렬해도 결과가 같다.
    공유되는 것은 records/중간저장/진행 카운터(아래 락)와 영속 이력 파일
    (evolve._IMPACT_LOCK)뿐이다.
    """
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
    progress = {"done": 0, "total": len(files) * runs * len(arms),
                "lock": threading.Lock()}
    t_batch = time.time()

    print(f"[batch] 문서 {len(files)}개 x run {runs} x arm {arms} x gen {generations} "
          f"= {progress['total']} runs (parallel={parallel})")
    try:
        if parallel > 1:
            with ThreadPoolExecutor(max_workers=parallel) as ex:
                futures = [
                    ex.submit(_run_doc, f, runs, arms, generations, n_qa,
                              batch_dir, state_path, records, progress)
                    for f in files
                ]
                for fut in futures:
                    fut.result()
        else:
            for f in files:
                _run_doc(f, runs, arms, generations, n_qa, batch_dir, state_path,
                         records, progress)
    finally:
        # 어떤 이유로 중단돼도(예외, Ctrl-C) 지금까지의 records로 집계는 남긴다
        batch_elapsed = time.time() - t_batch
        aggregate(records, batch_dir, generations, runs, batch_elapsed)
    return records, batch_dir


def _prepare_question_set(f, n_qa):
    """문서 읽기 + 질문 세트 확보. 실패(예외/빈 결과)는 None으로 돌려 배치를 살린다."""
    try:
        raw_text = open(f).read()
        return evolve.load_question_set(f, raw_text, n_qa) or None
    except Exception as e:  # LLMError, 타임아웃, 인코딩 오류 등 — 문서 1개 때문에 배치를 죽이지 않는다
        print(f"[batch] 질문 세트 생성 중 예외: {e}")
        return None


def _run_doc(f, runs, arms, generations, n_qa, batch_dir, state_path, records, progress):
    """문서 하나에 대해 run x arm을 돌린다. records/progress 갱신은 락으로 보호."""
    doc = os.path.splitext(os.path.basename(f))[0]
    size = os.path.getsize(f)
    # 질문 세트는 문서당 1회 생성해 모든 arm/run이 공유 (공정 비교)
    question_set = _prepare_question_set(f, n_qa)
    if not question_set:
        print(f"[batch] {doc}: 질문 세트 실패, 건너뜀")
        with progress["lock"]:
            progress["done"] += runs * len(arms)
        return

    for r in range(runs):
        for arm in arms:
            with progress["lock"]:
                progress["done"] += 1
                done = progress["done"]
            print(f"\n[batch {done}/{progress['total']}] {doc} (size={size}) run={r} arm={arm}")
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

            rec = _record(report, doc, size, r, arm, dt)
            with progress["lock"]:
                records.append(rec)
                # 크래시 대비 중간저장
                with open(state_path, "w") as sf:
                    json.dump(records, sf, ensure_ascii=False, indent=2)
            flag = "  [judge 파싱 실패 — 집계 제외]" if rec["parse_failed"] else ""
            print(f"[batch] {doc} run={r} arm={arm}: gen0={rec['gen0_total']} "
                  f"best={rec['best_total']} improvement={rec['improvement']} ({dt:.0f}s){flag}")


def _record(report, doc, size, r, arm, dt):
    """evolve report 하나 → 집계용 레코드.

    parse_failed: 어느 세대든 judge 파싱이 실패한 run. gen0/best 어느 쪽이
    0으로 자리만 채워졌는지 알 수 없으므로 run 통째로 net 산식에서 뺀다.
    """
    hist = report["history"]
    gen0 = hist[0]["score"]["total"]
    best_gen = report["best"]["generation"]
    parse_failed = bool(report.get("parse_failed"))
    if best_gen < 0:  # 전 세대 판정 실패 — 유효한 best 없음
        parse_failed = True
        best_gen = 0
    best = hist[best_gen]["score"]["total"]
    return {
        "doc": doc,
        "size": size,
        "run": r,
        "arm": arm,
        "gen0_total": gen0,
        "best_total": best,
        "best_gen": best_gen,
        "improvement": round(best - gen0, 3),
        "improved": best > gen0,
        "gen0_acc": hist[0]["score"]["accuracy"],
        "best_acc": hist[best_gen]["score"]["accuracy"],
        "parse_failed": parse_failed,
        "elapsed_sec": round(dt, 1),
        # 재현성: 이 점수가 어떤 백엔드/모델/코드로 나왔는지 행 단위로 남긴다
        "backend": (report.get("provenance") or {}).get("backend", ""),
        "model": (report.get("provenance") or {}).get("model", ""),
        "code_sha": (report.get("provenance") or {}).get("code_sha", ""),
        "question_set_sha": (report.get("provenance") or {}).get("question_set_sha", ""),
    }


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

    # judge 파싱 실패 run은 점수가 자리만 채운 0이라 향상폭/net 산식을 오염시킨다.
    # 통계에서는 빼고, 얼마나(어느 arm에서) 빠졌는지는 반드시 보여준다.
    failed = [r for r in records if r.get("parse_failed")]
    valid = [r for r in records if not r.get("parse_failed")]

    lines = []
    lines.append("# 배치 결과 요약 (held-out 점수 기준)\n")
    lines.append(f"- 실행: 문서 {len({r['doc'] for r in records})}개 x run {runs} x gen {generations}")
    lines.append(f"- 총 run: {len(records)}개")
    if failed:
        lines.append(f"- judge 파싱 실패 run: {len(failed)}개 (아래 통계에서 제외)")
    lines.append(f"- 총 소요: {batch_elapsed/60:.1f}분\n")

    if not records:
        lines.append("(성공한 run 없음)")
        _write(batch_dir, lines)
        return
    if not valid:
        lines.append("(유효한 run 없음 — 전부 judge 파싱 실패. 판정 프롬프트/모델을 점검할 것)")
        _write(batch_dir, lines)
        return

    by_arm = {}
    for r in valid:
        by_arm.setdefault(r["arm"], []).append(r)
    failed_by_arm = {}
    for r in failed:
        failed_by_arm[r["arm"]] = failed_by_arm.get(r["arm"], 0) + 1

    lines.append("## Arm별 지표 (best - gen0, held-out)")
    lines.append("| arm | runs | 평균 향상폭 | 표준편차 | 개선run 비율 | 파싱실패(제외) |")
    lines.append("|---|---|---|---|---|---|")
    for arm in sorted(set(by_arm) | set(failed_by_arm)):
        s = _arm_stats(by_arm.get(arm, []))
        lines.append(
            f"| {arm} | {s['n']} | {s['mean_imp']:+.3f} | {s['stdev_imp']:.3f} "
            f"| {s['improved_ratio']*100:.0f}% | {failed_by_arm.get(arm, 0)} |"
        )
    lines.append("")

    # 문서별 집계 (arm x run 평균)
    lines.append("## 문서별 (run 평균)")
    lines.append("| 문서 | size | arm | gen0(평균) | best(평균) | 향상폭 |")
    lines.append("|---|---|---|---|---|---|")
    docs = {}
    for r in valid:
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
    ap.add_argument("--parallel", type=int, default=1,
                    help="동시에 돌릴 문서 수 (기본 1=순차). CLI 세션 rate limit에 주의")
    args = ap.parse_args()

    files = args.files if args.files else select_docs(args.docs)
    print("[batch] 대상 문서:")
    for f in files:
        print(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")
    run_batch(files, args.runs, args.generations, args.n_qa,
              with_control=args.with_control, ablation=args.ablation,
              parallel=args.parallel)
