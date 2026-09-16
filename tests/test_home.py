"""Folder intake and streamed evidence, without model calls."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import inspection
import web


def test_inspection_streams_real_relative_files_and_prefers_raw(tmp_path):
    (tmp_path / 'raw' / 'nested').mkdir(parents=True)
    (tmp_path / 'wiki').mkdir()
    (tmp_path / 'raw' / 'nested' / 'guide.md').write_text('# Guide')
    (tmp_path / 'wiki' / 'summary.md').write_text('summary')
    (tmp_path / 'raw' / 'README.md').write_text('readme')
    events = list(inspection.inspect_folder(str(tmp_path)))
    assert [e['type'] for e in events] == ['started', 'file', 'complete']
    assert events[1]['name'] == 'nested/guide.md'
    assert events[-1]['has_wiki'] is True
    assert events[-1]['bytes'] == len('# Guide')
    assert events[-1]['files'][0]['path'] == str(tmp_path / 'raw/nested/guide.md')


def test_inspection_skips_symlinks_and_rejects_empty_or_oversized(tmp_path, monkeypatch):
    root = tmp_path / 'docs'
    root.mkdir()
    external = tmp_path / 'external.md'
    external.write_text('external')
    (root / 'link.md').symlink_to(external)
    with pytest.raises(ValueError, match='Markdown'):
        list(inspection.inspect_folder(str(root)))
    (root / 'real.md').write_text('real')
    monkeypatch.setattr(inspection, 'MAX_BYTES', 1)
    with pytest.raises(ValueError, match='20MB'):
        list(inspection.inspect_folder(str(root)))


def test_folder_upload_preserves_duplicate_basenames_and_rejects_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'UPLOAD_DIR', str(tmp_path / 'uploads'))
    files = [{'name':'guide.md', 'path':'raw/guide.md', 'content':'raw'},
             {'name':'guide.md', 'path':'wiki/guide.md', 'content':'wiki'}]
    result, error = web.save_uploads(files)
    assert error is None
    assert Path(result['dir'], 'raw/guide.md').read_text() == 'raw'
    assert Path(result['dir'], 'wiki/guide.md').read_text() == 'wiki'
    for path in ('../escape.md', '/absolute.md', 'a/../../escape.md', 'a//b.md', 'a/./b.md', 'a\\..\\escape.md'):
        result, error = web.save_uploads([{'name':'guide.md', 'path':path, 'content':'x'}])
        assert result is None and error
    result, error = web.save_uploads(files + files[:1])
    assert result is None and '중복' in error


def test_http_inspection_and_completed_job_event_stream(tmp_path, monkeypatch):
    (tmp_path / 'guide.md').write_text('guide')
    monkeypatch.setattr(web, 'JOBS', {'test': {'id':'test','mode':'structure','status':'done','dir':str(tmp_path / 'job')}})
    server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def read(path):
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{path}', timeout=3) as response:
            assert response.headers['Content-Type'].startswith('text/event-stream')
            return [json.loads(line[6:]) for line in response.read().decode().splitlines() if line.startswith('data: ')]
    try:
        events = read('/api/inspect?dir=' + str(tmp_path))
        assert events[-1]['count'] == 1
        events = read('/api/inspect?dir=/no-such-wikiopt-folder')
        assert events[-1]['type'] == 'error'
        events = read('/api/runs/test/events')
        assert events[-1]['job']['status'] == 'done'
    finally:
        server.shutdown()
        server.server_close()


def test_structure_activity_and_cancellation_stop_before_next_model_call(tmp_path, monkeypatch):
    import evolve_structure
    import structure
    doc = tmp_path / 'guide.md'
    doc.write_text('A source document')
    stop = threading.Event()
    stages = []
    def notify(event):
        stages.append(event['stage'])
        if event['stage'] == 'evaluating':
            stop.set()
    monkeypatch.setattr(structure, 'organize', lambda *args: {'files':[{'title':'Guide','content':'A guide'}]})
    def unexpected_score(*args):
        pytest.fail('Cancellation must stop before the next model call')
    monkeypatch.setattr(structure, 'score_structure', unexpected_score)
    report = evolve_structure.evolve_structure(files=[str(doc)], out_dir=str(tmp_path / 'runs'),
        question_set=[{'q':f'q{i}','a':f'a{i}'} for i in range(6)], progress_cb=notify, cancel_event=stop)
    assert stages == ['questions_ready', 'organizing', 'evaluating']
    assert report['cancelled'] is True
    assert report['history'] == []
