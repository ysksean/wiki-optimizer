---
title: Alignment (RLHF → DPO → Constitutional AI)
sources:
  - "claude-code session: study/model — Alignment 학습 노트"
added: 2026-06-08
---

# Alignment

**Alignment = 인간의 목표/가치와 모델의 목표·행동을 일치시키는 일** (= 사람 선호도에 적합하게 만드는 것).

## 1. 왜 Instruct-Tuning이 필요했나

LLM이 답은 하는데 유저가 불만족했기 때문. 그래서 **Instruct-Tuning**을 시작.

### InstructGPT (RLHF)
- Q-A format(질문, 답)을 사용
- 유저의 **선호 답 / 비선호 답**을 기반으로 가중치를 조정해 **보상 모델 학습**
- 그 보상 모델을 참고하게 해서 **강화학습**을 진행

### DPO (Direct Preference Optimization)
- RLHF는 **보상 모델을 따로 만들어야 하는** 문제가 있음
- DPO는 **보상모델 없이 선호도 데이터로 직접 학습**하는 방법

---

## 2. 발생한 문제 (Safety Problem의 원인)

위 방법들로 학습하면 다음 부작용이 생긴다:

1. **답이 멍청해진다** (Alignment Tax)
2. **Hallucination** (환각)
3. **Sycophancy (아부)** — 유저 말을 과도하게 칭찬. 말도 안 되는 소리에도 "핵심을 찔렀어요" 식으로 칭찬

→ 이 문제들 때문에 **Safety problem**이 생긴다.

---

## 3. 해결책 — Constitutional AI

- **헌법(constitution)으로 기준을 만들고**, 그 기준을 해석하면서 **스스로 자기 답변을 비판하고 수정**하는 **자기감독(self-supervision)** 방식
- 이렇게 더 나은 답변을 생성하고, 그 데이터로 **RLAIF**를 돌려 **AI가 선호도를 판단**

### 관련 개념
- **Self-Critique**: 모델이 스스로 답변 품질을 평가/수정
- **Reward Hacking**: 모델이 보상 점수만 높이려고 의미 없는 답변을 만듦 (Constitutional AI/안전장치가 막으려는 대상 중 하나)

---

## 4. 전체 흐름 요약

```
LLM
 → Instruct Tuning (RLHF / InstructGPT)
 → 문제 발생 (Alignment Tax · Hallucination · Sycophancy)
 → Constitutional AI (AI가 헌법 원칙으로 자기 답변을 스스로 평가·수정)
```

---

## 관련 노트
- 보상모델·RL 기법(RLVR/RLAIF/PPO/GRPO/RFT) 상세는 별도 노트 `reward-model-rl.md` 참조
