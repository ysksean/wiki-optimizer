"""Incremental update integration checks with deterministic LLM responses."""

import json
import re
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import incremental as inc
import web


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Provide original sources, two wiki pages and deterministic question/answer behavior."""
    root = tmp_path / "kb"
    (root / "raw").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "raw/old.md").write_text("Old fact: blue.")
    (root / "wiki/topic.md").write_text("# Topic\nOld fact: blue.\n")
    (root / "wiki/unrelated.md").write_bytes(b"# Unrelated\r\nPreserve my bytes.\r\n")
    source = tmp_path / "new.md"
    source.write_text("New fact: green.")
    env = {"root": root, "source": source, "out": tmp_path / "run", "regress": False,
           "new_fail": False, "conflicts": [], "no_match": False, "prompts": []}

    def fake_json(prompt: str) -> Any:
        env["prompts"].append(prompt)
        if prompt.startswith("Create source-grounded"):
            sources = json.loads(prompt.split("\nSources: ")[1])
            path, text = next(iter(sources.items()))
            count = int(re.search(r"exactly (\d+) questions", prompt).group(1))
            return [{"q": f"{text} Question {i}?", "a": text, "source": path, "evidence": text} for i in range(count)]
        if prompt.startswith("Select only existing"):
            return {"affected": [] if env["no_match"] else [{"path": "wiki/topic.md", "reason": "Adds a related fact"}]}
        if prompt.startswith("Update only"):
            selected = json.loads(prompt.split("\nPages: ")[1].split("\nImpact reasons: ")[0])
            return {"pages": [{"path": p, "content": ("" if env["regress"] else text) + "\nNew fact: green.\n"}
                              for p, text in selected.items()], "conflicts": env["conflicts"]}
        if prompt.startswith("Select up to 3"):
            index = json.loads(prompt.split("Index: ")[1].split("\nQuestion: ")[0])
            question = prompt.split("\nQuestion: ")[1]
            return [p["path"] for p in index if ("New fact:" if "New fact:" in question else "Old fact:") in p["description"]][:3]
        if prompt.startswith("Answer solely"):
            context = prompt.split("Context: ")[1].split("\nQuestion: ")[0]
            question = prompt.split("\nQuestion: ")[1]
            fact = "New fact: green." if "New fact:" in question else "Old fact: blue."
            return {"answer": fact if fact in context and not (env["new_fail"] and fact.startswith("New")) else "모름"}
        raise AssertionError(prompt)

    monkeypatch.setattr(inc, "_json_response", fake_json)
    monkeypatch.setattr(inc.scoring, "judge_all", lambda qs, preds, **kw: ([float(q["a"] == p) for q, p in zip(qs, preds)], False))
    return env


def prepare(env: dict[str, Any]) -> dict[str, Any]:
    """Prepare the fixture's one-document candidate."""
    return inc.prepare_update(str(env["root"]), str(env["source"]), str(env["out"]), n_qa=2)


def test_prepare_apply_undo_preserves_unrelated_bytes_and_originals(workspace: dict[str, Any]) -> None:
    """Preparation is read-only; apply imports the source; undo restores previous contents."""
    # Given
    root, out = workspace["root"], workspace["out"]
    original = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.md")}
    # When
    report = prepare(workspace)
    # Then
    assert report["status"] == "ready"
    assert report["unchanged_pages"] == 1
    assert [c["path"] for c in report["changes"]] == ["wiki/topic.md"]
    assert original == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.md")}
    assert not (root / ".wiki-optimizer").exists()
    inc.commit_update(str(out))
    assert (root / "raw/new.md").read_text() == "New fact: green."
    assert (root / "wiki/unrelated.md").read_bytes() == original["wiki/unrelated.md"]
    assert len(json.loads((root / inc.STATE).read_text())["questions"]) == 4
    assert (out / "backup/wiki/topic.md").read_bytes() == original["wiki/topic.md"]
    assert inc.commit_update(str(out), undo=True)["status"] == "rolled_back"
    assert original == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*.md")}
    assert not (root / inc.STATE).exists()


@pytest.mark.parametrize("flag", ["regress", "new_fail", "conflicts"])
def test_failed_checks_block_application(workspace: dict[str, Any], flag: str) -> None:
    """Regression, missed new information and reported conflicts each block writes."""
    workspace[flag] = ["Contradiction"] if flag == "conflicts" else True
    report = prepare(workspace)
    assert report["status"] == "blocked"
    with pytest.raises(ValueError, match="status blocked"):
        inc.commit_update(str(workspace["out"]))
    assert not (workspace["root"] / "raw/new.md").exists()


@pytest.mark.parametrize("target", ["wiki/topic.md", "wiki/unrelated.md", "raw/old.md", "wiki/added.md", inc.STATE])
def test_apply_rejects_stale_workspace(workspace: dict[str, Any], target: str) -> None:
    """Any source, wiki or baseline change invalidates the evaluated snapshot."""
    prepare(workspace)
    path = workspace["root"] / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("User edit")
    with pytest.raises(ValueError, match="changed"):
        inc.commit_update(str(workspace["out"]))
    assert path.read_text() == "User edit"


