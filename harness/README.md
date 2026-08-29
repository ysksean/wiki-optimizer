# Linear 개인 민원창구 하네스 (MVP)

Linear 팀 SEA에 이슈(민원)를 올리면, 로컬 폴링이 Triage → PM → Executor를 자동으로 돌려 PR과 리포트 코멘트까지 만든다. **머지는 항상 사람이 한다.**

```
이슈 등록 → poll.sh (launchd 10분 주기)
              ├─ 게이트: GraphQL로 대상 유무 확인 → 없으면 LLM 호출 없이 종료
              └─ claude -p 1회:
                   🔎 Triage  평가 + priority + 라벨(triaged/needs-info)
                   📋 PM      최우선 1건 작업지시 → In Progress
                   🔧 Executor worktree 작업 → PR → 리포트 → In Review
```

## 상태 규약 (Linear가 곧 상태 저장소)

| 이슈 상태 | 의미 |
|---|---|
| Todo/Backlog, 라벨 없음 | 미접수 → 다음 tick에 Triage |
| `needs-info` | 정보 부족. **답변 코멘트 후 라벨을 직접 제거**하면 재접수 |
| `triaged` + priority | 처리 대기열. PM이 우선순위순 1건/tick 집행 |
| `triaged` + priority 없음 | 보류 (파이프라인이 영구히 무시) |
| In Progress | Executor 작업 중 |
| In Review | PR 생성 완료, 사람 머지 대기 |

## 설치

```bash
# 1) Linear Personal API key (게이트용)
mkdir -p ~/.config/linear && printf '%s' 'lin_api_...' > ~/.config/linear/api_key && chmod 600 ~/.config/linear/api_key

# 2) launchd 등록
mkdir -p ~/.local/state/linear-harness
cp harness/launchd/com.ysksean.linear-harness.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ysksean.linear-harness.plist
```

해제: `launchctl unload ~/Library/LaunchAgents/com.ysksean.linear-harness.plist`

수동 1회 실행(디버깅): `bash harness/poll.sh` → 로그는 `~/.local/state/linear-harness/`

## 전제

- `linear-personal` MCP가 이 repo에 local 스코프로 등록·인증돼 있어야 함 (개인 워크스페이스)
- `jq`, `gh`(로그인), `claude` CLI 필요
- Executor는 자기 worktree에 responsible-vibe skip 마커를 만들고 커밋한다 (자율 실행 승인 사항)

## Grokbot/Hermes 리뷰 단계 (MVP-2)

PR이 생기면 같은 tick에서 `review.sh`가 이어 돈다 (자체 게이트 — 후보 PR 없으면 LLM 호출 없음).

```
open PR (grokbot:s1* 라벨 없음)
  → Hermes(hermes CLI, 일회용 worktree — chmod a-w로 쓰기 잠금)가 의견서
  → Grokbot(claude, 읽기 도구만)이 질문 최대 3개 → Hermes 답변   ← IRC 형식 대화록
  → Grokbot 판정: P0=WTF / P1=Seriously?... / P2=Hmm / P3=Fine / P4=Ship it
  → PR 코멘트(판정 + IRC 로그 접기) + 라벨 grokbot:s1-done, grokbot:pN
  → P0/P1이면 needs-changes 라벨 + Linear 이슈를 Todo로 롤백 → Executor가 재작업
     (재작업은 최대 2회 — 초과하면 사람 개입 대기)
사람이 PR에 "/stage2" 코멘트  →  Stage 2: Consider to merge 권고서 (머지는 여전히 사람)
```

- 에이전트는 텍스트만 생산하고, GitHub/Linear 변경(코멘트·라벨·상태)은 전부 review.sh가 수행
- 리뷰 실패 시 `grokbot:s1-error`/`s2-error` 라벨 — 사람이 라벨을 떼기 전까지 재시도하지 않음 (폭주 방지)
- 재리뷰 트리거: 수정 push 후 `grokbot:s1-done`(및 `needs-changes`) 라벨 제거
- `/stage2`는 `needs-changes`·`grokbot:p0`/`p1` 라벨이 있으면 무시됨 (s1 재통과가 선행 조건)
- **알려진 잔여 위험**: hermes `-t file` 토올셋은 write_file/patch를 포함한다. 건네주는 worktree·자료는
  chmod로 쓰기를 잠그지만, 절대경로로 다른 위치에 쓰는 것까지는 못 막는다 (PR diff에 섞인 프롬프트
  인젝션이 이론상 경로). 프로세스 샌드박스(sandbox-exec)는 MVP-3 과제.

## Non-Goals (다음 단계)

Hermes MOA 활성화(현재 reference model 1개뿐이라 명시적 연기), 진짜 IRC 서버, GitHub required check 연동, 멀티 CLI(Grok/GPT/Gemini), 멀티 repo, M3~M5 분배
