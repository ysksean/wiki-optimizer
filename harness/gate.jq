# 폴링 게이트 카운트 — poll.sh와 tests/test_harness_gate.py가 공유하는 단일 소스.
# 입력: Linear GraphQL 응답 (issues.nodes[]에 labels, priority, comments 포함)
# 출력: {"triage": N, "exec": N}
#
# 재접수(re-intake) 규칙: needs-info 이슈의 "가장 최근 코멘트"가 하네스 역할 태그로
# 시작하지 않으면 민원인이 답변한 것으로 보고 triage 대상에 포함한다.
# 하네스가 재평가 코멘트를 달면 최신 코멘트가 하네스 것이 되므로 루프가 생기지 않는다.
# (개인 워크스페이스라 작성자 계정으로는 하네스/민원인을 구분할 수 없다 — 태그가 유일한 판별자)

def label_names: .labels.nodes | map(.name);

def is_harness_comment: .body | test("^(🔎|📋|🔧|🧿)");

def needs_reintake:
  (label_names | any(. == "needs-info"))
  and ((.comments.nodes | length) > 0)
  and (.comments.nodes | max_by(.createdAt) | is_harness_comment | not);

def untriaged:
  label_names | any(. == "triaged" or . == "needs-info") | not;

{
  triage: [.data.issues.nodes[] | select(untriaged or needs_reintake)] | length,
  exec: [.data.issues.nodes[] | select((label_names | index("triaged")) != null and .priority > 0)] | length
}
