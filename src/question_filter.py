"""평가 질문 거르기와 기준선 — 문서 없이 풀리는 질문을 빼고, 점수를 두 기준선 사이에 놓는다.

점수 하나만으로는 "정답률 0.85가 좋은 값인가"를 알 수 없다. 같은 질문·같은 판정으로 두 값을 함께 잰다.
  - 문서 없이(floor)    모델이 일반 지식만으로 맞히는 비율
  - 원본 전체(ceiling)  원본을 통째로 읽고 답할 때의 비율. 원본으로도 못 푸는 질문은 질문 자체가 문제다
위키·구조의 정답률이 원본 전체에 가까우면 더 다듬을 여지가 적고, 문서 없이에 가까우면 위키가
거의 기여하지 못하고 있다.

문서 없이 풀리는 질문은 어떤 구조로 채점해도 맞으므로 구조 사이의 차이를 가리지 못하고 점수만
올린다(2026-09 실험: 36문항 중 6개). filter_questions는 요청 수의 1.5배를 만든 뒤 문서 없이 풀린
질문을 뒤로 미룬다. 그래도 모자라면 그 질문으로 채우고 closed_book=True로 표시한다. 검사를 거친
질문에는 모두 closed_book 값이 붙으므로 기준선은 추가 호출 없이 floor를 낸다.

QUESTION_FILTER=0이면 거르지 않고, QUESTION_BASELINES=0이면 기준선을 재지 않는다.
"""

import copy
import hashlib
import json
import math
import os
import re
import threading

import llm
import scoring

FILTER = os.environ.get("QUESTION_FILTER", "1") != "0"
BASELINES = os.environ.get("QUESTION_BASELINES", "1") != "0"
OVERSAMPLE = 1.5

_CLOSED_BOOK_PROMPT = (
    "아래 질문들에 문서 없이 당신의 일반 지식만으로 각각 한 문장 이내로 답하라. 확실히 모르면 '모름'.\n"
    '출력은 JSON 문자열 배열만: ["답1","답2",...]  질문 순서대로, 다른 텍스트 금지.\n\n'
    "질문들:\n{questions}\n\n답변 배열:"
)

_MEMO = {}
_MEMO_LOCK = threading.Lock()
_MEMO_MAX = 32


def _parse_answers(text, n):
    """JSON 문자열 배열 → 답 n개. 개수가 다르거나 읽을 수 없으면 None ('모름'으로 메우지 않는다)."""
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or len(data) != n:
        return None
    return [str(x).strip() for x in data]


def _judge(question_set, preds):
    if preds is None:
        return None
    scores, parse_failed = scoring.judge_all(question_set, preds)
    return None if parse_failed else scores


def closed_book(question_set):
    """문서 없이 답하고 판정한다 → 질문별 0/1. 답이나 판정을 읽지 못하면 None."""
    if not question_set:
        return []
    lines = "\n".join(f"{i + 1}. {qa['q']}" for i, qa in enumerate(question_set))
    out = llm.generate(_CLOSED_BOOK_PROMPT.format(questions=lines), num_predict=600,
                       temperature=0.0, effort="low")
    return _judge(question_set, _parse_answers(out, len(question_set)))


def with_context(context, question_set):
    """context를 읽고 답하고 판정한다 → 질문별 0/1. 답이나 판정을 읽지 못하면 None.

    프롬프트는 A 모드 채점(scoring._answer_all)과 같다. 다른 점은 답 개수가 어긋나면
    '모름'으로 메우지 않고 실패로 돌려준다는 것 — 기준선이 조용히 0으로 깔리면 안 된다."""
    if not question_set:
        return []
    out = llm.generate(scoring.answer_all_prompt(context, question_set), num_predict=600,
                       temperature=0.0, effort="low")
    return _judge(question_set, _parse_answers(out, len(question_set)))


