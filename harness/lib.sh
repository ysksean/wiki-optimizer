#!/usr/bin/env bash
# 하네스 결정론적 로직 모음 — poll.sh/review.sh가 source하고 tests/test_harness.sh가 오프라인 검증한다.
# 이 파일의 함수는 LLM·네트워크·gh 호출 없이 입력 → 출력만 있어야 한다.

_HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# $1=Linear 게이트 GraphQL 응답 JSON → triage 대상 수 (needs-info 재접수 포함, 로직은 gate.jq 단일 소스)
gate_triage_count() {
  jq -f "$_HARNESS_DIR/gate.jq" <<<"$1" | jq '.triage'
}

# $1=Linear 게이트 GraphQL 응답 JSON → 집행 대기 수 (로직은 gate.jq 단일 소스)
gate_exec_count() {
  jq -f "$_HARNESS_DIR/gate.jq" <<<"$1" | jq '.exec'
}

# $1=gh pr list JSON(number,labels,...) → grokbot:s1* 라벨이 없는 첫 PR 번호 (없으면 빈값)
s1_candidate() {
  jq -r '[.[] | select([.labels[].name] | any(startswith("grokbot:s1")) | not)][0].number // empty' <<<"$1"
}

# $1=gh pr list JSON → S2 자격 PR 번호 목록 (줄바꿈 구분, /stage2 코멘트 확인은 호출부 몫)
# 자격: grokbot:s1-done 보유 + grokbot:s2*·needs-changes·grokbot:p0·grokbot:p1 전부 없음
s2_eligible() {
  jq -r '.[] | select(([.labels[].name] | index("grokbot:s1-done")) and (([.labels[].name] | any(startswith("grokbot:s2") or . == "needs-changes" or . == "grokbot:p0" or . == "grokbot:p1")) | not)) | .number' <<<"$1"
}

# $1=gh pr list JSON(number,labels,updatedAt) $2=현재 epoch초 $3=임계 시간(h)
# → needs-changes가 붙은 채 updatedAt이 임계 시간보다 오래된 PR 번호 목록 (줄바꿈 구분, 없으면 빈 출력)
stalled_prs() {
  jq -r --argjson now "$2" --argjson hrs "$3" \
    '.[] | select([.labels[].name] | index("needs-changes"))
         | select(($now - (.updatedAt | sub("\\.[0-9]+"; "") | fromdateiso8601)) >= $hrs * 3600)
         | .number' <<<"$1"
}

# $1=verdict 파일 경로 → P0~P4 (줄 위치 무관, 범위 밖이거나 없으면 빈값)
parse_grade() {
  grep -oE '^GRADE: P[0-4]' "$1" | head -1 | grep -oE 'P[0-4]' || true
}

# $1=팀 키(예: SEA) $2=검색 텍스트 → 첫 <팀키>-N 식별자 (없으면 빈값)
extract_issue_ident() {
  grep -oE "${1}-[0-9]+" <<<"$2" | head -1 || true
}
