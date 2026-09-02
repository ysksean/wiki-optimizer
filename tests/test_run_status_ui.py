"""Static contracts for run-status feedback in the dashboard."""

from pathlib import Path


HTML = (Path(__file__).parents[1] / "src" / "static" / "index.html").read_text()


def test_running_badge_uses_a_spinner_and_honors_reduced_motion():
    assert ".b-running::before" in HTML
    assert "animation: status-spin .75s linear infinite" in HTML
    assert "@keyframes status-spin" in HTML
    assert ".b-running::before { animation: none" in HTML


def test_completed_badge_uses_a_check_and_localized_status_text():
    assert ".b-done::before" in HTML
    assert 'status_done: "완료"' in HTML
    assert 'status_done: "Done"' in HTML
    assert 'status_done: "已完成"' in HTML


def test_status_badge_is_announced_to_assistive_technology():
    assert 'role="status" aria-label="${esc(statusLabel)}"' in HTML
    assert '${esc(statusLabel)}</span>' in HTML
