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
import evolve_proposal
import evolve_structure
import skeleton as skeleton_mod

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
JOBS_DIR = "runs/web"

JOBS = {}          # job_id -> job dict (JSON 직렬화 가능한 값만 담는다)
JOBS_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()  # 동시 실행 1개 제한
CANCEL_EVENTS = {}  # job_id -> threading.Event (직렬화 불가라 JOBS 밖에 둔다)


def _save_job(job):
    """job 메타를 runs/web/<id>/job.json으로 남긴다 (재시작 후 이력 복원용).

    워커·HTTP 스레드가 같은 job dict를 만지므로 락 안에서 스냅샷을 뜬 뒤
    직렬화하고, tmp → os.replace로 원자적으로 써서 반쯤 쓰인 파일이
    load_jobs()의 복원을 깨뜨리지 않게 한다.
    """
    with JOBS_LOCK:
        snapshot = dict(job)
    path = os.path.join(snapshot["dir"], "job.json")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def load_jobs():
    """기동 시 runs/web/*/job.json을 스캔해 지난 실행 이력을 JOBS로 복원한다.

    복원 시점에 queued/running이던 job은 워커 스레드가 사라졌으므로
    interrupted로 마킹한다.
    """
    for p in sorted(glob.glob(os.path.join(JOBS_DIR, "*", "job.json"))):
        try:
            with open(p) as f:
                job = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(job, dict) or not job.get("id"):
            continue
        job["dir"] = os.path.dirname(p)
        if job.get("status") in ("queued", "running"):
            job["status"] = "interrupted"
            job.setdefault("finished_at", None)
            _save_job(job)
        with JOBS_LOCK:
            JOBS[job["id"]] = job


