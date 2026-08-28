"""요약 품질 자동 채점(Verifier).

요약은 "정답"이 없으므로 검증 가능한 프록시 지표로 채점한다:

1. faithfulness (재구성 정확도) — 원본에서 뽑은 Q&A의 질문을,
   *요약만* 컨텍스트로 주고 풀게 한 뒤 정답과 대조한다.
   요약이 핵심 정보를 담고 있으면 답할 수 있고, 빠뜨렸으면 못 답한다.
   -> 이것이 "verifiable signal". self-improving의 핵심.

2. compression (압축 효율) — 원본 대비 요약 길이 비율.
   목표 대역(기본 15~35%)을 벗어나면 감점. 너무 길면 요약이 아니고,
   너무 짧으면 정보 손실.

최종 점수 = faithfulness 가중 + compression 가중.
Q&A 세트는 문서당 한 번만 생성해 캐시한다(세대 간 동일 기준 = 공정 비교).
"""

import json
import re

import llm


def build_qa_set(raw_text, n=5):
    """원본 문서에서 사실 기반 Q&A n개를 추출한다. (벤치마크 고정용)"""
    prompt = (
        "다음 문서에서 핵심 사실을 묻는 질문-답변 쌍을 정확히 "
        f"{n}개 만들어라. 답은 문서에 명시된 사실이어야 하고 짧아야 한다.\n"
        "출력은 JSON 배열만: [{\"q\": \"...\", \"a\": \"...\"}, ...]\n"
        "다른 텍스트는 절대 쓰지 마라.\n\n"
        f"문서:\n{raw_text}"
    )
    out = llm.generate(prompt, num_predict=800, temperature=0.1)
    return _parse_qa(out)


def _parse_qa(text):
    """LLM 출력에서 JSON 배열을 최대한 관대하게 파싱한다."""
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    qa = []
    for item in data:
        if isinstance(item, dict) and "q" in item and "a" in item:
            qa.append({"q": str(item["q"]).strip(), "a": str(item["a"]).strip()})
    return qa


def _grade_answer(question, gold, predicted):
    """예측 답이 정답과 사실상 일치하는지 LLM으로 판정(0 또는 1)."""
    prompt = (
        "질문에 대한 두 답을 비교하라. '예측'이 '정답'과 사실상 같은 내용이면 1, "
        "틀리거나 '모름'이면 0. 숫자 하나만 출력.\n\n"
        f"질문: {question}\n정답: {gold}\n예측: {predicted}\n\n판정(0 또는 1):"
    )
    out = llm.generate(prompt, num_predict=4, temperature=0.0)
    return 1.0 if "1" in out else 0.0


def faithfulness(summary, qa_set):
    """요약만 보고 Q&A를 풀게 해서 정답률을 낸다. (0~1)"""
    if not qa_set:
        return 0.0
    correct = 0
    details = []
    for qa in qa_set:
        ans_prompt = (
            "아래 '요약'에 근거해서만 질문에 답하라. 요약에 없으면 '모름'이라고 답하라. "
            "답은 한 문장 이내로 짧게.\n\n"
            f"요약:\n{summary}\n\n질문: {qa['q']}\n답:"
        )
        pred = llm.generate(ans_prompt, num_predict=60, temperature=0.0)
        score = _grade_answer(qa["q"], qa["a"], pred)
        correct += score
        details.append({"q": qa["q"], "gold": qa["a"], "pred": pred, "score": score})
    return correct / len(qa_set), details


def compression(raw_text, summary, lo=0.15, hi=0.35):
    """길이 비율 점수. 목표 대역[lo,hi] 안이면 1.0, 벗어나면 선형 감점. (0~1)"""
    if not raw_text:
        return 0.0
    ratio = len(summary) / len(raw_text)
    if lo <= ratio <= hi:
        return 1.0, ratio
    if ratio < lo:
        return max(0.0, ratio / lo), ratio
    # 너무 긺
    return max(0.0, 1.0 - (ratio - hi) / hi), ratio


def score_summary(raw_text, summary, qa_set, w_faith=0.75, w_comp=0.25):
    """종합 점수와 세부 내역을 반환한다."""
    faith, faith_details = faithfulness(summary, qa_set)
    comp, ratio = compression(raw_text, summary)
    total = w_faith * faith + w_comp * comp
    return {
        "total": round(total, 3),
        "faithfulness": round(faith, 3),
        "compression": round(comp, 3),
        "length_ratio": round(ratio, 3),
        "qa_details": faith_details,
    }
