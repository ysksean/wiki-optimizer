# 에이전트 Reflector — 1차 실험 결과 (control / evolve / evolve-informed)

날짜: 2026-09-10
설계: `2026-09-04-agent-reflector-design.md` 3단계
증거: `docs/experiments/2026-09-10-exp1-informed/` (summary.md, records.json, results.csv, trajectories.json)

## 한 줄 결론

**Reflector에 근거 정보를 더 줘도 차이가 없다(p=0.79). 그런데 더 큰 발견은, 진화 자체가
"seed 규칙으로 4번 다시 조직하고 최고를 고르는" 대조군을 못 이긴다는 것이다.** 세 arm의
best 검증 점수는 0.90 / 0.91 / 0.89로 같고, 같은 묶음·같은 seed 규칙의 gen0가 run마다
0.0~0.97로 튄다. 병목은 Reflector가 보는 정보가 아니라 **측정 노이즈**와 **매 세대 백지에서
재조직하는 구조**다. 에이전트 arm(설계 4~6단계)은 이 둘을 고친 뒤에 붙인다.

## 설정

| 항목 | 값 |
|---|---|
| 문서 | `~/dev/llm_wiki/raw` 24개 중 큰 3개(claude-code-advanced, harness-engineering-practice, claude-cowork)와 README 제외 21개 |
| 묶음 | 3개 × 7문서, 크기 내림차순 round-robin (`--bundle-size 7`, PR #64). 62k / 74k / 80k자 |
| arm | `control`(진화 없음, seed 재샘플링) / `evolve`(고정 Reflector) / `evolve-informed`(근거 문단·실패 유형·구조 결함 제공, PR #63) |
| 예산 | 세대 4, 묶음당 run 2, 질문 8 → train 5 / held-out 3 (PR #60) |
| 모델 | 채점·조직·Reflector 전부 `claude-haiku-4-5` (`CLAUDE_MODEL` 기본), temperature 0.3(조직) |
| 총 | 18 run, 294분 |

명령: `python3 src/batch.py --stage structure --arms control,evolve,evolve-informed --files <21개> --bundle-size 7 --runs 2 --generations 4 --n-qa 8`

## 결과

### 절대 점수 (best held-out) — 이걸로 판단한다

| arm | runs | 평균 best | 표준편차 | 평균 gen0 |
|---|---|---|---|---|
| control | 6 | 0.902 | 0.123 | 0.577 |
| evolve | 6 | 0.907 | 0.124 | 0.783 |
| evolve-informed | 6 | 0.892 | 0.110 | 0.734 |

- evolve vs control (문서짝 3): **+0.005**, 95% CI [−0.026, +0.023], p=0.48 → 구분 안 됨
- evolve-informed vs control: **−0.011**, CI [−0.160, +0.116], p=0.83 → 구분 안 됨
- evolve-informed vs evolve (향상폭 기준, summary.md): +0.033, p=0.79 → 구분 안 됨

### 향상폭(best − gen0) — 이 지표는 이 실험에서 무의미하다

summary.md의 원래 판정은 "진화 효과 net = −0.202, p=0.000, 유의하게 더 나쁘다"였다.
**이건 지표의 결함이다.** gen0는 세 arm이 같은 seed 규칙으로 만든 무작위 표본 하나인데,
같은 묶음 안에서 gen0 표준편차 평균이 **0.260**이다. control의 gen0가 우연히 낮게
(0.0, 0.319, 0.617…) 나와 "향상폭"이 커 보였을 뿐, best는 같다. 이 실험을 계기로 summary.md에
절대 점수 비교와 gen0 노이즈 경고를 추가했다(이 PR).

### 세대별 궤적에서 보이는 것 (trajectories.json)

1. **검증 점수가 세대와 무관하게 요동친다.** 예: evolve 묶음1 run1 held-out
   [0.965, 0.954, 0.985, 0.654], 묶음3 run2 [0.944, 0.636, 0.939, 0.0]. held-out 3문항이라
   정확도가 0/⅓/⅔/1로 양자화되고, 그 위에 조직 단계 무작위성이 얹힌다.
2. **train과 held-out이 따로 논다.** train 0.986인 세대의 held-out 0.654, train 0.181인
   세대의 held-out 0.902. 5문항/3문항으로는 Reflector가 무엇을 배워도 검증에 안 옮겨진다.
3. **규칙이 파일 수를 폭주시킨다.** evolve는 세대가 갈수록 파일 19·21·14·19개, informed도
   17·14개까지. "적게 읽기(효율)"는 파일을 잘게 쪼개면 오르는데 정확도 노이즈가 그 대가를
   제때 벌하지 못한다. Reflector는 그 방향으로 계속 밀린다.
4. **실패 유형 분포**(train 기준, 18 run 합): content_lost 45 · routing_miss 21 · doc_dropped 8.
   가장 흔한 실패는 "고른 파일이 근거 문서를 담고 있는데도 틀림" — 조직 단계가 요약하며
   내용을 버리는 것이다. 이건 분할 **규칙** 한 문단으로 고칠 수 있는 종류가 아니다. informed가
   이 유형을 줄이지 못한 이유이기도 하다.

## 판정

설계 문서의 규칙("informed net > +0.05면 informed 채택, 격차가 남고 규칙으로 못 고치는 유형이
계속 나오면 에이전트")을 그대로 적용하면 "에이전트로"가 된다. 하지만 위 관찰은 **에이전트도
같은 이유로 판정 불가에 빠질 것**을 말한다. 어떤 Reflector든 결과는 (a) 3문항 검증 점수와
(b) 매번 새로 굴리는 조직 단계를 통과해 측정되는데, 이 둘의 분산이 arm 간 차이(±0.05)보다
한 자릿수 크다.

그래서 **에이전트 인프라(4~6단계)는 보류**하고, 그 앞에 측정 단계를 넣는다.

## 다음 단계 (설계 문서 3.5단계로 추가)

1. **지표**: B단계 비교의 1차 지표를 절대 best held-out(문서짝 bootstrap)으로. 향상폭은
   참고치. — 이 PR에서 반영
2. **증분 조직(evolve-incremental arm)**: 세대 g+1의 Organizer가 백지가 아니라 **세대 g의
   best 구조를 입력으로 받아 규칙에 따라 고친다.** 에이전트 설계의 핵심("초기안 위의 수정")을
   고정 파이프라인에 먼저 이식하는 것이고, "백지 재조직이 문제"라는 가설을 가장 싸게
   검증한다. content_lost가 줄어야 한다.
3. **측정 노이즈**: 질문 12개(train 7 / held-out 5)로 양자화를 0.2 단위로. 조직 temperature를
   0.3 → 0으로 내려 arm 간 차이만 남긴다(진화 손잡이가 규칙이므로 조직은 결정적일수록 좋다).
4. **2차 실험**: `control / evolve / evolve-incremental`, 같은 묶음 3개, run 2, 질문 12,
   temperature 0. 판정은 절대 best. 여기서 incremental이 control을 유의하게 이기면 그 위에
   에이전트 arm(4~6단계)을 얹을 가치가 생기고, 못 이기면 "이 채점기 아래에서는 어떤
   Reflector도 재샘플링을 못 이긴다"는 결론이고 채점기(judge 엄격도·질문 생성)를 먼저 본다.

## 부수 관찰

- 18 run 294분, 실패·파싱 오류 0. held-out 분리(PR #60), 근거 기록(PR #63), 묶음(PR #64)
  배선은 안정적이다.
- 세션과 분리해 띄우지 않은 첫 실행은 Claude Code 세션 종료와 함께 죽었다(3 run 손실).
  장시간 배치는 `subprocess.Popen(start_new_session=True)`로 띄운다 — macOS에는 setsid가 없다.
