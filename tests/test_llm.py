"""llm.generate 견고성 테스트 (SEA-12).

실제 CLI를 호출하지 않는다 — subprocess.run/shutil.which를 monkeypatch로 대체.
"""

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


def test_text_only_disables_builtin_tools_mcp_and_skills(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _proc("{}"))
    assert llm.generate("p", text_only=True) == "{}"
    cmd = calls[0]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--disable-slash-commands" in cmd


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


def test_stats_recording(monkeypatch, tmp_path):
    """LLM_STATS_PATH가 설정되면 호출별 jsonl이 남고, 없으면 아무 일도 없다."""
    import json
    stats = tmp_path / "stats.jsonl"
    monkeypatch.setattr(llm, "STATS_PATH", str(stats))
    monkeypatch.setattr(llm, "_generate_claude", lambda p, timeout=300, **kw: "요약 결과")
    llm.generate("문서를 요약하라.\n\n요약:")
    rec = json.loads(stats.read_text().splitlines()[0])
    assert rec["kind"] == "summarize" and rec["ok"] is True
    assert rec["prompt_chars"] > 0 and rec["out_chars"] == len("요약 결과")

    monkeypatch.setattr(llm, "STATS_PATH", "")
    llm.generate("아무 프롬프트")  # 경로 미설정 — 예외 없이 그냥 지나가야 한다


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda cmd, **kw: calls.append((cmd, kw)) or _proc("응답"))
    return calls


def test_default_call_is_isolated_from_user_environment(monkeypatch):
    """기본 호출은 Claude Code 환경(시스템 프롬프트·도구·MCP·스킬·전역 설정)을 싣지 않고 세션도 남기지 않는다."""
    monkeypatch.setattr(llm, "ISOLATE", True)
    monkeypatch.setattr(llm, "EFFORT", "")
    calls = _capture(monkeypatch)
    assert llm.generate("p") == "응답"
    cmd, kw = calls[0]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--disable-slash-commands" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "project"
    assert cmd[cmd.index("--system-prompt") + 1] == llm._SYSTEM_PROMPT
    assert kw["cwd"] == llm._NEUTRAL_CWD            # 프로젝트 CLAUDE.md가 발견되지 않는 폴더
    assert "--effort" not in cmd


def test_isolation_can_be_disabled_for_debugging(monkeypatch):
    monkeypatch.setattr(llm, "ISOLATE", False)
    monkeypatch.setattr(llm, "EFFORT", "")
    calls = _capture(monkeypatch)
    llm.generate("p")
    cmd, kw = calls[0]
    assert cmd == ["claude", "-p", "--model", llm.CLAUDE_MODEL] and kw["cwd"] is None
    llm.generate("p", text_only=True)              # 격리를 꺼도 text_only는 예전처럼 도구를 끈다
    assert "--disable-slash-commands" in calls[1][0] and "--system-prompt" not in calls[1][0]


def test_effort_per_call_overrides_env_default(monkeypatch):
    monkeypatch.setattr(llm, "ISOLATE", True)
    monkeypatch.setattr(llm, "EFFORT", "medium")
    calls = _capture(monkeypatch)
    llm.generate("p")
    llm.generate("p", effort="low")
    assert calls[0][0][calls[0][0].index("--effort") + 1] == "medium"
    assert calls[1][0][calls[1][0].index("--effort") + 1] == "low"


def test_evaluation_calls_request_low_effort(monkeypatch):
    """라우팅·답변·판정은 effort low — 판정 프롬프트 기준 thinking 2,576→450 토큰, 20→6초 (2026-09-24 측정)."""
    import audit
    import scoring
    import structure
    efforts = []
    monkeypatch.setattr(llm, "generate", lambda prompt, **kw: efforts.append(kw.get("effort")) or "[1]")
    scoring.judge_all([{"q": "q", "a": "a"}], ["a"])
    structure.route("q", [{"title": "A", "desc": "a"}])
    structure._answer("ctx", "q")
    audit.route_batch(["q"], [{"title": "A", "desc": "a"}])
    assert efforts and all(e == "low" for e in efforts)
