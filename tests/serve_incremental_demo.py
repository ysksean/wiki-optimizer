"""Serve an isolated, deterministic browser fixture; never calls an LLM.

Run: uv run --with pytest python tests/serve_incremental_demo.py
"""

import sys
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_incremental import workspace
import web


def main() -> None:
    """Expose the real dashboard and API with only model responses substituted."""
    with tempfile.TemporaryDirectory(prefix="wiki-update-ui-") as directory:
        patch = pytest.MonkeyPatch()
        env = workspace.__wrapped__(Path(directory), patch)
        web.JOBS_DIR = str(Path(directory) / "jobs")
        web.UPLOAD_DIR = str(Path(directory) / "uploads")
        web.JOBS.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        print(f"URL=http://127.0.0.1:{server.server_port}", flush=True)
        print(f"ROOT={env['root']}\nSOURCE={env['source']}", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            patch.undo()


if __name__ == "__main__":
    main()
