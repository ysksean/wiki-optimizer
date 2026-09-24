/* The landing view owns one inspection and one explicitly started job. */
const homeState = { stream: null, scan: null, job: null, busy: false, started: 0, clock: null, events: new Set(), revision: 0, lastResult: '' };
function homeTransition(change) {
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) document.startViewTransition(change);
  else change();
}
function showHome() {
  document.body.classList.add('home-active');
  $('view-home').hidden = false;
  window.scrollTo(0, 0);
}
function toggleHomeTheme() {
  setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
}
function homeMessage(message) {
  $('homeError').textContent = message;
  $('homeError').hidden = !message;
}
function homeEvent(key, message, detail = '') {
  if (homeState.events.has(key)) return;
  homeState.events.add(key);
  const item = document.createElement('li');
  item.textContent = message;
  if (detail) { const small = document.createElement('small'); small.textContent = detail; item.append(small); }
  $('homeEvents').append(item);
  $('homeEvents').scrollTop = $('homeEvents').scrollHeight;
}
function homeClock(started = Date.now()) {
  clearInterval(homeState.clock);
  homeState.clock = null;
  homeState.started = started;
  const update = () => {
    const seconds = Math.max(0, Math.floor((Date.now() - homeState.started) / 1000));
    $('glassClock').textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  };
  update(); homeState.clock = setInterval(update, 1000);
}
function homeIdle(status) {
  homeState.busy = false;
  clearInterval(homeState.clock);
  homeState.clock = null;
  $('glassStatus').textContent = status;
  $('glassDot').classList.add('idle');
  $('homeStop').hidden = true;
  $('homeReset').hidden = false;
  homeSyncButtons();
}
function homeSyncButtons() {
  for (const id of ['homeStart', 'homePick', 'homeImprove', 'homeAudit', 'homeBackend']) $(id).disabled = homeState.busy;
  $('homePath').disabled = homeState.busy;
}
function resetHome() {
  if (homeState.job && homeState.busy) return;
  homeState.revision++;
  homeState.stream?.close();
  clearInterval(homeState.clock);
  Object.assign(homeState, { stream: null, scan: null, job: null, detail: null, busy: false, lastResult: '' });
  homeState.events.clear();
  try { sessionStorage.removeItem('wikiopt_home_job'); } catch (_) {}
  homeTransition(() => {
    $('view-home').classList.remove('is-working');
    $('homeGlass').hidden = true;
    $('homeRecommendation').hidden = true;
    $('homeHealth').hidden = true; $('homeHealth').replaceChildren();
    $('homeEvents').replaceChildren(); $('homeFileList').replaceChildren(); $('homeResults').replaceChildren();
    homeMessage(''); homeSyncButtons(); $('homePath').focus();
  });
}
function beginHomeActivity() {
  homeState.busy = true;
  $('glassDot').classList.remove('idle');
  $('homeStop').hidden = false;
  $('homeStop').disabled = false;
  $('homeStop').textContent = '중단';
  $('homeReset').hidden = true;
  homeSyncButtons(); homeClock();
  homeTransition(() => {
    $('view-home').classList.add('is-working'); $('homeGlass').hidden = false;
    $('glassTitle').focus({preventScroll: true});
  });
}
function appendHomeFile(file) {
  const row = document.createElement('li');
  const name = document.createElement('span'); name.textContent = file.name;
  const size = document.createElement('small'); size.textContent = `${(file.size / 1024).toFixed(1)} KB`;
  row.append(name, size); $('homeFileList').append(row);
}
function homeScanComplete(data) {
  homeState.scan = data;
  homeState.stream?.close(); homeState.stream = null;
  $('glassTitle').textContent = '문서 폴더가 연결됐어요';
  $('homeFileCount').textContent = data.count;
  homeEvent('inventory', `${data.count}개 문서 · ${data.folders}개 폴더 확인`, `${(data.bytes / 1024).toFixed(1)} KB`);
  homeEvent('recommend', data.has_wiki ? '원본과 기존 위키를 찾았어요' : '질문으로 검증할 문서를 준비했어요', data.has_wiki ? 'raw/ → wiki/' : '아직 답변 성능을 평가하지 않았어요');
  $('recommendationTitle').textContent = data.has_wiki ? '기존 위키부터 확인해볼까요?' : '질문에 답하기 좋은 구조를 찾아볼까요?';
  $('recommendationCopy').textContent = data.has_wiki
    ? '원본을 근거로 현재 위키의 답변을 진단하거나, 새로운 구조를 만들고 비교할 수 있어요.'
    : '문서를 나누고, 필요한 파일을 찾아 질문에 답하게 합니다. 세대별 정확도와 읽은 분량을 비교해요.';
  $('homeAudit').hidden = !data.has_wiki;
  $('homeRecommendation').hidden = false;
  homeIdle('폴더 확인 완료 · 다음 작업을 선택해주세요');
  $('dir').value = data.root; $('updateRoot').value = data.root;
  savePrefs();
  if (data.has_wiki) loadHomeHealth(data.root);
}
/* 위키 점검 — raw/·wiki/를 LLM 없이 대조한다. 부가 정보라 실패해도 흐름은 그대로 둔다. */
async function loadHomeHealth(root) {
  const box = $('homeHealth');
  box.hidden = true; box.textContent = '';
  const revision = homeState.revision;
  try {
    const response = await fetch(`/api/health?dir=${encodeURIComponent(root)}`);
    const report = await response.json();
    if (revision !== homeState.revision || !response.ok || !report.layout) return;
    renderHomeHealth(report);
  } catch (_) { /* 점검 실패는 진단·구조 개선을 막지 않는다 */ }
}
function renderHomeHealth(report) {
  const c = report.counts;
  const issues = c.uncovered + c.stale + c.broken_links + c.ambiguous_links;
  homeEvent('health', issues ? `위키 점검 · 손볼 곳 ${issues}건` : '위키 점검 · 커버리지와 링크 이상 없음',
    `반영 안 된 원본 ${c.uncovered} · 오래된 페이지 ${c.stale} · 깨진 링크 ${c.broken_links} · 모호한 링크 ${c.ambiguous_links}`);
  const tiles = [['uncovered', '위키에 반영 안 된 원본'], ['stale', '원본보다 오래된 페이지'],
    ['broken_links', '깨진 링크'], ['ambiguous_links', '여러 페이지로 풀리는 링크'], ['not_in_index', '색인에 없는 페이지']];
  const list = (key, title, rows) => report[key].length
    ? `<details${key === 'uncovered' ? ' open' : ''}><summary>${title} ${report[key].length}개</summary><ol>${rows}</ol></details>` : '';
  const uncovered = report.uncovered.map(r => `<li><code>${esc(r.rel)}</code><small>${esc(r.modified)}</small>`
    + `<button type="button" class="health-apply" data-source="${esc(r.path)}">위키에 반영</button></li>`).join('');
  const stale = report.stale.map(r => `<li><code>${esc(r.page)}</code><small>${esc(r.updated)} 이후 바뀐 원본: `
    + `${r.newer_sources.map(s => esc(s.rel)).join(', ')}</small></li>`).join('');
  const links = [...report.broken_links.map(r => `<li><code>${esc(r.page)}</code><small>[[${esc(r.target)}]] 대상 없음</small></li>`),
    ...report.ambiguous_links.map(r => `<li><code>${esc(r.page)}</code><small>[[${esc(r.target)}]] → ${r.candidates.map(esc).join(' | ')}</small></li>`)].join('');
  const box = $('homeHealth');
  box.innerHTML = `<div class="health-head"><h3 id="healthTitle">위키 점검</h3>`
    + `<p>원본과 위키 페이지, 링크를 LLM 없이 대조했어요. 반영 안 된 원본은 구조를 아무리 바꿔도 답할 수 없어서 먼저 채우는 게 좋아요.</p></div>`
    + `<ul class="health-counts">${tiles.map(([k, label]) => `<li class="${c[k] ? 'warn' : ''}"><b>${c[k]}</b><span>${label}</span></li>`).join('')}</ul>`
    + list('uncovered', '반영 안 된 원본', uncovered) + list('stale', '원본보다 오래된 페이지', stale)
    + (links ? `<details><summary>링크 문제 ${report.broken_links.length + report.ambiguous_links.length}개</summary><ol>${links}</ol></details>` : '')
    + (report.versioned ? '' : '<p class="health-note">이 위키 폴더는 git으로 관리되지 않아요. 반영한 변경은 증분 갱신의 되돌리기로만 복구할 수 있어요.</p>');
  box.querySelectorAll('.health-apply').forEach(button => button.addEventListener('click', () => prepareHomeUpdate(button.dataset.source)));
  box.hidden = false;
}
/* 반영 안 된 원본 하나를 증분 갱신 폼에 채워 연다 — 변경안을 확인한 뒤에만 위키에 반영된다. */
function prepareHomeUpdate(source) {
  if (!homeState.scan) return;
  $('updateRoot').value = homeState.scan.root;
  $('updateSource').value = source;
  showView('opt');
  const panel = document.querySelector('.incremental-panel');
  panel.open = true;
  panel.scrollIntoView({ block: 'start' });
  $('updateGo').focus({ preventScroll: true });
}
function inspectHomeFolder(directory = $('homePath').value.trim()) {
  if (homeState.busy) return;
  if (!directory) { homeMessage('문서 폴더 경로를 입력해주세요.'); $('homePath').focus(); return; }
  homeState.stream?.close(); homeState.scan = null; homeState.job = null;
  const revision = ++homeState.revision;
  homeMessage(''); homeState.events.clear();
  $('homeEvents').replaceChildren(); $('homeFileList').replaceChildren(); $('homeResults').replaceChildren();
  $('homeFileCount').textContent = '0'; $('homeRecommendation').hidden = true;
  $('glassTitle').textContent = '폴더를 확인하고 있어요';
  $('glassStatus').textContent = '서버에서 문서 목록을 읽고 있어요';
  beginHomeActivity();
  const stream = new EventSource(`/api/inspect?dir=${encodeURIComponent(directory)}`);
  homeState.stream = stream;
  stream.onmessage = event => {
    if (revision !== homeState.revision) return;
    const data = JSON.parse(event.data);
    if (data.type === 'started') homeEvent('source', '폴더에 연결했어요', data.source);
    if (data.type === 'file') {
      appendHomeFile(data); $('homeFileCount').textContent = data.count;
      $('glassStatus').textContent = `${data.count}개 문서 확인 · ${data.name}`;
    }
    if (data.type === 'complete') homeScanComplete(data);
    if (data.type === 'error') {
      stream.close(); homeEvent('error', data.message); $('glassTitle').textContent = '폴더를 확인하지 못했어요';
      homeIdle('경로와 문서 수를 확인한 뒤 다시 연결해주세요');
    }
  };
  stream.onerror = () => {
    if (revision !== homeState.revision) return;
    stream.close(); homeEvent('connection', '서버 연결이 끊어졌어요'); homeIdle('다른 폴더 버튼으로 돌아가 다시 시도해주세요');
  };
}
function stopHomeScan() {
  if (homeState.job) { stopHomeJob(); return; }
  homeState.revision++; homeState.stream?.close(); homeState.stream = null;
  homeEvent('stopped', '폴더 확인을 중단했어요'); homeIdle('중단됨');
}
async function uploadHomeFolder(files, relativePaths = null) {
  if (homeState.busy) return;
  const items = [...files].map((file, index) => ({file, path: relativePaths?.[index] || file.webkitRelativePath || file.name}))
    .filter(({file, path}) => file.name.endsWith('.md') && !path.split('/').some(part => part.startsWith('.') || ['node_modules', '__pycache__'].includes(part)));
  if (!items.length) { homeMessage('폴더 안에 .md 문서가 없습니다.'); return; }
  if (items.length > 400 || items.some(x => x.file.size > 2 * 1024 * 1024) || items.reduce((sum,x) => sum + x.file.size,0) > 20 * 1024 * 1024) {
    homeMessage('최대 400개, 파일당 2MB, 총 20MB까지 업로드할 수 있어요.'); return;
  }
  const revision = ++homeState.revision;
  homeState.busy = true; homeSyncButtons(); homeMessage(''); $('homeStart').textContent = '업로드 중…';
  try {
    const commonRoot = items.every(x => x.path.includes('/') && x.path.split('/')[0] === items[0].path.split('/')[0]);
    const payload = await Promise.all(items.map(async ({file,path}) => ({name:file.name, path:commonRoot ? path.split('/').slice(1).join('/') : path, content:await file.text()})));
    const response = await fetch('/api/upload', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({files:payload})});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || '업로드하지 못했어요.');
    if (revision !== homeState.revision) return;
    $('homePath').value = data.dir;
    homeState.busy = false;
    inspectHomeFolder(data.dir);
  } catch (error) { homeState.busy = false; homeMessage(error.message); }
  finally { $('homeStart').innerHTML = '시작하기 <span aria-hidden="true">↗</span>'; $('homeFiles').value = ''; homeSyncButtons(); }
}
async function homeDrop(event) {
  event.preventDefault(); $('intakeSurface').classList.remove('drag-over');
  if (homeState.busy) return;
  const entries = [...event.dataTransfer.items].map(item => item.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) { uploadHomeFolder(event.dataTransfer.files); return; }
  const files = [], paths = [];
  let visited = 0;
  async function read(entry, parent = '') {
    if (++visited > 5000) throw new Error('폴더가 너무 큽니다. 문서 폴더만 선택해주세요.');
    if (entry.name.startsWith('.') || ['node_modules','__pycache__'].includes(entry.name)) return;
    const path = parent + entry.name;
    if (entry.isFile) {
      if (!entry.name.endsWith('.md')) return;
      if (files.length >= 400) throw new Error('문서는 최대 400개까지 업로드할 수 있어요.');
      const file = await new Promise((resolve,reject) => entry.file(resolve,reject));
      files.push(file); paths.push(path);
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      while (true) {
        const batch = await new Promise((resolve,reject) => reader.readEntries(resolve,reject));
        if (!batch.length) break;
        for (const child of batch) await read(child,path + '/');
      }
    }
  }
  try { for (const entry of entries) await read(entry); await uploadHomeFolder(files,paths); }
  catch (error) { homeMessage(error.message); }
}
async function startHomeJob(mode) {
  if (homeState.busy || !homeState.scan) return;
  const scan = homeState.scan;
  homeState.busy = true; homeSyncButtons();
  $('glassStatus').textContent = '작업을 요청하고 있어요';
  try {
    const response = await fetch('/api/runs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode, dir:scan.root, files:scan.files.map(file => file.path), generations:3, n_qa:6, backend:$('homeBackend').value, language:LANG})});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || '작업을 시작하지 못했어요.');
    homeState.job = data.id; homeState.lastResult = ''; $('homeResults').replaceChildren();
    try { sessionStorage.setItem('wikiopt_home_job', JSON.stringify({id:data.id, scan})); } catch (_) {}
    open_.add(data.id);
    beginHomeActivity(); $('homeStop').hidden = true;
    $('glassTitle').textContent = mode === 'audit' ? '현재 위키를 진단하고 있어요' : '답이 되는 구조를 찾고 있어요';
    homeEvent(`job-${data.id}`, mode === 'audit' ? '위키 진단을 요청했어요' : '구조 개선을 요청했어요', $('homeBackend').value);
    connectHomeJob(data.id); poll();
  } catch (error) { homeEvent(`request-${Date.now()}`, error.message); homeIdle('작업 요청 실패 · 다시 시도할 수 있어요'); }
}
function connectHomeJob(id) {
  homeState.stream?.close();
  const stream = new EventSource(`/api/runs/${encodeURIComponent(id)}/events`);
  homeState.stream = stream;
  stream.onmessage = event => {
    if (homeState.job !== id) return;
    const data = JSON.parse(event.data);
    if (data.type === 'job') renderHomeJob(data.job);
  };
  stream.onerror = () => {
    if (homeState.job === id && homeState.busy) {
      if (stream.readyState === EventSource.CLOSED) homeIdle('실행 정보를 찾을 수 없어요. 실행 기록을 확인해주세요.');
      else $('glassStatus').textContent = '연결을 복구하고 있어요 · 서버 작업은 계속 진행됩니다';
    }
  };
}
function renderHomeJob(job) {
  homeState.detail = job;
  const active = ['running','queued'].includes(job.status);
  homeState.busy = active;
  $('glassDot').classList.toggle('idle', !active);
  $('homeStop').hidden = !(job.status === 'queued' || (job.status === 'running' && ['summary','structure'].includes(job.mode)));
  $('homeStop').disabled = Boolean(job.cancel_requested);
  $('homeStop').textContent = job.cancel_requested ? '현재 단계 후 중단' : '중단';
  $('homeReset').hidden = active;
  $('glassStatus').textContent = job.status === 'running' ? '실행 중 · 다음 결과가 도착하면 자동으로 표시됩니다'
    : job.status === 'queued' ? '앞선 작업이 끝나기를 기다리고 있어요' : t('status_' + job.status);
  $('homeRecommendation').hidden = active;
  for (const activity of job.activity || []) homeEvent(`${job.id}-activity-${activity.index}`, activity.message, activity.detail);
  homeEvent(`${job.id}-${job.status}`, `${job.mode === 'structure' ? '구조 개선' : '위키 진단'} · ${t('status_' + job.status)}`);
  if (active && job.activity?.length) $('glassStatus').textContent = job.cancel_requested ? '현재 단계가 끝나면 중단합니다' : job.activity.at(-1).message;
  for (const run of job.activity?.length ? [] : job.runs || []) {
    const progress = run.progress || run.report;
    for (const generation of progress?.history || []) homeEvent(`${job.id}-${run.run_dir}-${generation.generation}`,
      `${generation.generation + 1}번째 구조 평가 완료`, `점수 ${generation.score?.total ?? '—'} · 정확도 ${generation.score?.accuracy ?? '—'}`);
  }
  if (job.result?.done != null) homeEvent(`${job.id}-result-${job.result.done}`, `${job.result.done} / ${job.result.total}개 문서 진단 완료`);
  let content = '';
  if (job.mode === 'audit' && job.result) content = auditView(job.result, job.status);
  else content = (job.runs || []).filter(run => run.progress || run.report).map(run => job.mode === 'structure' ? structureRun(run,job.id) : summaryRun(run,job.id)).join('');
  if (job.error) content += `<p class="err">${esc(job.error)}</p>`;
  if (content !== homeState.lastResult) {
    const focusedTab = $('homeResults').contains(document.activeElement) && document.activeElement.getAttribute('role') === 'tab';
    const expanded = [...$('homeResults').querySelectorAll('details')].map((item,index) => item.open ? index : -1);
    preserveFormState($('homeResults'),content); homeState.lastResult = content;
    const details = $('homeResults').querySelectorAll('details');
    expanded.filter(index => index >= 0).forEach(index => { if (details[index]) details[index].open = true; });
    if (focusedTab) $('homeResults').querySelector('[role=tab][aria-selected=true]')?.focus({preventScroll:true});
  }
  if (!active) {
    homeState.stream?.close(); homeState.stream = null;
    homeIdle(job.status === 'done' ? '작업 완료 · 아래에서 근거와 결과를 확인해주세요' : t('status_' + job.status));
    const seconds = Math.max(0, Math.floor((job.finished_at || Date.now()/1000) - job.created_at));
    $('glassClock').textContent = `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
    $('glassTitle').textContent = job.status === 'done' ? '결과를 확인할 준비가 됐어요' : '작업이 종료됐어요';
    if (job.status === 'done' && !content) homeEvent(`${job.id}-no-result`, '검증된 결과가 생성되지 않았어요. 실행 기록을 확인해주세요.');
  } else if (!homeState.clock) homeClock(job.created_at * 1000);
  homeSyncButtons();
}
function refreshHomeResult() {
  if (homeState.detail && !$('view-home').hidden) renderHomeJob(homeState.detail);
}
async function stopHomeJob() {
  $('homeStop').disabled = true;
  try {
    const response = await fetch(`/api/runs/${homeState.job}/cancel`,{method:'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    $('homeStop').textContent = '중단 요청됨';
  } catch (error) { $('glassStatus').textContent = error.message; $('homeStop').disabled = false; }
}
async function openHomeAdvanced() {
  if (!homeState.scan) return;
  $('dir').value = homeState.scan.root;
  showView('opt'); await loadDocs();
  document.querySelectorAll('#docs input[type=checkbox]').forEach(input => { input.checked = true; });
  selectMode('structure'); syncDocSelection();
}
const homeDropZone = $('intakeSurface');
homeDropZone.addEventListener('dragover', event => { event.preventDefault(); if (!homeState.busy) homeDropZone.classList.add('drag-over'); });
homeDropZone.addEventListener('dragleave', event => { if (!homeDropZone.contains(event.relatedTarget)) homeDropZone.classList.remove('drag-over'); });
homeDropZone.addEventListener('drop',homeDrop);
homeDropZone.addEventListener('pointermove', event => {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const rect = homeDropZone.getBoundingClientRect();
  homeDropZone.style.setProperty('--pointer-x', `${event.clientX - rect.left}px`);
  homeDropZone.style.setProperty('--pointer-y', `${event.clientY - rect.top}px`);
});
const homeReturn = document.createElement('button');
homeReturn.type = 'button'; homeReturn.className = 'ghost home-return'; homeReturn.textContent = '시작 화면'; homeReturn.addEventListener('click',showHome);
document.querySelector('.app-topbar').append(homeReturn);
try { if (!localStorage.getItem('wikiopt_theme')) setTheme('dark'); } catch (_) {}
$('homePath').value = $('dir').value;
$('homeBackend').value = $('backend').value;
$('homeGlass').insertBefore($('homeResults'), $('homeRecommendation'));
showHome();
try {
  const saved = JSON.parse(sessionStorage.getItem('wikiopt_home_job') || 'null');
  if (saved?.id && saved?.scan) {
    homeState.scan = saved.scan; homeState.job = saved.id;
    for (const file of saved.scan.files) appendHomeFile(file);
    homeScanComplete(saved.scan); beginHomeActivity(); $('homeStop').hidden = true;
    connectHomeJob(saved.id);
  }
} catch (_) { /* A stale session never prevents a fresh folder connection. */ }
document.addEventListener('visibilitychange', () => document.querySelector('.home-atmosphere').classList.toggle('paused', document.hidden));
