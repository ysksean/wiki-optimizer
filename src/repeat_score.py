"""채점기 반복성 측정 — 같은 구조를 같은 질문으로 여러 번 채점해 점수가 얼마나 흔들리는지 잰다.

1·2차 실험(2026-09-10)에서 어떤 Reflector도 재샘플링 대조군을 못 이겼다. 그 판정이 "최적화기가
못한다"인지 "채점기가 차이를 못 잰다"인지 가르려면 채점기 자체의 노이즈 바닥을 알아야 한다.
구조를 전혀 바꾸지 않고 N번 채점해서:

  - total/accuracy/efficiency의 표준편차  → 이보다 작은 arm 간 차이는 어떤 실험으로도 못 본다
  - 질문별 Router 선택 일치율            → 라우팅 단계의 무작위성
  - 질문별 판정 뒤집힘률                  → 답변+판정 단계의 무작위성
  - 같은 답을 다시 판정했을 때 뒤집힘률   → 판정(judge)만의 무작위성 (--rejudge)

사용법:
  python3 src/repeat_score.py --report runs/.../report.json --repeats 5 --rejudge 5
  (report.json의 best.struct + question_set을 그대로 쓴다. --heldout-only면 held-out 질문만)
"""

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scoring  # noqa: E402
import structure  # noqa: E402


def repeat_score(struct, question_set, total_raw_chars, repeats=5):
    """같은 구조·질문을 repeats번 채점한다. 반환: {"runs": [...], "summary": {...}}"""
    runs = [structure.score_structure(struct, question_set, total_raw_chars) for _ in range(repeats)]
    return {"runs": runs, "summary": summarize_runs(runs, question_set)}


def summarize_runs(runs, question_set):
    """반복 채점 결과의 흔들림 요약. LLM 호출 없음."""
    valid = [r for r in runs if not r.get("parse_failed")]
    out = {"repeats": len(runs), "parse_failed": len(runs) - len(valid)}
    if not valid:
        return out
    for key in ("total", "accuracy", "efficiency"):
        vals = [r[key] for r in valid]
        out[key] = {"mean": round(statistics.mean(vals), 3),
                    "sd": round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0,
                    "min": min(vals), "max": max(vals)}
    # 질문별: Router 선택이 매번 같은가, 판정이 뒤집히는가
    per_q = []
    for i, qa in enumerate(question_set):
        picks = [tuple(sorted(r["details"][i].get("picked", []))) for r in valid if i < len(r["details"])]
        scores = [r["details"][i].get("score", 0) for r in valid if i < len(r["details"])]
        if not picks:
            continue
        top = max(set(picks), key=picks.count)
        per_q.append({
            "q": qa["q"],
            "pick_agreement": round(picks.count(top) / len(picks), 2),
            "n_distinct_picks": len(set(picks)),
            "correct_rate": round(sum(1 for s in scores if s) / len(scores), 2),
            "flips": bool(0 < sum(1 for s in scores if s) < len(scores)),
        })
    out["per_question"] = per_q
    out["mean_pick_agreement"] = round(statistics.mean(q["pick_agreement"] for q in per_q), 3) if per_q else None
    out["flip_rate"] = round(sum(1 for q in per_q if q["flips"]) / len(per_q), 3) if per_q else None
    return out


def rejudge(question_set, predictions, repeats=5):
    """같은 (질문, 정답, 예측)을 repeats번 다시 판정한다 — 판정만의 무작위성."""
    verdicts = []
    for _ in range(repeats):
        scores, failed = scoring.judge_all(question_set, predictions)
        if not failed:
            verdicts.append(scores)
    return summarize_rejudge(verdicts, question_set, predictions)


def summarize_rejudge(verdicts, question_set, predictions):
    """재판정 결과 요약. LLM 호출 없음."""
    out = {"repeats": len(verdicts)}
    if not verdicts:
        return out
    per_q = []
    for i, qa in enumerate(question_set):
        vs = [v[i] for v in verdicts]
        ones = sum(1 for v in vs if v)
        per_q.append({"q": qa["q"], "pred": predictions[i], "correct_rate": round(ones / len(vs), 2),
                      "flips": 0 < ones < len(vs)})
    out["per_question"] = per_q
    out["flip_rate"] = round(sum(1 for q in per_q if q["flips"]) / len(per_q), 3)
    accs = [sum(v) / len(v) for v in verdicts]
    out["accuracy"] = {"mean": round(statistics.mean(accs), 3),
                       "sd": round(statistics.pstdev(accs), 3) if len(accs) > 1 else 0.0}
    return out


