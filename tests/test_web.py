"""웹 대시보드 테스트 — 취소·영속화(SEA-16) + 파라미터 검증(SEA-17).

LLM 호출 0회 — evolve 내부 단계(summarize/score/reflect)를 스텁으로 바꾼다.

커버 대상:
- evolve.evolve() cancel_event: 세대 경계에서 멈추고 부분 report를 남긴다
- web._run_job: queued 취소 / 문서 경계 취소 → status "cancelled"
- web.cancel_job: 404 / 409(종료된 job) / 정상 취소
- web._save_job + load_jobs: 재시작 복원, running/queued → interrupted
- web._clamp_int: 잘못된 generations/n_qa가 무응답 대신 400+메시지로 떨어진다
"""

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import evolve
import web


# ---------- evolve 스텁 ----------

@pytest.fixture
def evolve_env(tmp_path, monkeypatch):
    """영속 이력·run 출력이 tmp로 가도록 격리하고 문서 파일을 만든다."""
    monkeypatch.setattr(evolve, "WIKI_DIR", str(tmp_path / "wiki"))
    monkeypatch.setattr(evolve, "IMPACT_PATH", str(tmp_path / "wiki" / "impact.jsonl"))
    doc = tmp_path / "doc.md"
    doc.write_text("x" * 1000)
    qs = [{"q": f"q{i}", "a": f"a{i}"} for i in range(6)]
    return {"doc": str(doc), "out": str(tmp_path / "runs"), "qs": qs}


def _stub_evolve_steps(monkeypatch, on_summarize=None):
    """summarize/score/reflect를 결정론 스텁으로 바꾼다. summarize 호출 수 반환."""
    calls = {"n": 0}

    def fake_summarize(text, strategy):
        calls["n"] += 1
        if on_summarize:
            on_summarize(calls["n"])
        return f"SUM-{calls['n']}"

    monkeypatch.setattr(evolve, "summarize", fake_summarize)
    monkeypatch.setattr(evolve.scoring, "score", lambda raw, s, qs: {
        "total": 1.0, "accuracy": 1.0, "efficiency": 1.0, "length_ratio": 0.1,
        "qa_details": [], "parse_failed": False,
    })
    monkeypatch.setattr(evolve, "reflect", lambda st, tr, doc=None: st)
    return calls


def test_evolve_cancel_event_stops_at_generation_boundary(evolve_env, monkeypatch):
    ev = threading.Event()
    # 1세대 요약 직후 취소 → 2세대 경계에서 멈춰야 한다
    calls = _stub_evolve_steps(monkeypatch, on_summarize=lambda n: ev.set())
    rep = evolve.evolve(evolve_env["doc"], generations=4, out_dir=evolve_env["out"],
                        question_set=evolve_env["qs"], cancel_event=ev)
    assert calls["n"] == 1
    assert len(rep["history"]) == 1
    assert rep["cancelled"] is True


def test_evolve_cancel_preset_writes_empty_report(evolve_env, monkeypatch):
    ev = threading.Event()
    ev.set()
    _stub_evolve_steps(monkeypatch)
    rep = evolve.evolve(evolve_env["doc"], generations=3, out_dir=evolve_env["out"],
                        question_set=evolve_env["qs"], cancel_event=ev)
    assert rep["history"] == []
    assert rep["cancelled"] is True


def test_evolve_without_cancel_event_runs_all_generations(evolve_env, monkeypatch):
    calls = _stub_evolve_steps(monkeypatch)
    rep = evolve.evolve(evolve_env["doc"], generations=3, out_dir=evolve_env["out"],
                        question_set=evolve_env["qs"])
    assert calls["n"] == 3
    assert rep["cancelled"] is False


# ---------- web 스텁 ----------

@pytest.fixture
def web_env(tmp_path, monkeypatch):
    """JOBS/CANCEL_EVENTS를 비우고 JOBS_DIR을 tmp로 격리한다."""
    monkeypatch.setattr(web, "JOBS_DIR", str(tmp_path / "web"))
    monkeypatch.setattr(web, "JOBS", {})
    monkeypatch.setattr(web, "CANCEL_EVENTS", {})
    os.makedirs(web.JOBS_DIR, exist_ok=True)
    return tmp_path


def _make_job(status="queued", job_id="j1", **extra):
    job = {
        "id": job_id, "mode": "summary", "backend": "claude", "language": "ko",
        "files": [], "base_dir": None, "strategy": "", "doc_names": ["doc"],
        "generations": 2, "n_qa": 4, "dir": os.path.join(web.JOBS_DIR, job_id),
        "status": status, "error": None, "cancel_requested": False,
        "created_at": 0.0, "finished_at": None,
    }
    job.update(extra)
    os.makedirs(job["dir"], exist_ok=True)
    return job


def test_save_and_load_jobs_marks_unfinished_as_interrupted(web_env):
    for status, job_id in (("running", "r1"), ("queued", "q1"), ("done", "d1")):
        web._save_job(_make_job(status=status, job_id=job_id))
    web.JOBS.clear()

    web.load_jobs()

    assert web.JOBS["r1"]["status"] == "interrupted"
    assert web.JOBS["q1"]["status"] == "interrupted"
    assert web.JOBS["d1"]["status"] == "done"
    # 마킹이 디스크에도 반영돼 다음 재시작에도 유지된다
    with open(os.path.join(web.JOBS_DIR, "r1", "job.json")) as f:
        assert json.load(f)["status"] == "interrupted"


