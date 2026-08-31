"""폴더 구조 최적화 (B단계).

여러 raw 문서를 '분할 전략'에 따라 폴더 구조(여러 wiki 파일)로 조직하고,
질문이 오면 관련 파일만 골라 읽어 답한다. 구조의 좋고 나쁨을
Query 성능(정확도 x 효율)으로 평가한다.

  - Organizer: 문서들 -> 분할 전략에 따라 N개 파일 {title, content} + index
  - Router:    질문 + index -> 읽을 파일 선택 (관련 파일만)
  - 채점:      고른 파일로 답변 -> 정답 대조(정확도) + 읽은 글자수(효율)

진화 손잡이 = 분할 전략(자연어 지시). 세대마다 Reflector가 수정한다.

정답 근거는 항상 원본(raw). 효율 = 1 - (읽은 글자 / 전체 원본 글자).
종합 = 정확도 x 효율 (곱셈 결합, gaming 억제).
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor

import llm
import scoring


# ---------- Organizer ----------

def organize(docs, strategy):
    """문서들(dict: name->text)을 분할 전략에 따라 구조로 조직한다.

    반환: {"files": [{"title","content"}...], "index": [{"title","desc"}...]}
    """
    joined = "\n\n".join(
        f"=== 문서: {name} ===\n{text}" for name, text in docs.items()
    )
    prompt = (
        "너는 지식베이스 구조 설계자다. 아래 원본 문서들을 '분할 전략'에 따라 "
        "여러 개의 위키 파일로 재조직하라.\n"
        f"[분할 전략]\n{strategy}\n\n"
        "각 파일은 제목(title)과 내용(content)을 가진다. content는 원본에서 "
        "관련 내용을 추려 간결히 정리한다. 파일 개수와 분할 방식은 전략을 따르라.\n"
        '출력은 JSON만: {"files":[{"title":"...","content":"..."}]}  다른 텍스트 금지.\n\n'
        f"{joined}"
    )
    out = llm.generate(prompt, num_predict=1500, temperature=0.3)
    struct = _parse_struct(out)
    # 파싱 실패 시 1회 재시도, 그래도 실패하면 문서 자체를 파일로 두는 fallback
    if not struct.get("files"):
        out = llm.generate(prompt, num_predict=1500, temperature=0.5)
        struct = _parse_struct(out)
    if not struct.get("files"):
        struct = {"files": [{"title": name, "content": text} for name, text in docs.items()]}
    # index 생성 (파일명 + 한줄 설명) — Router가 파일 고를 때 쓴다
    struct["index"] = [
        {"title": f["title"], "desc": _one_line(f["content"])}
        for f in struct.get("files", [])
    ]
    return struct


def _one_line(content, limit=80):
    first = content.strip().split("\n")[0]
    return first[:limit]


def _parse_struct(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"files": []}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"files": []}
    files = data.get("files", [])
    clean = []
    for f in files:
        if isinstance(f, dict) and "title" in f and "content" in f:
            clean.append({"title": str(f["title"]).strip(), "content": str(f["content"]).strip()})
    return {"files": clean}


# ---------- Router ----------

def route(question, index):
    """질문 + index를 보고 읽을 파일 제목들을 고른다. (관련 파일만)"""
    idx_str = "\n".join(f"{i+1}. {e['title']} — {e['desc']}" for i, e in enumerate(index))
    prompt = (
        "질문에 답하는 데 필요한 파일을 아래 목록에서 고르라. "
        "꼭 필요한 것만(보통 1~2개). 번호만 JSON 배열로 출력.\n"
        '예: [2]  또는  [1,3]  (다른 텍스트 금지)\n\n'
        f"[파일 목록]\n{idx_str}\n\n질문: {question}\n선택:"
    )
    out = llm.generate(prompt, num_predict=20, temperature=0.0)
    m = re.search(r"\[.*\]", out, re.DOTALL)
    picks = []
    if m:
        picks = [int(x) - 1 for x in re.findall(r"\d+", m.group(0))]
    # 유효 인덱스만
    picks = [p for p in picks if 0 <= p < len(index)]
    return picks if picks else [0]  # 아무것도 못 고르면 첫 파일 fallback


# ---------- 채점 ----------

def _answer(context, question):
    prompt = (
        "아래 '컨텍스트'에 근거해서만 질문에 답하라. 없으면 '모름'. 한 문장 이내.\n\n"
        f"컨텍스트:\n{context}\n\n질문: {question}\n답:"
    )
    return llm.generate(prompt, num_predict=80, temperature=0.0)


def score_structure(struct, question_set, total_raw_chars):
    """구조 전체를 Query 성능으로 채점한다.

    각 질문마다: Router가 파일 선택 -> 그 파일만 읽어 답 -> 읽은 글자수 기록.
    정확도 = 평균 정답률. 효율 = 1 - (평균 읽은 글자 / 전체 원본 글자).
    종합 = 정확도 x 효율.
    판정은 scoring.judge_all 공용 — parse_failed=True면 점수는 신뢰 불가.
    """
    files = struct.get("files", [])
    index = struct.get("index", [])
    if not files:
        return {"total": 0.0, "accuracy": 0.0, "efficiency": 0.0,
                "avg_read": 0, "n_files": 0, "parse_failed": False, "details": []}

    by_title = {f["title"]: f["content"] for f in files}

    def _query_one(qa):
        picks = route(qa["q"], index)
        chosen_titles = [index[p]["title"] for p in picks]
        context = "\n\n".join(by_title.get(t, "") for t in chosen_titles)
        return {"q": qa["q"], "picked": chosen_titles,
                "read_chars": len(context), "pred": _answer(context, qa["q"])}

    # 질문별 route→answer 체인은 상호 독립 — 병렬로 세대당 2n회 직렬 호출을
    # 4폭으로 접는다. ex.map은 입력 순서를 보존하므로 judge와의 짝이 안 틀어진다.
    with ThreadPoolExecutor(max_workers=min(4, len(question_set))) as ex:
        details = list(ex.map(_query_one, question_set))
    preds = [d["pred"] for d in details]
    reads = [d["read_chars"] for d in details]

    scores, parse_failed = scoring.judge_all(question_set, preds)
    for d, s in zip(details, scores):
        d["score"] = s

    acc = sum(scores) / len(question_set)
    avg_read = sum(reads) / len(reads)
    # 효율: 전체 원본 대비 평균적으로 얼마나 적게 읽었나
    eff = 1.0 - min(1.0, avg_read / total_raw_chars) if total_raw_chars else 0.0
    total = acc * eff
    return {
        "total": round(total, 3),
        "accuracy": round(acc, 3),
        "efficiency": round(eff, 3),
        "avg_read": int(avg_read),
        "n_files": len(files),
        "parse_failed": parse_failed,
        "details": details,
    }


def build_cross_question_set(docs, n=4):
    """여러 문서 전체에 걸친 질문 세트를 만든다 (문서 경계 넘나드는 질문 포함)."""
    joined = "\n\n".join(f"=== {name} ===\n{text}" for name, text in docs.items())
    prompt = (
        "다음 문서 묶음을 지식베이스로 쓸 때 실제로 물어볼 법한 질문과 정답을 "
        f"정확히 {n}개 만들어라. 서로 다른 문서의 내용을 묻는 질문을 섞어라. "
        "정답은 문서에 명시된 사실, 한 문장 이내.\n"
        '출력은 JSON 배열만: [{"q":"...","a":"..."}]  다른 텍스트 금지.\n\n'
        f"{joined}"
    )
    out = llm.generate(prompt, num_predict=800, temperature=0.1)
    m = re.search(r"\[.*\]", out, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [
        {"q": str(x["q"]).strip(), "a": str(x["a"]).strip()}
        for x in data if isinstance(x, dict) and "q" in x and "a" in x
    ]