@pytest.mark.parametrize("kind", ["source", "candidate"])
def test_apply_rejects_modified_inputs(workspace: dict[str, Any], kind: str) -> None:
    """Changing incoming content or staged content requires fresh validation."""
    prepare(workspace)
    path = workspace["source"] if kind == "source" else workspace["out"] / "candidate/wiki/topic.md"
    path.write_text("tampered")
    with pytest.raises(ValueError, match="changed"):
        inc.commit_update(str(workspace["out"]))


def test_second_addition_reuses_questions(workspace: dict[str, Any]) -> None:
    """Accepted question suites persist into the next one-document update."""
    prepare(workspace)
    inc.commit_update(str(workspace["out"]))
    workspace["out"] = workspace["out"].parent / "second-run"
    second = workspace["source"].parent / "second.md"
    second.write_text("New fact: green.")
    workspace["source"] = second
    report = prepare(workspace)
    assert report["existing_total"] == 4
    assert report["new_total"] == 2
    assert sum(p.startswith("Create source-grounded") for p in workspace["prompts"]) == 3


def test_no_matching_page_creates_only_one_new_page(workspace: dict[str, Any]) -> None:
    """Unrelated source gets a new page without rewriting existing pages."""
    workspace["no_match"] = True
    report = prepare(workspace)
    assert report["status"] == "ready"
    assert len(report["changes"]) == 1
    assert report["changes"][0]["path"].startswith("wiki/updates/")
    assert report["unchanged_pages"] == 2


def test_questions_never_reach_writer(workspace: dict[str, Any]) -> None:
    """Candidate generation is independent of the final evaluation questions."""
    prepare(workspace)
    prompts = [p for p in workspace["prompts"] if p.startswith(("Select only", "Update only"))]
    assert all("Question 0?" not in p and "Question 1?" not in p for p in prompts)