def test_load_jobs_skips_broken_json(web_env):
    os.makedirs(os.path.join(web.JOBS_DIR, "bad"), exist_ok=True)
    with open(os.path.join(web.JOBS_DIR, "bad", "job.json"), "w") as f:
        f.write("{broken")
    web.load_jobs()
    assert web.JOBS == {}


def test_cancel_job_not_found_and_finished(web_env):
    _, code = web.cancel_job("nope")
    assert code == 404

    web.JOBS["d1"] = _make_job(status="done", job_id="d1")
    _, code = web.cancel_job("d1")
    assert code == 409


def test_cancel_job_sets_event_and_flag(web_env):
    job = _make_job(status="running", job_id="r1")
    web.JOBS["r1"] = job
    web.CANCEL_EVENTS["r1"] = threading.Event()

    payload, code = web.cancel_job("r1")

    assert code == 200 and payload["ok"]
    assert web.CANCEL_EVENTS["r1"].is_set()
    assert job["cancel_requested"] is True


def test_run_job_cancelled_while_queued(web_env):
    job = _make_job(status="queued", job_id="q1")
    web.JOBS["q1"] = job
    web.CANCEL_EVENTS["q1"] = threading.Event()
    web.CANCEL_EVENTS["q1"].set()

    web._run_job(job)

    assert job["status"] == "cancelled"
    assert job["finished_at"] is not None


def test_run_job_summary_skips_remaining_docs_after_cancel(web_env, monkeypatch):
    ran = []

    def fake_evolve(path, generations, n_qa, out_dir, cancel_event=None):
        ran.append(path)
        cancel_event.set()  # 첫 문서 처리 중 사용자가 취소한 상황

    monkeypatch.setattr(web.evolve, "evolve", fake_evolve)
    job = _make_job(status="queued", job_id="s1", files=["a.md", "b.md"])
    web.JOBS["s1"] = job
    web.CANCEL_EVENTS["s1"] = threading.Event()

    web._run_job(job)

    assert ran == ["a.md"]
    assert job["status"] == "cancelled"


def test_run_job_summary_completes_as_done(web_env, monkeypatch):
    monkeypatch.setattr(web.evolve, "evolve",
                        lambda *a, **kw: None)
    job = _make_job(status="queued", job_id="s2", files=["a.md"])
    web.JOBS["s2"] = job
    web.CANCEL_EVENTS["s2"] = threading.Event()

    web._run_job(job)

    assert job["status"] == "done"
    # 종료 상태가 디스크에 남아 재시작 후에도 done으로 복원된다
    with open(os.path.join(job["dir"], "job.json")) as f:
        assert json.load(f)["status"] == "done"


# ---------- _clamp_int 단위 ----------

def test_clamp_int_default_and_clamp():
    assert web._clamp_int({}, "generations", 3, 1, 10) == (3, None)
    assert web._clamp_int({"generations": 99}, "generations", 3, 1, 10) == (10, None)
    assert web._clamp_int({"generations": 0}, "generations", 3, 1, 10) == (1, None)
    assert web._clamp_int({"generations": "7"}, "generations", 3, 1, 10) == (7, None)


def test_clamp_int_null_uses_default():
    assert web._clamp_int({"generations": None}, "generations", 3, 1, 10) == (3, None)


@pytest.mark.parametrize("bad", ["abc", "3.5", [], {}])
def test_clamp_int_unparseable_returns_error(bad):
    n, err = web._clamp_int({"generations": bad}, "generations", 3, 1, 10)
    assert n is None
    assert "generations" in err


# ---------- start_job 경로 ----------

def _params(md_file, **over):
    p = {"mode": "summary", "files": [str(md_file)], "backend": "claude"}
    p.update(over)
    return p


@pytest.fixture
def md_file(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# 제목\n\n본문.\n")
    return f


def test_start_job_bad_generations_returns_error(md_file):
    job, err = web.start_job(_params(md_file, generations="abc"))
    assert job is None
    assert "generations" in err


def test_start_job_bad_n_qa_returns_error(md_file):
    job, err = web.start_job(_params(md_file, n_qa="많이"))
    assert job is None
    assert "n_qa" in err


# ---------- HTTP 레이어: 400 JSON 응답 (무응답 회귀 방지) ----------

@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, payload):
    req = urllib.request.Request(
        f"{url}/api/runs", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_post_bad_generations_gets_400_json(server, md_file):
    code, body = _post(server, _params(md_file, generations="abc"))
    assert code == 400
    assert "generations" in body["error"]


def test_post_null_generations_uses_default_but_bad_mode_400(server):
    # null generations는 기본값으로 통과하고, 검증은 mode에서 걸린다
    code, body = _post(server, {"mode": "없는모드", "generations": None})
    assert code == 400
    assert "mode" in body["error"]
