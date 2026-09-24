"""위키 MCP 서버 — 에이전트가 위키를 찾고 읽는 길목 + 실제 질문 기록 (stdio, 표준 라이브러리만).

왜: 지금까지의 평가는 모델이 원본에서 만든 질문으로만 쟀다. 사람이 실제로 위키에 묻는 질문은
거의 남아 있지 않다(2026-09-24 기준 wiki-ask 호출 3회). 에이전트(Claude Code 등)가 이 서버로
위키를 검색하고 페이지를 열면, 무엇을 찾았고 어떤 페이지를 읽었는지가 위키 루트의
`.wiki-optimizer/queries.jsonl`에 한 줄씩 쌓인다. 이 기록이 실제 질문 세트의 재료다.
기록에는 질의·경로·결과 경로만 남고 페이지 본문은 남기지 않는다.

도구
  wiki_index()                  페이지 경로·제목·한 줄 설명(index.md 설명 → frontmatter title → 첫 줄)
  wiki_search(query, limit=5)   제목·설명·본문에서 질의어를 찾아 점수순으로 경로와 짧은 발췌
  wiki_read(path)               페이지 원문. 'page', 'page.md', 'projects/x/overview', [[링크]] 모두 된다

실행   python3 src/wiki_mcp.py --wiki ~/dev/llm_wiki
등록   claude mcp add wiki -- python3 <이 파일의 절대경로> --wiki ~/dev/llm_wiki
기록   python3 src/wiki_mcp.py --wiki ~/dev/llm_wiki --queries   (쌓인 질의를 횟수순으로)
       WIKI_MCP_LOG=0 이면 기록하지 않는다. --log PATH로 기록 위치를 바꾼다.

프로토콜: MCP stdio — 한 줄에 JSON-RPC 2.0 메시지 하나. initialize / tools/list / tools/call / ping.
도구 실패는 JSON-RPC 오류가 아니라 isError=true 결과로 돌려준다(모델이 읽고 고칠 수 있게).
"""

import argparse
import json
import math
import os
import re
import sys
import uuid
from datetime import datetime, timezone

import frontmatter
from inspection import SKIP_DIRS

SERVER_NAME = "wiki-optimizer-wiki"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL = "2025-06-18"
LOG_ENABLED = os.environ.get("WIKI_MCP_LOG", "1") != "0"

_INDEX_ENTRY = re.compile(r"^\s*(?:[-*+]\s+)?\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]\s*[—–:-]\s*(.+?)\s*$")
_LINK = re.compile(r"^\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]$")
_WORD = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.+-]*|[가-힣]+")
_NOISE = re.compile(r"\*\*|__|`|\[\[([^\]|]+)\|?([^\]]*)\]\]")

