"""Behavior checks for the skill's read-only source-reference inspector."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".claude/skills/wiki-optimize/scripts/check_design.py"
SPEC = importlib.util.spec_from_file_location("wiki_design_checker", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


@pytest.fixture
def design(tmp_path: Path) -> dict[str, Any]:
    """Use a policy with a real section and one deliberately unresolved knowledge gap."""
    source = tmp_path / "policies"
    source.mkdir()
    (source / "release.md").write_text("# Release\n## Approval\nThe project owner approves deployment.\n")
    return {"sources": [str(source)], "pages": [
        {"path": "operations/release.md", "title": "Release", "purpose": "Who approves deployment?",
         "outline": ["## Approval"], "sources": ["policies/release.md#Approval"], "status": "grounded"},
        {"path": "operations/recovery.md", "title": "Recovery", "purpose": "How do we recover?",
         "outline": ["## Missing policy"], "sources": [], "status": "gap"},
    ]}


def test_valid_references_and_explicit_gaps_are_not_semantic_proof(design: dict[str, Any]) -> None:
    """A structural pass must keep the gap and untested semantic questions visible."""
    report = checker.inspect_design(design)
    assert report["structural_ok"]
    assert report["gap_pages"] == ["operations/recovery.md"]
    assert "semantic support of page claims" in report["not_evaluated"]


def test_grounded_label_without_evidence_is_rejected(design: dict[str, Any]) -> None:
    """A generated status label alone must not certify that evidence exists."""
    design["pages"][1]["status"] = "grounded"
    report = checker.inspect_design(design)
    assert not report["structural_ok"]
    assert "unsupported_grounding" in {e["code"] for e in report["errors"]}


@pytest.mark.parametrize("source,code", [("policies/absent.md", "unknown_source"),
                                        ("policies/release.md#Invented", "unknown_anchor")])
def test_invented_source_or_anchor_fails(design: dict[str, Any], source: str, code: str) -> None:
    """A plausible-looking reference is rejected unless it resolves to inspected evidence."""
    design["pages"][0]["sources"] = [source]
    report = checker.inspect_design(design)
    assert not report["structural_ok"]
    assert code in {e["code"] for e in report["errors"]}


@pytest.mark.parametrize("path", ["../outside.md", "/outside.md", "a/../outside.md", "a//page.md"])
def test_unsafe_or_ambiguous_output_paths_fail(design: dict[str, Any], path: str) -> None:
    """The inspector must not bless a page path the writer could interpret differently."""
    design["pages"][0]["path"] = path
    assert "invalid_path" in {e["code"] for e in checker.inspect_design(design)["errors"]}


def test_repeated_purpose_is_reviewable_but_duplicate_path_is_error(design: dict[str, Any]) -> None:
    """Page-boundary ambiguity is distinguished from a colliding output file."""
    design["pages"][1]["purpose"] = design["pages"][0]["purpose"]
    report = checker.inspect_design(design)
    assert report["structural_ok"]
    assert "repeated_purpose" in {e["code"] for e in report["warnings"]}
    design["pages"][1]["path"] = design["pages"][0]["path"]
    assert not checker.inspect_design(design)["structural_ok"]


def test_root_namespace_collision_fails_before_mapping(design: dict[str, Any], tmp_path: Path) -> None:
    """Two repositories named docs cannot safely share the source-label namespace."""
    other = tmp_path / "other/policies"
    other.mkdir(parents=True)
    design["sources"].append(str(other))
    assert checker.inspect_design(design)["errors"][0]["code"] == "ambiguous_namespace"


def test_stage0_report_works_and_failed_run_is_not_blessed(design: dict[str, Any]) -> None:
    """Support stored proposals directly, while preserving their failure status."""
    report = {"best": {"pages": design["pages"]}, "sources": design["sources"]}
    assert checker.inspect_design(report)["structural_ok"]
    report["parse_failed"] = True
    assert not checker.inspect_design(report)["structural_ok"]


def test_cli_writes_nothing_and_never_invokes_model(design: dict[str, Any], tmp_path: Path) -> None:
    """The actual command resolves evidence without creating runs, caches or wiki files."""
    path = tmp_path / "design.json"
    path.write_text(json.dumps(design))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run([sys.executable, str(SCRIPT), "--design", str(path)], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["structural_ok"]
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
