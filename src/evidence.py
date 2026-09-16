"""근거 탐색 — LLM 없이, 틀린 질문의 정답 근거가 원본 어느 문단에 있는지 찾고 실패 유형을 가른다.

B 구조 모드의 Reflector는 지금까지 "틀린 질문 + 고른 파일 이름"만 봤다. 정답 근거가
원본 어디에 있는지, 왜 틀렸는지(문서 누락 / 라우팅 실수 / 요약 손실)를 알 수 없었다.
이 모듈은 그 정보를 결정론적으로 만든다 — 에이전트 Reflector(설계 문서 2026-09-04)의
`inspect_failure` 도구도 같은 함수를 쓴다.

기대 답(expected)은 검색 키로만 쓰고 결과에 넣지 않는다. Reflector·에이전트가 정답 문장을
그대로 옮겨 쓰는 길을 막기 위해서다.

실패 유형:
  - doc_dropped   근거 문서가 어느 파일의 sources에도 없다 (조직 단계에서 통째로 빠짐)
  - routing_miss  근거 문서를 담은 파일이 있는데 Router가 다른 파일을 골랐다
  - content_lost  고른 파일이 근거 문서를 담고 있는데도 틀렸다 (요약하며 내용이 사라짐)
  - no_evidence   원본에서 근거 문단을 못 찾았다 (질문/정답이 원본과 어휘가 다르거나 추론형)
"""

import re

_TOKEN = re.compile(r"[\w]+", re.UNICODE)
# 너무 흔해 매칭 신호가 안 되는 토큰 — 한국어 조사·영어 관사 정도만. 길게 두면 오히려 근거를 놓친다.
_STOP = {"the", "a", "an", "of", "to", "in", "is", "and", "or", "for", "on", "with",
         "및", "또는", "그리고", "등", "때", "수", "것", "이", "그", "저", "은", "는", "을", "를",
         "의", "에", "로", "와", "과", "도", "다", "한", "할", "하는", "있다", "없다", "된다"}
SNIPPET_CHARS = 320
MAX_HITS = 3


def tokens(text):
    """소문자 단어 토큰 집합. 한 글자 토큰과 불용어는 뺀다."""
    out = set()
    for tok in _TOKEN.findall((text or "").lower()):
        if len(tok) >= 2 and tok not in _STOP:
            out.add(tok)
    return out


def split_paragraphs(text):
    """문서를 문단으로 나눈다. 각 문단은 직전 헤딩을 함께 기억한다.

    반환: [{"heading": str, "text": str}] — 빈 문단은 뺀다. 헤딩 줄 자체는 문단이 아니다.
    """
    paras, heading, buf = [], "", []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            paras.append({"heading": heading, "text": body})
        buf.clear()

    for line in (text or "").splitlines():
        if line.lstrip().startswith("#"):
            flush()
            heading = line.strip("# ").strip()
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line)
    flush()
    return paras


def find_evidence(docs, question, expected, max_hits=MAX_HITS):
    """원본 문서들에서 (질문, 기대 답)의 근거 문단을 찾는다.

    점수 = 기대 답과 겹치는 토큰 수 × 2 + 질문과 겹치는 토큰 수. 기대 답 쪽 가중치가
    큰 이유: 질문 어휘는 여러 문단에 흔하지만 정답 어휘는 근거 문단에 몰린다.
    반환: 점수 내림차순 [{"doc", "heading", "snippet", "score"}]. 0점은 뺀다.
    기대 답 원문은 결과에 포함하지 않는다.
    """
    q_tok, a_tok = tokens(question), tokens(expected)
    if not (q_tok or a_tok):
        return []
    hits = []
    for name, text in docs.items():
        for para in split_paragraphs(text):
            p_tok = tokens(para["text"])
            score = 2 * len(a_tok & p_tok) + len(q_tok & p_tok)
            if score <= 0:
                continue
            hits.append({"doc": name, "heading": para["heading"],
                         "snippet": para["text"][:SNIPPET_CHARS], "score": score,
                         "_len": len(para["text"])})
    # 같은 점수면 짧은 문단이 먼저 — 더 집중된 근거
    hits.sort(key=lambda h: (-h["score"], h["_len"]))
    for h in hits:
        h.pop("_len", None)
    return hits[:max_hits]


