"""현재 wiki 채점 (Before) — 1:1 짝 매칭 + Router 기반 개념 위키 진단.

llm_wiki 패턴(base/raw/*.md + base/wiki/*.md)을 가정한다. wiki가 raw와
1:1(파일명 짝)이면 pairing 모드로, 개념 단위 페이지(백링크 그래프)면
Router 모드로 진단한다 — variant="auto"가 커버리지를 보고 고른다.

공통 원칙:
- 질문 세트는 raw에서 자동 생성 후 runs/qcache/에 캐시 (정답 근거는 항상 원본)
- wiki/projects/ 하위는 진단에서 제외한다 (프로젝트/고객 정보 — 기술 위키가 아님)

Router 모드 (개념 위키):
- 질문마다 wiki 페이지 index에서 읽을 페이지를 골라(Router) 그 페이지만 읽고 답한다
- 페이지별 점수 = 그 페이지를 읽고 답한 질문들의 정답률 → 백링크 그래프에 오버레이
- 한 번도 선택되지 않은 페이지 = 커버리지 구멍(고아 후보)
"""

import glob
import json
import os
import re

import llm
import scoring
import structure

QCACHE_DIR = os.path.join("runs", "qcache")
PAIR_COVERAGE_THRESHOLD = 0.6  # 이름 짝 비율이 이보다 낮으면 Router 모드


def _md_files(d, exclude_dirs=()):
    out = {}
    for f in glob.glob(os.path.join(d, "**", "*.md"), recursive=True):
        rel = os.path.relpath(f, d)
        if os.path.basename(f).lower() == "readme.md":
            continue
        if any(rel.startswith(x + os.sep) for x in exclude_dirs):
            continue
        out[os.path.splitext(os.path.basename(f))[0]] = f
    return out


def find_pairs(base_dir):
    """raw 문서와 wiki 요약의 짝 목록. 반환: [{name, raw, wiki|None}]"""
    base = os.path.expanduser(base_dir)
    raw_dir = os.path.join(base, "raw")
    if not os.path.isdir(raw_dir):
        raw_dir = base
    wiki_dir = os.path.join(base, "wiki")
    raws = _md_files(raw_dir)
    wikis = _md_files(wiki_dir, exclude_dirs=("projects",)) if os.path.isdir(wiki_dir) else {}
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


# ---------- 백링크 그래프 ----------

_LINK_RE = re.compile(r"\[\[([^\]|#]+)")


def parse_links(text):
    """[[page]] / [[page|label]] / [[page#앵커]] → 페이지 이름 목록 (중복 제거, 순서 유지)."""
    seen, out = set(), []
    for m in _LINK_RE.findall(text):
        name = m.strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def wiki_pages(base_dir):
    """wiki/ 개념 페이지 목록 (projects/ 제외). [{name, path, text, desc}]"""
    base = os.path.expanduser(base_dir)
    wiki_dir = os.path.join(base, "wiki")
    if not os.path.isdir(wiki_dir):
        return []
    pages = []
    for name, path in sorted(_md_files(wiki_dir, exclude_dirs=("projects",)).items()):
        with open(path) as f:
            text = f.read()
        first = next((l.strip().lstrip("# ") for l in text.splitlines() if l.strip()), "")
        pages.append({"name": name, "path": path, "text": text, "desc": first[:80]})
    return pages


def build_graph(pages):
    """백링크 그래프. nodes=[{id, chars, inlinks, outlinks}], edges=[{source, target}]"""
    names = {p["name"] for p in pages}
    edges, inlinks = [], {p["name"]: 0 for p in pages}
    for p in pages:
        for target in parse_links(p["text"]):
            if target in names and target != p["name"]:
                edges.append({"source": p["name"], "target": target})
                inlinks[target] += 1
    nodes = [
        {"id": p["name"], "chars": len(p["text"]),
         "inlinks": inlinks[p["name"]],
         "outlinks": sum(1 for e in edges if e["source"] == p["name"])}
        for p in pages
    ]
    return {"nodes": nodes, "edges": edges}


# ---------- Router 진단 ----------

def route_batch(questions, index):
    """질문 여러 개의 라우팅을 한 번에. 반환: 질문별 인덱스 리스트.

    배치 파싱 실패 시 질문별 개별 라우팅(structure.route)으로 폴백.
    """
    idx_str = "\n".join(f"{i+1}. {e['title']} — {e['desc']}" for i, e in enumerate(index))
    q_str = "\n".join(f"Q{i+1}: {q}" for i, q in enumerate(questions))
    prompt = (
        "각 질문에 답하는 데 필요한 파일을 목록에서 골라라. 질문마다 꼭 필요한 "
        "것만(보통 1~2개). 질문 순서대로, 번호 배열의 배열로만 출력하라.\n"
        '예: [[2],[1,3],[4]]  (다른 텍스트 금지)\n\n'
        f"[파일 목록]\n{idx_str}\n\n[질문들]\n{q_str}\n\n선택:"
    )
    out = llm.generate(prompt, num_predict=200, temperature=0.0)
    m = re.search(r"\[\s*\[.*\]\s*\]", out, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list) and len(data) == len(questions):
                picks = []
                for row in data:
                    row = [int(x) - 1 for x in row if isinstance(x, (int, float))]
                    row = [p for p in row if 0 <= p < len(index)]
                    picks.append(row if row else [0])
                return picks
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return [structure.route(q, index) for q in questions]


