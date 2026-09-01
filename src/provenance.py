"""실행 재현성 메타데이터 — 결과가 '무엇으로' 만들어졌는지를 report에 남긴다.

실험 결과를 나중에 신뢰하려면 점수만으로는 부족하다: 어떤 백엔드/모델이,
어떤 코드 버전으로, 어떤 문서·질문 세트에 대해 낸 점수인지가 함께 남아야
재현·비교·감사가 가능하다. 이 모듈은 그 스냅샷 하나를 dict로 만든다.

전부 로컬 정보만 쓴다 — LLM·네트워크 호출 없음.
"""

import hashlib
import json
import os
import platform
import subprocess
import time

import llm

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))


def _git_sha():
    """이 코드의 git 커밋 (없으면 None — git 밖에서 돌려도 죽지 않는다)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=_SRC_DIR,
        )
    except OSError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _sha16(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def question_set_sha(question_set):
    """질문 세트의 내용 지문. 순서 무관 — 같은 세트면 같은 값."""
    canonical = json.dumps(sorted(question_set, key=json.dumps), ensure_ascii=False, sort_keys=True)
    return _sha16(canonical)


def collect(doc_text=None, question_set=None, params=None):
    """report.json에 넣을 provenance dict를 만든다."""
    if llm.BACKEND == "claude":
        model = llm.CLAUDE_MODEL
    else:
        model = llm.CODEX_MODEL or "codex-default"
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "code_sha": _git_sha(),
        "backend": llm.BACKEND,
        "model": model,
        "language": llm.LANGUAGE,
        "python": platform.python_version(),
        "doc_sha": _sha16(doc_text) if doc_text else None,
        "question_set_sha": question_set_sha(question_set) if question_set else None,
        "params": params or {},
    }
