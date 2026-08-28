---
title: 보상모델과 강화학습 (RLVR · RLAIF · PPO · GRPO · RFT)
sources:
  - "claude-code session: study/model — 보상모델/RL 학습 노트"
added: 2026-06-08
---

# 보상모델과 강화학습 (RL)

LLM을 사람 선호/정답 방향으로 학습시키기 위한 강화학습 기법들. 핵심은 **원본 모델과 가깝게 유지하면서, 정답(또는 선호)을 더 잘 말하도록** 미세 조정하는 것.

## 0. 기본 용어

- **SFT (Supervised Fine-Tuning)**: 주어진 Q-A Pair에 맞게 답변하도록 학습
- **Reward Learning**: Q-A pair에서 선호/비선호를 수치화해 보상 신호로 만들어 학습에 추가
- **DPO (Direct Preference Optimization)**: 보상모델 없이, 사람이 매긴 선호 순위를 바로 모델 학습에 반영
- **RLHF**: 사람이 평가자 / **RLAIF**: AI가 평가자
- **RLVR (RL with Verifiable Rewards)**: 명확한 정답이 있는 상황에서의 강화학습 (예: 숫자 연산, 코드)
- **Reward Hacking**: 모델이 보상 점수만 높이려고 의미 없는 행동·답변을 만들어내는 현상

---

## 1. RLVR vs RLAIF

| 구분 | RLVR | RLAIF (또는 RLHF) |
| --- | --- | --- |
| 적용 상황 | 정답이 명확한 복잡 문제 (수학 단계 풀이, 코딩) | 정답이 정해져 있지 않은 추론·선호 학습 |
| 보상모델 | **불필요** (정답 검증으로 보상) | 필요 (선호 판단) |
| 리소스 | 보상모델 제작 리소스 절약 | 보상모델 제작 필요 |

**핵심**: RLVR은 "대답을 검증할 수 있지만, **추론을 어떻게 해야 할지**를 학습"시키는 것. 즉 답은 채점 가능하되 추론 경로를 강화학습으로 가르친다.

→ 답이 정해져 있지 않고 추론 방식만 학습시키고 싶으면 RLHF/RLAIF 사용.

---

## 2. PPO vs GRPO

보상학습은 **원본 모델과 가깝되 정답을 더 잘 말하는** 방향으로 가야 한다. 그러나 학습이 진행되면 원본에서 크게 벗어날 위험이 있다.

### PPO (Proximal Policy Optimization)
- RLHF에서 사용. 정책을 **안전하게 조금씩** 개선해 원본과 너무 멀어지지 않게 하고 불안정을 줄임
- **단점: 모델 4개가 필요해 리소스가 많이 듦**
  1. 원본 모델 (reference)
  2. 학습 중인 모델 (policy)
  3. 보상 모델 (reward)
  4. **가치 모델 (value)** — 토큰 생성 시마다 맞는 방향으로 가는지 예측. (답변을 모두 생성한 뒤 채점하면 어느 부분에서 틀렸는지 알 수 없으므로 필요)

### GRPO (Group Relative Policy Optimization)
- PPO의 "리소스 과다" 한계를 해결
- 하나의 질문에 **여러 개의 답**을 내놓고, 그 **그룹 안에서 스스로 정답에 가까운 답변을 선택**하게 함
- → **가치 모델이 필요 없음** (그룹 내 상대 비교가 가치 추정을 대신)

---

## 3. OpenAI RFT (Reinforcement Fine-Tuning)

- **RLVR + RLAIF를 "채점기(grader)"라는 하나의 인터페이스로 통합**하고, 강화학습 인프라를 전부 갖춰놓은 서비스
- **로직(채점기)은 사람이 설계**하지만, 인프라는 이미 구축되어 있어 손쉽게 사용 가능

---

## 관련 노트
- Alignment (RLHF → DPO → Constitutional AI) 흐름은 별도 노트 `alignment-rlhf-constitutional-ai.md` 참조
