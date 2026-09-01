"""위키 구조 제안 생성 + 검증 (Stage 0).

제안 = 레포 위의 라우팅 테이블. flat 리스트 하나이며 path의 슬래시에서
폴더가 파생된다 (트리 자료구조 없음).

  pages: [{path, title, purpose, outline, sources, status}]
  - path:    "prerm/risk-cases.md" (상대경로, .md)
  - purpose: 이 페이지가 답해야 할 질문
  - outline: md 내부 섹션 목차 제안 ["## ...", ...]
  - sources: 근거 원본 ["rel" | "rel#헤딩"] — 앵커는 지도로 결정론 검증,
             미검증이면 앵커 제거(파일 전체 폴백) + anchor_miss 카운트
  - status:  grounded | gap (유효 source가 없으면 gap으로 강제)
"""

import json
import re

import audit
import llm
import repo_map
import scoring
import structure


def _parse_pages(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    pages = data.get("pages", [])
    return pages if isinstance(pages, list) else []


_SAFE_PATH_RE = re.compile(r"^[\w\-./가-힣 ]+$")


def _clean_path(path):
    """상대경로 강제. 절대경로·`..`·이상 문자는 거부(None)."""
    p = str(path).strip()
    if not p or p.startswith("/") or ".." in p.split("/") or not _SAFE_PATH_RE.match(p):
        return None
    if not p.endswith(".md"):
        p += ".md"
    return p


def validate(pages, entries):
    """LLM이 낸 pages를 지도 기준으로 정화한다.

    반환: (clean_pages, stats)  stats: {anchor_miss, dropped_sources, dropped_pages}
    """
    entries_map = repo_map.by_rel(entries)
    stats = {"anchor_miss": 0, "dropped_sources": 0, "dropped_pages": 0}
    clean = []
    seen_paths = set()
    for p in pages:
        if not isinstance(p, dict):
            stats["dropped_pages"] += 1
            continue
        path = _clean_path(p.get("path", ""))
        title = str(p.get("title", "")).strip()
        purpose = str(p.get("purpose", "")).strip()
        if not path or not title or not purpose or path in seen_paths:
            stats["dropped_pages"] += 1
            continue
        seen_paths.add(path)
        outline = [str(s).strip() for s in p.get("outline", [])
                   if isinstance(s, str) and str(s).strip()][:12]
        sources = []
        for s in p.get("sources", []):
            if not isinstance(s, str) or not s.strip():
                continue
            rel, _, anchor = s.strip().partition("#")
            rel = rel.strip()
            entry = entries_map.get(rel)
            if entry is None:
                stats["dropped_sources"] += 1
                continue
            if anchor:
                if repo_map.find_anchor(entry, anchor) is None:
                    stats["anchor_miss"] += 1
                    sources.append(rel)  # 파일 전체 폴백
                else:
                    sources.append(f"{rel}#{anchor.strip()}")
            else:
                sources.append(rel)
        status = "grounded" if sources else "gap"
        clean.append({
            "path": path, "title": title, "purpose": purpose,
            "outline": outline, "sources": sources, "status": status,
        })
    return clean, stats


def propose(task, entries, strategy, gap_questions=()):
    """태스크 + 지도 + 분할 전략 → 검증된 pages.

    반환: (pages, stats). 파싱 실패 시 1회 재시도, 그래도 실패면 ([], stats).
    """
    dg = repo_map.digest(entries)
    gap_str = "\n".join(f"- {q}" for q in gap_questions) or "(없음)"
    prompt = (
        "너는 지식베이스(위키) 구조 설계자다. 아래 태스크를 위한 위키의 "
        "폴더/페이지 구조를 '분할 전략'에 따라 제안하라.\n"
        f"[분할 전략]\n{strategy}\n\n"
        "규칙:\n"
        "- path는 상대경로 .md (슬래시로 폴더 표현, 파일 5~12개).\n"
        "- purpose는 그 페이지가 답해야 할 질문 한 문장.\n"
        "- outline은 그 md 안의 섹션 목차 3~6개 (\"## ...\" 형태).\n"
        "- sources는 아래 [소스 지도]의 경로만 사용. 문서는 \"경로#헤딩\"으로 "
        "섹션까지 좁혀도 된다 (지도에 있는 헤딩만).\n"
        "- 페이지마다 지도에서 관련 파일을 찾아 반드시 sources에 연결하라 — "
        "설계 문서·계획서도 그 축의 근거다. 정말 관련 파일이 없을 때만 비워라.\n"
        "- 근거 소스가 지도에 없지만 태스크에 필요한 축은 sources를 비우고 "
        "만들어라 (아래 [근거 없는 질문들]이 그 후보다).\n"
        '출력은 JSON만: {"pages":[{"path":"...","title":"...","purpose":"...",'
        '"outline":["## ..."],"sources":["..."]}]}  다른 텍스트 금지.\n\n'
        f"[태스크]\n{task}\n\n"
        f"[근거 없는 질문들 — gap 페이지 후보]\n{gap_str}\n\n"
        f"[소스 지도]\n{dg}"
    )
    pages = _parse_pages(llm.generate(prompt, num_predict=2000, temperature=0.3))
    if not pages:
        pages = _parse_pages(llm.generate(prompt, num_predict=2000, temperature=0.5))
    return validate(pages, entries)


# ---------- 채점 ----------

def _read_specs(entries_map, specs, cap, max_ctx):
    """source spec 목록의 실물을 읽어 (context, read_chars)를 만든다."""
    parts, seen = [], set()
    for s in specs:
        if s in seen:
            continue
        seen.add(s)
        text, _ = repo_map.read_source(entries_map, s, cap=cap)
        if text:
            parts.append(f"=== {s} ===\n{text}")
    context = "\n\n".join(parts)[:max_ctx]
    return context, len(context)


def total_source_chars(pages, entries):
    """구조가 참조하는 전체 source 실물 글자 합 (효율 분모).

    분모를 레포 전체가 아니라 '구조가 물고 있는 것'으로 잡는다 — 거대한
    파일을 마구 물면 분모도 커지지만 읽는 양이 더 빨리 커져 자기조정된다.
    """
    entries_map = repo_map.by_rel(entries)
    seen, total = set(), 0
    for p in pages:
        for s in p.get("sources", []):
            if s in seen:
                continue
            seen.add(s)
            text, _ = repo_map.read_source(entries_map, s, cap=10**9)
            total += len(text)
    return total


def score_proposal(pages, qa, entries, cap=12000, max_ctx=24000):
    """제안 구조를 Query 성능으로 채점한다.

    grounded 질문만 채점한다 (gap은 정답이 없어 시험 문제가 될 수 없다 —
    대신 구조 지표로 보고). 질문마다: Router가 pages 인덱스(path+purpose)에서
    페이지 선택 → 그 페이지 sources 실물을 읽고 답 → 원본 근거 정답과 대조.
    효율 분모 = total_source_chars. 종합 = 정확도 x 효율.
    """
    grounded = [x for x in qa if x.get("a")]
    n_gap = len(qa) - len(grounded)
    base = {"n_pages": len(pages), "n_gap_questions": n_gap,
            "n_grounded_questions": len(grounded)}
    if not pages or not grounded:
        return {**base, "total": None, "accuracy": None, "efficiency": None,
                "avg_read": 0, "parse_failed": False, "details": [],
                "reason": "no_pages" if not pages else "no_grounded_questions"}

    index = [{"title": p["path"], "desc": p["purpose"][:100]} for p in pages]
    picks_per_q = audit.route_batch([x["q"] for x in grounded], index)
    entries_map = repo_map.by_rel(entries)

    preds, reads, details = [], [], []
    for x, picks in zip(grounded, picks_per_q):
        chosen = [pages[p] for p in picks]
        specs = [s for pg in chosen for s in pg.get("sources", [])]
        context, read_chars = _read_specs(entries_map, specs, cap, max_ctx)
        pred = structure._answer(context, x["q"]) if context else "모름"
        preds.append(pred)
        reads.append(read_chars)
        details.append({"q": x["q"], "picked": [pg["path"] for pg in chosen],
                        "read_chars": read_chars, "pred": pred})

    qset = [{"q": x["q"], "a": x["a"]} for x in grounded]
    scores, parse_failed = scoring.judge_all(qset, preds)
    for d, s in zip(details, scores):
        d["score"] = s

    denom = total_source_chars(pages, entries)
    acc = sum(scores) / len(grounded)
    avg_read = sum(reads) / len(reads)
    eff = 1.0 - min(1.0, avg_read / denom) if denom else 0.0
    return {**base, "total": round(acc * eff, 3), "accuracy": round(acc, 3),
            "efficiency": round(eff, 3), "avg_read": int(avg_read),
            "denom_chars": denom, "parse_failed": parse_failed,
            "details": details}
