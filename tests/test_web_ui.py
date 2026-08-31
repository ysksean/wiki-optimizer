"""Static contracts for the dependency-free dashboard UI."""

from html.parser import HTMLParser
from pathlib import Path


HTML = (Path(__file__).parents[1] / "src" / "static" / "index.html").read_text()


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
    assert "function renderDocs(docs)" in HTML
    assert "document.createTextNode(d.name)" in HTML
    assert "input.value = d.path" in HTML
    assert "innerHTML = j.docs.map" not in HTML


def test_folder_changes_invalidate_selection_and_sequence_requests():
    assert 'oninput="handleDirInput()"' in HTML
    assert "docsController?.abort()" in HTML
    assert "const requestId = ++docsRequest" in HTML
    assert "requestId !== docsRequest" in HTML
    assert '$("docs").innerHTML = ""' in HTML


def test_job_controls_preserve_focus_and_prevent_duplicate_submissions():
    assert 'data-job-id="${j.id}"' in HTML
    assert "focusedJobId" in HTML
    assert "requestPending || hasActiveJob" in HTML
    assert "expandedDetails" in HTML


def test_motion_and_touch_target_contracts_are_present():
    assert "@media (prefers-reduced-motion: reduce)" in HTML
    assert ".docs-toolbar button { min-height: 44px" in HTML
    assert "min-height: 44px; border-radius: 8px" in HTML