TOOLS = [
    {
        "name": "wiki_index",
        "description": "List every page in the knowledge wiki with its path, title and one-line description. "
                       "Call this first to see what the wiki covers.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "wiki_search",
        "description": "Search the knowledge wiki. Returns the best-matching page paths with a short excerpt. "
                       "Use the user's own question as the query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you are looking for, in natural language."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "question": {"type": "string",
                             "description": "The user's original question, verbatim, if the query rephrases it. "
                                            "Recorded locally to build evaluation question sets."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wiki_read",
        "description": "Read one wiki page in full. Accepts a path from wiki_index/wiki_search, "
                       "a page name without .md, or a [[wiki link]].",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Page path or name, e.g. 'harness-engineering'."}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    """모델에게 isError=true로 보여 줄 도구 실패."""


class Wiki:
    """위키 루트(raw/ + wiki/) 또는 wiki 폴더 하나를 읽기 전용으로 연다."""

    def __init__(self, root, log_path=None, log_enabled=None):
        root = os.path.abspath(os.path.expanduser(root))
        self.root = root
        self.wiki_dir = os.path.join(root, "wiki") if os.path.isdir(os.path.join(root, "wiki")) else root
        if not os.path.isdir(self.wiki_dir):
            raise ValueError(f"위키 폴더가 없습니다: {root}")
        self.log_path = log_path or os.path.join(root, ".wiki-optimizer", "queries.jsonl")
        self.log_enabled = LOG_ENABLED if log_enabled is None else log_enabled
        self.session = uuid.uuid4().hex[:12]

    # ---------- 페이지 ----------

    def _files(self):
        """wiki 폴더 아래 .md → {이름(확장자 없음, / 구분): 절대경로}. 숨김·생성 폴더·심링크 제외."""
        out = {}
        for parent, dirs, names in os.walk(self.wiki_dir, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                             and not os.path.islink(os.path.join(parent, d)))
            for name in sorted(names):
                path = os.path.join(parent, name)
                if name.lower().endswith(".md") and name.lower() != "readme.md" and not os.path.islink(path):
                    out[os.path.relpath(path, self.wiki_dir)[:-3].replace(os.sep, "/")] = path
        return out

    def _index_descriptions(self):
        path = os.path.join(self.wiki_dir, "index.md")
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8", errors="replace") as f:
            _, body = frontmatter.split(f.read())
        out = {}
        for line in body.splitlines():
            m = _INDEX_ENTRY.match(line)
            if m:
                key = os.path.basename(m.group(1).strip().removesuffix(".md"))
                desc = _NOISE.sub(lambda w: (w.group(2) or w.group(1) or ""), m.group(2)).strip()
                if desc:
                    out.setdefault(key, desc)
        return out

    def pages(self):
        """[{name, path, title, description, text}] — 매 호출마다 디스크에서 새로 읽는다(위키가 바뀌어도 맞게)."""
        described = self._index_descriptions()
        out = []
        for name, path in self._files().items():
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            meta, _ = frontmatter.split(text)
            title = meta.get("title") if isinstance(meta.get("title"), str) else ""
            desc = described.get(os.path.basename(name)) or title or frontmatter.first_line(text, 160)
            out.append({"name": name, "path": path, "title": title or os.path.basename(name),
                        "description": desc[:200], "text": text})
        return out

    def resolve(self, ref):
        """'page' / 'page.md' / 'wiki/page.md' / 'projects/x/overview' / [[링크]] → 페이지 이름.

        위키 밖을 가리키거나(../) 없거나 여러 페이지에 맞으면 ToolError."""
        ref = str(ref or "").strip()
        m = _LINK.match(ref)
        if m:
            ref = m.group(1).strip()
        ref = ref.replace("\\", "/").removesuffix(".md").lstrip("/")
        if ref.startswith("./"):
            ref = ref[2:]
        if ref.startswith("wiki/") and os.path.basename(self.wiki_dir) == "wiki":
            ref = ref[len("wiki/"):]
        if not ref or ".." in ref.split("/"):
            raise ToolError(f"위키 안의 페이지 경로가 아닙니다: {ref!r}")
        names = self._files()
        if ref in names:
            return ref
        hits = sorted(n for n in names if n.endswith("/" + ref))
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise ToolError(f"'{ref}'에 맞는 페이지가 여럿입니다: {', '.join(hits)} — 경로를 골라 다시 읽으세요.")
        raise ToolError(f"'{ref}' 페이지가 없습니다. wiki_index나 wiki_search로 경로를 확인하세요.")

    # ---------- 도구 ----------

    def index(self):
        pages = self.pages()
        lines = [f"{len(pages)} pages in {self.wiki_dir}"]
        lines += [f"- {p['name']}.md — {p['title']}: {p['description']}" for p in pages]
        return "\n".join(lines)

    def search(self, query, limit=5, question=None):
        query = str(query or "").strip()
        if not query:
            raise ToolError("query가 비어 있습니다.")
        limit = max(1, min(10, int(limit or 5)))
        terms = _terms(query)
        if not terms:
            raise ToolError("검색할 낱말이 없습니다.")
        pages = self.pages()
        weights = _idf(terms, pages)
        ranked = []
        for p in pages:
            score = _score(terms, p, weights)
            if score > 0:
                ranked.append((score, p))
        ranked.sort(key=lambda x: (-x[0], x[1]["name"]))
        hits = ranked[:limit]
        entry = {"tool": "wiki_search", "query": query, "results": [p["name"] for _, p in hits]}
        if isinstance(question, str) and question.strip() and question.strip() != query:
            entry["question"] = question.strip()
        self._log(entry)
        if not hits:
            return f"'{query}'에 맞는 페이지가 없습니다. wiki_index로 전체 목록을 보세요."
        lines = [f"{len(hits)} result(s) for: {query}"]
        for score, p in hits:
            lines.append(f"- {p['name']}.md (score {score:.1f}) — {p['title']}: {p['description']}")
            excerpt = _excerpt(terms, p["text"])
            if excerpt:
                lines.append(f"    …{excerpt}…")
        return "\n".join(lines)

    def read(self, ref):
        try:
            name = self.resolve(ref)
        except ToolError:
            self._log({"tool": "wiki_read", "path": str(ref), "found": False})
            raise
        path = self._files()[name]
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        self._log({"tool": "wiki_read", "path": name, "found": True})
        return f"# {name}.md\n\n{text}"

    def _log(self, entry):
        if not self.log_enabled:
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "session": self.session, **entry}
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:  # 기록 실패가 검색·읽기를 막지 않는다
            print(f"[wiki-mcp] 질의 기록 실패: {e}", file=sys.stderr)


def _terms(text):
    """영숫자 낱말(소문자) + 한글 낱말의 두 글자 조각. '구조를'도 '구조'와 맞도록 조사에 덜 민감하게."""
    terms = set()
    for word in _WORD.findall(text.lower()):
        if re.fullmatch(r"[가-힣]+", word):
            if len(word) == 1:
                continue
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
        elif len(word) >= 2:
            terms.add(word)
    return terms


def _idf(terms, pages):
    """조각별 가중치 — 여러 페이지에 흔한 조각('프로젝트' 등)은 낮게, 드문 조각은 높게.

    가중치 없이 세면 흔한 낱말이 여러 개 겹친 페이지가 핵심 낱말 하나 맞은 페이지를 이겼다
    (실제 위키에서 'PII 모델 프로젝트…' 질의에 aipmo 프로젝트 페이지가 먼저 나왔다)."""
    n = len(pages)
    texts = [(p["title"] + " " + p["name"] + " " + p["description"] + " " + p["text"]).lower() for p in pages]
    return {t: math.log((n + 1) / (sum(1 for x in texts if t in x) + 0.5)) for t in terms}


def _score(terms, page, weights=None):
    """맞은 조각의 가중합 — 제목 3배, 설명 2배, 본문 1배(+자주 나오면 최대 1배 더). 조각마다 가장 높은 곳 하나."""
    title, desc = (page["title"] + " " + page["name"]).lower(), page["description"].lower()
    body = page["text"].lower()
    score = 0.0
    for term in terms:
        w = weights.get(term, 1.0) if weights else 1.0
        if term in title:
            score += 3 * w
        elif term in desc:
            score += 2 * w
        elif term in body:
            score += (1 + min(body.count(term), 5) * 0.2) * w
    return score


def _excerpt(terms, text, width=160):
    """질의 조각이 가장 많이 든 본문 줄 하나(frontmatter 제외)."""
    _, body = frontmatter.split(text)
    best, best_hits = "", 0
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        hits = sum(1 for t in terms if t in low)
        if hits > best_hits:
            best, best_hits = line, hits
    return best[:width]


# ---------- MCP (JSON-RPC 2.0 over stdio) ----------

def handle(wiki, message):
    """메시지 하나 → 응답 dict (알림이면 None)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
    method, msg_id = message["method"], message.get("id")
    params = message.get("params") or {}
    if msg_id is None:            # 알림(notifications/initialized 등) — 응답하지 않는다
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested) else DEFAULT_PROTOCOL
        return _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "Knowledge wiki. Use wiki_search with the user's question, then wiki_read the "
                            "pages you need. wiki_index lists every page.",
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        try:
            if name == "wiki_index":
                text = wiki.index()
            elif name == "wiki_search":
                text = wiki.search(args.get("query"), args.get("limit", 5), args.get("question"))
            elif name == "wiki_read":
                text = wiki.read(args.get("path"))
            else:
                return _error(msg_id, -32602, f"Unknown tool: {name}")
        except ToolError as e:
            return _result(msg_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        except (OSError, ValueError, TypeError) as e:
            return _result(msg_id, {"content": [{"type": "text", "text": f"도구 실행 실패: {e}"}], "isError": True})
        return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
    return _error(msg_id, -32601, f"Method not found: {method}")


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(wiki, stdin=None, stdout=None):
    """stdin의 줄마다 메시지 하나를 읽고 응답을 한 줄로 쓴다. stdin이 닫히면 끝난다."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply = _error(None, -32700, "Parse error")
        else:
            if isinstance(message, list):   # 일괄 요청은 지원하지 않는다(2025-06-18에서 제거됨)
                reply = _error(None, -32600, "Batch requests are not supported")
            else:
                reply = handle(wiki, message)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def query_summary(log_path):
    """기록 → 질문별 [{query, count, last, results, rephrased}] (횟수순). 실제 질문 세트의 재료.

    에이전트가 원래 질문을 question으로 넘겼으면 그 질문으로 묶고, 바꿔 쓴 검색어는 rephrased에 모은다."""
    if not os.path.isfile(log_path):
        return []
    stats = {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("tool") != "wiki_search" or not entry.get("query"):
                continue
            q = (entry.get("question") or entry["query"]).strip()
            row = stats.setdefault(q, {"query": q, "count": 0, "last": "", "results": [], "rephrased": []})
            if entry.get("question") and entry["query"] not in row["rephrased"]:
                row["rephrased"].append(entry["query"])
            row["count"] += 1
            row["last"] = max(row["last"], entry.get("ts", ""))
            row["results"] = entry.get("results", [])
    return sorted(stats.values(), key=lambda r: (-r["count"], r["query"]))


def main():
    ap = argparse.ArgumentParser(description="위키 MCP 서버 (stdio) — 검색·읽기 + 질의 기록")
    ap.add_argument("--wiki", required=True, help="위키 루트(raw/·wiki/ 포함) 또는 wiki 폴더")
    ap.add_argument("--log", help="질의 기록 파일 (기본: <위키 루트>/.wiki-optimizer/queries.jsonl)")
    ap.add_argument("--no-log", action="store_true", help="질의를 기록하지 않는다")
    ap.add_argument("--queries", action="store_true", help="서버 대신 쌓인 질의를 횟수순으로 출력")
    args = ap.parse_args()
    wiki = Wiki(args.wiki, log_path=args.log, log_enabled=False if args.no_log else None)
    if args.queries:
        rows = query_summary(wiki.log_path)
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(f"[wiki-mcp] {wiki.wiki_dir} · 기록 {'끔' if not wiki.log_enabled else wiki.log_path}", file=sys.stderr)
    serve(wiki)


if __name__ == "__main__":
    main()
