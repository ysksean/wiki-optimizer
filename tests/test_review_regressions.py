"""Review regressions: document identity, evaluation fairness, explicit exports."""
import json

import apply as apply_mod
import audit
import llm
import web


def test_nested_documents_remain_distinct(tmp_path):
    for rel in ('raw/a/intro.md', 'raw/b/intro.md', 'wiki/a/intro.md'):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rel)
    pairs = audit.find_pairs(str(tmp_path))
    assert len(pairs) == len(web.list_docs(str(tmp_path))) == 2
    assert {p['name'] for p in pairs} == {'a/intro', 'b/intro'}
    assert next(p for p in pairs if p['name'] == 'a/intro')['wiki']
    assert next(p for p in pairs if p['name'] == 'b/intro')['wiki'] is None


def test_question_cache_tracks_content_count_and_language(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, 'QCACHE_DIR', str(tmp_path))
    calls = []
    def build(raw, n):
        calls.append((raw, n, llm.LANGUAGE))
        return [{'q': raw, 'a': str(n)}]
    monkeypatch.setattr(audit.scoring, 'build_question_set', build)
    audit.get_questions('a/intro', 'old', 2)
    audit.get_questions('a/intro', 'old', 2)
    audit.get_questions('a/intro', 'new', 2)
    audit.get_questions('a/intro', 'new', 6)
    monkeypatch.setattr(llm, 'LANGUAGE', 'en' if llm.LANGUAGE != 'en' else 'ko')
    audit.get_questions('a/intro', 'new', 6)
    assert len(calls) == 4
    assert len(list(tmp_path.glob('*.json'))) == 4


def test_apply_compares_same_documents_and_preserves_nested_output(tmp_path, monkeypatch):
    for rel, text in [('raw/sub/a.md', 'a'), ('raw/sub/b.md', 'b'), ('wiki/sub/a.md', 'before')]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    monkeypatch.setattr(audit, 'get_questions', lambda *a, **k: [{'q': 'q', 'a': 'a'}])
    monkeypatch.setattr(apply_mod.evolve, 'summarize', lambda *a: 'after')
    monkeypatch.setattr(apply_mod.scoring, 'score', lambda raw, *a: {'total': .9 if raw == 'a' else .1})
    monkeypatch.setattr(apply_mod, '_register_strategy', lambda *a: None)
    result = apply_mod.run_apply(str(tmp_path), strategy='explicit', out_dir=str(tmp_path / 'out'))
    assert result['avg_before'] == result['avg_after'] == .9
    assert result['avg_after_all'] == .5
    assert result['n_compared'] == result['n_new'] == 1
    assert (tmp_path / 'out/sub/a.md').read_text() == 'after'
    result = apply_mod.run_apply(str(tmp_path), strategy='explicit', out_dir=str(tmp_path / 'only'),
                                files=[str(tmp_path / 'raw/sub/b.md')])
    assert [d['name'] for d in result['docs']] == ['sub/b']
    assert result['avg_before'] is None and result['avg_after'] is None
    assert not (tmp_path / 'only/sub/a.md').exists()


def test_web_apply_requires_strategy_and_current_sources(tmp_path):
    (tmp_path / 'a.md').write_text('a')
    _, err = web.start_job({'mode': 'apply', 'dir': str(tmp_path)})
    assert '전략' in err
    _, err = web.start_job({'mode': 'apply', 'dir': str(tmp_path), 'strategy': 'chosen', 'files': [__file__]})
    assert '원본 문서' in err


def test_export_structure_selected_run_never_overwrites(tmp_path, monkeypatch):
    job_dir = tmp_path / 'job'
    for run, content in [('r1', 'chosen'), ('r2', 'other')]:
        p = job_dir / run
        p.mkdir(parents=True)
        (p / 'report.json').write_text(json.dumps({'best': {'struct': {'files': [{'title': '../Guide', 'content': content}]}}}))
    monkeypatch.setattr(web, 'JOBS', {'j': {'id': 'j', 'status': 'done', 'mode': 'structure', 'dir': str(job_dir)}})
    out = tmp_path / 'out'
    result, err = web.export_skeleton('j', str(out), 'r1')
    assert err is None and len(result['written']) == 1
    file = out / result['written'][0]
    assert file.read_text() == 'chosen'
    file.write_text('user edit')
    result, err = web.export_skeleton('j', str(out), 'r1')
    assert len(result['skipped']) == 1 and file.read_text() == 'user edit'
    assert web.export_skeleton('j', str(out), '../../r1')[1]


def test_best_strategy_ignores_failed_judging(tmp_path):
    (tmp_path / 'report.json').write_text(json.dumps({'parse_failed': True, 'best': {'strategy': 'bad', 'total': 1}}))
    assert apply_mod.best_strategy_from_runs(str(tmp_path)) is None
