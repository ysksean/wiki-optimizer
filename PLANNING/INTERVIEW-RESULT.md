# Interview Result: Linear 개인 민원창구 하네스 (MVP)

## User Intent (원문 보존)

> "Linear 는 일종의 내 전용 민원창구임. 내가 겪은 불편함이나 아이디어를 잘 작성해서 이슈를 올리면, 리니어에 상주하는 Triage agent 가 민원을 평가하고 (...) 우선순위 배분을 함. 배분에 따라서, 로컬의 PM 에이전트가 업무 할당과 Executor 에이전트를 지목하며 지시를 내림. executor 에이전트는 (...) 해당 이슈를 끝까지 추적하며 작업 한 다음 리포트를 올림. 경우에 따라선, repo에 PR 을 올리고, 코멘트 대응을 하도록 함."
> "여튼 멀리멀리 돌아오긴 했지만, 리니어로 나름의 개인용 하네스를 구축했는데 토큰 낭비는 심해지고 있다."
> "이거 구축부터 시작해야해."

## Decisions

| # | 질문 | 사용자 답변 | 근거 |
|---|------|-----------|------|
| 1 | MVP 범위 | **수직 슬라이스** — 이슈 1건이 등록→Triage→PM→Executor→리포트까지 끝까지 흐르는 최소 파이프라인. Grokbot/Hermes는 다음 단계 | 핵심 경험("민원 넣으면 처리된다")을 가장 빨리 검증 |
| 2 | Triage 구동 방식 | **로컬 폴링** (launchd 주기 실행, 서버·웹훅 없음) | 지금 가진 것만으로 구축 가능, 응답 지연은 폴링 주기만큼 허용 |
| 3 | Executor CLI | **claude 단일** (headless `claude -p`). 멀티 CLI는 다음 단계 | 인증·스킬·MCP 세팅 재사용 |
| 4 | 민원 대상 범위 | **wiki-optimizer repo 한정** | 첫 슬라이스는 좁고 안전하게 (추천안이던 "임의 repo+비코드"를 사용자가 기각) |
| 5 | 승인 지점 | **브랜치+PR까지 자동, 머지는 항상 사람** | 원래 그림과 일치, 되돌리기 쉬움 |

## Environment Facts (research)

- 개인 Linear: 워크스페이스 `sean` (linear.app/ysksean), 계정 timelesseda@gmail.com, 팀 **SEA**
- SEA 팀 상태: Backlog / Todo / In Progress / In Review / Done / Canceled / Duplicate (Triage 상태 미사용)
- 기본 라벨: Bug / Feature / Improvement → 하네스용 `triaged`, `needs-info` 추가 예정
- MCP: `linear-personal` (local 스코프, wiki-optimizer 한정) — headless에서도 사용 가능
- Personal API key: `~/.config/linear/api_key` (chmod 600) — GraphQL 게이트 전용, 검증 완료
- 로컬 CLI: claude 2.1.251, codex, gh 모두 존재. repo에 GitHub remote 있음 (PR 플로우 사용 중)

## Priorities

1. 토큰 낭비 최소화 — 처리할 이슈가 없으면 LLM 호출 없이 종료하는 GraphQL 게이트
2. 상태는 Linear 그 자체 (로컬 DB 없음) — 상태·라벨이 곧 파이프라인 상태
3. 사람 개입 지점은 머지 하나로 수렴

## Devil's Advocate & Resolution

critic 생략. 근거: 아키텍처가 "로컬 스크립트 + Linear를 상태 저장소로 사용"이라 전부 되돌리기 쉽고, 비용 우려(토큰)는 게이트 설계로 직접 대응. 확장 단계(멀티 머신, Grokbot/Hermes)에서 호출 예정.

## Non-Goals (이번엔 안 함)

- Grokbot/Hermes MOA 리뷰, P0=WTF/P1=Seriously? 등급 코멘트, Stage 1/2 리뷰
- 멀티 CLI (Grok/GPT/Gemini), 멀티 repo, M3~M5 멀티 머신 분배
- Linear 웹훅/Agents API (실시간 반응)

## Success Criteria (PROVE)

테스트 민원 1건 등록 → 다음 폴링 tick에서 자동으로:
평가 코멘트+우선순위 → 작업지시 코멘트 → In Progress → 작업 브랜치+PR → 리포트 코멘트 → In Review.
사람은 머지만 한다. → **2026-08-29 달성 (SEA-5 → PR #10, 3분)**

---

# MVP-2: Grokbot/Hermes 리뷰 단계 (2026-08-29)

## Decisions

| # | 질문 | 사용자 답변 | 근거 |
|---|------|-----------|------|
| 6 | Grokbot 실체 | **claude 판정 + hermes 의견서** — 두 주체 구조 유지, 지금 인증된 도구만 | grok CLI 없음, hermes CLI(v0.20.1) 존재 |
| 7 | IRC 소통 | **파일 기반 IRC 형식 대화록** — 전문을 PR 코멘트에 접어 첨부 | 서버 없이 재미·투명성 유지 |
| 8 | Stage 트리거 | **S1 자동(PR 감지), S2는 사람이 /stage2 코멘트** | 비용 통제 + 머지 결정권 사람 |
| 9 | MOA (critic 반박 후) | **MVP-2는 MOA 없이, MVP-3로 명시적 연기** | `hermes moa list` 실측 결과 off, reference model 1개뿐 |
| 10 | 판정 후 액션 (critic 반박 후) | **P0/P1 → needs-changes 라벨 + Linear Todo 롤백으로 Executor 재작업** (한도 2회) | '기록자'가 아닌 '감시자'로 — 단 머지 차단 같은 무거운 장치는 없이 |

## Devil's Advocate & Resolution (critic 실행함)

| # | 반박 | 처리 |
|---|------|------|
| hermes 쓰기 권한 통제 없음 (HIGH) | 수용 — `-t file` 토올셋 제한 + 일회용 detached worktree에서 실행, 종료 후 폐기 |
| headless 승인 프롬프트 vs --yolo (HIGH) | 수용 — --yolo 안 씀. read-only 구성으로 승인 프롬프트 자체가 안 뜨는 조합. VALIDATE로 실측 통과 |
| MOA 실제 비활성 (MEDIUM) | 수용 — 사용자 결정 #9로 명시적 연기 |
| 등급이 아무것도 안 막음 (MEDIUM) | 수용 — 사용자 결정 #10 |
| 실패 시 재시도 폭주 (HIGH) | 수용 — grokbot:s1-error/s2-error 라벨로 차단, 사람이 라벨 제거 시 재시도 |
| 코멘트 65536자 제한 (LOW) | 수용 — IRC 로그 40000자 절단 |
| 외부 provider로 diff 유출 (LOW) | 인지하고 수용 — 개인 공개 repo라 허용 (hermes provider 경유) |
| Checks API 대안 (MEDIUM) | 기각 — 1인 repo에 과함, Non-goal로 기록 |

## Success Criteria (PROVE)

새 PR 생성 → 다음 tick에서 Hermes 의견서 + IRC 문답 + Grokbot 등급 코멘트/라벨 자동 부착.
P0/P1 판정 시 Linear 이슈가 Todo로 돌아가고 Executor가 재작업. /stage2 코멘트 → 머지 권고서.
