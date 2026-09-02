"""Static contracts for the dependency-free dashboard UI."""

from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).parents[1] / "src" / "static"
HTML = (STATIC / "index.html").read_text()
JS = (STATIC / "app.js").read_text()
CSS = (STATIC / "app.css").read_text()


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.labels = {}
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if element_id := attributes.get("id"):
            self.ids.add(element_id)
        if tag == "label" and attributes.get("for"):
            self.labels[attributes["for"]] = attributes


def _dashboard():
    parser = DashboardParser()
    parser.feed(HTML)
    return parser


def test_workflow_uses_semantic_landmarks_and_headings():
    parser = _dashboard()
    tags = [tag for tag, _ in parser.tags]
    heading_ids = {
        attrs.get("id")
        for tag, attrs in parser.tags
        if tag == "h2"
    }

    assert "main" in tags
    assert {"setup-title", "experiment-title", "wiki-title"} <= heading_ids


def test_every_visible_form_field_has_an_accessible_label():
    parser = _dashboard()
    field_ids = {
        attrs["id"]
        for tag, attrs in parser.tags
        if tag in {"input", "select", "textarea"} and attrs.get("id")
    }

    assert field_ids <= parser.labels.keys()
    assert 'aria-describedby="gensHelp"' in HTML
    assert 'aria-describedby="nqaHelp"' in HTML


def test_document_results_are_rendered_with_dom_text_apis():
    assert "function renderDocs(docs)" in JS
    assert "document.createTextNode(d.name)" in JS
    assert "input.value = d.path" in JS
    assert "innerHTML = j.docs.map" not in JS


def test_folder_changes_invalidate_selection_and_sequence_requests():
    assert 'oninput="handleDirInput()"' in HTML
    assert "docsController?.abort()" in JS
    assert "const requestId = ++docsRequest" in JS
    assert "requestId !== docsRequest" in JS
    assert '$("docs").innerHTML = ""' in JS


def test_job_controls_preserve_focus_and_prevent_duplicate_submissions():
    assert 'data-job-id="${j.id}"' in JS
    assert "focusedJobId" in JS
    assert "requestPending || hasActiveJob" in JS
    assert "expandedDetails" in JS


def test_busy_actions_explain_their_state_and_localize_score_labels():
    parser = _dashboard()

    assert "workStatus" in parser.ids
    assert 'id="workStatus" role="status" aria-live="polite"' in HTML
    assert 'button.setAttribute("aria-busy", "true")' in JS
    assert 't("starting_job")' in JS
    assert 't("job_in_progress")' in JS
    assert 't("acc_short")' in JS
    assert 't("eff_short")' in JS
    assert "점수 (종합 · 정확도 · 효율)" in JS
    assert "分数（总分 · 准确率 · 效率）" in JS


def test_motion_and_touch_target_contracts_are_present():
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert ".docs-toolbar button { min-height: 44px" in CSS
    assert "min-height: 44px; border-radius: 8px" in CSS


def test_markup_links_split_assets_and_boots_theme_before_paint():
    """CSS/JS는 분리 파일, 테마는 첫 페인트 전 동기 스크립트로 적용 (flash 방지)."""
    assert '<link rel="stylesheet" href="/static/app.css">' in HTML
    assert '<script src="/static/app.js"></script>' in HTML
    head = HTML.split("<body", 1)[0]
    assert "wikiopt_theme" in head and "<script>" in head
    assert 'id="theme"' in HTML and "setTheme(" in JS


def test_css_tokens_define_both_themes():
    css = (STATIC / "app.css").read_text()
    assert css.count(":root {") == 1  # 토큰 정의는 한 곳
    assert '@media (prefers-color-scheme: dark)' in css
    assert ':root[data-theme="dark"]' in css
    assert "font-size: 10px" not in css and "font-size: 11px" not in css and "font-size: 9px" not in css
