"""Inspect wiki design structure and source references without LLM calls or writes.

Accept a design.json containing pages, or an existing Stage 0 report.json.
Exit 0 means no structural/reference errors; it is not a semantic quality verdict.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

import repo_map


def inspect_design(design: dict[str, Any], roots: Optional[list[str]] = None,
                   max_files: int = 400) -> dict[str, Any]:
    """Check page paths, declared grounding and source/anchor references.

    Args:
        design: Page design or Stage 0 report loaded from JSON.
        roots: Explicit source directories, overriding design.sources if supplied.
        max_files: Maximum mapped files; missing references are relative to this map.

    Returns:
        JSON-ready diagnostics, with no mutation of the supplied design or sources.
    """
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    gaps: list[str] = []

    def issue(code: str, page: str, message: str, warning: bool = False) -> None:
        (warnings if warning else errors).append({"code": code, "page": page, "message": message})

    def result(mapped_files: int, page_count: int) -> dict[str, Any]:
        return {"structural_ok": not errors, "page_count": page_count,
                "mapped_files": mapped_files, "map_limit": max_files,
                "gap_pages": gaps, "errors": errors, "warnings": warnings,
                "not_evaluated": ["semantic support of page claims", "question coverage",
                                  "actual reader navigation", "finished page contents"]}

    if not isinstance(design, dict):
        issue("invalid_design", "", "Design must be a JSON object")
        return result(0, 0)
    best = design.get("best")
    pages = design.get("pages", best.get("pages") if isinstance(best, dict) else None)
    if not isinstance(pages, list) or not pages:
        issue("missing_pages", "", "Expected a nonempty pages or best.pages array")
        return result(0, 0)
    if design.get("parse_failed"):
        issue("failed_run", "", "The source report marks evaluation/parsing as failed")
    source_roots = roots if roots is not None else design.get("sources", [])
    if not isinstance(source_roots, list) or any(not isinstance(p, str) or not p.strip() for p in source_roots):
        issue("invalid_roots", "", "Sources must be a list of directory paths")
        return result(0, len(pages))
    expanded = [Path(os.path.abspath(Path(p).expanduser())) for p in source_roots]
    if len({p.name for p in expanded}) != len(expanded):
        issue("ambiguous_namespace", "", "Source roots share a basename; repo_map references would be ambiguous")
    for root in expanded:
        if not root.is_dir():
            issue("missing_root", "", f"Source directory does not exist: {root}")
    if errors:
        return result(0, len(pages))
    entries = repo_map.build_map([str(p) for p in expanded], max_files=max_files, use_cache=False)
    by_rel = {entry["rel"]: entry for entry in entries}
    if len(entries) >= max_files:
        issue("map_may_be_capped", "", "Source map reached the file limit; absence is not proof a source does not exist", True)

    seen: set[str] = set()
    purposes: dict[str, str] = {}
    for index, page in enumerate(pages):
        label = f"page[{index}]"
        if not isinstance(page, dict):
            issue("invalid_page", label, "Page must be an object")
            continue
        path = page.get("path")
        if not isinstance(path, str) or not path.strip():
            issue("invalid_path", label, "Page path is required")
            continue
        label = path
        normalized = str(PurePosixPath(path))
        if (PurePosixPath(path).is_absolute() or ".." in path.split("/")
                or not path.endswith(".md") or normalized != path
                or re.search(r"[\\\x00-\x1f:*?<>|]", path)):
            issue("invalid_path", label, "Use a normalized relative .md path without traversal or reserved characters")
        if normalized in seen:
            issue("duplicate_path", label, "Another page has the same normalized path")
        seen.add(normalized)
        for field in ("title", "purpose"):
            if not isinstance(page.get(field), str) or not page[field].strip():
                issue(f"missing_{field}", label, f"Nonempty {field} is required")
        purpose = re.sub(r"\s+", " ", str(page.get("purpose", "")).strip()).casefold()
        if purpose:
            if purpose in purposes:
                issue("repeated_purpose", label, f"Same purpose as {purposes[purpose]}; review the page boundary", True)
            purposes[purpose] = path
        outline = page.get("outline")
        if not isinstance(outline, list) or any(not isinstance(s, str) or not s.strip() for s in outline):
            issue("invalid_outline", label, "Outline must be a list of nonempty section headings")
        elif not outline:
            issue("empty_outline", label, "No sections planned; inspect whether the page can answer its purpose", True)
        sources = page.get("sources")
        if not isinstance(sources, list) or any(not isinstance(s, str) or not s.strip() for s in sources):
            issue("invalid_sources", label, "Page sources must be an array of source references")
            continue
        status = page.get("status")
        if status not in ("grounded", "gap"):
            issue("invalid_status", label, "Status must be grounded or gap")
        if not sources:
            gaps.append(path)
            if status == "grounded":
                issue("unsupported_grounding", label, "grounded page has no source references")
        elif status == "gap":
            issue("gap_with_sources", label, "Gap page has linked sources; explain which needed evidence is still missing", True)
        for spec in sources:
            relative, marker, anchor = spec.partition("#")
            entry = by_rel.get(relative)
            if entry is None:
                issue("unknown_source", label, f"Not found in the inspected source map: {relative}")
            elif marker and (not anchor or repo_map.find_anchor(entry, anchor) is None):
                issue("unknown_anchor", label, f"Section anchor does not exist in the inspected source: {spec}")
    if design.get("scoreable") is False:
        issue("unscored", "", "No supported question set was scored; present this as an unscored draft", True)
    return result(len(entries), len(pages))


def main() -> int:
    """Print diagnostics as JSON and return a structural-validation exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, help="design.json or a Stage 0 report.json")
    parser.add_argument("--source", action="append", help="Source root; repeat for multiple roots")
    parser.add_argument("--max-files", type=int, default=400)
    args = parser.parse_args()
    if args.max_files < 1:
        parser.error("--max-files must be positive")
    try:
        design = json.loads(Path(args.design).expanduser().read_text(encoding="utf-8"))
        report = inspect_design(design, roots=args.source, max_files=args.max_files)
    except (OSError, ValueError) as exc:
        print(json.dumps({"structural_ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["structural_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
