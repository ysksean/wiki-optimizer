"""best 전략으로 재요약(After) 생성 + Before/After 비교.

- 전략: 지정이 없으면 runs/**/report.json에서 held-out best가 가장 높은
  요약 전략을 찾아 쓴다 (없으면 seed 전략).
- 원본과 기존 wiki는 절대 수정하지 않는다. After 요약은 runs/apply-<stamp>/에 쓴다.
- Before/After 모두 같은 캐시 질문 세트로 채점한다 (공정 비교).
- 사용한 전략은 runs/strategies.json에 append (다음 적용 때 재사용 근거).
"""

import glob
import json
import os
from datetime import datetime

import audit
import evolve
import scoring


def best_strategy_from_runs(runs_dir="runs"):
    """실험 결과 전체에서 held-out best total이 가장 높은 요약 전략."""
    best = None
    for p in glob.glob(os.path.join(runs_dir, "**", "report.json"), recursive=True):
        try:
            with open(p) as f:
                r = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        b = r.get("best") or {}
        strategy, total = b.get("strategy"), b.get("total")
        if not strategy or not isinstance(total, (int, float)):
            continue
        if "struct" in b:  # B단계(구조) 결과는 요약 전략이 아니다
            continue
        if best is None or total > best["total"]:
            best = {"strategy": strategy, "total": total, "source": p}
    return best


def _slim(score):
    return {k: v for k, v in score.items() if k != "qa_details"}


def run_apply(base_dir, strategy=None, n_qa=6, out_dir=None, progress_cb=None):
    """전체 raw를 전략으로 재요약하고 before/after를 채점해 반환."""
    strategy_source = "user"
    if not strategy:
        found = best_strategy_from_runs()
        if found:
            strategy, strategy_source = found["strategy"], found["source"]
        else:
            strategy, strategy_source = evolve.SEED_STRATEGY, "seed"

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = out_dir or os.path.join("runs", f"apply-{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    pairs = audit.find_pairs(base_dir)
    docs, before_totals, after_totals = [], [], []
    for i, pr in enumerate(pairs):
        with open(pr["raw"]) as f:
            raw_text = f.read()
        qs = audit.get_questions(pr["name"], raw_text, n=n_qa)

        after_text = evolve.summarize(raw_text, strategy)
        with open(os.path.join(out_dir, f"{pr['name']}.md"), "w") as f:
            f.write(after_text)

        entry = {"name": pr["name"], "raw_chars": len(raw_text),
                 "after": {"content": after_text}}
        if qs:
            s_after = scoring.score(raw_text, after_text, qs)
            entry["after"]["score"] = _slim(s_after)
            after_totals.append(s_after["total"])
        if pr["wiki"]:
            with open(pr["wiki"]) as f:
                before_text = f.read()
            entry["before"] = {"content": before_text}
            if qs:
                s_before = scoring.score(raw_text, before_text, qs)
                entry["before"]["score"] = _slim(s_before)
                before_totals.append(s_before["total"])
        docs.append(entry)
        if progress_cb:
            progress_cb(i + 1, len(pairs), docs)

    result = {
        "base_dir": base_dir,
        "out_dir": out_dir,
        "strategy": strategy,
        "strategy_source": strategy_source,
        "avg_before": round(sum(before_totals) / len(before_totals), 3) if before_totals else None,
        "avg_after": round(sum(after_totals) / len(after_totals), 3) if after_totals else None,
        "docs": docs,
    }
    _register_strategy(result)
    return result


def _register_strategy(result):
    """사용한 전략과 성과를 runs/strategies.json에 축적."""
    path = os.path.join("runs", "strategies.json")
    entries = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                entries = json.load(f)
        except (OSError, json.JSONDecodeError):
            entries = []
    entries.append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "base_dir": result["base_dir"],
        "strategy": result["strategy"],
        "strategy_source": result["strategy_source"],
        "avg_before": result["avg_before"],
        "avg_after": result["avg_after"],
    })
    with open(path, "w") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
