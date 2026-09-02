"""Static contracts for run-status feedback in the dashboard."""

from pathlib import Path


HTML = (Path(__file__).parents[1] / "src" / "static" / "index.html").read_text()


def test_running_badge_uses_a_spinner_and_honors_reduced_motion():
    assert ".b-running::before" in HTML
    assert "animation: status-spin .75s linear infinite" in HTML
    assert "@keyframes status-spin" in HTML
    assert ".b-running::before, .action-spinner { animation: none" in HTML


def test_completed_badge_uses_a_check_and_localized_status_text():
    assert ".b-done::before" in HTML
    assert 'status_done: "완료"' in HTML
    assert 'status_done: "Done"' in HTML
    assert 'status_done: "已完成"' in HTML


def test_status_badge_is_announced_to_assistive_technology():
    assert 'role="status" aria-label="${esc(statusLabel)}"' in HTML
    assert '${esc(statusLabel)}</span>' in HTML


def test_primary_actions_have_icons_and_busy_feedback():
    assert "const ACTION_ICONS =" in HTML
    assert "function actionButtonContent(mode, label, running)" in HTML
    assert 'class="action-spinner" aria-hidden="true"' in HTML
    assert '["go", "run", "run_exp"]' in HTML
    assert '["propGo", "propose", "btn_propose"]' in HTML


def test_run_cards_expose_honest_progress_steps():
    assert "function jobProgress(j, detail)" in HTML
    assert 'role="group" aria-label="${esc(t("progress_label"))}"' in HTML
    assert HTML.count("progress_label:") == 3
    assert 'progress_wait: "대기"' in HTML
    assert 'progress_done: "완료"' in HTML


def test_toasts_announce_job_lifecycle_changes():
    assert 'id="toastRegion" role="status" aria-live="polite"' in HTML
    assert "function showToast(message, tone" in HTML
    assert "function announceJobTransitions(jobs)" in HTML
    assert HTML.count("toast_started:") == 3
    assert 'showToast(t("toast_done", label), "success")' in HTML
    assert 'showToast(t("toast_error", label), "error")' in HTML
