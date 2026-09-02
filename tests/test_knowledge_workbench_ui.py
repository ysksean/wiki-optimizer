"""Regression contracts for the Knowledge Workbench redesign."""

from html.parser import HTMLParser
from pathlib import Path


STATIC = Path(__file__).parents[1] / "src" / "static"
HTML = (STATIC / "index.html").read_text()
# 대시보드는 index.html + app.css + app.js 세 파일로 서빙된다 — 계약은 번들 기준
BUNDLE = HTML + (STATIC / "app.css").read_text() + (STATIC / "app.js").read_text()


class WorkbenchParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.nav_classes = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if element_id := attributes.get("id"):
            self.ids.add(element_id)
        if tag == "nav":
            self.nav_classes.append(attributes.get("class", ""))


def test_workbench_exposes_primary_documents_and_experiment_controls():
    parser = WorkbenchParser()
    parser.feed(HTML)

    assert {
        "docSearch",
        "filterAll",
        "filterSelected",
        "experiment-panel",
        "modeSummary",
        "modeStructure",
    } <= parser.ids
    assert "function setDocFilter(selectedOnly)" in BUNDLE
    assert "function syncModeCards()" in BUNDLE
    assert 'id="filterAll" class="on" aria-pressed="true"' in BUNDLE
    assert 'id="filterSelected" aria-pressed="false"' in BUNDLE


def test_mobile_navigation_mirrors_all_desktop_destinations():
    parser = WorkbenchParser()
    parser.feed(HTML)

    assert "mobile-nav" in parser.nav_classes
    assert {"mobile-opt", "mobile-propose", "mobile-runs", "mobileLang"} <= parser.ids
    assert '$("mobile-" + k).classList.toggle("on", k === v)' in BUNDLE
    assert 'item.setAttribute("aria-current", "page")' in BUNDLE
    assert '$("mobileLang").value = LANG' in BUNDLE


def test_result_summary_includes_evolution_trail_and_localized_copy():
    assert 'class="evolution-steps"' in BUNDLE
    assert 't("evolution_trail")' in BUNDLE
    assert BUNDLE.count('evolution_trail: "Evolution Trail"') == 3
    assert 't("score_improved"' in BUNDLE
    assert "improvementValue > 0" in BUNDLE


def test_mobile_layout_guards_against_horizontal_overflow():
    assert "@media (max-width: 760px)" in BUNDLE
    assert ".workbench-grid { grid-template-columns: 1fr; }" in BUNDLE
    assert ".mobile-nav { position: fixed" in BUNDLE
    assert "@media (max-width: 360px)" in BUNDLE
    assert ".mode-cards { grid-template-columns: 1fr; }" in BUNDLE
    assert ".doc-filter button { min-height: 44px" in BUNDLE
    assert ".doc-search input { min-height: 44px; }" in BUNDLE
    assert ".advanced-grid input, .advanced-grid select { min-height: 44px; }" in BUNDLE
    assert ".docs { max-height: 360px; }" in BUNDLE
    assert ".docs-toolbar { position: fixed; left: 16px; right: 16px; bottom: 76px; }" in BUNDLE


def test_topbar_does_not_advertise_unimplemented_actions():
    assert 'data-i18n-aria-label="help"' not in BUNDLE
    assert 'data-i18n-aria-label="settings"' not in BUNDLE
