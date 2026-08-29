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

## Non-Goals (다음 단계)

Grokbot/Hermes 리뷰, P0=WTF 등급, Stage 1/2, 멀티 CLI(Grok/GPT/Gemini), 멀티 repo, M3~M5 분배