def cancel_job(job_id):
    """취소 요청 처리. (payload, http_code)를 반환한다."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return {"error": "없는 job"}, 404
    if job["status"] not in ("queued", "running"):
        return {"error": f"이미 끝난 job입니다: {job['status']}"}, 409
    if job["status"] == "running" and job["mode"] != "summary":
        # summary만 실행 중 취소 지점이 있다 — 나머지 모드는 완주 후 done이 뜨는
        # 거짓 취소가 되므로 정직하게 거부한다 (queued일 땐 모든 모드 취소 가능)
        return {"error": "이 모드는 실행 중 취소를 지원하지 않습니다"}, 409
    ev = CANCEL_EVENTS.get(job_id)
    if ev is None:  # 재시작으로 복원된 job엔 워커가 없다 (방어)
        return {"error": "취소할 수 없는 job"}, 409
    ev.set()
    job["cancel_requested"] = True
    _save_job(job)
    return {"ok": True, "status": job["status"]}, 200


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
    cancel = CANCEL_EVENTS.get(job["id"]) or threading.Event()
    try:
        # queued 취소는 RUN_LOCK을 기다리지 않고 즉시 처리한다
        # (앞 job이 길게 돌면 락 안에서만 체크해서는 그때까지 취소가 안 먹는다)
        if cancel.is_set():
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            _save_job(job)
            return
        _run_job_locked(job, cancel)
    finally:
        CANCEL_EVENTS.pop(job["id"], None)  # 종료된 job의 Event 누수 방지


def _result_summary(job):
    """완료된 job의 run 리포트들을 목록용 한 줄로 요약한다 — 열지 않아도 점수·출처가 보이게.

    best_total은 판정 파싱 실패가 없는 run 중 최댓값. provenance는 첫 리포트 기준.
    리포트가 없거나 깨져 있으면 None (구 job·audit/apply 등).
    """
    reports = []
    for run_dir in sorted(glob.glob(os.path.join(job["dir"], "*"))):
        p = os.path.join(run_dir, "report.json")
        if not os.path.isfile(p):
            continue
        try:
            with open(p) as f:
                reports.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    if not reports:
        return None
    valid = [r for r in reports if not r.get("parse_failed")]
    totals = [r.get("best", {}).get("total") for r in valid]
    totals = [x for x in totals if isinstance(x, (int, float))]
    prov = next((r.get("provenance") for r in reports if r.get("provenance")), None) or {}
    return {
        "n_runs": len(reports),
        "parse_failed_runs": len(reports) - len(valid),
        "best_total": max(totals) if totals else None,
        "arms": sorted({r.get("arm") for r in reports if r.get("arm")}),
        "backend": prov.get("backend"), "model": prov.get("model"), "code_sha": prov.get("code_sha"),
    }


def _backfill_result_summaries():
    """리뉴얼 전에 끝난 job(result_summary 키 자체가 없음)을 목록 조회 시 1회 요약해 저장한다.

    None으로라도 키를 박아 두어 매 폴링마다 다시 계산하지 않는다.
    """
    with JOBS_LOCK:
        todo = [j for j in JOBS.values()
                if j.get("status") in ("done", "cancelled", "error") and "result_summary" not in j]
    for job in todo:
        try:
            job["result_summary"] = _result_summary(job)
        except Exception:
            job["result_summary"] = None
        _save_job(job)


def _run_job_locked(job, cancel):
    with RUN_LOCK:
        if cancel.is_set():  # 락 대기 중(queued) 취소됨
            job["status"] = "cancelled"
            job["finished_at"] = time.time()
            _save_job(job)
            return
        job["status"] = "running"
        _save_job(job)
        llm.BACKEND = job["backend"]
        llm.LANGUAGE = job.get("language", "ko")

        def flush_result(payload):
            with open(os.path.join(job["dir"], "result.json"), "w") as f:
                json.dump(payload, f, ensure_ascii=False)

        try:
            if job["mode"] == "summary":
                for path in job["files"]:
                    if cancel.is_set():
                        break
                    job["current_doc"] = os.path.basename(path)
                    evolve.evolve(
                        path, generations=job["generations"], n_qa=job["n_qa"],
                        out_dir=job["dir"], cancel_event=cancel,
                    )
            elif job["mode"] == "structure":
                evolve_structure.evolve_structure(
                    generations=job["generations"], n_qa=job["n_qa"],
                    out_dir=job["dir"], files=job["files"],
                )
            elif job["mode"] == "propose":
                strategy = None
                if job.get("seed_from_runs"):
                    prev = evolve_proposal.best_proposal_strategy_from_runs("runs")
                    if prev:
                        strategy = prev["strategy"]
                evolve_proposal.run_evolve(
                    job["sources"], job["task"], generations=job["generations"],
                    n_qa=job["n_qa"], strategy=strategy, out_dir=job["dir"],
                )
            elif job["mode"] == "audit":
                def cb(done, total, partial):
                    flush_result({"type": "audit", "done": done, "total": total,
                                  **partial})
                res = audit.audit(job["base_dir"], n_qa=job["n_qa"], progress_cb=cb,
                                  max_docs=job.get("max_docs"))
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
            # summary 모드만 중간 취소 지점이 있다 — 나머지 모드는 끝까지 돌면 done
            aborted = job["mode"] == "summary" and cancel.is_set()
            job["status"] = "cancelled" if aborted else "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["finished_at"] = time.time()
            try:
                job["result_summary"] = _result_summary(job)
            except Exception:  # 요약은 부가 정보 — 실패해도 job 종료를 막지 않는다
                job["result_summary"] = None
            _save_job(job)


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
    if mode not in ("summary", "structure", "audit", "apply", "propose"):
        return None, f"알 수 없는 mode: {mode}"
    backend = params.get("backend", "claude")
    if backend not in ("claude", "codex"):
        return None, f"알 수 없는 backend: {backend}"
    language = params.get("language", "ko")
    if language not in ("ko", "en", "zh"):
        return None, f"알 수 없는 language: {language}"

    files, base_dir, doc_names = [], None, []
    sources, task = [], ""
    if mode == "propose":
        sources = [os.path.expanduser(str(s).strip())
                   for s in (params.get("sources") or []) if str(s).strip()]
        missing = [s for s in sources if not os.path.isdir(s)]
        if not sources:
            return None, "소스 폴더가 없습니다 (한 줄에 하나씩 입력)"
        if missing:
            return None, f"폴더가 없습니다: {', '.join(missing)}"
        task = (params.get("task") or "").strip()
        if not task:
            return None, "태스크 설명이 비어 있습니다"
        doc_names = [os.path.basename(s.rstrip("/")) for s in sources]
    elif mode in ("summary", "structure"):
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
    max_docs = None
    if params.get("max_docs"):
        max_docs, err = _clamp_int(params, "max_docs", None, 1, 1000)
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
        "sources": sources,
        "task": task,
        "seed_from_runs": bool(params.get("seed_from_runs")),
        "doc_names": doc_names,
        "generations": generations,
        "n_qa": n_qa,
        "max_docs": max_docs,
        "dir": os.path.join(JOBS_DIR, job_id),
        "status": "queued",
        "error": None,
        "cancel_requested": False,
        "created_at": time.time(),
        "finished_at": None,
    }
    os.makedirs(job["dir"], exist_ok=True)
    with JOBS_LOCK:
        JOBS[job_id] = job
        CANCEL_EVENTS[job_id] = threading.Event()
    _save_job(job)
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


UPLOAD_DIR = os.path.join("runs", "uploads")
UPLOAD_EXTS = (".md", ".txt")
UPLOAD_MAX_FILE = 2 * 1024 * 1024   # 파일당 2MB
UPLOAD_MAX_TOTAL = 20 * 1024 * 1024  # 요청당 20MB


def save_uploads(files):
    """브라우저 업로드 파일들을 runs/uploads/<id>/에 저장한다.

    files: [{"name": str, "content": str}]  (content는 텍스트 그대로)
    Stage 0 소스 root로 쓸 수 있는 (payload, error)를 반환한다.
    """
    if not isinstance(files, list) or not files:
        return None, "업로드할 파일이 없습니다"
    total = 0
    clean = []
    for f in files:
        if not isinstance(f, dict):
            return None, "잘못된 파일 항목"
        name = os.path.basename(str(f.get("name") or "").strip())
        content = f.get("content")
        if not name or not isinstance(content, str):
            return None, f"파일명/내용이 비었습니다: {name!r}"
        if not name.lower().endswith(UPLOAD_EXTS):
            return None, f"허용되지 않는 확장자: {name} (md/txt만)"
        size = len(content.encode("utf-8"))
        if size > UPLOAD_MAX_FILE:
            return None, f"파일이 너무 큽니다 (2MB 제한): {name}"
        total += size
        if total > UPLOAD_MAX_TOTAL:
            return None, "업로드 총량이 20MB를 넘습니다"
        clean.append((name, content))
    batch = uuid.uuid4().hex[:8]
    dest = os.path.join(UPLOAD_DIR, batch)
    os.makedirs(dest, exist_ok=True)
    for name, content in clean:
        with open(os.path.join(dest, name), "w") as fh:
            fh.write(content)
    return {"dir": os.path.abspath(dest),
            "saved": [n for n, _ in clean]}, None


def export_skeleton(job_id, write_dir):
    """propose job의 best 구조를 write_dir에 골격으로 쓴다 (기존 파일 skip)."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job["mode"] != "propose":
        return None, "없는 propose job"
    write_dir = os.path.expanduser((write_dir or "").strip())
    if not write_dir:
        return None, "내보낼 폴더 경로가 비어 있습니다"
    pages = None
    for p in sorted(glob.glob(os.path.join(job["dir"], "*", "report.json")),
                    reverse=True):
        try:
            with open(p) as f:
                r = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        pages = (r.get("best") or {}).get("pages")
        if pages:
            break
    if not pages:
        return None, "완료된 제안이 없습니다"
    res = skeleton_mod.write_skeleton(pages, write_dir, write=True, run_id=job_id)
    return {"written": res["written"], "skipped": res["skipped"],
            "dir": write_dir}, None


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
        if url.path.startswith("/static/"):
            # CSS/JS만, 파일명 하나만 — 경로 탈출 차단
            name = os.path.basename(url.path)
            if name != url.path[len("/static/"):] or not name.endswith((".css", ".js")):
                self.send_error(404)
                return
            try:
                with open(os.path.join(STATIC_DIR, name), "rb") as f:
                    body = f.read()
            except OSError:
                self.send_error(404)
                return
            ctype = "text/css" if name.endswith(".css") else "application/javascript"
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
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
            _backfill_result_summaries()
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
        parts = url.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "cancel":
            payload, code = cancel_job(parts[2])
            self._json(payload, code)
            return
        if url.path not in ("/api/runs", "/api/skeleton", "/api/upload"):
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            params = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "잘못된 JSON"}, 400)
            return
        if url.path == "/api/upload":
            try:
                res, err = save_uploads(params.get("files"))
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
                return
            self._json({"error": err} if err else res, 400 if err else 200)
            return
        if url.path == "/api/skeleton":
            try:
                res, err = export_skeleton(params.get("job_id", ""),
                                           params.get("write_dir", ""))
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
                return
            self._json({"error": err} if err else res, 400 if err else 200)
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
    load_jobs()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"wiki-optimizer 대시보드: http://localhost:{args.port}  (백엔드 기본: {llm.BACKEND})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
