"""구조화 전략 위키 (WikiSkill 스타일) — evolve-wiki arm 전용.

flat 이력(strategy-impact.jsonl)과 달리, 패턴 1개당 페이지 1개에
**문제 + 근본 원인 + 사례 + 해법**을 적고 index.md 카탈로그로 잇는다.
논문(arxiv 2608.27454)의 핵심 주장은 "이력 유무"가 아니라 "이력의 구조"다 —
이 모듈이 그 대비축(flat vs 구조화)을 우리 실험대에 만든다.

설계 원칙:
- Wiki Maintainer LLM 호출은 세대당 1회. 응답은 JSON patch 연산
  (create/append/replace)으로만 위키를 수정한다 — 통째 재작성 금지.
- 파싱 실패 시 위키를 **건드리지 않고 skip**한다. 실패를 빈 갱신으로
  메우면 어느 세대의 교훈이 소실됐는지 알 수 없다 (SEA-13 교훈).
- index.md는 LLM이 직접 쓰지 않는다 — 패턴 페이지의 요약 줄(제목 다음
  첫 줄)에서 결정론적으로 재생성한다. index/페이지 불일치가 생길 수 없다.
- 페이지는 MAX_PAGE_LINES를 넘으면 요약 줄만 남기고 오래된 본문부터
  버린다 (무한 누적 방지 — 논문이 한계로 인정한 지점의 최소 방어).
"""

import json
import os
import re
import threading

import llm


WIKI_DIR = os.path.join("runs", "wiki")


def _patterns_dir():
    return os.path.join(WIKI_DIR, "patterns")


def _index_path():
    return os.path.join(WIKI_DIR, "index.md")


_WIKI_LOCK = threading.Lock()

MAX_PAGE_LINES = 40      # 요약 줄 포함 페이지 상한
MAX_BLOCK_CHARS = 6000   # reflect에 주입하는 위키 블록 총량 상한
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


# ---------- 읽기 ----------

def _page_path(slug):
    return os.path.join(_patterns_dir(), f"{slug}.md")


def _read_page(slug):
    path = _page_path(slug)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read()


def _page_summary(text):
    """페이지의 요약 줄 = 제목(#) 다음 첫 비어있지 않은 줄."""
    for line in text.splitlines()[1:]:
        line = line.strip()
        if line:
            return line
    return ""


def _list_pages():
    """(slug, 본문, mtime) 목록. 없으면 빈 리스트."""
    d = _patterns_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".md"):
            continue
        slug = name[:-3]
        path = os.path.join(d, name)
        with open(path) as f:
            out.append((slug, f.read(), os.path.getmtime(path)))
    return out


def wiki_block():
    """reflect 프롬프트에 넣을 위키 텍스트 (index + 최근 패턴 페이지).

    위키가 비어 있으면 빈 문자열 — reflect는 이력 없이 동작한다.
    """
    with _WIKI_LOCK:
        pages = _list_pages()
    if not pages:
        return ""
    index = "\n".join(f"- **{slug}**: {_page_summary(text)}" for slug, text, _ in pages)
    lines = ["[전략 패턴 위키 — 과거 실험에서 축적된 패턴. 근본 원인이 같은 실패를 반복하지 마라]",
             "[index]", index, ""]
    budget = MAX_BLOCK_CHARS - sum(len(x) for x in lines)
    # 최근 갱신된 페이지부터 상세 본문을 싣는다 (예산 내에서)
    for slug, text, _ in sorted(pages, key=lambda p: p[2], reverse=True):
        if len(text) > budget:
            break
        lines.append(f"[패턴: {slug}]\n{text.strip()}")
        budget -= len(text)
    return "\n".join(lines) + "\n\n"


# ---------- Wiki Maintainer (LLM 1회/세대) ----------

def _parse_ops(out):
    """Maintainer 응답에서 JSON patch 연산 목록을 뽑는다. 실패는 None.

    None(파싱 실패)과 [](수정할 것 없음)를 구분한다 — 전자는 위키를
    건드리지 않았다는 사실이 로그에 남아야 한다.
    """
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    ops = data.get("ops")
    if not isinstance(ops, list):
        return None
    valid = []
    for op in ops:
        if not isinstance(op, dict):
            return None
        kind = op.get("op")
        slug = op.get("slug", "")
        if kind not in ("create", "append", "replace") or not _SLUG_RE.match(slug):
            return None
        if kind in ("create", "append") and not isinstance(op.get("content"), str):
            return None
        if kind == "replace" and not (
            isinstance(op.get("old"), str) and isinstance(op.get("new"), str)
        ):
            return None
        valid.append(op)
    return valid


