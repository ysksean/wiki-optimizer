"""위키 상태 리포트 — raw/ + wiki/ 워크스페이스의 커버리지·신선도·링크를 LLM 없이 점검한다.

점수 실험은 "지금 위키가 담고 있는 것" 안에서만 의미가 있다. 원본이 위키에 아예 반영되지
않았거나 원본이 페이지보다 나중에 바뀌었다면, 구조를 아무리 최적화해도 그 질문에는 답하지 못한다.
여기서 보는 것:

  - uncovered     어느 위키 페이지도 참조하지 않는 원본 (frontmatter `sources` 또는 본문의 `raw/…md`)
  - stale         페이지의 `updated`(없으면 파일 수정일)보다 나중에 수정된 원본을 근거로 하는 페이지
  - broken_links  어느 페이지로도 풀리지 않는 [[링크]]
  - ambiguous_links  여러 페이지로 풀리는 [[링크]] (독자가 엉뚱한 페이지를 열 수 있다)
  - orphans       다른 페이지(색인 포함)에서 한 번도 링크되지 않은 개념 페이지
  - not_in_index  wiki/index.md에 없는 개념 페이지
  - no_sources    frontmatter에 sources가 없는 개념 페이지

"개념 페이지"는 색인·로그와 wiki/projects/ 하위를 뺀 페이지다. projects/ 페이지도 링크 해석과
원본 참조에는 포함한다. 순회 규칙은 inspection.inspect_folder와 같다(숨김·생성 폴더·심링크 제외).

사용법:  python3 src/wiki_health.py ~/dev/llm_wiki [--json]
"""

import argparse
import json
import os
import re
from datetime import date, datetime

import frontmatter
from inspection import SKIP_DIRS

NAV_PAGES = {"index", "log"}
_LINK = re.compile(r"\[\[([^\]|#]+)")
_RAW_MENTION = re.compile(r"raw/([^\s)\]`'\"]+?\.md)")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _walk_md(root):
    """root 아래 .md (README 제외) → {상대경로(.md 포함, /구분): 절대경로}. 심링크·숨김·생성 폴더 제외."""
    out = {}
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                         and not os.path.islink(os.path.join(parent, d)))
        for name in sorted(names):
            path = os.path.join(parent, name)
            if not name.lower().endswith(".md") or name.lower() == "readme.md" or os.path.islink(path):
                continue
            out[os.path.relpath(path, root).replace(os.sep, "/")] = path
    return out


def _mdate(path):
    return datetime.fromtimestamp(os.path.getmtime(path)).date()


