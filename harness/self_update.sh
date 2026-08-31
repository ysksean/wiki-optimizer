#!/usr/bin/env bash
# 하네스 자가 갱신 — 메인 체크아웃을 origin/master로 ff-only pull 한다.
#
# 사용: self_update.sh <repo_dir> [branch=master]
# 항상 exit 0. 결과를 한 줄로 stdout에 낸다 (poll.sh가 로그로 남김).
#
# 안전장치: 사람이 직접 작업하는 디렉터리이기도 하므로 아래 경우 pull을 건너뛴다.
#   - HEAD가 <branch>가 아님 (detached 포함)
#   - 추적 파일에 변경이 있음 (untracked는 무시)
# ff-only라 어떤 경우에도 로컬 커밋을 덮어쓰지 않는다.
set -uo pipefail

REPO="${1:?usage: self_update.sh <repo_dir> [branch]}"
BRANCH="${2:-master}"
SKIP_COUNT_FILE="${LINEAR_HARNESS_LOG_DIR:-$HOME/.local/state/linear-harness}/self-update-skips"
SKIP_ALERT_AFTER=3

# skip이 조용히 쌓이면 아무도 모른 채 옛 코드로 돈다 (2026-08-31 1시간+ 실사고).
# 연속 N회부터 WARNING + macOS 알림으로 사람을 부른다. 감지만 하고 복구는 안 한다.
_skip() {
  local n=0
  [ -f "$SKIP_COUNT_FILE" ] && n="$(cat "$SKIP_COUNT_FILE" 2>/dev/null || echo 0)"
  n=$((n + 1)); printf '%s' "$n" > "$SKIP_COUNT_FILE" 2>/dev/null || true
  if [ "$n" -ge "$SKIP_ALERT_AFTER" ]; then
    osascript -e "display notification \"$1 (${n}회 연속)\" with title \"linear-harness self-update 중단\"" >/dev/null 2>&1 || true
    echo "self-update WARNING (${n}회 연속 skip) — $1"
  else
    echo "self-update skipped — $1"
  fi
  exit 0
}

current="$(git -C "$REPO" symbolic-ref --short -q HEAD 2>/dev/null || true)"
if [ "$current" != "$BRANCH" ]; then
  _skip "HEAD is '${current:-detached}', not $BRANCH"
fi

if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  _skip "working tree dirty"
fi

rm -f "$SKIP_COUNT_FILE" 2>/dev/null || true

before="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?')"
if out="$(git -C "$REPO" pull --ff-only -q origin "$BRANCH" 2>&1)"; then
  after="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?')"
  if [ "$before" = "$after" ]; then
    echo "self-update ok — already up to date ($after)"
  else
    echo "self-update ok — $before -> $after"
  fi
else
  echo "self-update failed (continuing on $before) — ${out//$'\n'/ }"
fi
exit 0
