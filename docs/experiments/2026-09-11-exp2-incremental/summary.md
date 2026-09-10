# 배치 결과 요약 (held-out 점수 기준)

- 실행: 문서 3개 x run 2 x gen 4
- 총 run: 18개
- 총 소요: 280.6분

## Arm별 지표 (best - gen0, held-out)
| arm | runs | 평균 향상폭 | 표준편차 | 개선run 비율 | 파싱실패(제외) |
|---|---|---|---|---|---|
| control | 6 | +0.193 | 0.212 | 50% | 0 |
| evolve | 6 | +0.196 | 0.215 | 50% | 0 |
| evolve-incremental | 6 | +0.074 | 0.087 | 83% | 0 |

## 문서별 (run 평균)
| 문서 | size | arm | gen0(평균) | best(평균) | 향상폭 |
|---|---|---|---|---|---|
| agentic-engineering-5-concepts+claude-md-guide+transformer-architecture+event-rescoring-idempotency-cache+scoring-rag-reference-free-eval+self-evolving-persistent-history-effect+karpathy-llm-wiki-pattern | 62170 | control | 0.728 | 0.935 | +0.208 |
| agentic-engineering-5-concepts+claude-md-guide+transformer-architecture+event-rescoring-idempotency-cache+scoring-rag-reference-free-eval+self-evolving-persistent-history-effect+karpathy-llm-wiki-pattern | 62170 | evolve | 0.568 | 0.973 | +0.405 |
| agentic-engineering-5-concepts+claude-md-guide+transformer-architecture+event-rescoring-idempotency-cache+scoring-rag-reference-free-eval+self-evolving-persistent-history-effect+karpathy-llm-wiki-pattern | 62170 | evolve-incremental | 0.740 | 0.826 | +0.086 |
| karpathy-vibe-to-agentic+agentic-engineering-pillars+claude-code-basics+redis-be-ai-realtime-communication+llm-deterministic-post-gates+wikiskill-persistent-knowledge-skill-evolution+pii-model-design-session-2026-06 | 73737 | control | 0.672 | 0.857 | +0.185 |
| karpathy-vibe-to-agentic+agentic-engineering-pillars+claude-code-basics+redis-be-ai-realtime-communication+llm-deterministic-post-gates+wikiskill-persistent-knowledge-skill-evolution+pii-model-design-session-2026-06 | 73737 | evolve | 0.858 | 0.858 | +0.000 |
| karpathy-vibe-to-agentic+agentic-engineering-pillars+claude-code-basics+redis-be-ai-realtime-communication+llm-deterministic-post-gates+wikiskill-persistent-knowledge-skill-evolution+pii-model-design-session-2026-06 | 73737 | evolve-incremental | 0.749 | 0.867 | +0.118 |
| harness-engineering-concept+claude-code-workflow+claude-code-agent-teams+aipmo-1.5-design-judgments-2026-06+llm-judge-scoring-principles+reward-model-rl+alignment-rlhf-constitutional-ai | 79677 | control | 0.668 | 0.855 | +0.187 |
| harness-engineering-concept+claude-code-workflow+claude-code-agent-teams+aipmo-1.5-design-judgments-2026-06+llm-judge-scoring-principles+reward-model-rl+alignment-rlhf-constitutional-ai | 79677 | evolve | 0.663 | 0.847 | +0.184 |
| harness-engineering-concept+claude-code-workflow+claude-code-agent-teams+aipmo-1.5-design-judgments-2026-06+llm-judge-scoring-principles+reward-model-rl+alignment-rlhf-constitutional-ai | 79677 | evolve-incremental | 0.942 | 0.960 | +0.018 |

## 절대 점수 비교 (best held-out — gen0와 무관)
- gen0 노이즈(같은 문서 안 gen0 표준편차 평균): 0.163 — **0.1 이상이면 위 '향상폭'은 gen0 운에 좌우된다. 아래 절대 점수로 판단할 것.**
| arm | runs | 평균 best | 표준편차 | 평균 gen0 |
|---|---|---|---|---|
| control | 6 | 0.882 | 0.084 | 0.690 |
| evolve | 6 | 0.893 | 0.091 | 0.696 |
| evolve-incremental | 6 | 0.884 | 0.086 | 0.810 |
- evolve vs control (best 절대 점수, 문서짝 3개): +0.010, 95% CI [-0.008, +0.037], p=0.518 → 구분되지 않는다
- evolve-incremental vs control (best 절대 점수, 문서짝 3개): +0.002, 95% CI [-0.110, +0.105], p=0.710 → 구분되지 않는다

## 해석
- 진화 효과(net) = evolve +0.196 - control +0.193 = **+0.003**
- 유의성 (문서 단위 paired bootstrap 1000회, 문서 3개): net(문서짝) +0.003, 95% CI [-0.185, +0.198], p=0.826
- control과 통계적으로 **구분되지 않는다** — 관측된 향상폭은 **선택 노이즈**로 설명 가능. (p=0.826, 유의수준 0.05 미달)
