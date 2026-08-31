"""harness/self_update.sh — 임시 bare origin + clone으로 자가 갱신 동작을 검증한다 (네트워크 불필요)."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "harness" / "self_update.sh"
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    ).stdout.strip()


def commit_file(repo: Path, name: str, content: str, msg: str) -> str:
    (repo / name).write_text(content)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def run_self_update(repo: Path) -> str:
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(repo)],
        check=False,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """(local, upstream) — local은 upstream을 origin으로 clone한 master 체크아웃.
    upstream은 별도 작업 clone으로, 여기서 커밋하고 bare origin에 push해 '뒤처짐'을 만든다."""
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(bare)], check=True
    )

    upstream = tmp_path / "upstream"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(upstream)],
        check=True,
        capture_output=True,
    )
    git(upstream, "checkout", "-q", "-b", "master")
    commit_file(upstream, "poll.sh", "v1\n", "init")
    git(upstream, "push", "-q", "-u", "origin", "master")

    local = tmp_path / "local"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(local)], check=True, capture_output=True
    )
    return local, upstream


def test_clean_master_behind_is_fast_forwarded(repos: tuple[Path, Path]) -> None:
    local, upstream = repos
    new_head = commit_file(upstream, "poll.sh", "v2\n", "harness change")
    git(upstream, "push", "-q", "origin", "master")

    out = run_self_update(local)

    assert out.startswith("self-update ok"), out
    assert git(local, "rev-parse", "HEAD") == new_head
    assert (local / "poll.sh").read_text() == "v2\n"


def test_already_up_to_date(repos: tuple[Path, Path]) -> None:
    local, _ = repos
    head = git(local, "rev-parse", "HEAD")

    out = run_self_update(local)

    assert "already up to date" in out, out
    assert git(local, "rev-parse", "HEAD") == head


def test_dirty_tree_is_skipped_without_touching_files(repos: tuple[Path, Path]) -> None:
    local, upstream = repos
    old_head = git(local, "rev-parse", "HEAD")
    commit_file(upstream, "poll.sh", "v2\n", "harness change")
    git(upstream, "push", "-q", "origin", "master")
    (local / "poll.sh").write_text("local edit\n")

    out = run_self_update(local)

    assert "skipped" in out and "dirty" in out, out
    assert git(local, "rev-parse", "HEAD") == old_head
    assert (local / "poll.sh").read_text() == "local edit\n"


def test_untracked_files_do_not_block_update(repos: tuple[Path, Path]) -> None:
    local, upstream = repos
    new_head = commit_file(upstream, "poll.sh", "v2\n", "harness change")
    git(upstream, "push", "-q", "origin", "master")
    (local / "scratch.txt").write_text("untracked\n")

    out = run_self_update(local)

    assert out.startswith("self-update ok"), out
    assert git(local, "rev-parse", "HEAD") == new_head
    assert (local / "scratch.txt").exists()


def test_other_branch_is_skipped(repos: tuple[Path, Path]) -> None:
    local, upstream = repos
    old_head = git(local, "rev-parse", "HEAD")
    commit_file(upstream, "poll.sh", "v2\n", "harness change")
    git(upstream, "push", "-q", "origin", "master")
    git(local, "checkout", "-q", "-b", "feature/x")

    out = run_self_update(local)

    assert "skipped" in out and "feature/x" in out, out
    assert git(local, "rev-parse", "HEAD") == old_head
    assert git(local, "symbolic-ref", "--short", "HEAD") == "feature/x"


def test_diverged_local_commit_fails_softly(repos: tuple[Path, Path]) -> None:
    local, upstream = repos
    commit_file(upstream, "poll.sh", "v2\n", "upstream change")
    git(upstream, "push", "-q", "origin", "master")
    local_head = commit_file(local, "other.txt", "mine\n", "local commit")

    out = run_self_update(local)

    assert out.startswith("self-update failed"), out
    assert git(local, "rev-parse", "HEAD") == local_head
