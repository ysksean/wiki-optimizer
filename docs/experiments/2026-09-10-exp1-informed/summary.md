# 배치 결과 요약 (held-out 점수 기준)

- 실행: 문서 3개 x run 2 x gen 4
- 총 run: 18개
- 총 소요: 294.3분

## Arm별 지표 (best - gen0, held-out)
| arm | runs | 평균 향상폭 | 표준편차 | 개선run 비율 | 파싱실패(제외) |
|---|---|---|---|---|---|
| control | 6 | +0.326 | 0.277 | 83% | 0 |
| evolve | 6 | +0.124 | 0.240 | 83% | 0 |
| evolve-informed | 6 | +0.157 | 0.232 | 83% | 0 |

## 문서별 (run 평균)
| 문서 | size | arm | gen0(평균) | best(평균) | 향상폭 |
|---|---|---|---|---|---|
| agentic-engineering-5-concepts+claude-md-guide+transformer-architecture+event-rescoring-idempotency-cache+scoring-rag-reference-free-eval+self-evolving-persistent-history-effect+karpathy-llm-wiki-pattern | 62170 | control | 0.622 | 0.959 | +0.337 |
| agentic-engineering-5-concepts+claude-md-guide+transformer-architecture+event-rescoring-idempotency-cache+scoring-rag-reference-free-eval+self-evolving-persistent-history-effect+karpathy-llm-wiki-pattern | 62170 | evolve | 0.626 | 0.932 | +0.306 |
| agentic-engineering-5-concepts+claude-md-guide+transformer-architecture+event-rescoring-idempotency-cache+scoring-rag-reference-free-eval+self-evolving-persistent-history-effect+karpathy-llm-wiki-pattern | 62170 | evolve-informed | 0.633 | 0.799 | +0.165 |
| karpathy-vibe-to-agentic+agentic-engineering-pillars+claude-code-basics+redis-be-ai-realtime-communication+llm-deterministic-post-gates+wikiskill-persistent-knowledge-skill-evolution+pii-model-design-session-2026-06 | 73737 | control | 0.471 | 0.785 | +0.314 |
| karpathy-vibe-to-agentic+agentic-engineering-pillars+claude-code-basics+redis-be-ai-realtime-communication+llm-deterministic-post-gates+wikiskill-persistent-knowledge-skill-evolution+pii-model-design-session-2026-06 | 73737 | evolve | 0.773 | 0.803 | +0.030 |
| karpathy-vibe-to-agentic+agentic-engineering-pillars+claude-code-basics+redis-be-ai-realtime-communication+llm-deterministic-post-gates+wikiskill-persistent-knowledge-skill-evolution+pii-model-design-session-2026-06 | 73737 | evolve-informed | 0.616 | 0.901 | +0.285 |
| harness-engineering-concept+claude-code-workflow+claude-code-agent-teams+aipmo-1.5-design-judgments-2026-06+llm-judge-scoring-principles+reward-model-rl+alignment-rlhf-constitutional-ai | 79677 | control | 0.636 | 0.963 | +0.327 |
| harness-engineering-concept+claude-code-workflow+claude-code-agent-teams+aipmo-1.5-design-judgments-2026-06+llm-judge-scoring-principles+reward-model-rl+alignment-rlhf-constitutional-ai | 79677 | evolve | 0.951 | 0.986 | +0.035 |
| harness-engineering-concept+claude-code-workflow+claude-code-agent-teams+aipmo-1.5-design-judgments-2026-06+llm-judge-scoring-principles+reward-model-rl+alignment-rlhf-constitutional-ai | 79677 | evolve-informed | 0.954 | 0.975 | +0.021 |

## 해석
- 근거 정보 효과(net) = evolve-informed +0.157 - evolve +0.124 = **+0.033**
- 유의성 (문서 단위 paired bootstrap 1000회, 문서 3개): net(문서짝) +0.033, 95% CI [-0.141, +0.255], p=0.792
- 근거 정보 유무가 통계적으로 **구분되지 않는다** — 관측된 차이는 노이즈로 설명 가능. (p=0.792, 유의수준 0.05 미달)
- 진화 효과(net) = evolve +0.124 - control +0.326 = **-0.202**
- 유의성 (문서 단위 paired bootstrap 1000회, 문서 3개): net(문서짝) -0.202, 95% CI [-0.292, -0.031], p=0.000
- 유의하게 **더 나쁘다**. 방향이 반대다 — 설계를 의심할 것.
