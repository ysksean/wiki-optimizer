"""Regression contracts for the Knowledge Workbench redesign."""

from html.parser import HTMLParser
from pathlib import Path


HTML = (Path(__file__).parents[1] / "src" / "static" / "index.html").read_text()


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
    assert "function setDocFilter(selectedOnly)" in HTML
    assert "function syncModeCards()" in HTML
    assert 'id="filterAll" class="on" aria-pressed="true"' in HTML
    assert 'id="filterSelected" aria-pressed="false"' in HTML


def test_mobile_navigation_mirrors_all_desktop_destinations():
    parser = WorkbenchParser()
    parser.feed(HTML)

    assert "mobile-nav" in parser.nav_classes
    assert {"mobile-opt", "mobile-propose", "mobile-runs", "mobileLang"} <= parser.ids
    assert '$("mobile-" + k).classList.toggle("on", k === v)' in HTML
    assert 'item.setAttribute("aria-current", "page")' in HTML
    assert '$("mobileLang").value = LANG' in HTML


def test_result_summary_includes_evolution_trail_and_localized_copy():
    assert 'class="evolution-steps"' in HTML
    assert 't("evolution_trail")' in HTML
    assert HTML.count('evolution_trail: "Evolution Trail"') == 3
    assert 't("score_improved"' in HTML
    assert "improvementValue > 0" in HTML


def test_mobile_layout_guards_against_horizontal_overflow():
    assert "@media (max-width: 760px)" in HTML
    assert ".workbench-grid { grid-template-columns: 1fr; }" in HTML
    assert ".mobile-nav { position: fixed" in HTML
    assert "@media (max-width: 360px)" in HTML
    assert ".mode-cards { grid-template-columns: 1fr; }" in HTML
    assert ".doc-filter button { min-height: 44px" in HTML
    assert ".docs { max-height: 360px; }" in HTML
    assert ".docs-toolbar { position: fixed; left: 16px; right: 16px; bottom: 76px; }" in HTML


def test_topbar_does_not_advertise_unimplemented_actions():
    assert 'data-i18n-aria-label="help"' not in HTML
    assert 'data-i18n-aria-label="settings"' not in HTML
