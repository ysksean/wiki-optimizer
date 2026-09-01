"""태스크 기반 질문 세트 (Stage 0).

기존과 질문의 출처가 다르다: 문서("문서에 뭐가 적혀 있나")가 아니라
태스크("이 일을 하려면 위키가 무엇에 답해야 하나")에서 질문을 뽑는다.
그래야 "소스에 근거가 없는 것"(gap)이 드러난다.

정답 근거는 항상 원본이라는 기존 원칙 유지 — oracle이 지도에서 관련 파일을
골라(audit.route_batch 재사용) 실물을 읽고 정답을 만든다. 못 만들면
a=None = gap 질문. gap은 채점에서 빼되 버리지 않는다 — 구조 제안에
status:gap 페이지의 근거가 된다.
"""

import hashlib
import json
import os
import re

import audit
import llm
import repo_map
import structure

QCACHE_DIR = os.path.join("runs", "qcache")


def _parse_q_list(text, n):
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = [str(x).strip() for x in data if isinstance(x, str) and str(x).strip()]
    return out[:n]


def build_questions(task, entries, n=8):
    """태스크 설명 + 지도 digest → 위키가 답해야 할 질문 n개."""
    dg = repo_map.digest(entries)
    prompt = (
        "너는 지식베이스(위키) 설계자다. 아래 '태스크'를 수행하는 사람이 "
        f"이 위키에 실제로 던질 법한 질문을 정확히 {n}개 만들어라.\n"
        "- 태스크의 목적·소비 시점 관점에서 물어라 (소스 파일 내용 요약 금지).\n"
        "- 소스에 답이 있을지 없을지는 신경 쓰지 마라 — 필요한 질문이면 만든다.\n"
        "- 구체적으로: '~은 무엇인가/어떻게 하나/어디에 있나' 형태.\n"
        '출력은 JSON 배열만: ["질문1","질문2",...]  다른 텍스트 금지.\n\n'
        f"[태스크]\n{task}\n\n[소스 지도 (참고용)]\n{dg}"
    )
    qs = _parse_q_list(llm.generate(prompt, num_predict=600, temperature=0.3), n)
    if not qs:  # 1회 재시도
        qs = _parse_q_list(llm.generate(prompt, num_predict=600, temperature=0.5), n)
    return qs


_UNKNOWN_RE = re.compile(r"모름|알 수 없|근거가 없|나와 있지 않|없습니다|찾을 수 없")


def oracle_answers(questions, entries, cap=12000, max_ctx=24000):
    """질문마다 지도에서 파일을 골라 실물을 읽고 정답을 만든다.

    반환: [{"q","a","evidence"}...]  a=None이면 gap 질문.
    """
    entries_map = repo_map.by_rel(entries)
    index = [
        {"title": e["rel"],
         "desc": (" | ".join(h["text"] for h in e.get("headings", [])[:4])
                  if e["kind"] == "doc"
                  else "code: " + ", ".join(e.get("names", [])[:4]))[:100]}
        for e in entries
    ]
    picks_per_q = audit.route_batch(questions, index)
    out = []
    for q, picks in zip(questions, picks_per_q):
        rels = [index[p]["title"] for p in picks]
        parts = []
        for rel in rels:
            text, _ = repo_map.read_source(entries_map, rel, cap=cap)
            if text:
                parts.append(f"=== {rel} ===\n{text}")
        context = "\n\n".join(parts)[:max_ctx]
        ans = structure._answer(context, q).strip() if context else "모름"
        if not ans or _UNKNOWN_RE.search(ans):
            out.append({"q": q, "a": None, "evidence": rels})
        else:
            out.append({"q": q, "a": ans, "evidence": rels})
    return out


def get_question_set(task, entries, n=8, use_cache=True):
    """질문 생성 + oracle 정답 (태스크+지도 기준 캐시).

    반환: (qa 리스트, gap 질문 리스트)
    """
    key = hashlib.sha1(
        json.dumps([task, sorted(e["rel"] for e in entries), n],
                   ensure_ascii=False).encode()
    ).hexdigest()[:16]
    path = os.path.join(QCACHE_DIR, f"task-{key}.json")
    if use_cache and os.path.isfile(path):
        try:
            with open(path) as f:
                qa = json.load(f)
            return qa, [x["q"] for x in qa if x["a"] is None]
        except (OSError, json.JSONDecodeError):
            pass
    questions = build_questions(task, entries, n=n)
    qa = oracle_answers(questions, entries) if questions else []
    if qa and use_cache:
        os.makedirs(QCACHE_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(qa, f, ensure_ascii=False, indent=2)
    return qa, [x["q"] for x in qa if x["a"] is None]
