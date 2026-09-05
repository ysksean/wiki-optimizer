# 에이전트 Reflector — B 구조 최적화의 "고치는 쪽"을 도구 기반 에이전트로

날짜: 2026-09-04
상태: 설계 검토 완료 — critic 반박 13건 중 3건 설계 반영, 9건 문서 보강, 1건 기각 (하단 '반박과 반영')
선행 논의: 2026-09-04 세션 — "스킬 기반 말고 에이전트로 만드는 건 어때"

## 한 줄 요약

**채점기는 그대로 두고, 구조를 고치는 쪽만 에이전트로 바꾼다.** 단, 바꾸기 전에 두 단계로
확인한다: (1) 고정 Reflector에 **근거 정보만 더 준** 싼 변형이 문제를 얼마나 푸는지 먼저 보고,
(2) 그래도 남는 격차가 있을 때 에이전트를 같은 예산으로 붙여 본다.

## 문제

B 구조 모드는 고정 파이프라인이다. 시도(세대)마다:

1. Organizer가 선택 문서 전체(예: 24개, 172k자)를 한 번에 읽고 파일을 **전부 다시** 쓴다
2. Router → 답변 → LLM 판정으로 채점한다 (`structure.score_structure`)
3. Reflector가 **분할 규칙 한 문단**을 고쳐 쓴다 (`evolve_structure.reflect`)

Reflector가 보는 것은 "틀린 질문 + 고른 파일 이름 + 점수 요약" 몇 줄이 전부다. 정답이
무엇이었는지, 왜 틀렸는지(라우팅 실수 / 파일에 내용 누락 / 요약 중 손실), 그 근거가
원본 어디에 있는지 볼 수단이 없다. 그 결과:

- **눈 감고 고친다.** 2026-09-04 소형 실행(`49247301`)에서 2차 시도는 transformer 문서를
  어느 파일에도 넣지 않았다. 규칙 문장만 보고는 알 수 없는 종류의 실수다.
- **국소 수정이 불가능하다.** 파일 하나에 문단 하나가 빠졌어도 다음 시도는 24개 문서를
  백지에서 재조직한다. 시도당 2분 이상, 결과는 규칙 문장의 표현에 따라 크게 흔들린다.