def _sources_by_title(struct):
    return {f.get("title", ""): set(f.get("sources") or []) for f in (struct or {}).get("files", [])}


def structure_warnings(docs, struct):
    """구조의 결정론적 결함 — LLM 판정 없이 즉시 알 수 있는 것들."""
    files = (struct or {}).get("files", [])
    warnings = []
    covered = set()
    for f in files:
        covered |= set(f.get("sources") or [])
    for name in docs:
        if name not in covered:
            warnings.append(f"문서 '{name}'가 어느 파일의 sources에도 없음 — 그 문서의 내용은 어떤 질문에도 쓰이지 않는다")
    seen = set()
    for f in files:
        title = f.get("title", "")
        if not (f.get("content") or "").strip():
            warnings.append(f"파일 '{title}'의 본문이 비어 있음")
        if not f.get("sources"):
            warnings.append(f"파일 '{title}'에 sources가 없음 — 어느 원본에서 왔는지 알 수 없다")
        if title in seen:
            warnings.append(f"파일 제목 '{title}'이 중복됨 — Router가 구분하지 못한다")
        seen.add(title)
    return warnings


def classify_failure(detail, struct, hits):
    """틀린 질문 하나의 실패 유형과, 근거 문서를 담고 있는 파일 목록."""
    if not hits:
        return "no_evidence", []
    evidence_doc = hits[0]["doc"]
    by_title = _sources_by_title(struct)
    holders = [t for t, srcs in by_title.items() if evidence_doc in srcs]
    picked = set(detail.get("picked") or [])
    if not holders:
        return "doc_dropped", []
    if picked & set(holders):
        return "content_lost", holders
    return "routing_miss", holders


def enrich(details, question_set, docs, struct, max_hits=MAX_HITS):
    """채점 details의 틀린 질문마다 근거·실패 유형을 붙인다.

    반환: [{"q", "picked", "type", "holders", "evidence": [hits]}] — 틀린 질문만.
    question_set은 기대 답을 찾는 데만 쓰고 결과에는 넣지 않는다.
    """
    expected = {qa["q"]: qa.get("a", "") for qa in (question_set or [])}
    out = []
    for d in details or []:
        if d.get("score", 0) >= 1:
            continue
        hits = find_evidence(docs, d.get("q", ""), expected.get(d.get("q", ""), ""), max_hits)
        kind, holders = classify_failure(d, struct, hits)
        out.append({"q": d.get("q", ""), "picked": list(d.get("picked") or []),
                    "type": kind, "holders": holders, "evidence": hits})
    return out


TYPE_LABEL = {
    "doc_dropped": "근거 문서가 어느 파일에도 안 들어감",
    "routing_miss": "근거는 다른 파일에 있는데 Router가 엉뚱한 파일을 고름",
    "content_lost": "고른 파일이 근거 문서를 담고 있는데도 틀림 — 요약하며 내용이 빠짐",
    "no_evidence": "원본에서 근거 문단을 찾지 못함",
}


def render_for_reflector(enriched, warnings, max_snippet=200):
    """Reflector 프롬프트에 넣을 텍스트. 기대 답은 없고 원본 근거 문단과 실패 유형만 있다."""
    lines = []
    for e in enriched:
        lines.append(f"- Q:{e['q']}")
        lines.append(f"  유형: {TYPE_LABEL.get(e['type'], e['type'])}")
        if e["holders"]:
            lines.append(f"  근거 문서를 담은 파일: {e['holders']}  (Router가 고른 파일: {e['picked']})")
        for h in e["evidence"][:2]:
            head = f" › {h['heading']}" if h["heading"] else ""
            snippet = h["snippet"][:max_snippet].replace("\n", " ")
            lines.append(f"  근거: [{h['doc']}{head}] {snippet}")
    block = "\n".join(lines) or "(없음)"
    warn = "\n".join(f"- {w}" for w in warnings) or "(없음)"
    return block, warn