def test_judge_failure_produces_no_applicable_report(workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken judge is never interpreted as a valid score."""
    monkeypatch.setattr(inc.scoring, "judge_all", lambda qs, preds, **kw: ([0.0] * len(qs), True))
    with pytest.raises(ValueError, match="Judge failed"):
        prepare(workspace)
    assert not (workspace["out"] / "report.json").exists()


def test_write_failure_rolls_back_all_touched_files(workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure midway through applying a candidate restores earlier writes."""
    prepare(workspace)
    original = (workspace["root"] / "wiki/topic.md").read_bytes()
    atomic = inc._atomic
    failed = False

    def fail_once(path: Path, content: str) -> None:
        nonlocal failed
        if path == workspace["root"] / "raw/new.md" and not failed:
            failed = True
            raise OSError("disk failure")
        atomic(path, content)

    monkeypatch.setattr(inc, "_atomic", fail_once)
    with pytest.raises(OSError, match="disk failure"):
        inc.commit_update(str(workspace["out"]))
    assert (workspace["root"] / "wiki/topic.md").read_bytes() == original
    assert not (workspace["root"] / "raw/new.md").exists()
    assert not (workspace["root"] / ".wiki-optimizer/pending.json").exists()
    assert json.loads((workspace["out"] / "report.json").read_text())["status"] == "ready"


def test_undo_refuses_to_overwrite_later_edits(workspace: dict[str, Any]) -> None:
    """Undo must preserve changes made after applying this update."""
    prepare(workspace)
    inc.commit_update(str(workspace["out"]))
    (workspace["root"] / "wiki/topic.md").write_text("Later user edit")
    with pytest.raises(ValueError, match="changed"):
        inc.commit_update(str(workspace["out"]), undo=True)


def test_source_already_in_raw_is_preserved_on_undo(workspace: dict[str, Any]) -> None:
    """A document placed in raw by the user must not be deleted by undo."""
    source = workspace["root"] / "raw/new.md"
    source.write_bytes(workspace["source"].read_bytes())
    workspace["source"] = source
    prepare(workspace)
    inc.commit_update(str(workspace["out"]))
    inc.commit_update(str(workspace["out"]), undo=True)
    assert source.read_text() == "New fact: green."


def test_existing_source_collision_fails_before_model_calls(workspace: dict[str, Any]) -> None:
    """An import cannot overwrite a different raw file sharing its name."""
    (workspace["root"] / "raw/new.md").write_text("Different original")
    with pytest.raises(ValueError, match="different source"):
        prepare(workspace)
    assert workspace["prompts"] == []


def test_pending_transaction_blocks_further_writes(workspace: dict[str, Any]) -> None:
    """A crash journal must not be ignored when a user clicks Apply again."""
    prepare(workspace)
    journal = workspace["root"] / ".wiki-optimizer/pending.json"
    journal.parent.mkdir(parents=True)
    journal.write_text('{}')
    with pytest.raises(ValueError, match="interrupted update"):
        inc.commit_update(str(workspace["out"]))


def test_preparing_again_cannot_replace_an_applied_report(workspace: dict[str, Any]) -> None:
    """A saved report is the undo record and must survive later preparations."""
    prepare(workspace)
    original = (workspace["out"] / "report.json").read_bytes()
    with pytest.raises(ValueError, match="already contains"):
        prepare(workspace)
    assert (workspace["out"] / "report.json").read_bytes() == original


def test_missing_raw_baseline_rejects_unverifiable_existing_wiki(workspace: dict[str, Any]) -> None:
    """Existing pages cannot be checked against nonexistent original evidence."""
    (workspace["root"] / "raw/old.md").unlink()
    with pytest.raises(ValueError, match="original raw sources"):
        prepare(workspace)


def test_modified_incorporated_source_requires_separate_reconciliation(workspace: dict[str, Any]) -> None:
    """A later addition cannot silently forget questions after source replacement."""
    prepare(workspace)
    inc.commit_update(str(workspace["out"]))
    (workspace["root"] / "raw/old.md").write_text("Replacement")
    workspace["out"] = workspace["out"].parent / "later"
    with pytest.raises(ValueError, match="sources changed"):
        prepare(workspace)


@pytest.mark.parametrize("relative", ["../escape.md", "/tmp/escape.md", "wiki/../../escape.md"])
def test_path_traversal_rejected(tmp_path: Path, relative: str) -> None:
    """Mutation paths cannot escape the workspace."""
    with pytest.raises(ValueError):
        inc._safe(tmp_path, relative)


def test_symlink_page_rejected(workspace: dict[str, Any]) -> None:
    """Symlinks cannot turn an affected-page write into an external write."""
    (workspace["root"] / "wiki/link.md").symlink_to(workspace["source"])
    with pytest.raises(ValueError, match="Symlinks"):
        prepare(workspace)


def test_invalid_evidence_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The question oracle must cite an actual passage in raw data."""
    monkeypatch.setattr(inc, "_json_response", lambda p: [{"q": "q", "a": "a", "source": "raw/a.md", "evidence": "invented"}])
    with pytest.raises(ValueError, match="evidence"):
        inc._questions({"raw/a.md": "real"}, 1, "")


@pytest.mark.parametrize("response", ['{"answer":"ok"}\nExplanation follows.', '```json\n{"answer":"ok"}\n```\n설명', 'Here is the JSON:\n{"answer":"ok"}'])
def test_json_with_prose_is_supported(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    """CLI formatting prose must not discard otherwise valid structured output."""
    monkeypatch.setattr(inc.llm, "generate", lambda *a, **kw: response)
    assert inc._json_response("prompt") == {"answer": "ok"}


@pytest.mark.parametrize("response", ['{"broken":}', '[]\n{}', 'not JSON'])
def test_broken_or_ambiguous_json_rejected(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    """Invalid structured answers stop the update instead of silently falling back."""
    monkeypatch.setattr(inc.llm, "generate", lambda *a, **kw: response)
    with pytest.raises(ValueError):
        inc._json_response("prompt")


def test_web_job_prepares_and_commits(workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The dashboard worker and commit endpoint use the same persisted report."""
    monkeypatch.setattr(web, "JOBS", {})
    monkeypatch.setattr(web, "CANCEL_EVENTS", {})
    monkeypatch.setattr(web, "JOBS_DIR", str(workspace["out"]))
    monkeypatch.setattr(web.threading.Thread, "start", lambda self: None)
    job, error = web.start_job({"mode": "incremental", "dir": str(workspace["root"]),
                                "source_file": str(workspace["source"]), "n_qa": 2})
    assert error is None
    web._run_job(job)
    assert job["status"] == "done", job.get("error")
    assert web.job_detail(job)["result"]["status"] == "ready"
    assert web.commit_incremental(job["id"])["status"] == "applied"
    assert web.job_detail(job)["result"]["status"] == "applied"
    assert web.commit_incremental(job["id"], undo=True)["status"] == "rolled_back"


@pytest.mark.parametrize("origin,content_type,expected", [(None, "application/json", 200),
                                                          ("https://unrelated.example", "application/json", 403),
                                                          (None, "text/plain", 403)])
def test_mutation_endpoint_requires_same_origin_json(monkeypatch: pytest.MonkeyPatch, origin: str,
                                                    content_type: str, expected: int) -> None:
    """An external website or HTML form cannot apply a local wiki update."""
    calls = []
    monkeypatch.setattr(web, "commit_incremental", lambda job, undo: calls.append(job) or {"status": "applied"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = HTTPConnection("127.0.0.1", server.server_port)
    try:
        headers = {"Content-Type": content_type}
        if origin:
            headers["Origin"] = origin
        connection.request("POST", "/api/runs/example/apply-update", body="{}", headers=headers)
        response = connection.getresponse()
        assert response.status == expected
        response.read()
        assert bool(calls) == (expected == 200)
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
