"""llm.generate 견고성 테스트 (SEA-12).

실제 CLI를 호출하지 않는다 — subprocess.run/shutil.which를 monkeypatch로 대체.
"""

import subprocess
from types import SimpleNamespace

import pytest

import llm


@pytest.fixture(autouse=True)
def _claude_backend_no_sleep(monkeypatch):
    monkeypatch.setattr(llm, "BACKEND", "claude")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.shutil, "which", lambda cmd: "/usr/bin/" + cmd)


def _proc(stdout="ok", rc=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=rc, stderr=stderr)


def test_generate_returns_output(monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: _proc("응답"))
    assert llm.generate("p") == "응답"


def test_missing_cli_fails_immediately_without_retry(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda cmd: None)
    calls = []
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: calls.append(1))
    with pytest.raises(llm.LLMError, match="찾을 수 없음"):
        llm.generate("p", retries=2)
    assert calls == []  # 미설치는 재시도 없이 즉시 실패


def test_empty_output_retries_then_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm.subprocess, "run", lambda *a, **k: calls.append(1) or _proc("")
    )
    with pytest.raises(llm.LLMError, match="빈 응답"):
        llm.generate("p", retries=2)
    assert len(calls) == 3  # 빈 응답은 재시도 경로를 탄다


def test_empty_then_success_recovers(monkeypatch):
    procs = iter([_proc(""), _proc("회복됨")])
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: next(procs))
    assert llm.generate("p", retries=2) == "회복됨"


def test_oserror_is_retried_and_wrapped(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(llm.subprocess, "run", boom)
    with pytest.raises(llm.LLMError):
        llm.generate("p", retries=1)


def test_health_check_false_when_cli_missing(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda cmd: None)
    assert llm.health_check() is False


def test_health_check_false_on_unexpected_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("unexpected")
    monkeypatch.setattr(llm, "generate", boom)
    assert llm.health_check() is False


def test_nonzero_rc_still_raises(monkeypatch):
    monkeypatch.setattr(
        llm.subprocess, "run", lambda *a, **k: _proc("", rc=1, stderr="err")
    )
    with pytest.raises(llm.LLMError, match="claude CLI 실패"):
        llm.generate("p", retries=0)
