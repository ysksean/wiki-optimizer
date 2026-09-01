#!/usr/bin/env bash
# Grokbot/Hermes 리뷰 단계 — Stage 1(자동 최초 리뷰) + Stage 2(/stage2 요청 시 머지 권고)
# poll.sh tick 안에서 호출된다. 후보 PR이 없으면 LLM 호출 없이 종료.
# 모든 GitHub/Linear 변경(코멘트·라벨·상태)은 이 셸이 수행한다 — 에이전트는 텍스트만 생산한다.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
source "$REPO/harness/lib.sh"
GH_REPO="ysksean/wiki-optimizer"
TEAM_KEY="SEA"
KEY_FILE="${LINEAR_API_KEY_FILE:-$HOME/.config/linear/api_key}"
LOG_DIR="${LINEAR_HARNESS_LOG_DIR:-$HOME/.local/state/linear-harness}"
LINEAR_TODO_STATE="bed15c6b-e518-4056-88b8-0b6250ced11a"
IRC_MAX_CHARS=40000
REWORK_LIMIT=2

mkdir -p "$LOG_DIR"
log() { printf '[%s] review: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_DIR/poll.log"; }
now() { date '+%H:%M'; }

# ── 게이트 (LLM 호출 없음) ──────────────────────────────────────────
PRS="$(gh pr list --repo "$GH_REPO" --state open --json number,labels,title,updatedAt)"

S1_PR="$(s1_candidate "$PRS")"

# S2 자격: s1-done이고, s2 이력·needs-changes·P0/P1 판정이 없어야 한다 (P1 상태로 /stage2 치는 구멍 차단)
S2_PR=""
for n in $(s2_eligible "$PRS"); do
  if gh pr view "$n" --repo "$GH_REPO" --json comments -q '.comments[].body' | grep -q '^/stage2'; then
    S2_PR="$n"; break
  fi
done

linear_issue_id() { # $1=PR **제목** — 첫 SEA-N의 UUID 반환 (본문 금지: 2026-08-31 실사고)
  local ident num
  ident="$(extract_issue_ident "$TEAM_KEY" "$1")"
  [ -z "$ident" ] && return 0
  num="${ident#${TEAM_KEY}-}"
  curl -sf --max-time 30 -X POST https://api.linear.app/graphql \
    -H "Content-Type: application/json" -H "Authorization: $(cat "$KEY_FILE")" \
    -d "{\"query\":\"{ issues(filter: { team: { key: { eq: \\\"${TEAM_KEY}\\\" } }, number: { eq: ${num} } }) { nodes { id } } }\"}" \
    | jq -r '.data.issues.nodes[0].id // empty'
}

linear_comment() { # $1=issue-uuid $2=코멘트 본문
  curl -sf --max-time 30 -X POST https://api.linear.app/graphql \
    -H "Content-Type: application/json" -H "Authorization: $(cat "$KEY_FILE")" \
    -d "$(jq -n --arg id "$1" --arg body "$2" \
      '{query: "mutation($id: String!, $body: String!) { commentCreate(input: { issueId: $id, body: $body }) { success } }", variables: {id: $id, body: $body}}')" >/dev/null
}

# ── 워치독: needs-changes 교착 감지 (LLM 호출 없음, SEA-8) ─────────
# 재작업 지시 후 라벨 제거 실패·집행 누락으로 In Review에 멈춘 PR을 Linear에 1회 경고.
WATCHDOG_HOURS="${WATCHDOG_HOURS:-6}"

watchdog() {
  local n issue_id marker has body
  for n in $(stalled_prs "$PRS" "$(date +%s)" "$WATCHDOG_HOURS"); do
    issue_id="$(linear_issue_id "$(gh pr view "$n" --repo "$GH_REPO" --json title -q '.title')")" \
      || { log "watchdog — PR#$n 이슈 조회 실패, 건너뜀"; continue; }
    [ -z "$issue_id" ] && { log "watchdog — PR#$n 교착이나 Linear 이슈 미연결"; continue; }
    marker="<!-- watchdog:PR#$n -->"
    # 마커 조회가 실패하면 코멘트하지 않는다 — "경고는 1회" 보장이 중복 경고보다 우선
    has="$(curl -sf --max-time 30 -X POST https://api.linear.app/graphql \
      -H "Content-Type: application/json" -H "Authorization: $(cat "$KEY_FILE")" \
      -d "$(jq -n --arg id "$issue_id" \
        '{query: "query($id: String!) { issue(id: $id) { comments { nodes { body } } } }", variables: {id: $id}}')" \
      | jq -r --arg m "$marker" '[.data.issue.comments.nodes[].body | contains($m)] | any')" \
      || { log "watchdog — PR#$n 마커 조회 실패, 건너뜀"; continue; }
    [ "$has" = "true" ] && continue
    body="🧿 워치독 경고 — PR #$n 이 \`needs-changes\` 상태로 ${WATCHDOG_HOURS}시간 이상 활동이 없다. 재작업이 멈췄을 수 있으니 확인 필요: https://github.com/$GH_REPO/pull/$n

$marker"
    if linear_comment "$issue_id" "$body"; then
      log "watchdog — PR#$n 교착 경고 코멘트 등록"
    else
      log "watchdog — PR#$n 경고 코멘트 실패"
    fi
  done
}
watchdog || log "watchdog error (ignored)"

[ -z "$S1_PR" ] && [ -z "$S2_PR" ] && { log "quiet — s1:none s2:none (no LLM call)"; exit 0; }

# ── 공통: PR 자료 준비 ──────────────────────────────────────────────
prepare_pr() { # $1=pr번호 → $WORK/$WT 세팅
  PR="$1"
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/grokbot-pr${PR}.XXXX")"
  WT="$WORK/wt"
  gh pr view "$PR" --repo "$GH_REPO" --json title,body,headRefName,additions,deletions > "$WORK/meta.json"
  gh pr diff "$PR" --repo "$GH_REPO" > "$WORK/diff.patch"
  HEAD_REF="$(jq -r .headRefName "$WORK/meta.json")"
  git -C "$REPO" fetch -q origin "$HEAD_REF"
  git -C "$REPO" worktree add -q --detach "$WT" FETCH_HEAD
  : > "$WORK/irc.log"
  # hermes `-t file` 토올셋에는 write_file/patch가 포함된다 (PR#13 Grokbot 지적) —
  # 건네주는 경로는 파일시스템 레벨로 쓰기를 막는다. 절대경로로 다른 곳에 쓰는 것까지는
  # 못 막으므로 잔여 위험은 README에 기록, 프로세스 샌드박스는 MVP-3.
  chmod -R a-w "$WT" "$WORK/diff.patch" "$WORK/meta.json" 2>/dev/null || true
}

cleanup_pr() {
  chmod -R u+w "$WT" "$WORK" 2>/dev/null || true
  git -C "$REPO" worktree remove --force "$WT" 2>/dev/null || true
  [ "${KEEP_WORK:-0}" = 1 ] || rm -rf "$WORK"
}

ensure_labels() {
  for l in "grokbot:s1-done" "grokbot:s1-error" "grokbot:s2-done" "grokbot:s2-error" needs-changes; do
    gh label create "$l" --repo "$GH_REPO" --color 8B5CF6 2>/dev/null || true
  done
}

irc() { printf '[%s] <%s> %s\n' "$(now)" "$1" "$2" >> "$WORK/irc.log"; }
step() { log "PR#$PR step: $1"; }

hermes_call() { # $1=프롬프트 → stdout. 일회용 read-only worktree에서만 읽게 한다
  timeout 600 hermes -z "$1" --no-restore-cwd -t file 2>>"$WORK/stderr.log"
}

claude_call() { # $1=프롬프트 → stdout. 읽기 도구만, MCP 없음
  # Grokbot은 Opus 5 고정 — Executor(Fable 5)와 모델을 갈라 리뷰 독립성 확보 + 비용 절감
  timeout 600 claude -p "$1" --model claude-opus-5 --allowedTools "Read,Glob,Grep" --max-turns 40 2>>"$WORK/stderr.log"
}

render() { # $1=프롬프트 파일 — {{PR}}/{{WT}}/{{WORK}} 치환
  sed -e "s|{{PR}}|$PR|g" -e "s|{{WT}}|$WT|g" -e "s|{{WORK}}|$WORK|g" "$REPO/harness/prompts/$1"
}

fail_label() { # $1=pr $2=stage — 실패 시 라벨 박아 재시도 폭주 방지 (사람이 라벨 제거해야 재시도)
  KEEP_WORK=1
  gh pr edit "$1" --repo "$GH_REPO" --add-label "grokbot:${2}-error" || true
  log "PR#$1 ${2} FAILED — grokbot:${2}-error 라벨, 재시도 중단. 작업물 보존: ${WORK:-?}"
}

linear_rollback() { # $1=issue-uuid $2=코멘트 본문 — 이슈를 Todo로 되돌려 Executor 재작업 유도
  local payload
  payload="$(jq -n --arg id "$1" --arg body "$2" --arg state "$LINEAR_TODO_STATE" \
    '{query: "mutation($id: String!, $body: String!, $state: String!) { issueUpdate(id: $id, input: { stateId: $state }) { success } commentCreate(input: { issueId: $id, body: $body }) { success } }", variables: {id: $id, body: $body, state: $state}}')"
  curl -sf --max-time 30 -X POST https://api.linear.app/graphql \
    -H "Content-Type: application/json" -H "Authorization: $(cat "$KEY_FILE")" \
    -d "$payload" >/dev/null
}

post_review() { # $1=stage — verdict.md + irc.log를 PR 코멘트로
  # GRADE 줄은 위치와 무관하게 찾는다 — LLM이 서두를 붙여도 파싱되게 (2026-08-29 PR#12 실패 교훈)
  local grade
  grade="$(parse_grade "$WORK/verdict.md")"
  [ -z "$grade" ] && return 1
  if [ "$1" = "s1" ]; then irc "grokbot" "판정: $grade. 끝."; else irc "grokbot" "Stage 2 판정: $grade. 머지는 사람이 눌러라."; fi
  {
    sed -n '/^GRADE: P[0-4]/,$p' "$WORK/verdict.md" | tail -n +2
    echo; echo "<details><summary>📡 #wiki-optimizer IRC 로그 (grokbot ↔ hermes)</summary>"; echo
    echo '```irc'; head -c "$IRC_MAX_CHARS" "$WORK/irc.log"; echo '```'; echo "</details>"
  } > "$WORK/comment.md"
  gh pr comment "$PR" --repo "$GH_REPO" --body-file "$WORK/comment.md" >/dev/null
  # -done 부착 실패를 삼키면 다음 tick마다 중복 리뷰가 돈다 (PR#13 재심사 지적) — 실패 시 fail 경로로
  gh pr edit "$PR" --repo "$GH_REPO" --add-label "grokbot:$1-done" >/dev/null
  local gl; gl="$(tr '[:upper:]' '[:lower:]' <<<"$grade")"
  if [ "$1" = "s1" ]; then
    for old in p0 p1 p2 p3 p4; do  # 재심사 시 이전 등급 라벨 제거 (P1·P3 동거 방지)
      [ "$old" = "$gl" ] || gh pr edit "$PR" --repo "$GH_REPO" --remove-label "grokbot:$old" >/dev/null 2>&1 || true
    done
    gh label create "grokbot:$gl" --repo "$GH_REPO" --color 8B5CF6 >/dev/null 2>&1 || true
    gh pr edit "$PR" --repo "$GH_REPO" --add-label "grokbot:$gl" >/dev/null 2>&1 || true
  fi
  echo "$grade"
}

# ── Stage 1 ─────────────────────────────────────────────────────────
if [ -n "$S1_PR" ]; then
  log "S1 dispatch — PR#$S1_PR"
  ensure_labels
  if prepare_pr "$S1_PR"; then
    trap cleanup_pr EXIT
    (
      irc "grokbot" "PR #$PR 떴다. hermes, 들여다봐라."
      step "hermes opinion"
      OPINION="$(hermes_call "$(render hermes_opinion.md)")" && [ -n "$OPINION" ] || exit 1
      printf '%s\n' "$OPINION" > "$WORK/opinion.md"
      while IFS= read -r l; do irc "hermes" "$l"; done < <(printf '%s\n' "$OPINION" | sed '/^[[:space:]]*$/d')
      step "grokbot questions"
      QUESTIONS="$(claude_call "$(render grokbot_questions.md)")" || exit 1
      printf '%s\n' "$QUESTIONS" > "$WORK/questions.md"
      while IFS= read -r l; do irc "grokbot" "$l"; done < <(printf '%s\n' "$QUESTIONS" | sed '/^[[:space:]]*$/d')
      if ! grep -qi "NO QUESTIONS" "$WORK/questions.md"; then
        step "hermes answers"
        ANSWERS="$(hermes_call "$(render hermes_answers.md)")" || exit 1
        printf '%s\n' "$ANSWERS" > "$WORK/answers.md"
        while IFS= read -r l; do irc "hermes" "$l"; done < <(printf '%s\n' "$ANSWERS" | sed '/^[[:space:]]*$/d')
      fi
      step "grokbot verdict"
      claude_call "$(render grokbot_verdict_s1.md)" > "$WORK/verdict.md" && [ -s "$WORK/verdict.md" ] || exit 1
      step "post review"
      GRADE="$(post_review s1)" || exit 1
      log "S1 done — PR#$PR grade=$GRADE"
      if [ "$GRADE" = "P0" ] || [ "$GRADE" = "P1" ]; then
        gh label create "needs-changes" --repo "$GH_REPO" --color D93F0B 2>/dev/null || true
        gh pr edit "$PR" --repo "$GH_REPO" --add-label "needs-changes"
        # 재작업 횟수 = 지금 것 포함 누적 S1 리뷰 코멘트 수 (한도 초과 시 사람 개입)
        S1_RUNS="$(gh pr view "$PR" --repo "$GH_REPO" --json comments -q '[.comments[].body | select(contains("Grokbot Stage 1"))] | length')"
        ISSUE_ID="$(linear_issue_id "$(jq -r '.title' "$WORK/meta.json")")"
        if [ -n "$ISSUE_ID" ] && [ "$S1_RUNS" -le "$REWORK_LIMIT" ]; then
          SUMMARY="🧿 Grokbot 재작업 지시 ($GRADE) — PR #$PR 리뷰에서 지적사항이 나왔다. PR 코멘트의 Grokbot 리뷰를 읽고 기존 브랜치에 수정 커밋을 올려라. 수정 push 후 PR의 grokbot:s1-done, needs-changes 라벨을 제거할 것."
          linear_rollback "$ISSUE_ID" "$SUMMARY" && log "S1 — PR#$PR $GRADE → Linear 재작업 롤백 (${S1_RUNS}회차)"
        else
          log "S1 — PR#$PR $GRADE, 재작업 한도 초과 또는 이슈 미연결 — 사람 개입 필요"
        fi
      fi
    ) || fail_label "$S1_PR" s1
    cleanup_pr; trap - EXIT
  else
    fail_label "$S1_PR" s1
  fi
fi

# ── Stage 2 ─────────────────────────────────────────────────────────
if [ -n "$S2_PR" ]; then
  log "S2 dispatch — PR#$S2_PR"
  ensure_labels
  if prepare_pr "$S2_PR"; then
    trap cleanup_pr EXIT
    (
      irc "grokbot" "PR #$PR 머지 심사 들어간다 (/stage2 접수). hermes, 최종 의견."
      OPINION="$(hermes_call "$(render hermes_opinion.md)")" && [ -n "$OPINION" ] || exit 1
      printf '%s\n' "$OPINION" > "$WORK/opinion.md"
      while IFS= read -r l; do irc "hermes" "$l"; done < <(printf '%s\n' "$OPINION" | sed '/^[[:space:]]*$/d')
      claude_call "$(render grokbot_verdict_s2.md)" > "$WORK/verdict.md" && [ -s "$WORK/verdict.md" ] || exit 1
      GRADE="$(post_review s2)" || exit 1
      log "S2 done — PR#$PR grade=$GRADE"
    ) || fail_label "$S2_PR" s2
    cleanup_pr; trap - EXIT
  else
    fail_label "$S2_PR" s2
  fi
fi