def render(summary, rejudge_summary=None):
    """사람이 읽을 요약 줄들."""
    lines = []
    if "total" in summary:
        t, a, e = summary["total"], summary["accuracy"], summary["efficiency"]
        lines.append(f"- 반복 {summary['repeats']}회 (파싱 실패 {summary['parse_failed']}): "
                     f"total {t['mean']} ± {t['sd']} [{t['min']}, {t['max']}] · "
                     f"accuracy {a['mean']} ± {a['sd']} · efficiency {e['mean']} ± {e['sd']}")
        lines.append(f"- Router 선택 일치율 평균 {summary['mean_pick_agreement']} · "
                     f"판정이 뒤집힌 질문 비율 {summary['flip_rate']}")
        for q in summary.get("per_question", []):
            flag = " ⚠ 뒤집힘" if q["flips"] else ""
            lines.append(f"  - {q['q'][:60]} — 정답률 {q['correct_rate']}, 선택 일치 {q['pick_agreement']}"
                         f" ({q['n_distinct_picks']}가지){flag}")
    if rejudge_summary and "accuracy" in rejudge_summary:
        r = rejudge_summary
        lines.append(f"- 같은 답 재판정 {r['repeats']}회: accuracy {r['accuracy']['mean']} ± {r['accuracy']['sd']}, "
                     f"뒤집힌 질문 비율 {r['flip_rate']}")
        for q in r["per_question"]:
            if q["flips"]:
                lines.append(f"  - ⚠ {q['q'][:50]} — 같은 답 '{q['pred'][:50]}'이 {q['correct_rate']}만 정답 판정")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, help="best.struct와 question_set을 가진 report.json")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--rejudge", type=int, default=0, help="첫 반복의 답을 이 횟수만큼 다시 판정")
    ap.add_argument("--heldout-only", action="store_true")
    ap.add_argument("--out", default=None, help="결과 JSON 경로 (기본: report 옆 repeat_score.json)")
    ap.add_argument("--answer-mode", choices=["free", "extractive"], default=None,
                    help="답변 방식 override (기본: STRUCTURE_ANSWER_MODE 환경변수 또는 free)")
    args = ap.parse_args()
    if args.answer_mode:
        structure.ANSWER_MODE = args.answer_mode

    with open(args.report) as f:
        rep = json.load(f)
    struct = (rep.get("best") or {}).get("struct")
    if not struct or not struct.get("files"):
        sys.exit("report에 best.struct가 없다")
    qs = rep["question_set"]
    if args.heldout_only:
        ho = set((rep.get("question_split") or {}).get("heldout_questions") or [])
        qs = [q for q in qs if q["q"] in ho] or qs
    # organize()가 붙이는 index가 report에는 있을 수도 없을 수도 있다 — 없으면 같은 규칙으로 만든다
    if not struct.get("index"):
        struct["index"] = [{"title": f["title"], "desc": structure._one_line(f["content"])} for f in struct["files"]]
    total_raw = rep.get("total_raw_chars") or 1

    print(f"[repeat] 구조 파일 {len(struct['files'])}개, 질문 {len(qs)}개, 반복 {args.repeats}회, 답변 {structure.ANSWER_MODE}")
    res = repeat_score(struct, qs, total_raw, repeats=args.repeats)
    rj = None
    if args.rejudge and res["runs"] and not res["runs"][0].get("parse_failed"):
        preds = [d["pred"] for d in res["runs"][0]["details"]]
        rj = rejudge(qs, preds, repeats=args.rejudge)
    out_path = args.out or os.path.join(os.path.dirname(args.report), "repeat_score.json")
    with open(out_path, "w") as f:
        json.dump({"report": args.report, "n_questions": len(qs), "answer_mode": structure.ANSWER_MODE,
                   "summary": res["summary"],
                   "rejudge": rj, "runs": res["runs"]}, f, ensure_ascii=False, indent=1)
    print("\n".join(render(res["summary"], rj)))
    print(f"[repeat] 저장: {out_path}")


if __name__ == "__main__":
    main()