def filter_questions(generate, n, enabled=None):
    """generate(k) → [{"q","a"}, ...]. 반환: (질문 최대 n개, info).

    info: enabled, requested, generated, checked(닫힌 책 검사를 실제로 했는가),
          dropped(뺀 질문 수), dropped_questions, kept_closed_book(모자라서 채운 수)
    검사 호출이 실패하면 거르지 않고 앞에서 n개를 쓴다 — 질문 세트가 비는 것보다 낫다."""
    enabled = FILTER if enabled is None else enabled
    if not enabled:
        qs = list(generate(n) or [])
        return qs, {"enabled": False, "requested": n, "generated": len(qs), "checked": False,
                    "dropped": 0, "dropped_questions": [], "kept_closed_book": 0}
    pool = list(generate(max(n, math.ceil(n * OVERSAMPLE))) or [])
    info = {"enabled": True, "requested": n, "generated": len(pool), "checked": False,
            "dropped": 0, "dropped_questions": [], "kept_closed_book": 0}
    if not pool:
        return [], info
    try:
        scores = closed_book(pool)
    except Exception as e:  # LLMError·타임아웃 — 질문 세트 자체는 살린다
        print(f"[questions] 문서 없이 풀리는지 검사하지 못했다 — 거르지 않는다: {e}")
        scores = None
    if scores is None:
        return pool[:n], info
    info["checked"] = True
    clean = [dict(qa, closed_book=False) for qa, s in zip(pool, scores) if s < 1]
    leaked = [dict(qa, closed_book=True) for qa, s in zip(pool, scores) if s >= 1]
    chosen = clean[:n]
    fill = leaked[:n - len(chosen)]
    dropped = leaked[len(fill):]
    info.update(dropped=len(dropped), dropped_questions=[qa["q"] for qa in dropped],
                kept_closed_book=len(fill))
    print(f"[questions] {len(pool)}개 생성 → 문서 없이 풀린 {len(leaked)}개 중 {len(dropped)}개 제외"
          + (f", 모자라서 {len(fill)}개는 표시하고 사용" if fill else ""))
    return chosen + fill, info


def floor_from_flags(question_set):
    """검사를 거친 질문 세트면 closed_book 표시로 floor를 낸다. 하나라도 표시가 없으면 None."""
    if not question_set or any("closed_book" not in qa for qa in question_set):
        return None
    return [1.0 if qa["closed_book"] else 0.0 for qa in question_set]


def _summary(question_set, scores, list_key):
    """list_key="hits"면 맞힌 질문, "misses"면 틀린 질문 목록을 붙인다."""
    if scores is None:
        return None
    want_correct = list_key == "hits"
    return {"accuracy": round(sum(scores) / len(question_set), 3), "n": len(question_set),
            list_key: [qa["q"] for qa, s in zip(question_set, scores) if (s >= 1) == want_correct]}


def baselines(question_set, context):
    """같은 질문·같은 판정으로 문서 없이(floor) / 원본 전체(ceiling) 정답률을 잰다.

    반환: {"n", "floor": {"accuracy", "n", "hits", "source"} | None,
           "ceiling": {"accuracy", "n", "misses"} | None}
    같은 원본·질문·모델이면 프로세스 안에서 한 번만 잰다(배치의 arm·run이 공유)."""
    if not question_set:
        return None
    key = hashlib.sha256(json.dumps(
        [llm.BACKEND, llm.CLAUDE_MODEL if llm.BACKEND == "claude" else llm.CODEX_MODEL, llm.LANGUAGE,
         context, [(qa["q"], qa["a"], qa.get("closed_book")) for qa in question_set]],
        ensure_ascii=False).encode("utf-8")).hexdigest()
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
    if hit is not None:
        return copy.deepcopy(hit)

    flags = floor_from_flags(question_set)
    floor_source = "filter"
    if flags is None:
        floor_source = "measured"
        try:
            flags = closed_book(question_set)
        except Exception as e:
            print(f"[baseline] 문서 없이 답하기 실패: {e}")
            flags = None
    try:
        ceiling = with_context(context, question_set)
    except Exception as e:
        print(f"[baseline] 원본 전체로 답하기 실패: {e}")
        ceiling = None

    floor = _summary(question_set, flags, "hits")
    if floor:
        floor["source"] = floor_source
    result = {"n": len(question_set), "floor": floor, "ceiling": _summary(question_set, ceiling, "misses")}
    if floor or result["ceiling"]:
        with _MEMO_LOCK:
            _MEMO[key] = copy.deepcopy(result)
            while len(_MEMO) > _MEMO_MAX:
                _MEMO.pop(next(iter(_MEMO)))
    print(f"[baseline] 질문 {len(question_set)}개 · 원본 전체 "
          f"{result['ceiling']['accuracy'] if result['ceiling'] else '실패'} · 문서 없이 "
          f"{floor['accuracy'] if floor else '실패'}")
    return result
