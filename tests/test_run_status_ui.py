"""실행 피드백 UI 계약 — 스피너 뱃지, 아이콘 버튼, 진행 단계, 토스트 (PR #47 포팅, 분리된 번들 기준)."""

from pathlib import Path


STATIC = Path(__file__).parents[1] / "src" / "static"
HTML = (STATIC / "index.html").read_text()
CSS = (STATIC / "app.css").read_text()
JS = (STATIC / "app.js").read_text()


def test_running_badge_uses_a_spinner_and_honors_reduced_motion():
    assert ".b-running::before" in CSS
    assert "animation: status-spin .75s linear infinite" in CSS
    assert "@keyframes status-spin" in CSS
    assert ".b-running::before, .action-spinner { animation: none" in CSS


def test_completed_badge_uses_a_check_and_localized_status_text():
    assert ".b-done::before" in CSS
    assert 'status_done: "완료"' in JS
    assert 'status_done: "Done"' in JS
    assert 'status_done: "已完成"' in JS


def test_status_badge_carries_localized_label():
    assert 'class="badge b-${j.status}" aria-label="${esc(statusLabel)}"' in JS
    assert "${esc(statusLabel)}</span>" in JS


def test_primary_actions_have_icons_and_busy_feedback():
    assert "const ACTION_ICONS =" in JS
    assert "function actionButtonContent(mode, label, running)" in JS
    assert 'class="action-spinner" aria-hidden="true"' in JS
    assert '["go", "run", "run_exp"]' in JS
    assert '["propGo", "propose", "btn_propose"]' in JS
    assert 'id="propGo"' in HTML
    assert JS.count("action_running:") == 3


def test_start_run_goes_through_the_shared_request_path():
    """startRun이 startJobRequest에 위임해야 토스트·activeJobMode가 모든 액션에 같이 걸린다."""
    assert "function startRun() {" in JS
    assert "async function startRun" not in JS
    assert JS.count('fetch("/api/runs", { method:"POST"') == 1


def test_run_cards_expose_honest_progress_steps():
    assert "function jobProgress(j, detail)" in JS
    assert 'role="group" aria-label="${esc(t("progress_label"))}"' in JS
    assert JS.count("progress_label:") == 3
    assert 'progress_wait: "대기"' in JS
    assert 'progress_done: "완료"' in JS
    assert ".run-progress {" in CSS and ".progress-step.active" in CSS
    assert 'if (!["queued", "running"].includes(j.status)) return "";' in JS


def test_toasts_announce_job_lifecycle_changes():
    assert 'id="toastRegion" role="status" aria-live="polite"' in HTML
    assert "function showToast(message, tone" in JS
    assert "function announceJobTransitions(jobs)" in JS
    assert JS.count("toast_started:") == 3
    assert 'showToast(t("toast_done", label), "success")' in JS
    assert 'showToast(t("toast_error", label), "error")' in JS
    assert ".toast { animation: none; }" in CSS
    assert ".toast-region { right: 16px; bottom: 82px; }" in CSS


def test_toast_uses_theme_tokens_not_hardcoded_colors():
    toast_rule = CSS[CSS.index("  .toast {"):CSS.index("  .toast-mark")]
    assert "#" not in toast_rule and "var(--card-raised)" in toast_rule


def test_mobile_job_head_keeps_badge_compact():
    """모바일 3열 그리드에서 칩 묶음이 1열 폭을 넓혀 뱃지가 늘어나던 회귀 방지."""
    assert ".job-head .job-chips { grid-column: 1 / -1; }" in CSS
    assert ".job-head .badge { justify-self: start; }" in CSS
