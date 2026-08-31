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

current="$(git -C "$REPO" symbolic-ref --short -q HEAD 2>/dev/null || true)"
if [ "$current" != "$BRANCH" ]; then
  echo "self-update skipped — HEAD is '${current:-detached}', not $BRANCH"
  exit 0
fi

if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  echo "self-update skipped — working tree dirty"
  exit 0
fi

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
