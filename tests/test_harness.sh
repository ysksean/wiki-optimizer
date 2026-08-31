#!/usr/bin/env bash
# harness/lib.sh 오프라인 회귀 테스트 — LLM/네트워크 없이 픽스처로 검증 (SEA-9)
# 실행: bash tests/test_harness.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../harness/lib.sh
source "$HERE/../harness/lib.sh"

PASS=0; FAIL=0
assert_eq() { # $1=설명 $2=기대값 $3=실제값
  if [ "$2" = "$3" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL: %s\n  expected: [%s]\n  actual:   [%s]\n' "$1" "$2" "$3"
  fi
}

# ── 픽스처: Linear 게이트 응답 ──────────────────────────────────────
# 라벨 조합별 케이스: 무라벨 / triaged+priority>0 / triaged+priority 0 /
# needs-info / triaged+needs-info / 무관 라벨만
LINEAR_GATE='{
  "data": { "issues": { "nodes": [
    { "identifier": "SEA-20", "priority": 0, "labels": { "nodes": [] } },
    { "identifier": "SEA-21", "priority": 3, "labels": { "nodes": [{ "name": "triaged" }, { "name": "Bug" }] } },
    { "identifier": "SEA-22", "priority": 0, "labels": { "nodes": [{ "name": "triaged" }] } },
    { "identifier": "SEA-23", "priority": 2, "labels": { "nodes": [{ "name": "needs-info" }] } },
    { "identifier": "SEA-24", "priority": 1, "labels": { "nodes": [{ "name": "triaged" }, { "name": "needs-info" }] } },
    { "identifier": "SEA-25", "priority": 4, "labels": { "nodes": [{ "name": "Feature" }] } }
  ] } }
}'
LINEAR_EMPTY='{ "data": { "issues": { "nodes": [] } } }'

# triage 대상: triaged/needs-info 둘 다 없는 것 → SEA-20, SEA-25
assert_eq "gate_triage_count: 혼합 라벨" "2" "$(gate_triage_count "$LINEAR_GATE")"
# exec 대상: triaged 있고 priority>0 → SEA-21, SEA-24
assert_eq "gate_exec_count: 혼합 라벨" "2" "$(gate_exec_count "$LINEAR_GATE")"
assert_eq "gate_triage_count: 빈 목록" "0" "$(gate_triage_count "$LINEAR_EMPTY")"
assert_eq "gate_exec_count: 빈 목록" "0" "$(gate_exec_count "$LINEAR_EMPTY")"

# ── 픽스처: gh pr list ──────────────────────────────────────────────
PRS_MIXED='[
  { "number": 30, "title": "a", "labels": [{ "name": "grokbot:s1-done" }, { "name": "grokbot:p2" }] },
  { "number": 31, "title": "b", "labels": [{ "name": "grokbot:s1-error" }] },
  { "number": 32, "title": "c", "labels": [] },
  { "number": 33, "title": "d", "labels": [{ "name": "enhancement" }] }
]'
# s1 후보: grokbot:s1* 없는 첫 PR (s1-done·s1-error 모두 배제)
assert_eq "s1_candidate: s1 라벨 없는 첫 PR" "32" "$(s1_candidate "$PRS_MIXED")"
assert_eq "s1_candidate: 후보 없음" "" "$(s1_candidate '[{ "number": 40, "title": "x", "labels": [{ "name": "grokbot:s1-done" }] }]')"
assert_eq "s1_candidate: 빈 목록" "" "$(s1_candidate '[]')"

PRS_S2='[
  { "number": 50, "title": "eligible", "labels": [{ "name": "grokbot:s1-done" }, { "name": "grokbot:p2" }] },
  { "number": 51, "title": "needs-changes", "labels": [{ "name": "grokbot:s1-done" }, { "name": "needs-changes" }] },
  { "number": 52, "title": "p1", "labels": [{ "name": "grokbot:s1-done" }, { "name": "grokbot:p1" }] },
  { "number": 53, "title": "p0", "labels": [{ "name": "grokbot:s1-done" }, { "name": "grokbot:p0" }] },
  { "number": 54, "title": "s2-done", "labels": [{ "name": "grokbot:s1-done" }, { "name": "grokbot:s2-done" }] },
  { "number": 55, "title": "s2-error", "labels": [{ "name": "grokbot:s1-done" }, { "name": "grokbot:s2-error" }] },
  { "number": 56, "title": "no-s1", "labels": [] },
  { "number": 57, "title": "eligible-2", "labels": [{ "name": "grokbot:s1-done" }, { "name": "grokbot:p3" }] }
]'
# S2 자격: s1-done 필수 + s2*·needs-changes·p0·p1 전부 배제 → 50, 57
assert_eq "s2_eligible: 배제 규칙" "50
57" "$(s2_eligible "$PRS_S2")"
assert_eq "s2_eligible: 자격 없음" "" "$(s2_eligible '[{ "number": 60, "title": "x", "labels": [] }]')"

# ── GRADE 파싱 ──────────────────────────────────────────────────────
VD="$(mktemp "${TMPDIR:-/tmp}/verdict.XXXX")"
trap 'rm -f "$VD"' EXIT

# 서두가 붙은 verdict — GRADE 줄이 첫 줄이 아니어도 잡아야 한다 (PR#12 교훈)
printf '리뷰 요약입니다.\n이런저런 서두.\nGRADE: P2\n상세 내용.\n' > "$VD"
assert_eq "parse_grade: 서두 있는 verdict" "P2" "$(parse_grade "$VD")"

printf 'GRADE 줄이 아예 없는 verdict.\n' > "$VD"
assert_eq "parse_grade: GRADE 없음 → 빈값" "" "$(parse_grade "$VD")"

printf 'GRADE: P5\n' > "$VD"
assert_eq "parse_grade: 범위 밖 P5 거부" "" "$(parse_grade "$VD")"

printf '  GRADE: P1\n인용문 안의 들여쓴 GRADE는 무시.\nGRADE: P0\n' > "$VD"
assert_eq "parse_grade: 줄 시작만 인정, 첫 매치" "P0" "$(parse_grade "$VD")"

printf 'GRADE: P3\nGRADE: P1\n' > "$VD"
assert_eq "parse_grade: 복수 GRADE는 첫 줄" "P3" "$(parse_grade "$VD")"

# ── SEA-N 추출 ──────────────────────────────────────────────────────
assert_eq "extract_issue_ident: 제목에서" "SEA-9" "$(extract_issue_ident SEA "fix(harness): tests (SEA-9) body text")"
assert_eq "extract_issue_ident: 첫 매치 우선" "SEA-7" "$(extract_issue_ident SEA "SEA-7 관련이며 SEA-12도 언급")"
assert_eq "extract_issue_ident: 없으면 빈값" "" "$(extract_issue_ident SEA "no issue reference here")"
assert_eq "extract_issue_ident: 다른 팀 키 무시" "" "$(extract_issue_ident SEA "ABC-3 only")"

# ── 결과 ────────────────────────────────────────────────────────────
printf 'test_harness: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
