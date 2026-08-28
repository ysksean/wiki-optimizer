"""Query 기반 평가(scoring).

llm_wiki의 실제 용도 = "질문하면 관련 내용을 읽어 답한다"(Query).
따라서 요약/구조의 품질도 그 실제 용도로 평가한다:

  "이 요약에 질문을 던졌을 때 제대로 답이 나오는가?"

핵심 원칙:
- 정답의 근거는 항상 *원본(raw)*이다. 남의 요약본이 정답이 아니다 (순환논리 회피).
- 두 축을 동시에 본다:
    (1) accuracy   — 요약만 보고 질문에 정확히 답하는 비율 (원본 근거 정답과 대조)
    (2) efficiency — 답하는 데 읽어야 하는 분량 (요약이 짧을수록 효율↑)
- 종합점수는 곱셈 결합. 한쪽만 높으면 전체가 낮아져 gaming을 억제한다:
    · 정보 다 때려넣기 → accuracy↑지만 efficiency↓ → 종합 낮음
    · 극단 압축 → efficiency↑지만 accuracy↓ → 종합 낮음

질문 세트(golden Q&A)는 문서당 한 번 만들어 고정한다(공정 비교).
"""

import json
import re

import llm


# ---------- 질문 세트 (golden) ----------

def build_question_set(raw_text, n=6):
    """원본에서 '실제로 물어볼 법한 질문 + 원본 근거 정답'을 추출한다.

    정답은 원본에 명시된 사실이어야 한다. (요약본이 아니라 raw가 근거)
    """
    prompt = (
        "다음 문서에 대해, 이 문서를 지식베이스로 쓸 때 실제로 물어볼 법한 "
        f"질문과 그 정답을 정확히 {n}개 만들어라.\n"
        "- 정답은 반드시 문서에 명시된 사실이어야 한다.\n"
        "- 단순 사실뿐 아니라 '무엇을 왜/어떻게'류 질문도 섞어라.\n"
        "- 답은 한 문장 이내로 짧게.\n"
        '출력은 JSON 배열만: [{"q":"...","a":"..."}]  다른 텍스트 금지.\n\n'
        f"문서:\n{raw_text}"
    )
    out = llm.generate(prompt, num_predict=900, temperature=0.1)
    return _parse_qa(out)


def _parse_qa(text):
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and "q" in item and "a" in item:
            out.append({"q": str(item["q"]).strip(), "a": str(item["a"]).strip()})
    return out


# ---------- 채점 ----------

def _answer_all(summary, question_set):
    """모든 질문을 한 번의 호출로 답한다 (배치 = 속도 최적화)."""
    q_lines = "\n".join(f"{i+1}. {qa['q']}" for i, qa in enumerate(question_set))
    prompt = (
        "아래 '컨텍스트'에 근거해서만 각 질문에 답하라. "
        "컨텍스트에 없으면 해당 답은 '모름'으로. 답은 각각 한 문장 이내.\n"
        '출력은 JSON 배열만: ["답1","답2",...] (질문 순서대로, 다른 텍스트 금지)\n\n'
        f"컨텍스트:\n{summary}\n\n질문들:\n{q_lines}\n\n답변 배열:"
    )
    out = llm.generate(prompt, num_predict=400, temperature=0.0)
    return _parse_str_list(out, len(question_set))


def _judge_all(question_set, predictions):
    """모든 (정답 vs 예측)을 한 번의 호출로 판정한다 (배치)."""
    lines = []
    for i, (qa, pred) in enumerate(zip(question_set, predictions)):
        lines.append(f"{i+1}. 질문:{qa['q']} | 정답:{qa['a']} | 예측:{pred}")
    block = "\n".join(lines)
    prompt = (
        "각 항목에서 '예측'이 '정답'과 사실상 같은 내용이면 1, 틀리거나 '모름'이면 0. "
        "질문 순서대로 0/1 값을 JSON 배열로만 출력.\n"
        '예: [1,0,1,1,0]  (다른 텍스트 금지)\n\n'
        f"{block}\n\n판정 배열:"
    )
    out = llm.generate(prompt, num_predict=60, temperature=0.0)
    nums = re.findall(r"[01]", re.search(r"\[.*\]", out, re.DOTALL).group(0)) if re.search(r"\[.*\]", out, re.DOTALL) else []
    scores = [float(x) for x in nums[: len(question_set)]]
    # 파싱 실패분은 0으로 채움
    while len(scores) < len(question_set):
        scores.append(0.0)
    return scores


def _parse_str_list(text, n):
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                out = [str(x).strip() for x in data]
                while len(out) < n:
                    out.append("모름")
                return out[:n]
        except json.JSONDecodeError:
            pass
    return ["모름"] * n


def accuracy(summary, question_set):
    """요약으로 질문 세트를 풀어 정답률을 낸다. (0~1) + 세부내역.

    배치 처리: 질문 N개를 답변 1회 + 판정 1회, 총 2호출로 채점한다.
    """
    if not question_set:
        return 0.0, []
    preds = _answer_all(summary, question_set)
    scores = _judge_all(question_set, preds)
    details = [
        {"q": qa["q"], "gold": qa["a"], "pred": p, "score": s}
        for qa, p, s in zip(question_set, preds, scores)
    ]
    return sum(scores) / len(question_set), details


def efficiency(raw_text, summary, floor=0.10):
    """컨텍스트 효율. 답하는 데 읽는 분량(요약)이 원본 대비 작을수록 높다. (0~1)

    efficiency = 1 - (요약길이 / 원본길이).  단, floor 미만으로 짧아지는 건
    정보손실 위험이라 보상하지 않는다(하한에서 상한 고정).
    """
    if not raw_text:
        return 0.0, 0.0
    ratio = len(summary) / len(raw_text)
    eff = 1.0 - max(ratio, floor)
    return max(0.0, eff), ratio


def score(raw_text, summary, question_set):
    """종합 점수 = accuracy * efficiency (곱셈 결합, gaming 억제)."""
    acc, details = accuracy(summary, question_set)
    eff, ratio = efficiency(raw_text, summary)
    total = acc * eff
    return {
        "total": round(total, 3),
        "accuracy": round(acc, 3),
        "efficiency": round(eff, 3),
        "length_ratio": round(ratio, 3),
        "qa_details": details,
    }
