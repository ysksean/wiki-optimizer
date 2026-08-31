"""웹 대시보드 서버 (stdlib만).

각자 wiki 폴더(.md 모음)를 지정해 A단계(요약 진화) / B단계(구조 진화)를
백그라운드로 실행하고, 진행 상황과 결과를 브라우저에서 본다.

  python3 src/web.py            # http://localhost:8765
  python3 src/web.py --port 9000

개인 로컬 도구다: 인증 없음, localhost 바인딩. 외부에 열지 말 것.
동시 실행은 1개로 제한한다 (llm.BACKEND 전역을 run마다 바꾸므로).
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import llm
import apply as apply_mod
import audit
import evolve
import evolve_structure

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
JOBS_DIR = "runs/web"

JOBS = {}          # job_id -> job dict
JOBS_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()  # 동시 실행 1개 제한


def list_docs(wiki_dir):
    """지정 폴더의 md 파일 목록. raw/ 하위 폴더가 있으면 그쪽 우선."""
    base = os.path.expanduser(wiki_dir)
    if not os.path.isdir(base):
        return None
    search = os.path.join(base, "raw") if os.path.isdir(os.path.join(base, "raw")) else base
    files = sorted(
        (f for f in glob.glob(os.path.join(search, "**", "*.md"), recursive=True)
         if os.path.basename(f).lower() != "readme.md"),
        key=os.path.getsize,
    )
    return [{"path": f, "name": os.path.splitext(os.path.basename(f))[0],
             "size": os.path.getsize(f)} for f in files]


def _run_job(job):
    """워커 스레드: 백엔드 설정 후 모드별 작업 실행."""
    with RUN_LOCK:
        job["status"] = "running"
        llm.BACKEND = job["backend"]
        llm.LANGUAGE = job.get("language", "ko")

        def flush_result(payload):
            with open(os.path.join(job["dir"], "result.json"), "w") as f:
                json.dump(payload, f, ensure_ascii=False)

        try:
            if job["mode"] == "summary":
                for path in job["files"]:
                    job["current_doc"] = os.path.basename(path)
                    evolve.evolve(
                        path, generations=job["generations"], n_qa=job["n_qa"],
                        out_dir=job["dir"],
                    )
            elif job["mode"] == "structure":
                evolve_structure.evolve_structure(
                    generations=job["generations"], n_qa=job["n_qa"],
                    out_dir=job["dir"], files=job["files"],
                )
            elif job["mode"] == "audit":
                def cb(done, total, docs):
                    flush_result({"type": "audit", "done": done, "total": total,
                                  "docs": docs})
                res = audit.audit(job["base_dir"], n_qa=job["n_qa"], progress_cb=cb)
                res["type"] = "audit"
                res["done"] = res["total"] = res["n_docs"]
                flush_result(res)
            elif job["mode"] == "apply":
                def cb(done, total, docs):
                    flush_result({"type": "apply", "done": done, "total": total,
                                  "docs": docs})
                res = apply_mod.run_apply(
                    job["base_dir"], strategy=job.get("strategy") or None,
                    n_qa=job["n_qa"], progress_cb=cb,
                )
                res["type"] = "apply"
                res["done"] = res["total"] = len(res["docs"])
                flush_result(res)
            job["status"] = "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["finished_at"] = time.time()


def _clamp_int(params, key, default, lo, hi):
    """정수 파라미터를 [lo, hi]로 클램프해 반환. 파싱 불가면 (None, 에러메시지)."""
    val = params.get(key, default)
    if val is None:
        val = default
    try:
        n = int(val)
    except (TypeError, ValueError):
        return None, f"{key}는 정수여야 합니다: {val!r}"
    return max(lo, min(hi, n)), None


def start_job(params):
    mode = params.get("mode", "summary")
    if mode not in ("summary", "structure", "audit", "apply"):
        return None, f"알 수 없는 mode: {mode}"
    backend = params.get("backend", "claude")
    if backend not in ("claude", "codex"):
        return None, f"알 수 없는 backend: {backend}"
    language = params.get("language", "ko")
    if language not in ("ko", "en", "zh"):
        return None, f"알 수 없는 language: {language}"

    files, base_dir, doc_names = [], None, []
    if mode in ("summary", "structure"):
        files = params.get("files") or []
        files = [f for f in files if os.path.isfile(f) and f.endswith(".md")]
        if not files:
            return None, "선택된 md 파일이 없습니다"
        doc_names = [os.path.splitext(os.path.basename(f))[0] for f in files]
    else:
        base_dir = os.path.expanduser(params.get("dir") or "")
        if not os.path.isdir(base_dir):
            return None, f"폴더가 없습니다: {base_dir}"
        doc_names = [os.path.basename(base_dir.rstrip("/"))]

    generations, err = _clamp_int(params, "generations", 3, 1, 10)
    if err:
        return None, err
    n_qa, err = _clamp_int(params, "n_qa", 6, 2, 12)
    if err:
        return None, err

    job_id = uuid.uuid4().hex[:8]
    job = {
        "id": job_id,
        "mode": mode,
        "backend": backend,
        "language": language,
        "files": files,
        "base_dir": base_dir,
        "strategy": (params.get("strategy") or "").strip(),
        "doc_names": doc_names,
        "generations": generations,
        "n_qa": n_qa,
        "dir": os.path.join(JOBS_DIR, job_id),
        "status": "queued",
        "error": None,
        "created_at": time.time(),
        "finished_at": None,
    }
    os.makedirs(job["dir"], exist_ok=True)
    with JOBS_LOCK:
        JOBS[job_id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job, None


def job_detail(job):
    """job 메타 + 하위 run 디렉터리들의 progress/report를 모아 반환."""
    runs = []
    for run_dir in sorted(glob.glob(os.path.join(job["dir"], "*"))):
        if not os.path.isdir(run_dir):
            continue
        entry = {"run_dir": os.path.basename(run_dir)}
        for name in ("progress", "report"):
            p = os.path.join(run_dir, f"{name}.json")
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        entry[name] = json.load(f)
                except (OSError, json.JSONDecodeError):
                    pass
        runs.append(entry)
    out = {k: v for k, v in job.items() if k != "dir"}
    out["runs"] = runs
    # audit/apply 결과 (진행 중엔 부분 결과)
    rp = os.path.join(job["dir"], "result.json")
    if os.path.exists(rp):
        try:
            with open(rp) as f:
                out["result"] = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return out


def pick_dir():
    """macOS 네이티브 폴더 선택창을 띄워 선택된 경로를 반환한다."""
    if sys.platform != "darwin":
        return None, "폴더 선택창은 macOS에서만 지원됩니다. 경로를 직접 입력하세요."
    script = 'POSIX path of (choose folder with prompt "wiki 폴더를 선택하세요")'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "선택창 응답 시간 초과"
    if proc.returncode != 0:  # 사용자가 취소
        return None, None
    return proc.stdout.strip(), None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 조용히
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            try:
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(500, "index.html missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/api/docs":
            q = parse_qs(url.query)
            wiki_dir = (q.get("dir") or [""])[0]
            docs = list_docs(wiki_dir)
            if docs is None:
                self._json({"error": f"폴더가 없습니다: {wiki_dir}"}, 404)
            else:
                self._json({"docs": docs})
            return
        if url.path == "/api/pick-dir":
            path, err = pick_dir()
            if err:
                self._json({"error": err}, 400)
            elif path is None:
                self._json({"cancelled": True})
            else:
                self._json({"dir": path})
            return
        if url.path == "/api/runs":
            with JOBS_LOCK:
                jobs = [
                    {k: v for k, v in j.items() if k not in ("dir", "files")}
                    for j in sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)
                ]
            self._json({"jobs": jobs})
            return
        if url.path.startswith("/api/runs/"):
            job_id = url.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._json({"error": "없는 job"}, 404)
            else:
                self._json(job_detail(job))
            return
        self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/runs":
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            params = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "잘못된 JSON"}, 400)
            return
        try:
            job, err = start_job(params)
        except Exception as e:  # 검증을 뚫은 예외도 무응답 대신 JSON으로
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        if err:
            self._json({"error": err}, 400)
        else:
            self._json({"id": job["id"], "status": job["status"]}, 201)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    os.makedirs(JOBS_DIR, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"wiki-optimizer 대시보드: http://localhost:{args.port}  (백엔드 기본: {llm.BACKEND})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
