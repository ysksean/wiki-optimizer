"""로컬 Qwen(Ollama) 클라이언트.

Ollama HTTP API를 표준 라이브러리(urllib)만으로 호출한다. 추가 의존성 없음.
thinking 모드를 끄면(think=False) 응답이 극적으로 빨라지고 출력이 깔끔하다.
(검증: 짧은 프롬프트 기준 1m33s -> 2.3s)
"""

import json
import time
import urllib.request
import urllib.error

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen38-local"


class LLMError(RuntimeError):
    pass


def generate(
    prompt,
    *,
    model=DEFAULT_MODEL,
    temperature=0.2,
    num_predict=512,
    think=False,
    timeout=300,
    retries=2,
):
    """Qwen에 프롬프트를 보내고 응답 텍스트를 반환한다.

    실패 시 retries만큼 재시도한다. 최종 실패하면 LLMError.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": think,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    data = json.dumps(payload).encode("utf-8")

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                OLLAMA_URL, data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
            return body.get("response", "").strip()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
            continue
    raise LLMError(f"Ollama 호출 실패 ({retries + 1}회 시도): {last_err}")


def health_check():
    """모델이 응답 가능한지 빠르게 확인. (True/False)"""
    try:
        out = generate("Reply with the single word: ready", num_predict=8, timeout=60)
        return "ready" in out.lower()
    except LLMError:
        return False


if __name__ == "__main__":
    # 직접 실행하면 헬스체크 + 속도 측정
    t = time.time()
    ok = health_check()
    print(f"health_check: {ok}  ({time.time() - t:.1f}s)")