def _clamp_page(text):
    """페이지 상한 초과 시 요약 줄(제목+첫 줄)은 지키고 오래된 본문부터 버린다."""
    lines = text.splitlines()
    if len(lines) <= MAX_PAGE_LINES:
        return text
    head, body = lines[:2], lines[2:]
    return "\n".join(head + body[-(MAX_PAGE_LINES - 2):])


def _apply_ops(ops):
    """patch 연산 적용 + index 재생성. 적용된 연산 수를 돌려준다."""
    applied = 0
    os.makedirs(_patterns_dir(), exist_ok=True)
    for op in ops:
        slug = op["slug"]
        existing = _read_page(slug)
        if op["op"] == "create":
            if existing is not None:
                continue  # 이미 있는 페이지를 통째로 덮지 않는다
            content = op["content"].strip()
            if not content.startswith("#"):
                content = f"# {slug}\n{content}"
            new_text = content + "\n"
        elif op["op"] == "append":
            if existing is None:
                continue
            new_text = existing.rstrip("\n") + "\n" + op["content"].strip() + "\n"
        else:  # replace — 정확히 1회 일치할 때만 (오적용 방지)
            if existing is None or existing.count(op["old"]) != 1:
                continue
            new_text = existing.replace(op["old"], op["new"])
        with open(_page_path(slug), "w") as f:
            f.write(_clamp_page(new_text))
        applied += 1
    _rebuild_index()
    return applied


def _rebuild_index():
    """index.md를 패턴 페이지 요약 줄에서 결정론적으로 재생성한다."""
    pages = _list_pages()
    lines = ["# 전략 패턴 index", ""]
    lines += [f"- **{slug}**: {_page_summary(text)}" for slug, text, _ in pages]
    os.makedirs(WIKI_DIR, exist_ok=True)
    with open(_index_path(), "w") as f:
        f.write("\n".join(lines) + "\n")


MAINTAINER_PROMPT = """너는 요약 전략 실험의 '위키 관리자'다.
아래 세대 실행 결과에서 재사용 가능한 **패턴**(문제 + 근본 원인 + 해법)을 뽑아
패턴 위키를 갱신하라. 세대 하나의 일회성 사실이 아니라, 다음 실험에도 통할
일반화된 교훈만 기록한다. 갱신할 것이 없으면 빈 ops를 내라.

규칙:
- 페이지 1개 = 패턴 1개. 형식: 1행 `# <slug>`, 2행 요약 한 문장(문제+근본 원인+해법), 이후 본문 10~30줄
- 기존 페이지와 근본 원인이 같으면 새 페이지를 만들지 말고 append/replace로 보강하라
- 출력은 JSON 하나만: {{"ops": [{{"op": "create", "slug": "kebab-case", "content": "..."}}, {{"op": "append", "slug": "...", "content": "..."}}, {{"op": "replace", "slug": "...", "old": "...", "new": "..."}}]}}

[현재 index]
{index}

[이번 세대 실행 결과]
- 전략: {strategy}
- held-out 점수: {held_out} (채택={accepted})
- 길이 비율: {ratio} (작을수록 효율↑)
- 요약이 답 못한 train 질문: {missed}

JSON:"""


def maintain(doc, generation, strategy, r_train, r_test, accepted):
    """세대 결과를 위키에 반영한다 (LLM 1회). 성공 여부를 돌려준다.

    파싱 실패 시 위키를 건드리지 않는다 — 조용한 빈 갱신으로 메우지 않는다.
    """
    missed = [d["q"] for d in (r_train or {}).get("qa_details", []) if d["score"] < 1]
    with _WIKI_LOCK:
        pages = _list_pages()
        index = "\n".join(f"- **{s}**: {_page_summary(t)}" for s, t, _ in pages) or "(비어 있음)"
    prompt = MAINTAINER_PROMPT.format(
        index=index,
        strategy=strategy[:500],
        held_out=r_test["total"],
        accepted=accepted,
        ratio=r_test["length_ratio"],
        missed="; ".join(missed[:5]) or "(없음)",
    )
    out = llm.generate(prompt, num_predict=600, temperature=0.3)
    ops = _parse_ops(out)
    if ops is None:
        print(f"[wiki] maintainer 응답 파싱 실패 (doc={doc} gen={generation}) — 위키 변경 없음")
        return False
    with _WIKI_LOCK:
        applied = _apply_ops(ops)
    print(f"[wiki] doc={doc} gen={generation}: 패턴 연산 {applied}/{len(ops)}건 적용")
    return True
