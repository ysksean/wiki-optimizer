"""현재 wiki 채점 (Before) — raw↔wiki 짝 맞추기 + query 기반 채점.

llm_wiki 패턴(base/raw/*.md + base/wiki/*.md)을 가정한다.
- raw/가 없으면 지정 폴더 자체를 raw로 취급 (wiki 짝 없음 = 전부 미요약)
- 짝은 파일 이름(확장자 제외) 기준
- 질문 세트는 raw에서 자동 생성 후 runs/qcache/에 캐시 — audit과 apply가
  같은 질문으로 채점해야 before/after 비교가 공정하다
"""

import glob
import json
import os

import scoring

QCACHE_DIR = os.path.join("runs", "qcache")


def _md_files(d):
    return {
        os.path.splitext(os.path.basename(f))[0]: f
        for f in glob.glob(os.path.join(d, "**", "*.md"), recursive=True)
        if os.path.basename(f).lower() != "readme.md"
    }


def find_pairs(base_dir):
    """raw 문서와 wiki 요약의 짝 목록. 반환: [{name, raw, wiki|None}]"""
    base = os.path.expanduser(base_dir)
    raw_dir = os.path.join(base, "raw")
    if not os.path.isdir(raw_dir):
        raw_dir = base
    wiki_dir = os.path.join(base, "wiki")
    raws = _md_files(raw_dir)
    wikis = _md_files(wiki_dir) if os.path.isdir(wiki_dir) else {}
    return [
        {"name": n, "raw": p, "wiki": wikis.get(n)}
        for n, p in sorted(raws.items())
    ]


def get_questions(name, raw_text, n=6):
    """문서별 질문 세트 (자동 생성 + 캐시)."""
    os.makedirs(QCACHE_DIR, exist_ok=True)
    path = os.path.join(QCACHE_DIR, f"{name}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                qs = json.load(f)
            if qs:
                return qs
        except (OSError, json.JSONDecodeError):
            pass
    qs = scoring.build_question_set(raw_text, n=n)
    if qs:
        with open(path, "w") as f:
            json.dump(qs, f, ensure_ascii=False)
    return qs


def _slim(score):
    return {k: v for k, v in score.items() if k != "qa_details"}


def audit(base_dir, n_qa=6, progress_cb=None):
    """현재 wiki 요약본을 채점한다. 요약 없는 문서는 표시만."""
    pairs = find_pairs(base_dir)
    docs, totals = [], []
    for i, pr in enumerate(pairs):
        with open(pr["raw"]) as f:
            raw_text = f.read()
        entry = {
            "name": pr["name"],
            "raw_chars": len(raw_text),
            "has_summary": bool(pr["wiki"]),
        }
        if pr["wiki"]:
            with open(pr["wiki"]) as f:
                wiki_text = f.read()
            entry["wiki_chars"] = len(wiki_text)
            qs = get_questions(pr["name"], raw_text, n=n_qa)
            if qs:
                s = scoring.score(raw_text, wiki_text, qs)
                entry["score"] = _slim(s)
                totals.append(s["total"])
        docs.append(entry)
        if progress_cb:
            progress_cb(i + 1, len(pairs), docs)
    return {
        "base_dir": base_dir,
        "n_docs": len(pairs),
        "n_scored": len(totals),
        "avg_total": round(sum(totals) / len(totals), 3) if totals else None,
        "docs": docs,
    }