def _parse_date(value):
    m = _DATE.search(str(value or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _is_versioned(path):
    cur = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _resolve_source(ref, raws, raw_by_base):
    """sources 항목 → raw 상대경로. 'raw/a.md', 'a.md', 'a', 'sub/a' 모두 허용. 못 찾으면 None."""
    ref = str(ref).strip().lstrip("./")
    ref = ref[len("raw/"):] if ref.startswith("raw/") else ref
    if not ref.endswith(".md"):
        ref += ".md"
    if ref in raws:
        return ref
    base = os.path.basename(ref)
    hits = raw_by_base.get(base, [])
    return hits[0] if len(hits) == 1 else None


def _resolve_link(target, page, names):
    """[[링크]] → (페이지 이름 | None, 후보 목록). 정확히 일치 → 현재 페이지 기준 상대경로 →
    경로 끝부분 일치(Obsidian 방식: [[pii-model/overview]] = projects/pii-model/overview).
    후보가 여럿이면 None과 후보 목록(모호), 없으면 None과 빈 목록(깨짐)."""
    target = target.strip().removesuffix(".md")
    if target in names:
        return target, [target]
    rel = os.path.normpath(os.path.join(os.path.dirname(page), target)).replace(os.sep, "/")
    if rel in names:
        return rel, [rel]
    hits = sorted(n for n in names if n.endswith("/" + target))
    return (hits[0] if len(hits) == 1 else None), hits


def health(base_dir):
    """위키 상태 리포트(dict). raw/와 wiki/가 둘 다 없으면 {"layout": False}."""
    base = os.path.abspath(os.path.expanduser(base_dir))
    raw_dir, wiki_dir = os.path.join(base, "raw"), os.path.join(base, "wiki")
    if not (os.path.isdir(raw_dir) and os.path.isdir(wiki_dir)):
        return {"layout": False, "root": base}

    raws = _walk_md(raw_dir)
    raw_by_base = {}
    for rel in raws:
        raw_by_base.setdefault(os.path.basename(rel), []).append(rel)

    pages = {}
    for rel, path in _walk_md(wiki_dir).items():
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        meta, body = frontmatter.split(text)
        pages[rel[:-3]] = {"path": path, "meta": meta, "body": body}
    names = set(pages)

    def concept(name):
        return os.path.basename(name).lower() not in NAV_PAGES and not name.startswith("projects/")

    covered, stale, broken, ambiguous = set(), [], [], []
    inbound = {name: 0 for name in names}
    index_links = set()
    for name, page in sorted(pages.items()):
        meta, body = page["meta"], page["body"]
        declared = meta.get("sources") or []
        declared = [declared] if isinstance(declared, str) else declared
        page_sources = {s for s in (_resolve_source(r, raws, raw_by_base) for r in declared) if s}
        page_sources |= {s for s in (_resolve_source(m, raws, raw_by_base) for m in _RAW_MENTION.findall(body)) if s}
        covered |= page_sources

        updated = _parse_date(meta.get("updated")) or _mdate(page["path"])
        newer = [{"rel": f"raw/{s}", "modified": _mdate(raws[s]).isoformat()}
                 for s in sorted(page_sources) if _mdate(raws[s]) > updated]
        if newer and concept(name):
            stale.append({"page": name, "updated": updated.isoformat(), "newer_sources": newer})

        seen = set()
        for target in _LINK.findall(body):
            resolved, candidates = _resolve_link(target, name, names)
            if resolved is None:
                if candidates:
                    ambiguous.append({"page": name, "target": target.strip(), "candidates": candidates})
                else:
                    broken.append({"page": name, "target": target.strip()})
            elif resolved != name and resolved not in seen:
                seen.add(resolved)
                inbound[resolved] += 1
                if os.path.basename(name).lower() == "index":
                    index_links.add(resolved)

    concepts = sorted(n for n in names if concept(n))
    has_index = any(os.path.basename(n).lower() == "index" for n in names)
    uncovered = sorted(
        ({"rel": f"raw/{rel}", "path": raws[rel], "name": rel[:-3], "size": os.path.getsize(raws[rel]),
          "modified": _mdate(raws[rel]).isoformat()} for rel in raws if rel not in covered),
        key=lambda r: (r["modified"], r["rel"]), reverse=True)
    report = {
        "layout": True,
        "root": base,
        "versioned": _is_versioned(base),
        "counts": {},
        "raw_total": len(raws),
        "page_total": len(concepts),
        "uncovered": uncovered,
        "stale": stale,
        "broken_links": broken,
        "ambiguous_links": ambiguous,
        "orphans": [n for n in concepts if inbound[n] == 0],
        "not_in_index": [n for n in concepts if n not in index_links] if has_index else [],
        "no_sources": [n for n in concepts if not pages[n]["meta"].get("sources")],
    }
    report["counts"] = {k: len(report[k]) for k in
                        ("uncovered", "stale", "broken_links", "ambiguous_links", "orphans", "not_in_index",
                         "no_sources")}
    return report


def render(report):
    """사람이 읽을 요약 줄들."""
    if not report.get("layout"):
        return [f"{report.get('root')}: raw/와 wiki/가 모두 있는 위키 루트가 아니다"]
    c = report["counts"]
    lines = [f"위키 상태 · 원본 {report['raw_total']}개 · 개념 페이지 {report['page_total']}개"
             + ("" if report["versioned"] else " · git 미관리(되돌릴 이력 없음)")]
    labels = [("uncovered", "위키에 반영 안 된 원본"), ("stale", "원본보다 오래된 페이지"),
              ("broken_links", "깨진 링크"), ("ambiguous_links", "여러 페이지로 풀리는 링크"),
              ("orphans", "링크 없는 페이지"),
              ("not_in_index", "색인에 없는 페이지"), ("no_sources", "출처 없는 페이지")]
    for key, label in labels:
        lines.append(f"- {label}: {c[key]}")
        items = report[key]
        for item in items[:10]:
            if key == "uncovered":
                lines.append(f"    {item['rel']}  ({item['modified']})")
            elif key == "stale":
                srcs = ", ".join(s["rel"] for s in item["newer_sources"])
                lines.append(f"    {item['page']}  updated {item['updated']} < {srcs}")
            elif key == "broken_links":
                lines.append(f"    {item['page']} → [[{item['target']}]]")
            elif key == "ambiguous_links":
                lines.append(f"    {item['page']} → [[{item['target']}]] = {' | '.join(item['candidates'])}")
            else:
                lines.append(f"    {item}")
        if len(items) > 10:
            lines.append(f"    … 외 {len(items) - 10}개")
    return lines


def main():
    ap = argparse.ArgumentParser(description="raw/ + wiki/ 위키의 커버리지·신선도·링크 점검 (LLM 없음)")
    ap.add_argument("dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = health(args.dir)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else "\n".join(render(report)))


if __name__ == "__main__":
    main()
