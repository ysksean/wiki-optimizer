"""Offline browser regression checks. Run with uv run --with playwright python tests/browser_review.py.
Requires a local Chromium installation (CHROMIUM_PATH may override it).
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import web  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        web.JOBS_DIR = str(root / 'jobs')
        web.JOBS = {}
        for jid, mode in [('proposal', 'propose'), ('structure', 'structure'), ('summary', 'summary')]:
            run = root / 'jobs' / jid / 'r1'
            run.mkdir(parents=True)
            if mode == 'propose':
                report = {'scoreable': False, 'best': {'pages': [{'path': 'guide.md', 'title': 'Guide', 'purpose': 'QA', 'status': 'grounded', 'sources': [], 'outline': []}]}}
            elif mode == 'structure':
                report = {'best': {'struct': {'files': [{'title': 'Very-long-name-' * 12, 'content': 'Verified structure content', 'sources': ['intro']}]}}}
            else:
                report = {'best': {'strategy': 'Selected summary strategy', 'summary': 'Summary', 'generation': 0}, 'history': []}
            (run / 'report.json').write_text(json.dumps(report))
            if mode in ('summary', 'structure'):
                history = [{'generation': 0, 'strategy': 'Selected summary strategy', 'score': {'total': .5, 'accuracy': 1, 'efficiency': .5, 'length_ratio': .5}, 'train_score': {'total': .5}, 'n_files': 1, 'files': report.get('best', {}).get('struct', {}).get('files', [])}]
                progress = {'mode': mode, 'doc': 'intro', 'docs': ['intro'], 'best_gen': 0, 'best_total': .5, 'done_generations': 1, 'generations': 1, 'history': history}
                (run / 'progress.json').write_text(json.dumps(progress))
            web.JOBS[jid] = {'id': jid, 'mode': mode, 'status': 'done', 'dir': str(run.parent), 'doc_names': ['intro'], 'backend': 'claude', 'generations': 1, 'created_at': {'proposal': 3, 'structure': 2, 'summary': 1}[jid]}
        srv = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as pw:
                candidates = list((Path.home() / 'Library/Caches/ms-playwright').glob('chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
                executable = os.environ.get('CHROMIUM_PATH') or (str(candidates[-1]) if candidates else None)
                browser = pw.chromium.launch(executable_path=executable)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{srv.server_port}')
                page.locator('#nav-propose').click()
                form = page.locator('#proposeTimeline .export-form')
                form.wait_for()
                form.locator('input').fill(str(root / 'proposal-out'))
                form.locator('button').click()
                page.wait_for_function("document.querySelector('#proposeTimeline form [role=status]').textContent.includes('생성')")
                assert (root / 'proposal-out/guide.md').exists()
                # Same card in another view must use its own input.
                page.locator('#nav-runs').click()
                form = page.locator('#jobs form[data-job=proposal]')
                form.locator('input').fill(str(root / 'history-out'))
                form.locator('button').click()
                page.wait_for_function("document.querySelector('#jobs form[data-job=proposal] [role=status]').textContent.includes('생성')")
                assert (root / 'history-out/guide.md').exists()
                page.locator('#jobs [data-job-id=structure]').click()
                form = page.locator('#jobs form[data-job=structure]')
                form.locator('input').fill(str(root / 'structure-out'))
                form.locator('button').click()
                page.wait_for_function("document.querySelector('#jobs form[data-job=structure] [role=status]').textContent.includes('생성')")
                assert next((root / 'structure-out').glob('*.md')).read_text() == 'Verified structure content'
                page.locator('#jobs [data-job-id=summary]').click()
                page.get_by_role('button', name='이 요약 전략 사용').click()
                assert page.locator('#strategy').input_value() == 'Selected summary strategy'
                # A completed summary must not force new HTML every poll.
                page.evaluate('window.savedSummaryNode=document.querySelector("#jobs .evolution-run")')
                page.evaluate('poll()')
                assert page.evaluate('window.savedSummaryNode===document.querySelector("#jobs .evolution-run")')
                page.locator('#nav-propose').click()
                page.locator('#prop-backend').select_option('codex')
                assert page.locator('#backend').input_value() == 'codex'
                page.locator('#nav-runs').click()
                page.locator('#runsSearch').fill('no-such-document')
                page.get_by_text('검색 결과가 없습니다', exact=True).wait_for()
                page.get_by_role('button', name='필터 초기화').click()
                page.locator('#jobs .structure-run').wait_for()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.locator('#mobileTheme').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                file_button = page.locator('#jobs .smap .dst').first
                file_button.focus()
                page.keyboard.press('Enter')
                assert file_button.get_attribute('aria-pressed') == 'true'
                # Verify the nested optimization timeline, where overflow was reported.
                page.evaluate("_timelineCache.optTimeline=''; const card=document.querySelector('#jobs .structure-run').closest('.card').outerHTML; _renderTimelineInto('optTimeline',[{id:'structure'}],[card],0); showView('opt'); mobilePanel('result'); clearTimeout(timer)")
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not page.locator('.setup-rail').first.is_visible()
                assert not page.locator('#docsToolbar').is_visible()
                assert not errors, errors
                browser.close()
                print('Browser checks passed: local exports, result strategy, stable polling, shared settings, search reset, mobile overflow, keyboard controls.')
        finally:
            srv.shutdown()


if __name__ == '__main__':
    main()
