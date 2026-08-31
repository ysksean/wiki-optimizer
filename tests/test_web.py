"""web.py 파라미터 검증 테스트 — SEA-17.

잘못된 generations/n_qa가 무응답(커넥션 끊김) 대신 400+메시지로
떨어지는지, 정상 범위 밖 값이 클램프되는지 확인한다.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import web


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