- **"왜"가 남지 않는다.** 화면에 보여줄 수 있는 근거가 규칙 문장의 diff뿐이다. 오늘
  결과 카드에 매핑 다이어그램과 점수 근거를 붙였지만(#57, #58), 변경 의도는 여전히 없다.

## 결정 원칙 (바꾸지 않는 것)

| 원칙 | 이유 |
|---|---|
| **채점기는 에이전트 밖에 둔다.** 질문 세트 고정, Router→답변→LLM 판정, 효율 산식, 대조군(control arm), provenance 모두 지금 그대로 | 이 프로젝트의 신뢰 근거. 에이전트가 채점까지 하면 "스스로 채점하고 스스로 만족하는" 구조가 된다 |
| **예산 단위는 평가 호출 횟수(K).** 세대 수 대신 `evaluate` 호출 상한 | 고정 Reflector(세대 = 평가 1회)와 같은 예산에서 비교 가능 |
| **최종 점수는 held-out 질문.** 에이전트는 train 질문의 판정만 본다 | 에이전트는 질문·기대 답을 보고 고치므로, 그 질문에는 과적합이 당연하다. 숨긴 질문으로만 채택 판단 |
| **결과 형식(`report.json`)은 호환.** `history[]`·`best.struct`·`details` 그대로 | 대시보드(#57·#58)와 배치 요약(`batch.py`)이 그대로 동작해야 실험 비교가 된다 |

## 범위

- **대상**: B 구조 모드(`evolve_structure`)만. A 요약 모드·audit·apply·Stage 0은 건드리지 않는다.
- **산출물**: 새 arm `agent`. 기존 `evolve`(고정 Reflector)·`control`은 그대로 남는다 —
  대체가 아니라 **추가 후 비교**다.
- **비범위**: A 요약 모드의 에이전트화(B 결과를 보고 판단), Reflector 이외의 Organizer
  초기안 생성 방식 변경, 멀티 에이전트, 실시간 UI 스트리밍.

## 아키텍처

```
                 ┌──────────────────────────────── run 디렉터리 ─────────────────────────────┐
                 │  raw/          원본 문서 복사 (읽기 전용, chmod a-w)                        │
                 │  workspace/    제안 구조 = 실제 .md 파일들 (frontmatter: title, sources)   │
                 │  attempts/N/   evaluate 호출 N번째 시점의 workspace 스냅샷 + 채점 결과      │
                 │  transcript.jsonl  에이전트 도구 호출 로그                                   │
                 └───────────────────────────────────────────────────────────────────────────┘
        ▲ Read/Grep (raw, workspace)        ▲ Edit/Write (workspace만)         │ evaluate(note)
        │                                   │                                  ▼
┌───────┴───────────────────────────────────┴─────────┐      ┌──────────────────────────────────┐
│  Reflector 에이전트  (claude -p, 헤드리스)            │ ───▶ │  환경 = 기존 채점기 (Python)       │
│  "train 질문 판정을 보고 workspace를 고쳐라.          │ ◀─── │  workspace → struct 로드          │
│   evaluate는 K번까지."                               │ 결과 │  route → answer → judge (train)   │
└─────────────────────────────────────────────────────┘      │  스냅샷 저장, held-out은 마지막에  │
                                                             └──────────────────────────────────┘
```

### 핵심 표현 결정: 구조 = 디스크 위의 파일

지금 구조는 메모리 안의 JSON(`{"files":[{title, content, sources}]}`)이다. 에이전트 버전은
이것을 **작업 폴더의 실제 마크다운 파일**로 표현한다.

```
workspace/
  01-claude-code-핵심-기능.md
  ---
  title: Claude Code 핵심 기능: Skills와 MCP
  purpose: (선택) 이 파일이 답해야 할 질문
  sources:
    - claude-code-basics
    - claude-code-advanced
  generated_by: wiki-optimizer structure <run_id>
  ---
  (본문)
```

frontmatter는 새 스키마가 아니라 **Stage 0의 `skeleton.py`가 쓰는 형식**(title/purpose/sources/
status/generated_by)을 그대로 쓴다. 두 흐름의 산출물이 같은 포맷이어야 나중에 "폴더로 내보내기"
소비자가 하나로 유지된다. `sources`는 YAML 리스트 한 형태만 허용하고, 파서는 이 다섯 키만
읽는 결정론적 최소 파서로 둔다(외부 YAML 의존성 없음).

이렇게 하면:

- 에이전트가 **Claude Code 내장 도구(Read/Edit/Write/Grep)** 로 구조를 고친다. 커스텀
  도구를 `edit_file / split / merge`처럼 따로 만들 필요가 없다 — 파일 조작은 Claude Code가
  가장 잘하는 일이다.
- `evaluate`는 workspace를 읽어 기존 `struct` 형식으로 변환한 뒤 **기존 `score_structure`를
  그대로** 호출한다. 채점기 코드 변경 없음.
- 스냅샷이 곧 제안 폴더다. "B 구조 결과를 폴더로 내보내기"(현재 없음)가 공짜로 생긴다.
- 사람이 `attempts/N/`을 열어 diff로 무엇이 바뀌었는지 볼 수 있다.

Organizer의 첫 안(seed 규칙으로 만든 구조)을 `workspace/`에 써 넣은 상태에서 에이전트를
시작한다. 백지 시작이 아니라 **초기안 위의 수정**이다. 초기안 생성은 기존 `organize()`
그대로 — 이 부분은 `evolve` arm과 동일해서 비교가 공정하다.

### 도구

| 도구 | 종류 | 권한 | 역할 |
|---|---|---|---|
| `Read`, `Grep`, `Glob` | 내장 | `raw/`, `workspace/` | 원본과 현재 구조 읽기. 근거 문단 찾기 |
| `Edit`, `Write` | 내장 | **`workspace/` 한정** (`Edit(<run>/workspace/**)` 패턴) | 구조 수정. 파일 추가·삭제·분할·병합 |
| `evaluate(note)` | MCP 커스텀 | 항상 허용, **K회 상한** | workspace 채점(train). `note` = "무엇을 왜 바꿨는지" 한두 문장 — 필수 인자 |
| `inspect_failure(q)` | MCP 커스텀 | 항상 허용 | 틀린 train 질문 하나에 대해: 구조가 낸 답, 읽은 파일, **원본에서 정답 근거가 있는 문서·문단**(서버가 기대 답을 키로 검색하되 기대 답 자체는 돌려주지 않는다) |
| `Bash` 등 나머지 | — | **차단** | 필요 없음. 공격면 축소 |

`evaluate` 반환값 (train 질문 기준):

```json
{"attempt": 2, "remaining": 2,
 "total": 0.71, "accuracy": 0.75, "efficiency": 0.94, "avg_read": 2989,
 "details": [{"q": "...", "pred": "...", "picked": ["..."], "read_chars": 1422, "score": 0}],
 "warnings": ["문서 transformer-architecture 가 어느 파일의 sources에도 없음"]}
```

**기대 답(`expected`)은 어느 도구도 돌려주지 않는다.** 에이전트가 보는 것은 질문·자기 답·판정·
읽은 파일, 그리고 `inspect_failure`가 찾아 주는 원본 근거 문단이다. 기대 답을 그대로 노출하면
파일에 붙여 넣는 것만으로 train 점수를 올릴 수 있다 — critic 반박 #1.

frontmatter가 깨졌거나 파일이 비었으면 `evaluate`는 채점하지 않고 `is_error`로 **어느 파일의
무엇이 잘못됐는지**를 돌려준다(K를 소모하지 않음). 조용히 warnings에 묻지 않는다 — 에이전트가
고치고 다시 부르는 편이 깨진 구조를 채점하는 것보다 싸다.

`warnings`는 채점기가 아니라 환경이 붙이는 결정론적 검사다: 출처 없는 원본, sources에
없는 문서 이름, frontmatter 없는 파일, 빈 파일. 에이전트가 자주 하는 실수를 LLM 판정 없이
즉시 되돌려 준다.

### 루프와 예산

- `max_evaluations = K` (기본 4). K번째 `evaluate` 이후 도구는 `remaining: 0`을 돌려주고
  다음 호출은 거부한다. 에이전트 프롬프트에 "evaluate가 0이 되면 종료 보고를 쓰라"고 명시.
- `--max-turns` (기본 80)와 벽시계 상한(기본 20분)을 `claude -p`에 걸어 무한 루프를 막는다.
- **결과는 마지막 상태가 아니라 best 스냅샷.** 매 `evaluate`가 `attempts/N/`에 workspace를
  복사하므로, 종료 시 train 점수 최고인 스냅샷을 골라 held-out으로 채점해 `best`로 기록한다.
  에이전트가 마지막에 망쳐 놓아도 결과가 나빠지지 않는다.
- 에이전트가 evaluate를 한 번도 부르지 않고 끝나면 초기안(attempt 0)이 결과다. 실패가 아니라
  "개선 없음"으로 기록한다.
- **바깥 `claude -p` 프로세스가 죽었을 때**(rc≠0, 세션 만료, 벽시계 초과): 그때까지 쌓인
  `attempts/`로 best를 계산해 `report.json`을 쓰되 `agent.status = "aborted"`와 stderr 꼬리를
  기록한다. attempts가 없으면 초기안이 결과. 즉 어떤 경우에도 report는 나온다 — 실행 기록 화면이
  빈 카드를 보여주는 일이 없어야 한다.
- **비용 상한은 사전 차단이 아니라 사후 측정이다.** `claude -p`에는 SDK의 `max_budget_usd` 같은
  장치가 없다. 통제 수단은 K·`--max-turns`·벽시계 셋이고, 토큰은 종료 후 `total_cost_usd`로
  기록만 한다. 단계 5의 첫 실행에서 시도당 비용을 재어 `--max-turns` 기본값을 정한다. 에이전트가
  큰 원본을 반복해 읽는 것은 프롬프트("전체 읽기는 마지막")와 turn 상한으로만 억제된다 — 이
  한계는 결과 문서에 함께 적는다.
- `raw/`는 chmod a-w에 더해 실행 전후 해시를 비교한다. 다르면 그 run은 무효(`agent.status =
  "raw_modified"`). 이것은 사후 탐지다 — 사전 방지는 `--restricted`/`--add-dir`에 기대며, 그
  플래그들의 실효성은 단계 5 첫 실행에서 별도로 확인한다(아래 '운영 검증 항목').

### held-out — B 모드에 신설 (선행 작업)

A 요약 모드에는 `split_questions(holdout_ratio=0.4)`가 있지만 B 구조 모드에는 없다. 이번에
**두 arm 모두에** 넣는다 (`evolve` arm도 train으로만 reflect, 최종은 held-out). 그래야
"에이전트가 held-out에서 이겼다"가 성립한다. 기존 결과와 점수 스케일이 달라지므로 리포트에
`question_split: {"train": n, "heldout": m}`을 기록하고 UI 점수 근거에 표시한다.

질문 수가 적을 때(n_qa ≤ 4)는 held-out 2개 보장 규칙 때문에 train이 2개뿐이다. 실험은
`n_qa ≥ 8`로 돈다.

### 정답 복사 방지

- 기대 답은 어느 도구도 노출하지 않는다(위). 에이전트가 얻을 수 있는 최선은 "정답 근거가 이
  문서 이 문단에 있다"이고, 그 문단을 옮기는 것이 곧 바른 수정이다
- 채택 판단은 held-out으로만 → train 질문에 맞춘 수정은 채택 점수에 영향이 없다
- 효율 항이 불필요한 내용 추가를 벌한다

완전한 방지는 아니다(근거 문단을 통째로 복사해 파일을 불리는 길이 남는다 — 효율이 벌하지만
막지는 않음). 실험 결과에서 train↔held-out 격차가 `evolve` arm보다 눈에 띄게 크면 그 자체를
gaming 신호로 기록한다.

## 실행 방식 — `claude -p` + stdio MCP 서버 (결정)

| | A. `claude -p` + MCP 서버 | B. Claude Agent SDK (Python) |
|---|---|---|
| 인증 | 구독 로그인 그대로. 레포의 전제(`llm.py`: "개인 구독 CLI, API 키 없음")와 일치 | **API 키 필수.** 공식 문서: 서드파티 개발자는 SDK 기반 에이전트에 claude.ai 로그인을 쓸 수 없음 → 종량 과금 |
| 도구 | `--mcp-config` + `--strict-mcp-config`로 이 서버만 연결, `--restricted`로 Bash·WebFetch 제거와 파일 도구 작업 디렉터리 한정, `--add-dir`로 `raw/`·`workspace/`만 노출, `--allowedTools`로 권한 | 인프로세스 MCP(`@tool`, `create_sdk_mcp_server`) |
| 제어 | `--max-turns`(help에는 없지만 2.1.251에서 동작 확인) + 벽시계. 종료 후 `--output-format json`의 `total_cost_usd`·`usage`로 비용 회수. 중간 개입 불가 | 훅, `max_budget_usd`, 메시지 스트림, 세션 재개 |
| 레포 적합성 | `harness/poll.sh`가 이미 이 방식. 의존성 추가 없음(MCP 서버는 stdlib JSON-RPC 또는 `mcp` 패키지) | 새 의존성 + 새 과금 경로 |

**A로 간다.** 핵심 코드(MCP 도구 서버, 환경, 스냅샷)는 두 방식이 공유하므로, 나중에 훅이나
예산 제어가 필요해지면 B로 갈아타는 비용은 실행 스크립트 하나다. 2026-09-04 세션에서
"Agent SDK는 로그인만으로 된다"고 한 발언은 문서 확인 결과 틀렸다 — 여기서 정정한다.

MCP 서버는 `src/agent_tools.py`(stdio, JSON-RPC)로 두고, `evaluate`·`inspect_failure`는
LLM 호출 없이 단위 테스트한다(채점기는 기존 스텁 패턴 `tests/test_structure_batch.py` 재사용).

### 운영 검증 항목 (단계 5 첫 실행에서 로그로 확인)

이 레포에 전례가 없는 것 세 가지를 소형 실행 한 번에 같이 확인한다.

1. **`claude -p` 중첩.** 에이전트 자체가 `claude -p`인데, `evaluate` 안의 채점기는 질문마다
   또 `claude -p`를 서브프로세스로 띄운다(`llm.py`, `ThreadPoolExecutor(4)`). 한 세션 인증으로
   동시에 최대 5개 프로세스가 도는 셈이다. 레이트리밋·세션 잠금이 걸리는지, 걸리면 채점기의
   병렬도를 1로 낮춰야 하는지 확인한다.
2. **격리 플래그 실효성.** `--restricted`·`--add-dir`·`--strict-mcp-config`는 이 레포에서 처음
   쓴다. 에이전트에게 일부러 `raw/`와 run 밖 경로 쓰기를 시켜 보고 거부되는지 본다.
3. **벽시계 강제.** `harness/review.sh`처럼 `timeout`으로 감싼다(`poll.sh`에는 없다 — 문서
   초안에서 poll.sh를 근거로 든 것은 정정). 초과 시 위 실패 모드 규약대로 report가 나오는지 확인.

`provenance.collect()`에 에이전트 arm의 `claude --version`을 추가한다. `--max-turns`처럼
버전에 따라 동작이 다른 플래그에 의존하므로 재현성 메타데이터에 있어야 한다.

에이전트 모델은 arm 이름에 붙여 변수로 둔다(`agent:sonnet`, `agent:opus`). 채점기 쪽
모델(`CLAUDE_MODEL`, 기본 haiku)은 모든 arm에서 동일 — 비교 대상은 "고치는 지능"만이다.

## 산출물 형식 (호환)

`report.json`은 기존 키를 유지하고 다음을 더한다.

```json
{
  "arm": "agent",
  "agent": {"model": "claude-sonnet-5", "max_evaluations": 4, "turns": 37, "cost_usd": 0.84, "wall_sec": 611},
  "question_split": {"train": 5, "heldout": 3},
  "history": [
    {"generation": 0, "strategy": "(초기안) 문서들을 주제별로 3~4개 파일로…",
     "note": "초기안", "files": [...], "score": {...train...}, "details": [...]},
    {"generation": 1, "strategy": null,
     "note": "transformer 문서가 어느 파일에도 없어 06에 '모델 구조' 절로 추가. 질문 3 라우팅 실패는 index 설명이 Redis만 언급해서 — 제목에 Transformer 병기",
     "files": [...], "score": {...}, "details": [...]}
  ],
  "best": {"generation": 1, "total": 0.88, "heldout": {"total": 0.83, ...}, "struct": {...}, "snapshot": "attempts/1"},
  "transcript": "transcript.jsonl"
}
```

- `history[].strategy`는 `evolve` arm에서만 의미가 있다. `agent` arm은 `note`가 그 자리를
  대신한다. UI의 "분할 규칙" 박스는 `note`가 있으면 그것을 **"이번 시도에서 바꾼 것"** 으로
  보여준다. **주의**: 현재 `app.js`의 `ruleBlock`은 `cur.strategy.split(...)`을 무조건 호출해
  `strategy: null`이면 카드 렌더가 죽는다(critic #3). 이 가드는 "긍정일 때 별도 PR"이 아니라
  **단계 5에 포함**한다 — 실험 결과를 화면으로 봐야 하는 단계 6보다 먼저 있어야 한다.
- `best.total`은 held-out 점수다. train 점수는 `history[].score`에 남긴다.

## 실험 설계 — 바꿀지 말지는 이걸로 정한다

### 먼저: 싼 변형으로 스크리닝 (critic #8 반영)

문제 정의가 "Reflector가 보는 정보가 부족하다"인데, 그 처방 중 가장 싼 것은 **정보를 더 주는
것**이다. `inspect_failure`가 만들 근거 탐색 로직은 에이전트 arm에도 필요하니, 그것을 먼저 만들어
고정 Reflector 프롬프트에 붙인 변형 **`evolve-informed`** arm을 만든다. `reflect()` 프롬프트에
틀린 질문마다 "원본 근거 문단"과 "출처 없는 문서 목록"(결정론 warnings)이 추가되는 것이 전부다.

1차 실험은 `control / evolve / evolve-informed` 3팔이다. 여기서 `evolve-informed`가 `evolve`
대비 순효과 +0.05를 넘기고 실패 유형이 대부분 "정보 부족"으로 설명되면, 에이전트 인프라(단계 4
이후)는 **보류**하고 informed를 기본으로 채택한다. 격차가 남거나(문서 누락·라우팅 실패처럼 규칙
한 문단으로 못 고치는 유형이 계속 나오면) 2차 실험으로 간다.

### 2차: 에이전트 arm 추가

| 항목 | 값 |
|---|---|
| arm | `control` / `evolve` / `evolve-informed` / `agent:<model>` |
| 문서 묶음 | **서로 겹치지 않는 묶음 3개 이상**(각 5~8개 문서, 24개 원본을 나눔). 묶음이 표본 단위다 |
| 질문 | 묶음별 `n_qa=8`, train 5 / held-out 3, 1회 생성 후 **모든 arm·run이 공유** |
| 예산 | K=4 (evolve의 generations=4와 같은 평가 횟수). 에이전트가 평가 사이에 쓰는 턴·토큰은 더 크다 — 이 비대칭은 감추지 않고 결과에 벽시계·비용을 나란히 적는다 |
| 반복 | 묶음당 run 2회 |
| 지표 | held-out `total`. 부가: train↔held-out 격차(gaming 신호), 벽시계, `total_cost_usd`, 실패 유형 분포 |

### 판정 — 이 레포의 기존 장치를 그대로 쓴다

`batch.py::paired_bootstrap_net`은 **문서(묶음) 단위** paired bootstrap이고, 표본이 2개 미만이면
"판정 불가"를 낸다. 같은 묶음의 run들은 질문 세트를 공유해 독립이 아니라서 run을 표본으로 세면
p값이 부풀려진다(그 파일의 주석이 이미 그렇게 못 박고 있다). 따라서:

- 묶음 3개는 이 장치가 도는 **최소치**다. 1차 결과는 "결론"이 아니라 **신호**로 취급하고, 채택
  결정은 묶음을 5개 이상으로 늘린 재확인 후에 한다. 성공 기준에도 그렇게 적는다.
- 판정 규칙은 `wiki-optimize` 스킬과 동일: 순효과(arm 이득 − control 이득) **> +0.05이고 묶음
  과반 개선**, 부트스트랩 CI가 0을 넘지 않을 때 채택. 0~+0.05면 묶음 추가. ≤0이면 효과 없음.
- held-out 3개는 정확도가 0.333 단위로 양자화된다. 한 칸 미만 차이는 개선으로 세지 않는다.
  결론 전 재확인은 `n_qa=12`(held-out 5)로 돈다.

### 비용 어림

`evaluate` 1회 = 질문 5(train) × (route + answer) + judge ≈ 11회 haiku 호출 ≈ 40초. 고정
Reflector는 시도당 여기에 organize 1회 + reflect 1회. 에이전트는 시도당 턴 20~40회를 더 쓰고
모델이 Sonnet/Opus 급이면 토큰은 3~5배. 묶음 3 × arm 4 × run 2 = 24 run, 소형이면 2~3시간,
구독 한도 안. 중형(묶음당 12개 문서)은 소형 신호가 긍정일 때만.

## 대시보드 영향

- 이미 `history[].files`·`details`·`best.struct`를 쓰므로 `agent` arm 결과가 **그대로 그려진다.**
- 추가 세 가지: (1) "분할 규칙" → `note`가 있으면 "이번 시도에서 바꾼 것" (2) arm 뱃지에
  `agent` 추가(`arm_agent` i18n) (3) 점수 근거에 held-out/train 구분 표시.
- 트랜스크립트 뷰어(도구 호출 타임라인)는 채택 시 별도 PR(단계 7).

## 단계 (PR 분할)

1. **held-out을 B 모드에** — `evolve_structure`에 `split_questions` 적용, 리포트에
   `question_split`, UI 점수 근거에 train/held-out 표기. LLM 없이 테스트. (선행, 독립 머지)
2. **근거 탐색 + informed Reflector** — `inspect_failure`의 핵심(질문·기대 답을 키로 원본 문단
   찾기, 결정론 warnings)을 `src/evidence.py`로 만들고, `reflect()`에 주입한 `evolve-informed`
   arm. `batch.py::resolve_arms`가 structure stage에서도 `--arms`를 받게 확장(현재는
   `ValueError`, critic #4). 스텁 테스트.
3. **1차 실험** — `control/evolve/evolve-informed`, 묶음 3 × run 2. 결과를
   `docs/superpowers/specs/…-agent-reflector-result.md`에 기록. **여기서 멈출 수 있다.**
4. **환경 + 도구 서버** — workspace ↔ struct 변환(skeleton 포맷 파서), `evaluate`(기대 답 비노출,
   frontmatter 오류는 is_error), 스냅샷, stdio MCP 서버. 스텁 테스트.
5. **에이전트 러너** — `src/agent_structure.py`: 초기안 → `timeout` + `claude -p --restricted
   --strict-mcp-config --add-dir … --max-turns` → best 스냅샷 held-out 채점 → report(실패 모드
   규약 포함). `app.js` `ruleBlock` null 가드 + `arm_agent` 뱃지 + note 라벨. provenance에 CLI
   버전. 소형 1회 실행으로 '운영 검증 항목' 3개 확인.
6. **2차 실험** — 4팔, 묶음 3 × run 2 → 신호 확인 → 묶음 5 이상 재확인 → 채택/기각 기록.
7. (채택 시) 트랜스크립트 뷰어, `wiki-optimize` 스킬을 에이전트 진입점으로 축소, B 결과 폴더
   내보내기 버튼.

1·2·4는 실험 결과와 무관하게 남는 가치가 있다(held-out 공정성, 근거 탐색, 구조의 폴더 표현).

## 리스크와 미결

| 리스크 | 대응 |
|---|---|
| 에이전트가 24개 문서를 전부 Read해 컨텍스트를 태움 | 프롬프트에 "inspect_failure의 근거 문단부터, 전체 읽기는 마지막"; 중형 세트에서 턴·토큰 측정 후 `--max-turns` 조정 |
| `evaluate` 남용(한 번 고치고 바로 채점) | K 상한이 곧 예산. 남은 횟수를 매 응답에 돌려줌 |
| workspace 밖 쓰기 | `--restricted`(파일 도구를 작업 디렉터리에 한정) + `--add-dir`를 `raw/`·`workspace/`로 제한 + `raw/` chmod a-w + 종료 후 raw 해시 검증(다르면 run 무효) |
| held-out 3개는 양자화가 거칠다(0.333 단위) | 실험 판정은 run 3회 평균 + "한 칸(0.333) 미만 차이는 개선 아님" 규칙 유지. 결론 전엔 n_qa 12로 재확인 |
| 판정 LLM의 엄격도(같은 뜻인데 ❌) — 2026-09-04 관찰 | 양 arm에 같은 편향이라 비교엔 영향 적음. 별도 이슈로 추적(Linear) |
| `claude -p`가 MCP 도구 결과를 잘라 보여줄 가능성 | `details`를 8개로 제한, 본문은 `inspect_failure`로 지연 로드 |
| 비용·시간 | 소형 세트로 먼저. 중형은 조건부. 사전 비용 차단은 없음(위 '루프와 예산') |
| 스냅샷 용량 | evaluate마다 workspace 전체 복사. 문서 24개면 시도당 ~10k자 × K — 무시 가능하나 상한 K≤8로 고정 |
| 에이전트 비결정성 | 같은 입력에도 결과가 다르다. run 2회 이상과 묶음 단위 bootstrap으로만 다룬다. 시도별 `note`·transcript로 재현은 못 해도 설명은 남긴다 |

미결(구현 중 결정): `inspect_failure`의 근거 탐색을 키워드 매칭으로 충분한지(아니면 임베딩
없이 LLM 1회로 "근거 문단 찾기"를 시키는지), 에이전트 모델 기본값(sonnet vs opus), 스냅샷 보관
개수 상한.

## 성공 기준

- 단계 2 완료 시: `python3 src/batch.py --stage structure --arms control,evolve,evolve-informed --docs 5 --runs 1`
  이 끝까지 돌고 대시보드에 세 arm 카드가 그려진다.
- 단계 3: 1차 실험 결과가 신호 수준(묶음 3)으로 기록되고, "에이전트로 갈지 / informed로 멈출지"가
  근거와 함께 문서에 남는다.
- 단계 5 완료 시: 위 명령에 `agent:sonnet`을 더해 끝까지 돌고, 운영 검증 항목 3개가 로그로
  확인된다. 에이전트 프로세스를 중간에 죽여도 report.json이 나온다.
- 단계 6: 판정 규칙으로 채택/기각이 문서에 기록된다. **어느 쪽이든 결론이 나는 것이 성공**이고,
  에이전트가 이기는 것이 성공 조건은 아니다.

## 반박과 반영 (critic, 2026-09-04)

| # | 반박 | 처리 |
|---|---|---|
| 1 | `evaluate`가 기대 답을 매번 노출 — gaming 방지가 엉뚱한 곳을 막음 | **설계 변경**: 어느 도구도 기대 답을 돌려주지 않음 |
| 2 | held-out 3개·묶음 1개는 이 레포의 `paired_bootstrap_net`(문서 단위, n<2 판정 불가)과 충돌 | **설계 변경**: 묶음 ≥3, 1차는 신호, 채택은 묶음 5+ 재확인 후 |
| 8 | 고정 Reflector 입력 보강이 훨씬 싼데 실패 시 선회안으로 밀려 있음 | **설계 변경**: `evolve-informed`를 선행 스크리닝으로. 에이전트 인프라는 조건부 |
| 3 | `app.js ruleBlock`이 `strategy: null`에서 죽음 | 문서 보강: 단계 5에 가드 포함 |
| 4 | `resolve_arms`가 structure stage에서 `--arms` 거부 | 문서 보강: 단계 2 작업 항목 |
| 5·6 | `claude -p` 중첩, 격리 플래그 미검증 | 문서 보강: 운영 검증 항목 |
| 7 | 벽시계 근거를 poll.sh로 든 것은 오류(review.sh가 맞음) | 문서 보강: 정정 |
| 9·10 | 프로세스 실패 모드·비용 상한 부재 | 문서 보강: 루프와 예산 |
| 11 | provenance에 CLI 버전 없음 | 문서 보강: 운영 검증 항목 |
| 12 | frontmatter를 `skeleton.py` 포맷과 따로 정의 | 문서 보강: skeleton 포맷 재사용 |
| 13 | 깨진 frontmatter에 폴백 없음 | 문서 보강: evaluate가 is_error로 즉시 반환 |
| — | "예산 비교가 불공정" | **기각**: 문서가 비대칭을 이미 명시하고 결과에 비용·시간을 나란히 적는다 |
