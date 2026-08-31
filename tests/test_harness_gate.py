"""harness/gate.jq 폴링 게이트 카운트 테스트 — poll.sh가 쓰는 jq 필터를 fixture로 검증."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

GATE_JQ = Path(__file__).resolve().parent.parent / "harness" / "gate.jq"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def run_gate(issues: list[dict]) -> dict:
    resp = {"data": {"issues": {"nodes": issues}}}
    proc = subprocess.run(
        ["jq", "-c", "-f", str(GATE_JQ)],
        input=json.dumps(resp),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def issue(labels: list[str], priority: int = 0, comments: list[dict] | None = None) -> dict:
    return {
        "identifier": "SEA-0",
        "title": "t",
        "priority": priority,
        "labels": {"nodes": [{"name": n} for n in labels]},
        "comments": {"nodes": comments or []},
    }


def comment(body: str, created_at: str) -> dict:
    return {"body": body, "createdAt": created_at}


def test_unlabeled_issue_counts_as_triage() -> None:
    counts = run_gate([issue(labels=[])])
    assert counts == {"triage": 1, "exec": 0}


def test_triaged_with_priority_counts_as_exec_only() -> None:
    counts = run_gate([issue(labels=["triaged"], priority=3)])
    assert counts == {"triage": 0, "exec": 1}


def test_triaged_without_priority_is_ignored() -> None:
    counts = run_gate([issue(labels=["triaged"], priority=0)])
    assert counts == {"triage": 0, "exec": 0}


def test_needs_info_with_user_reply_reenters_triage() -> None:
    comments = [
        comment("🔎 Triage\n\n재현 방법을 알려주세요.", "2026-08-30T10:00:00.000Z"),
        comment("이렇게 하면 재현됩니다.", "2026-08-30T11:00:00.000Z"),
    ]
    counts = run_gate([issue(labels=["needs-info"], comments=comments)])
    assert counts == {"triage": 1, "exec": 0}


def test_needs_info_with_harness_comment_last_stays_dormant() -> None:
    # 하네스가 재평가 후 추가 질문을 남긴 상태 — 다시 계수하면 무한 루프
    comments = [
        comment("이렇게 하면 재현됩니다.", "2026-08-30T11:00:00.000Z"),
        comment("🔎 Triage\n\n로그도 같이 부탁드립니다.", "2026-08-30T12:00:00.000Z"),
    ]
    counts = run_gate([issue(labels=["needs-info"], comments=comments)])
    assert counts == {"triage": 0, "exec": 0}


@pytest.mark.parametrize("tag", ["🔎", "📋", "🔧", "🧿"])
def test_every_harness_role_tag_is_excluded(tag: str) -> None:
    comments = [comment(f"{tag} 어떤 역할\n\n본문", "2026-08-30T12:00:00.000Z")]
    counts = run_gate([issue(labels=["needs-info"], comments=comments)])
    assert counts == {"triage": 0, "exec": 0}


def test_needs_info_without_comments_stays_dormant() -> None:
    counts = run_gate([issue(labels=["needs-info"])])
    assert counts == {"triage": 0, "exec": 0}


def test_latest_comment_wins_regardless_of_order() -> None:
    # 응답 배열 순서와 무관하게 createdAt 최대값 기준으로 판정해야 한다
    comments = [
        comment("답변입니다.", "2026-08-30T13:00:00.000Z"),
        comment("🔎 Triage\n\n정보가 부족합니다.", "2026-08-30T10:00:00.000Z"),
    ]
    counts = run_gate([issue(labels=["needs-info"], comments=comments)])
    assert counts == {"triage": 1, "exec": 0}


def test_mixed_backlog_snapshot() -> None:
    issues = [
        issue(labels=[]),  # 신규 → triage
        issue(labels=["needs-info"], comments=[comment("답변", "2026-08-30T11:00:00.000Z")]),  # 재접수 → triage
        issue(labels=["triaged"], priority=2),  # 대기열 → exec
        issue(labels=["triaged"], priority=0),  # 보류
    ]
    counts = run_gate(issues)
    assert counts == {"triage": 2, "exec": 1}
