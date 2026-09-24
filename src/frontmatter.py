"""마크다운 frontmatter 최소 파서 — 외부 YAML 의존성 없이 위키 페이지 메타데이터를 읽는다.

지원하는 것: 문서 맨 앞 `---` ~ `---` 블록의
  - `key: value`
  - `key: [a, b]` (인라인 리스트)
  - `key:` 다음 줄들의 `  - item` (블록 리스트)
그 밖의 YAML 문법은 문자열 그대로 둔다. 어떤 입력에도 예외를 던지지 않는다 — 깨진
frontmatter는 본문으로 취급한다.
"""

import re

_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def _scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def split(text):
    """(meta: dict, body: str). frontmatter가 없거나 닫히지 않았으면 ({}, 원문)."""
    text = text or ""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text
    meta, key = {}, None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s*-\s+(.*)$", raw)
        if item and key is not None:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(_scalar(item.group(1)))
            continue
        m = _KEY.match(raw.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [_scalar(v) for v in value[1:-1].split(",") if v.strip()]
        elif value:
            meta[key] = _scalar(value)
        else:
            meta[key] = []          # 블록 리스트가 이어질 수 있다
    body = "\n".join(lines[end + 1:])
    return meta, body


def first_line(text, limit=80):
    """frontmatter를 건너뛴 본문의 첫 내용 줄(헤딩 기호 제거)."""
    _, body = split(text)
    for line in body.splitlines():
        line = line.strip()
        if line:
            return line.lstrip("# ").strip()[:limit]
    return ""
