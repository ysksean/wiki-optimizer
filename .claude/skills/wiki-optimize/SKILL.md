---
name: wiki-optimize
description: >
  Run and interpret wiki-optimizer experiments from chat: point at a wiki folder,
  evolve summary strategies (stage A) or folder structures (stage B), judge whether
  the improvement is real (held-out + control arm), and apply the best strategy.
  Use when the user says "위키 최적화", "optimize my wiki", "요약 전략 실험",
  "구조 실험 돌려", "실험 결과 해석해줘", "best 전략 적용해줘", or asks whether
  their wiki summaries/structure are any good.
---

# wiki-optimize — 실험 실행 · 해석 · 적용

이 레포의 실험 도구를 채팅에서 운전한다. 코드는 손대지 않고 아래 진입점만 쓴다.

## 0. 전제

- 백엔드는 로그인된 CLI: `LLM_BACKEND=claude|codex` (기본 claude). API 키 없음.
- 원본 문서는 절대 수정하지 않는다. 모든 산출물은 `runs/`에만 쌓인다.
- 사용자의 wiki 폴더 경로를 모르면 먼저 묻는다 (예: `~/dev/llm_wiki`).

## 1. 실험 실행

**대화형/소규모** — 웹 대시보드를 띄워 사용자가 보게 한다:

```bash
python3 src/web.py   # http://localhost:8765
```

**통계적 증거가 필요할 때** — 반드시 대조군과 함께 배치로 돌린다:

```bash
python3 src/batch.py --files <md파일들> --runs 2 --generations 3 --with-control
```

단일 문서 빠른 실험: `python3 src/evolve.py <md파일> --generations 3`
구조(B단계): `python3 src/evolve_structure.py --docs 3 --generations 2 --n-qa 4`

실행은 문서당 수 분 걸린다. 백그라운드로 돌리고 `runs/**/progress.json`을 폴링해
진행 상황을 사용자에게 중계한다.

## 2. 결과 해석 — 숫자를 그대로 읽지 말 것

`runs/batch-*/summary.md`와 `report.json`을 읽고 아래 순서로 판단한다:

1. **net effect가 기준**: 진화 효과 = evolve 평균 향상폭 − control 평균 향상폭.
   evolve 향상폭 단독은 노이즈 max 편향이 있어 증거가 아니다.
2. **held-out 점수만 인용한다**. train 점수는 reflect 전용이며 과적합돼 있다.
3. 판정 가이드: net > +0.05 & 개선 run 비율 우세 → 채택 권고 /
   0 ~ +0.05 → "run·세대 늘려 재확인" / ≤ 0 → "현 설정에선 효과 없음"을 정직하게.
4. 정확도는 질문 1개 단위로 양자화된다(질문 6개면 0.167 단위) — 그보다 작은
   차이를 개선이라 부르지 않는다.

사용자에게는 표 덤프가 아니라 "채택할까 말까 + 근거 두 줄"로 보고한다.

## 3. best 전략 적용

report.json의 `best.strategy`(요약 프롬프트) 또는 `best.struct`(파일 구성)를 읽는다.

- **A단계 적용**: best 전략 프롬프트로 대상 raw 문서들을 요약해 사용자가 지정한
  출력 폴더(기본 `runs/apply-<날짜>/`)에 `<docname>.md`로 생성한다. 기존 wiki
  파일을 덮어쓰려면 반드시 diff를 보여주고 확인받는다.
- **B단계 적용**: `best.struct.files`의 title/content를 그대로 파일로 export한다.
- 적용에 쓴 전략은 `runs/strategies.json`에 `{doc_type, strategy, score, date}`로
  append해 다음에 재사용한다 (같은 유형 문서는 재실험 없이 이 전략부터 시도).

## 4. 하지 말 것

- control 없이 "개선됐다"고 결론짓기
- train 점수로 전략 채택하기
- 사용자 원본 wiki를 확인 없이 덮어쓰기
- 실험 스크립트 수정 (요청받지 않는 한)