def router_audit(base_dir, n_qa=6, progress_cb=None, max_docs=None):
    """개념 위키 진단: 질문마다 Router가 wiki 페이지를 골라 읽고 채점한다."""
    base = os.path.expanduser(base_dir)
    raw_dir = os.path.join(base, "raw")
    if not os.path.isdir(raw_dir):
        raw_dir = base
    raws = sorted(_md_files(raw_dir).items(),
                  key=lambda kv: os.path.getsize(kv[1]))
    if max_docs:
        raws = raws[:max_docs]

    pages = wiki_pages(base_dir)
    graph = build_graph(pages)
    index = [{"title": p["name"], "desc": p["desc"]} for p in pages]
    by_name = {p["name"]: p["text"] for p in pages}
    total_wiki_chars = sum(len(p["text"]) for p in pages)

    page_stats = {p["name"]: {"uses": 0, "correct": 0.0} for p in pages}
    questions, reads, all_scores = [], [], []
    parse_failed_docs = []

    for i, (doc, path) in enumerate(raws):
        with open(path) as f:
            raw_text = f.read()
        qs = get_questions(doc, raw_text, n=n_qa)
        if not qs:
            continue
        picks_per_q = route_batch([qa["q"] for qa in qs], index)
        preds, doc_details = [], []
        for qa, picks in zip(qs, picks_per_q):
            titles = [index[p]["title"] for p in picks]
            context = "\n\n".join(by_name.get(t, "") for t in titles)
            pred = structure._answer(context, qa["q"])
            preds.append(pred)
            reads.append(len(context))
            doc_details.append({"doc": doc, "q": qa["q"], "picked": titles,
                                "read_chars": len(context)})
        doc_scores, parse_failed = scoring.judge_all(qs, preds)
        if parse_failed:
            parse_failed_docs.append(doc)
        for d, s in zip(doc_details, doc_scores):
            d["score"] = s
            for t in d["picked"]:
                page_stats[t]["uses"] += 1
                page_stats[t]["correct"] += s
        questions.extend(doc_details)
        all_scores.extend(doc_scores)
        if progress_cb:
            progress_cb(i + 1, len(raws), {
                "variant": "router", "questions": questions,
                "pages": _page_rows(pages, page_stats, graph),
                "graph": graph,
            })

    acc = round(sum(all_scores) / len(all_scores), 3) if all_scores else None
    avg_read = int(sum(reads) / len(reads)) if reads else 0
    eff = round(1.0 - min(1.0, avg_read / total_wiki_chars), 3) if total_wiki_chars else 0.0
    page_rows = _page_rows(pages, page_stats, graph)
    return {
        "base_dir": base_dir,
        "variant": "router",
        "n_docs": len(raws),
        "n_questions": len(all_scores),
        "accuracy": acc,
        "avg_read": avg_read,
        "efficiency": eff,
        "total": round(acc * eff, 3) if acc is not None else None,
        "n_pages": len(pages),
        "n_pages_used": sum(1 for r in page_rows if r["uses"] > 0),
        "pages": page_rows,
        "graph": graph,
        "questions": questions,
        "parse_failed_docs": parse_failed_docs,
    }


def _page_rows(pages, page_stats, graph):
    inl = {n["id"]: n["inlinks"] for n in graph["nodes"]}
    rows = []
    for p in pages:
        st = page_stats[p["name"]]
        rows.append({
            "name": p["name"], "chars": len(p["text"]),
            "inlinks": inl.get(p["name"], 0),
            "uses": st["uses"],
            "correct_rate": round(st["correct"] / st["uses"], 3) if st["uses"] else None,
        })
    rows.sort(key=lambda r: (-(r["uses"]), r["name"]))
    return rows


# ---------- 진입점 ----------

def audit(base_dir, n_qa=6, progress_cb=None, variant="auto", max_docs=None):
    """wiki 진단. variant: auto(커버리지로 판단) | pairing | router"""
    pairs = find_pairs(base_dir)
    if variant == "auto":
        pages = wiki_pages(base_dir)
        matched = sum(1 for p in pairs if p["wiki"])
        coverage = matched / len(pairs) if pairs else 0.0
        variant = "router" if pages and coverage < PAIR_COVERAGE_THRESHOLD else "pairing"

    if variant == "router":
        return router_audit(base_dir, n_qa=n_qa, progress_cb=progress_cb,
                            max_docs=max_docs)

    # ---- pairing 모드 (1:1 이름 짝) ----
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
            progress_cb(i + 1, len(pairs), {"docs": docs})
    return {
        "base_dir": base_dir,
        "variant": "pairing",
        "n_docs": len(pairs),
        "n_scored": len(totals),
        "avg_total": round(sum(totals) / len(totals), 3) if totals else None,
        "docs": docs,
    }
