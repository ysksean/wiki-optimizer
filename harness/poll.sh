#!/usr/bin/env bash
# Linear 개인 민원창구 하네스 — 폴링 게이트 + 디스패치
# launchd가 주기 실행. 처리할 이슈가 없으면 LLM 호출 없이 종료한다 (토큰 게이트).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEY_FILE="${LINEAR_API_KEY_FILE:-$HOME/.config/linear/api_key}"
LOG_DIR="${LINEAR_HARNESS_LOG_DIR:-$HOME/.local/state/linear-harness}"
LOCK_DIR="${TMPDIR:-/tmp}/linear-harness.lock"
TEAM_KEY="SEA"
MAX_TURNS=150

mkdir -p "$LOG_DIR"
log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_DIR/poll.log"; }

command -v jq >/dev/null || { log "jq not found — abort"; exit 1; }
[ -f "$KEY_FILE" ] || { log "api key missing: $KEY_FILE"; exit 1; }

# 중복 실행 방지 (executor가 오래 돌 수 있음)
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "previous run still active — skip tick"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

# ── 게이트: GraphQL로 처리 대상 유무만 확인 ──────────────────────────
QUERY='{"query":"{ issues(filter: { team: { key: { eq: \"'"$TEAM_KEY"'\" } }, state: { type: { in: [\"unstarted\", \"backlog\"] } } }, first: 50) { nodes { identifier title priority labels { nodes { name } } } } }"}'

RESP="$(curl -sf --max-time 30 -X POST https://api.linear.app/graphql \
  -H "Content-Type: application/json" \
  -H "Authorization: $(cat "$KEY_FILE")" \
  -d "$QUERY")" || { log "linear api unreachable"; exit 1; }

TRIAGE_COUNT="$(jq '[.data.issues.nodes[] | select((.labels.nodes | map(.name) | any(. == "triaged" or . == "needs-info")) | not)] | length' <<<"$RESP")"
EXEC_COUNT="$(jq '[.data.issues.nodes[] | select((.labels.nodes | map(.name) | index("triaged")) != null and .priority > 0)] | length' <<<"$RESP")"

if [ "$TRIAGE_COUNT" -eq 0 ] && [ "$EXEC_COUNT" -eq 0 ]; then
  log "quiet tick — triage:0 exec:0 (no LLM call)"
  exit 0
fi

log "dispatch — triage:$TRIAGE_COUNT exec:$EXEC_COUNT"

# ── 디스패치: claude headless 1회 호출로 Triage → PM → Executor ─────
RUN_LOG="$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"
cd "$REPO"
claude -p "$(cat "$REPO/harness/prompts/pipeline.md")" \
  --allowedTools "mcp__linear-personal,Read,Glob,Grep,Edit,Write,TodoWrite,Bash(git:*),Bash(gh:*),Bash(mkdir:*),Bash(ls:*),Bash(cat:*),Bash(printf:*),Bash(uv:*),Bash(pnpm:*),Bash(python:*),Bash(python3:*),Bash(pytest:*)" \
  --permission-mode acceptEdits \
  --max-turns "$MAX_TURNS" \
  >> "$RUN_LOG" 2>&1 || log "claude run exited non-zero (see $RUN_LOG)"

log "tick done — $RUN_LOG"
