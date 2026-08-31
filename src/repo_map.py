"""소스(레포/데이터 폴더) 증거 지도 — LLM 호출 없는 순수 로직 (Stage 0).

여러 root 폴더를 걸어 파일별 요약 항목을 만든다. 레포 전체를 LLM에 넣지
않기 위한 장치:
- doc(.md/.txt): 헤딩 목록(level+offset)과 앞부분 미리보기
- code(.py/.js/...): 모듈 첫 주석/docstring 한 줄 + top-level def/class/export 이름만

rel 경로는 "<root명>/<root 내 상대경로>". sources 참조는 "rel" 또는
"rel#헤딩" — 헤딩 앵커는 지도의 헤딩 목록으로 검증하며, 없으면 파일
전체로 폴백한다(환각 앵커가 빈 컨텍스트로 새는 것을 차단).
"""

import hashlib
import json
import os
import re

CACHE_DIR = os.path.join("runs", "repomap")

EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", ".cache", ".claude", ".idea", ".vscode", "runs", "coverage",
}
EXCLUDE_FILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock",
}
DOC_EXTS = {".md", ".txt"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rs", ".sh", ".sql"}

_HEADING_RE = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# top-level만: 줄 시작(들여쓰기 없음) 기준
_CODE_NAME_RE = re.compile(
    r"^(?:def|class)[ \t]+(\w+)"
    r"|^(?:export[ \t]+)?(?:default[ \t]+)?(?:async[ \t]+)?function[ \t]+(\w+)"
    r"|^(?:export[ \t]+)?class[ \t]+(\w+)"
    r"|^export[ \t]+const[ \t]+(\w+)",
    re.MULTILINE,
)


def _norm_anchor(text):
    """헤딩/앵커 비교용 정규화: 소문자, 공백·-·_ 를 '-' 하나로, '#' 제거."""
    t = text.strip().lstrip("#").strip().lower()
    return re.sub(r"[\s\-_]+", "-", t)


def _scan_file(path, rel, head_chars):
    ext = os.path.splitext(path)[1].lower()
    kind = "doc" if ext in DOC_EXTS else "code"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    entry = {"rel": rel, "path": path, "size": len(text), "kind": kind}
    if kind == "doc":
        entry["headings"] = [
            {"level": len(m.group(1)), "text": m.group(2), "offset": m.start()}
            for m in _HEADING_RE.finditer(text)
        ]
        entry["head"] = text[:head_chars]
    else:
        names = []
        for m in _CODE_NAME_RE.finditer(text):
            name = next(g for g in m.groups() if g)
            if name not in names:
                names.append(name)
        entry["names"] = names[:40]
        first = text.strip().split("\n")[0][:120]
        entry["head"] = first
    return entry


def _walk_root(root, head_chars):
    root = os.path.abspath(os.path.expanduser(root))
    label = os.path.basename(root.rstrip(os.sep))
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")
        )
        for fn in sorted(filenames):
            if fn in EXCLUDE_FILES:
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext not in DOC_EXTS and ext not in CODE_EXTS:
                continue
            path = os.path.join(dirpath, fn)
            rel = label + "/" + os.path.relpath(path, root).replace(os.sep, "/")
            e = _scan_file(path, rel, head_chars)
            if e:
                entries.append(e)
    return entries


def _fair_cut(entries, max_files):
    """상한 초과 시 최상위 디렉터리별 라운드로빈으로 고르게 남긴다(폭 우선)."""
    if len(entries) <= max_files:
        return entries
    groups = {}
    for e in entries:
        parts = e["rel"].split("/")
        key = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
        groups.setdefault(key, []).append(e)
    # 각 그룹 안에서는 doc 우선, 작은 파일 우선 (지도 가치 대비 비용)
    for g in groups.values():
        g.sort(key=lambda e: (e["kind"] != "doc", e["size"]))
    kept = []
    keys = sorted(groups)
    i = 0
    while len(kept) < max_files:
        advanced = False
        for k in keys:
            if i < len(groups[k]):
                kept.append(groups[k][i])
                advanced = True
                if len(kept) >= max_files:
                    break
        if not advanced:
            break
        i += 1
    return kept


def _cache_key(roots, max_files, head_chars):
    sig = []
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")
            )
            for fn in sorted(filenames):
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                sig.append((p, st.st_size, int(st.st_mtime)))
    raw = json.dumps([sorted(sig), max_files, head_chars], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def build_map(roots, max_files=400, head_chars=600, use_cache=True):
    """roots 전체의 증거 지도. 반환: entries 리스트 (rel 정렬)."""
    key = _cache_key(roots, max_files, head_chars) if use_cache else None
    cache_path = os.path.join(CACHE_DIR, f"{key}.json") if key else None
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    entries = []
    for root in roots:
        entries.extend(_walk_root(root, head_chars))
    entries = _fair_cut(entries, max_files)
    entries.sort(key=lambda e: e["rel"])
    if cache_path:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(entries, f, ensure_ascii=False)
    return entries


def by_rel(entries):
    return {e["rel"]: e for e in entries}


def find_anchor(entry, anchor):
    """entry의 헤딩 목록에서 anchor와 맞는 헤딩을 찾는다. 없으면 None."""
    if entry.get("kind") != "doc":
        return None
    want = _norm_anchor(anchor)
    for h in entry.get("headings", []):
        if _norm_anchor(h["text"]) == want:
            return h
    return None


def read_source(entries_map, spec, cap=12000):
    """'rel' 또는 'rel#헤딩'의 실물 텍스트.

    반환: (text, anchor_hit). 앵커 미검증이면 파일 전체 폴백 + anchor_hit=False.
    지도에 없는 rel이면 ("", False).
    """
    rel, _, anchor = spec.partition("#")
    entry = entries_map.get(rel.strip())
    if entry is None:
        return "", False
    try:
        with open(entry["path"], encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return "", False
    if not anchor:
        return text[:cap], True
    h = find_anchor(entry, anchor)
    if h is None:
        return text[:cap], False
    start = h["offset"]
    end = len(text)
    for nxt in entry.get("headings", []):
        if nxt["offset"] > start and nxt["level"] <= h["level"]:
            end = nxt["offset"]
            break
    return text[start:end][:cap], True


def digest(entries, per_file=6):
    """LLM 프롬프트용 지도 요약 — 파일당 한 줄."""
    lines = []
    for e in entries:
        if e["kind"] == "doc":
            marks = " | ".join(h["text"] for h in e.get("headings", [])[:per_file])
            desc = marks or e.get("head", "").strip().split("\n")[0][:80]
        else:
            desc = "code: " + ", ".join(e.get("names", [])[:per_file])
        lines.append(f"- {e['rel']} — {desc}"[:200])
    return "\n".join(lines)
