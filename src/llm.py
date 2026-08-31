"""LLM 클라이언트 — 개인 구독 CLI(claude/codex)로 호출한다.

API 키가 아니라 **각자 로그인해둔 CLI 세션**으로 동작한다.
백엔드 선택: 환경변수 LLM_BACKEND=claude|codex (기본 claude).

- claude: `claude -p` 헤드리스 호출 (Claude Code 구독 로그인).
- codex:  `codex exec` 헤드리스 호출 (ChatGPT 구독 로그인).

temperature/num_predict 등은 CLI가 지원하지 않아 무시된다
(호출부 호환을 위해 인자만 받는다).
"""

import os
import shutil
import subprocess
import tempfile
import time

BACKEND = os.environ.get("LLM_BACKEND", "claude")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
CODEX_MODEL = os.environ.get("CODEX_MODEL", "")  # 비우면 codex 기본 모델

# 출력 언어. 모든 프롬프트 앞에 지시문을 중앙 주입한다 (ko|en|zh).
LANGUAGE = os.environ.get("LLM_LANG", "ko")
_LANG_DIRECTIVES = {
    "ko": "모든 출력(요약·질문·답변·전략)은 한국어로 작성하라. JSON 형식 지시는 그대로 따르라.",
    "en": "Write all output (summaries, questions, answers, strategies) in English. Still follow any JSON format instructions exactly.",
    "zh": "所有输出（摘要、问题、答案、策略）请使用中文。JSON 格式要求仍需严格遵守。",
}


class LLMError(RuntimeError):
    pass


def generate(
    prompt,
    *,
    model=None,
    temperature=0.2,
    num_predict=512,
    think=False,
    timeout=300,
    retries=2,
):
    """프롬프트를 보내고 응답 텍스트를 반환한다.

    실패 시 retries만큼 재시도한다. 최종 실패하면 LLMError.
    """
    if BACKEND not in ("claude", "codex"):
        raise LLMError(f"지원하지 않는 백엔드: {BACKEND} (claude|codex)")
    if shutil.which(BACKEND) is None:
        # 미설치는 재시도해도 소용없다 — 즉시 실패
        raise LLMError(f"{BACKEND} CLI를 찾을 수 없음 (미설치 또는 PATH 누락)")
    directive = _LANG_DIRECTIVES.get(LANGUAGE)
    if directive:
        prompt = f"[언어 지시] {directive}\n\n{prompt}"
    last_err = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            if BACKEND == "codex":
                out = _generate_codex(prompt, timeout=timeout)
            else:
                out = _generate_claude(prompt, timeout=timeout)
            _record_stat(prompt, out, time.time() - t0, attempt, ok=True)
            return out
        except (subprocess.SubprocessError, OSError) as e:
            _record_stat(prompt, "", time.time() - t0, attempt, ok=False)
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
            continue
    raise LLMError(f"LLM 호출 실패 (백엔드={BACKEND}, {retries + 1}회 시도): {last_err}")


# 옵트인 호출 계측 — LLM_STATS_PATH가 설정돼 있으면 호출별 jsonl 한 줄 append.
# 어디가 비싼지는 추측하지 않고 잰다. 실패해도 본 호출에 영향 없음.
STATS_PATH = os.environ.get("LLM_STATS_PATH", "")


def _record_stat(prompt, out, elapsed, attempt, ok):
    if not STATS_PATH:
        return
    try:
        import json as _json
        rec = {"ts": round(time.time(), 1), "backend": BACKEND,
               "elapsed_sec": round(elapsed, 2), "prompt_chars": len(prompt),
               "out_chars": len(out), "attempt": attempt, "ok": ok,
               "kind": _prompt_kind(prompt)}
        with open(STATS_PATH, "a") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _prompt_kind(prompt):
    """프롬프트 말미 마커로 호출 종류를 분류한다 (계측 라벨용, 로직에 미사용)."""
    tail = prompt.rstrip()[-30:]
    for marker, kind in (("요약:", "summarize"), ("답변 배열:", "answer"),
                         ("판정 배열:", "judge"), ("전략 프롬프트]:", "reflect")):
        if tail.endswith(marker):
            return kind
    return "other"


def _generate_claude(prompt, timeout=300):
    proc = subprocess.run(
        ["claude", "-p", "--model", CLAUDE_MODEL],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise subprocess.SubprocessError(
            f"claude CLI 실패 (rc={proc.returncode}): {proc.stderr.strip()[:200]}"
        )
    out = proc.stdout.strip()
    if not out:
        # rc=0이어도 빈 출력(사용량 한도, 세션 만료 등)은 실패다 — 재시도 경로로
        raise subprocess.SubprocessError("claude CLI 빈 응답 (rc=0)")
    return out


def _generate_codex(prompt, timeout=300):
    """codex exec 헤드리스 호출. 마지막 메시지를 파일로 받아 읽는다."""
    with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=False) as tf:
        out_path = tf.name
    try:
        cmd = [
            "codex", "exec",
            "--skip-git-repo-check", "--ephemeral",
            "-s", "read-only",
            "--color", "never",
            "-o", out_path,
            "-",  # 프롬프트는 stdin으로
        ]
        if CODEX_MODEL:
            cmd[2:2] = ["-m", CODEX_MODEL]
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=timeout
        )
        if proc.returncode != 0:
            raise subprocess.SubprocessError(
                f"codex CLI 실패 (rc={proc.returncode}): {proc.stderr.strip()[:200]}"
            )
        with open(out_path) as f:
            out = f.read().strip()
        if not out:
            raise subprocess.SubprocessError("codex CLI 빈 응답 (rc=0)")
        return out
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def health_check():
    """모델이 응답 가능한지 빠르게 확인. (True/False)"""
    try:
        out = generate("Reply with the single word: ready", timeout=120)
        return "ready" in out.lower()
    except Exception:
        # 진단 함수는 어떤 상황에서도 크래시 대신 False를 반환한다
        return False


if __name__ == "__main__":
    # 직접 실행하면 헬스체크 + 속도 측정
    t = time.time()
    ok = health_check()
    print(f"health_check[{BACKEND}]: {ok}  ({time.time() - t:.1f}s)")
