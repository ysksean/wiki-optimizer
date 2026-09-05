const $ = id => document.getElementById(id);
const open_ = new Set();   // 펼쳐둔 job id

// ---------- 뷰 전환 (좌측 네비) ----------
let curView = "opt";
function showView(v, save = true) {
  curView = v;
  for (const k of ["opt", "propose", "runs"]) {
    $("view-" + k).hidden = k !== v;
    $("nav-" + k).classList.toggle("on", k === v);
    $("mobile-" + k).classList.toggle("on", k === v);
    for (const prefix of ["nav-", "mobile-"]) {
      const item = $(prefix + k);
      if (k === v) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    }
  }
  $("topCurrent").textContent = t("nav_" + v);
  window.scrollTo(0, 0);
  if (save) savePrefs();
}

function focusFolder() {
  showView("opt");
  $("dir").focus();
}

let selectedDocsOnly = false;
function setDocFilter(selectedOnly) {
  selectedDocsOnly = selectedOnly;
  $("filterAll").classList.toggle("on", !selectedOnly);
  $("filterSelected").classList.toggle("on", selectedOnly);
  $("filterAll").setAttribute("aria-pressed", String(!selectedOnly));
  $("filterSelected").setAttribute("aria-pressed", String(selectedOnly));
  filterDocs();
}

function filterDocs() {
  const query = $("docSearch").value.trim().toLocaleLowerCase(LANG);
  document.querySelectorAll("#docs label").forEach(label => {
    const matchesQuery = !query || label.dataset.name.includes(query);
    const matchesSelection = !selectedDocsOnly || label.querySelector("input").checked;
    label.hidden = !(matchesQuery && matchesSelection);
  });
}

function selectMode(mode) {
  $("mode").value = mode;
  syncModeCards();
  savePrefs();
}

function syncModeCards() {
  const summary = $("mode").value === "summary";
  $("modeSummary").classList.toggle("on", summary);
  $("modeStructure").classList.toggle("on", !summary);
  $("modeSummary").setAttribute("aria-pressed", String(summary));
  $("modeStructure").setAttribute("aria-pressed", String(!summary));
}

function syncExperimentSummary() {
  $("gensSummary").textContent = $("gens").value;
  $("nqaSummary").textContent = $("nqa").value;
  $("backendSummary").textContent = $("backend").value === "claude" ? "Claude" : "Codex";
}

function syncWorkspaceSummary() {
  const dir = $("dir").value.trim();
  const boxes = [...document.querySelectorAll("#docs input[type=checkbox]")];
  const ready = Boolean(loadedDir) && dir === loadedDir;
  const name = dir.split("/").filter(Boolean).pop() || "—";
  $("currentPath").textContent = dir || t("path_not_set");
  $("pathChip").classList.toggle("ready", ready);
  $("currentWorkspaceName").textContent = name;
  $("currentWorkspaceMeta").textContent = ready ? t("docs_selected", boxes.filter(x => x.checked).length, boxes.length) : t("workspace_empty");
  $("topWorkspace").textContent = ready ? name : "wiki-optimizer";
}

// ---------- 구조 제안 소스 리스트 ----------
let srcState = [];   // [{kind:"dir"|"upload", value, label}]
function renderSrcList() {
  const box = $("srcList");
  box.innerHTML = "";
  const cnt = $("srcCount");
  if (cnt) cnt.textContent = String(srcState.length);
  if (!srcState.length) { const e = document.createElement("div"); e.className = "docs-empty"; e.textContent = t("src_empty"); box.appendChild(e); return; }
  for (let i = 0; i < srcState.length; i++) {
    const it = srcState[i];
    const row = document.createElement("div");
    row.className = "srcrow";
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = it.kind === "upload" ? t("src_kind_file") : t("src_kind_dir");
    const val = document.createElement("span");
    val.className = "val";
    val.textContent = it.label || it.value;
    val.title = it.value;
    const x = document.createElement("button");
    x.type = "button"; x.className = "x"; x.textContent = "✕";
    x.setAttribute("aria-label", t("src_remove"));
    x.onclick = () => { srcState.splice(i, 1); renderSrcList(); savePrefs(); };
    row.append(kind, val, x);
    box.appendChild(row);
  }
}
function addSrcPath(path) {
  path = (path || "").trim();
  if (!path) return;
  if (!srcState.some(x => x.value === path)) srcState.push({ kind: "dir", value: path });
  renderSrcList(); savePrefs();
}
async function pickSrcDir() {
  const r = await fetch("/api/pick-dir");
  const j = await r.json();
  if (j.error) { $("msg3").textContent = j.error; return; }
  if (j.dir) addSrcPath(j.dir);
}
async function uploadSrcFiles(files) {
  if (!files || !files.length) return;
  $("msg3").textContent = "";
  const payload = [];
  for (const f of files) payload.push({ name: f.name, content: await f.text() });
  const r = await fetch("/api/upload", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files: payload }) });
  const j = await r.json();
  if (j.error) { $("msg3").textContent = j.error; return; }
  srcState.push({ kind: "upload", value: j.dir,
    label: t("src_upload_label", j.saved[0], j.saved.length) });
  renderSrcList(); savePrefs();
  $("srcFile").value = "";
}

// ---------- 점수 히트맵 (Braintrust) ----------
function heat(v) {
  if (v == null || v === "—" || isNaN(v)) return "";
  return v >= 0.75 ? ' class="hG"' : v >= 0.5 ? ' class="hM"' : ' class="hB"';
}
const LOCALES = { ko: "ko-KR", en: "en-US", zh: "zh-CN" };
let loadedDir = "";
let docsRequest = 0;
let docsController = null;
let requestPending = false;
let hasActiveJob = false;
let submittingMode = null;
let activeJobMode = null;
let jobStatusesReady = false;
let jobStatusSnapshot = new Map();

// ---------- i18n ----------
const I18N = {
  ko: {
    workbench_eyebrow: "Knowledge Workbench", opt_title: "위키 최적화", opt_desc: "문서를 고르고 실행하면 세대별 점수와 전략 변화가 오른쪽에 쌓입니다. 원본은 바뀌지 않아요.",
    current_wiki: "현재 위키", workspace_empty: "폴더를 연결해 시작하세요", path_not_set: "연결된 폴더 없음", change_folder: "폴더 변경",
    docs_title: "실험할 문서", docs_hint: "핵심 자료만 선택할수록 결과를 빠르게 비교할 수 있어요.", docs_empty: "위키 폴더를 연결하면 문서가 여기에 표시됩니다.",
    doc_search: "문서 이름 검색", filter_all: "전체", filter_selected: "선택됨", experiment_friendly_desc: "목표만 고르면 나머지는 추천값으로 시작합니다.",
    mode_summary_title: "A · 요약 개선", mode_summary_desc: "문서별 요약 전략을 진화시킵니다", mode_structure_title: "B · 구조 개선", mode_structure_desc: "폴더와 문서 구성을 재설계합니다",
    advanced_settings: "고급 설정 열기", safe_note: "원본과 기존 wiki 파일은 그대로 유지됩니다.", next_actions_desc: "현재 상태를 진단하거나 검증된 전략으로 새 파일을 만듭니다.",
    mobile_menu: "모바일 메뉴", new_wiki_eyebrow: "New wiki", prop_friendly_title: "새 위키 구조를 설계하세요", prop_friendly_desc: "자료와 사용 목적을 바탕으로 폴더와 문서 구조를 제안합니다.",
    runs_eyebrow: "Experiment history", runs_desc: "점수 변화와 세대별 전략을 비교하고 원하는 결과만 적용하세요.",
    score_improved: n => `${n}% 개선`, evolution_trail: "Evolution Trail", baseline: "기준", best_label: "최고",
    action_running: "실행 중…", progress_label: "작업 진행 단계", progress_wait: "대기", progress_run: "실행", progress_result: "결과 생성", progress_done: "완료",
    toast_started: m => `${m} 작업을 시작했습니다.`, toast_done: m => `${m} 작업이 완료되었습니다.`, toast_error: m => `${m} 작업을 완료하지 못했습니다.`, toast_cancelled: m => `${m} 작업이 중지되었습니다.`,
    subtitle: "wiki 폴더를 지정하고 요약(A) / 구조(B) 최적화를 돌려 결과 변화를 확인합니다",
    theme: "테마", theme_system: "시스템", theme_light: "라이트", theme_dark: "다크",
    language: "언어", language_tip: "화면과 LLM 출력(요약·질문·답변) 언어를 함께 바꿉니다",
    setup_title: "위키 불러오기", setup_desc: "최적화할 원본 마크다운이 있는 폴더를 선택하세요.",
    experiment_title: "실험 설정", experiment_desc: "진화 방식과 반복 횟수를 정한 뒤 선택한 문서로 실험을 시작합니다.",
    folder_label: "wiki 폴더 경로 (.md 모음 — raw/ 하위 폴더가 있으면 그쪽을 읽음)",
    folder_ph: "~/dev/llm_wiki  또는  data/raw",
    pick_dir: "폴더 선택…", load_docs: "문서 불러오기",
    mode: "모드", mode_a: "A — 요약 전략 진화 (문서별)", mode_b: "B — 폴더 구조 진화 (선택 문서 전체)",
    gens: "세대 수", gens_help: "세대당 문서별 약 1–2분",
    nqa: "질문 수", nqa_help: "정답률 평가 문항 (2–12)",
    backend: "백엔드", backend_claude: "claude (Claude Code 구독)", backend_codex: "codex (ChatGPT 구독)",
    run_exp: "실험 실행", starting: "시작 중…", starting_job: "작업을 시작하는 중…", job_in_progress: "작업 실행 중 · 완료 후 새 작업을 시작할 수 있습니다",
    wiki_title: "내 위키 진단 · 최적화",
    wiki_desc: "위 폴더(raw/ + wiki/)를 기준으로 현재 요약을 채점(Before)하고, best 전략으로 다시 요약해(After) 무엇이 바뀌는지 비교합니다. 원본과 기존 wiki는 수정하지 않습니다.",
    btn_audit: "현재 위키 진단 (Before)", btn_apply: "최적화 생성 (After)",
    strategy_custom: "전략 직접 지정 (비우면 실험 결과의 best 전략 사용)",
    strategy_label: "직접 지정할 최적화 전략",
    docs_selected: (n,t) => `${t}개 문서 중 ${n}개 선택`, select_all: "모두 선택", clear_all: "선택 해제",
    runs_title: "실행 기록", runs_count: n => `${n}건`,
    need_dir: "폴더 경로를 입력하세요", need_dir_first: "위에서 폴더를 먼저 지정하세요",
    no_md: "md 파일이 없습니다", need_docs: "문서를 하나 이상 선택하세요", load_failed: "문서를 불러오지 못했습니다. 경로와 서버 상태를 확인하세요.",
    request_failed: "요청에 실패했습니다. 잠시 후 다시 시도하세요.",
    no_jobs: "아직 실행한 실험이 없습니다", no_jobs_hint: "문서를 불러오고 실험을 실행하면 세대별 점수와 전략 변화가 여기에 표시됩니다.", no_propose_hint: "소스와 태스크를 넣고 실행하면 제안된 폴더 구조가 여기에 표시됩니다.",
    waiting_gen: "아직 첫 세대 결과 대기 중…",
    filter_all: "전체", filter_mode: "모드 필터", filter_status: "상태 필터", filter_active: "진행 중", runs_search: "문서 이름으로 찾기",
    runs_filtered: (n, total) => `${n} / ${total}건`, chip_best: "best", chip_failed: n => `판정 실패 ${n}`,
    stop: "중지", stopping: "중지 요청됨…",
    preparing_q: "질문 세트 준비 중…", failed: "실패",
    status_running: "실행 중", status_done: "완료", status_error: "오류", status_queued: "대기 중", status_cancelled: "중지됨", status_interrupted: "중단됨",
    run_meta: (mode,backend,gens,time) => [backend, ...(mode === "summary" || mode === "structure" ? [`${gens}세대`] : []), time].join(" · "),
    best_gen: n => `최고 점수 (${n}세대)`, held_out: "검증", train: "학습", gen_short: n => `${n}세대`,
    generation_label: "세대", ratio: "비율", total: "종합", acc_short: "정확도", eff_short: "효율",
    chart_label: n => `세대별 검증 점수 추세${n == null ? "" : `, 최고 점수 ${n}세대`}`,
    mode_summary: "A 요약", mode_structure: "B 구조", mode_audit: "진단", mode_apply: "최적화",
    audit_title: "현재 위키 진단", scoring_docs: (d,t) => ` — ${t}개 문서 중 ${d}개 채점 중…`,
    r_kpi_acc: "정확도", r_kpi_eff: "효율", r_kpi_read: "평균 읽은 글자",
    r_kpi_cov: "페이지 커버리지",
    r_graph: "백링크 그래프 — 색 = 정답률 · 크기 = 인링크 · 회색 테두리 = 미사용",
    r_pages: "페이지별 진단", th_page: "페이지", th_inlinks: "인링크",
    th_uses: "읽힘", th_rate: "정답률", r_unused: "미사용",
    r_questions: "질문별 라우팅 상세", th_src_doc: "출처 문서",
    r_parse_warn: d => `judge 파싱 실패 문서: ${d} — 해당 점수는 신뢰 불가`,
    avg_score: n => `평균 점수 (${n}개 채점)`,
    th_doc: "문서", th_raw: "raw", th_summary: "요약", th_score: "점수 (종합 · 정확도 · 효율)", none: "없음",
    apply_title: "최적화 Before → After", generating: (d,t) => ` — ${d}/${t} 생성 중…`,
    before_avg: "Before 평균", after_avg: "After 평균",
    used_strategy: s => `사용한 전략 (${s})`,
    after_files: p => `After 파일: ${p}/ (원본·기존 wiki는 그대로)`,
    diff_legend: (b,a) => `기존 요약 대비 변경 (<del>삭제</del> / <ins>추가</ins>) · ${b} → ${a}자`,
    new_summary: "기존 요약 없음 — 신규 생성",
    gen_progress: (d,t) => `(${d}/${t}세대)`, accuracy: "정확도", efficiency: "효율",
    "arm_evolve": "진화", "arm_control": "대조군 · 무진화", "arm_evolve-nohist": "진화 · 이력 없음", "arm_evolve-wiki": "진화 · 패턴 위키",
    parse_failed_note: n => `${n}개 세대는 판정 파싱 실패로 점수가 무효 — 집계에서 제외`, strategy_unchanged: "변경 없음",
    vs_baseline: "vs 기준", delta_flat: "= 기준", tile_best: "best held-out", tile_gain: "개선폭", tile_gens: "세대", tile_judge: "판정",
    tile_failed: n => `${n} 실패`, tile_valid: "전부 유효", tile_excluded: "집계 제외", tile_parse_ok: "판정 정상", baseline_val: n => `기준 ${n}`, best_gen_short: n => `best ${n}세대`, and_more: n => `외 ${n}`,
    th_elapsed: "소요", provenance_title: "재현성 — 백엔드 · 모델 · 코드", strategy_diff_hint: "이전 세대 대비 바뀐 부분만 강조",
    strategy_per_gen: "세대별 전략 프롬프트", th_strategy: "전략",
    best_summary: "best 요약 보기", len_vs_raw: "원본 대비 길이",
    structure_title_n: n => `구조 진화 · 문서 ${n}개`, doc_list: "문서 목록",
    structure_title: d => `구조 진화 — ${d}`,
    files_per_gen: "세대별 파일 구성", th_files: "파일",
    structure_head: (d, f) => `구조 재편 제안 · 문서 ${d}개 → 파일 ${f}개`,
    structure_intro: (n, b) => `AI가 "문서를 어떻게 나눌지" 규칙을 ${n}번 고쳐 쓰며 시도했고, 각 시도를 같은 질문 세트로 채점했습니다. 점수가 가장 높은 ${b}차 시도가 제안입니다.`,
    structure_intro_running: (d, n) => `AI가 "문서를 어떻게 나눌지" 규칙을 고쳐 쓰며 시도하는 중입니다 · ${d}/${n}차`,
    structure_hero: n => `${n}차 시도 종합`, attempt_n: n => `${n}차 시도`, attempt_meta: (f, a) => `파일 ${f}개 · 정확도 ${a}`,
    rule_diff: (n, p) => `${n}차 시도의 분할 규칙 — ${p}차에서 바뀐 부분`, rule_seed: n => `${n}차 시도의 분할 규칙 — 기본값`, rule_rewritten: (n, p) => `${n}차 시도의 분할 규칙 — ${p}차 결과를 보고 거의 새로 씀`, rule_prev: p => `${p}차 규칙 보기`,
    map_now: "지금 문서", map_from: "출처", map_proposed: "제안 구조", map_tip: "파일에 마우스를 올리거나 클릭하면 어느 원본 문서에서 왔는지 선으로 표시됩니다.",
    map_no_sources: "이 실행은 출처 기록 이전 버전이라 문서→파일 연결선이 없습니다. 새로 실행하면 표시됩니다.",
    sb_formula: (a, e) => `종합 = 정확도 ${a} × 효율 ${e}`, sb_acc: (c, n) => `질문 ${n}개 중 ${c}개 정답.`, sb_acc_how: "질문은 원본 문서 전체에서 미리 만들어 모든 시도에 같은 것을 씁니다. 제안 구조의 파일만 골라 읽고 답한 뒤, 정답과 사실상 같으면 1점(LLM 판정).",
    sb_eff: (r, t) => `질문당 평균 ${r}자만 읽음 / 원본 전체 ${t}자.`, sb_eff_how: "효율 = 1 − (평균 읽은 글자 ÷ 원본 전체 글자). 적게 읽고 맞힐수록 높습니다.",
    sb_heldout_tag: "검증 질문 기준", sb_split_label: "질문 분리", sb_split: (h, tr) => `채택 판단은 검증(held-out) 질문 ${h}개로만 합니다. 학습(train) 질문 ${tr}개는 규칙을 고칠 때만 보여줍니다 — 고친 규칙이 본 적 없는 질문에도 통하는지 확인하기 위해서입니다.`, sb_train_score: (t, a) => `학습 질문 점수: ${t} (정확도 ${a}).`, sb_acc_heldout: (c, n) => `검증 질문 ${n}개 중 ${c}개 정답.`, sb_train_questions: n => `학습 질문 판정 · ${n}개 (규칙 수정에 쓴 것)`,
    sb_questions: n => `질문별 판정 · ${n}개`, th_expected: "기대한 답", th_answer: "구조로 낸 답",
    n_chars: n => `${n}자`, n_sources: n => `출처 ${n}개`, file_previews: "제안 파일 본문 미리보기", score_trend: "시도별 점수 추이",
    routing: "best 구조의 질문별 라우팅", th_q: "질문", th_picked: "읽은 파일", th_chars: "글자", th_correct: "정답",
    prop_title: "구조 제안", mode_propose: "구조 제안", prop_rail_desc: "재료가 될 소스와 태스크를 적습니다.", runs_list_title: "기록",
    rail_title: "설정", rail_desc: "문서와 실험 방식을 고릅니다.",
    timeline_title: "실행", timeline_desc: "가장 최근 실행이 여기 표시됩니다.",
    nav_opt: "위키 최적화", nav_propose: "구조 제안", nav_runs: "실행 기록",
    src_add_dir: "＋ 폴더 선택", src_add_files: "＋ 파일 업로드",
    src_manual_ph: "경로 직접 입력 후 Enter (예: ~/repo/docs)",
    src_kind_dir: "폴더", src_kind_file: "파일", src_remove: "소스 제거",
    src_upload_label: (first, n) => n > 1 ? `${first} 외 ${n - 1}개 (업로드)` : `${first} (업로드)`,
    prop_desc: "위키가 아직 없을 때 씁니다. 소스와 태스크를 바탕으로 폴더 구조를 제안하고, 근거 없는 축(gap)을 알려줍니다.",
    prop_sources: "소스", prop_sources_hint: "위키의 재료가 될 레포·문서 폴더를 추가하세요.", src_empty: "폴더를 고르거나 경로를 입력하면 소스가 여기에 표시됩니다.",
    prop_task: "태스크 설명", prop_task_ph: "이 위키의 소비자·소비 시점, 실제로 던질 질문 예시 2~3개, 데이터의 성질, 산출물 용도",
    btn_propose: "구조 제안 실행", prop_seed: "이어서 개선", prop_seed_tip: "이전 제안 run의 best 분할 전략을 seed로 재사용합니다 (warm start)",
    need_sources_task: "소스 폴더와 태스크 설명을 입력하세요",
    prop_run_title: "구조 제안", prop_unscored: "채점 불가 — 소스에 정답 근거가 있는 질문이 0개 (순수 백지)",
    prop_grounded: "근거 확보 질문", prop_gapq: "근거 없는 질문 (gap)", prop_gapq_desc: "필요한데 소스에 근거가 없는 축 — 추가로 확보할 데이터 목록입니다",
    prop_tree: "제안 구조", prop_pages_per_gen: "세대별 페이지",
    prop_export: "골격 내보내기", prop_export_ph: "골격을 쓸 폴더 경로", prop_export_done: (w,s) => `생성 ${w}개, 기존 파일 유지 ${s}개`,
    th_page: "페이지", th_purpose: "purpose", th_sources: "sources",
    chart_aria: (n, best) => `세대별 검증 점수 추이, 총 ${n}세대` + (best == null ? "" : `, 최고 점수 ${best}세대`),
  },
  en: {
    workbench_eyebrow: "Knowledge Workbench", opt_title: "Optimize wiki", opt_desc: "Pick documents and run — generation scores and strategy changes stack up on the right. Originals stay untouched.",
    current_wiki: "Current wiki", workspace_empty: "Connect a folder to begin", path_not_set: "No folder connected", change_folder: "Change folder",
    docs_title: "Documents to experiment on", docs_hint: "A focused selection makes results faster to compare.", docs_empty: "Connect a wiki folder to see its documents here.",
    doc_search: "Search document names", filter_all: "All", filter_selected: "Selected", experiment_friendly_desc: "Choose a goal and start with recommended settings.",
    mode_summary_title: "A · Improve summaries", mode_summary_desc: "Evolve a summary strategy for each document", mode_structure_title: "B · Improve structure", mode_structure_desc: "Redesign folders and document organization",
    advanced_settings: "Open advanced settings", safe_note: "Original and existing wiki files remain untouched.", next_actions_desc: "Audit the current state or create new files with a proven strategy.",
    mobile_menu: "Mobile menu", new_wiki_eyebrow: "New wiki", prop_friendly_title: "Design a new wiki structure", prop_friendly_desc: "Use source material and intent to propose folders and documents.",
    runs_eyebrow: "Experiment history", runs_desc: "Compare score and strategy changes, then apply only the result you want.",
    score_improved: n => `${n}% improved`, evolution_trail: "Evolution Trail", baseline: "Baseline", best_label: "Best",
    action_running: "Running…", progress_label: "Job progress", progress_wait: "Queued", progress_run: "Running", progress_result: "Building result", progress_done: "Done",
    toast_started: m => `${m} started.`, toast_done: m => `${m} completed.`, toast_error: m => `${m} could not be completed.`, toast_cancelled: m => `${m} stopped.`,
    subtitle: "Point at a wiki folder, run summary (A) / structure (B) optimization, and watch results evolve",
    theme: "Theme", theme_system: "System", theme_light: "Light", theme_dark: "Dark",
    language: "Language", language_tip: "Switches both the UI and LLM outputs (summaries, questions, answers)",
    setup_title: "Load your wiki", setup_desc: "Choose the folder containing the source Markdown you want to optimize.",
    experiment_title: "Configure experiment", experiment_desc: "Choose an evolution mode and iteration count, then run it on the selected documents.",
    folder_label: "Wiki folder path (.md files — raw/ subfolder is used if present)",
    folder_ph: "~/dev/llm_wiki  or  data/raw",
    pick_dir: "Choose folder…", load_docs: "Load documents",
    mode: "Mode", mode_a: "A — evolve summary strategy (per document)", mode_b: "B — evolve folder structure (all selected)",
    gens: "Generations", gens_help: "About 1–2 min per document each",
    nqa: "Questions", nqa_help: "Accuracy sample size (2–12)",
    backend: "Backend", backend_claude: "claude (Claude Code subscription)", backend_codex: "codex (ChatGPT subscription)",
    run_exp: "Run experiment", starting: "Starting…", starting_job: "Starting job…", job_in_progress: "Job in progress · new actions unlock when it finishes",
    wiki_title: "Audit & optimize my wiki",
    wiki_desc: "Scores your current summaries (Before) against the folder above (raw/ + wiki/), then re-summarizes with the best strategy (After) so you can compare. Originals and existing wiki files are never modified.",
    btn_audit: "Audit current wiki (Before)", btn_apply: "Generate optimized (After)",
    strategy_custom: "Custom strategy (leave empty to use the best strategy from experiments)",
    strategy_label: "Custom optimization strategy",
    docs_selected: (n,t) => `${n} of ${t} document${t === 1 ? "" : "s"} selected`, select_all: "Select all", clear_all: "Clear selection",
    runs_title: "Runs", runs_count: n => `${n} total`,
    need_dir: "Enter a folder path", need_dir_first: "Set the folder above first",
    no_md: "No md files found", need_docs: "Select at least one document", load_failed: "Documents could not be loaded. Check the path and server status.",
    request_failed: "The request failed. Please try again.",
    no_jobs: "No experiments yet", no_jobs_hint: "Load documents and run an experiment to see generation scores and strategy changes here.", no_propose_hint: "Add sources and a task, then run to see the proposed folder structure here.",
    waiting_gen: "Waiting for the first generation…",
    filter_all: "All", filter_mode: "Filter by mode", filter_status: "Filter by status", filter_active: "Active", runs_search: "Search by document",
    runs_filtered: (n, total) => `${n} / ${total}`, chip_best: "best", chip_failed: n => `${n} parse failed`,
    stop: "Stop", stopping: "Stop requested…",
    preparing_q: "Preparing question set…", failed: "Failed",
    status_running: "Running", status_done: "Done", status_error: "Error", status_queued: "Queued", status_cancelled: "Stopped", status_interrupted: "Interrupted",
    run_meta: (mode,backend,gens,time) => [backend, ...(mode === "summary" || mode === "structure" ? [`${gens} gen`] : []), time].join(" · "),
    best_gen: n => `Best score (gen ${n})`, held_out: "Held-out", train: "Train", gen_short: n => `gen ${n}`,
    generation_label: "Generation", ratio: "Ratio", total: "Total", acc_short: "Accuracy", eff_short: "Efficiency",
    chart_label: n => `Validation score by generation${n == null ? "" : `, best at generation ${n}`}`,
    mode_summary: "A summary", mode_structure: "B structure", mode_audit: "Audit", mode_apply: "Optimize",
    audit_title: "Current wiki audit", scoring_docs: (d,t) => ` — scored ${d} of ${t} documents…`,
    r_kpi_acc: "accuracy", r_kpi_eff: "efficiency", r_kpi_read: "avg chars read",
    r_kpi_cov: "page coverage",
    r_graph: "Backlink graph — color = correct rate · size = inlinks · gray outline = unused",
    r_pages: "Per-page results", th_page: "page", th_inlinks: "inlinks",
    th_uses: "reads", th_rate: "correct", r_unused: "unused",
    r_questions: "Per-question routing", th_src_doc: "source doc",
    r_parse_warn: d => `judge parse failed for: ${d} — those scores are unreliable`,
    avg_score: n => `average score (${n} scored)`,
    th_doc: "document", th_raw: "raw", th_summary: "summary", th_score: "score (total · accuracy · efficiency)", none: "none",
    apply_title: "Optimization Before → After", generating: (d,t) => ` — generating ${d}/${t}…`,
    before_avg: "Before avg", after_avg: "After avg",
    used_strategy: s => `strategy used (${s})`,
    after_files: p => `After files: ${p}/ (originals & existing wiki untouched)`,
    diff_legend: (b,a) => `changes vs existing summary (<del>removed</del> / <ins>added</ins>) · ${b} → ${a} chars`,
    new_summary: "no existing summary — newly generated",
    gen_progress: (d,t) => `(gen ${d}/${t})`, accuracy: "accuracy", efficiency: "efficiency",
    "arm_evolve": "evolve", "arm_control": "control · no evolution", "arm_evolve-nohist": "evolve · no history", "arm_evolve-wiki": "evolve · pattern wiki",
    parse_failed_note: n => `${n} generation(s) have invalid scores (judge parse failed) — excluded from aggregates`, strategy_unchanged: "unchanged",
    vs_baseline: "vs baseline", delta_flat: "= baseline", tile_best: "best held-out", tile_gain: "Gain", tile_gens: "Generations", tile_judge: "Judge",
    tile_failed: n => `${n} failed`, tile_valid: "all valid", tile_excluded: "excluded", tile_parse_ok: "parse ok", baseline_val: n => `baseline ${n}`, best_gen_short: n => `best gen ${n}`, and_more: n => `+${n} more`,
    th_elapsed: "elapsed", provenance_title: "Provenance — backend · model · code", strategy_diff_hint: "changes vs previous generation are highlighted",
    strategy_per_gen: "strategy prompt per generation", th_strategy: "strategy",
    best_summary: "view best summary", len_vs_raw: "length vs raw",
    structure_title_n: n => `Structure evolution · ${n} documents`, doc_list: "Documents",
    structure_title: d => `Structure evolution — ${d}`,
    files_per_gen: "files per generation", th_files: "files",
    structure_head: (d, f) => `Proposed restructure · ${d} documents → ${f} files`,
    structure_intro: (n, b) => `The AI rewrote its "how to split the documents" rule ${n} times, scoring each attempt on the same question set. Attempt ${b} scored highest and is the proposal.`,
    structure_intro_running: (d, n) => `The AI is rewriting its "how to split" rule and trying again · attempt ${d}/${n}`,
    structure_hero: n => `attempt ${n} total`, attempt_n: n => `Attempt ${n}`, attempt_meta: (f, a) => `${f} files · accuracy ${a}`,
    rule_diff: (n, p) => `Attempt ${n} split rule — changes since attempt ${p}`, rule_seed: n => `Attempt ${n} split rule — default`, rule_rewritten: (n, p) => `Attempt ${n} split rule — largely rewritten after attempt ${p}`, rule_prev: p => `Show attempt ${p} rule`,
    map_now: "Current documents", map_from: "From", map_proposed: "Proposed structure", map_tip: "Hover or click a file to see which source documents it came from.",
    map_no_sources: "This run predates source tracking, so no document→file lines are available. Run again to see them.",
    sb_formula: (a, e) => `Total = accuracy ${a} × efficiency ${e}`, sb_acc: (c, n) => `${c} of ${n} questions correct.`, sb_acc_how: "Questions are generated once from the full source documents and reused for every attempt. The router reads only the proposed files, answers, and an LLM judge scores 1 if the answer matches the expected one.",
    sb_eff: (r, t) => `Read ${r} chars per question on average / ${t} chars in the sources.`, sb_eff_how: "Efficiency = 1 − (average chars read ÷ total source chars). Higher when less reading still gets the answer.",
    sb_heldout_tag: "held-out", sb_split_label: "Question split", sb_split: (h, tr) => `Adoption is judged on ${h} held-out questions only. The ${tr} training questions are shown to the rule-writer alone, so we can check whether a revised rule also works on questions it has never seen.`, sb_train_score: (t, a) => `Training score: ${t} (accuracy ${a}).`, sb_acc_heldout: (c, n) => `${c} of ${n} held-out questions correct.`, sb_train_questions: n => `Training-question verdicts · ${n} (used to revise the rule)`,
    sb_questions: n => `Per-question verdicts · ${n}`, th_expected: "Expected answer", th_answer: "Answer from structure",
    n_chars: n => `${n} chars`, n_sources: n => `${n} sources`, file_previews: "Preview proposed file contents", score_trend: "Score by attempt",
    routing: "per-question routing of best structure", th_q: "question", th_picked: "files read", th_chars: "chars", th_correct: "correct",
    prop_title: "Propose structure", mode_propose: "Propose", prop_rail_desc: "Add source material and describe the task.", runs_list_title: "History",
    rail_title: "Setup", rail_desc: "Pick documents and an experiment.",
    timeline_title: "Run", timeline_desc: "The latest run shows up here.",
    nav_opt: "Optimize wiki", nav_propose: "Propose structure", nav_runs: "Runs",
    src_add_dir: "+ Choose folder", src_add_files: "+ Upload files",
    src_manual_ph: "Type a path and press Enter (e.g. ~/repo/docs)",
    src_kind_dir: "dir", src_kind_file: "file", src_remove: "Remove source",
    src_upload_label: (first, n) => n > 1 ? `${first} +${n - 1} more (uploaded)` : `${first} (uploaded)`,
    prop_desc: "For when there is no wiki yet. Proposes a folder structure from your sources and task, flagging unbacked axes as gaps.",
    prop_sources: "Sources", prop_sources_hint: "Add repo and document folders the wiki will be built from.", src_empty: "Pick a folder or type a path to list sources here.",
    prop_task: "Task description", prop_task_ph: "Who consumes this wiki and when, 2-3 example questions, nature of the data, intended use",
    btn_propose: "Propose structure", prop_seed: "continue improving", prop_seed_tip: "Warm-start from the best split strategy of previous proposal runs",
    need_sources_task: "Enter source folders and a task description",
    prop_run_title: "Structure proposal", prop_unscored: "Unscorable — zero questions have grounded answers in the sources (pure blank slate)",
    prop_grounded: "grounded questions", prop_gapq: "unbacked questions (gap)", prop_gapq_desc: "Needed but no evidence in sources — this is your data acquisition list",
    prop_tree: "proposed structure", prop_pages_per_gen: "pages per generation",
    prop_export: "Export skeleton", prop_export_ph: "folder to write skeleton into", prop_export_done: (w,s) => `wrote ${w}, kept ${s} existing`,
    th_page: "page", th_purpose: "purpose", th_sources: "sources",
    chart_aria: (n, best) => `Held-out score by generation, ${n} generations` + (best == null ? "" : `, best generation ${best}`),
  },
  zh: {
    workbench_eyebrow: "Knowledge Workbench", opt_title: "Wiki 优化", opt_desc: "选择文档并运行，各代评分与策略变化会显示在右侧。原文不会被修改。",
    current_wiki: "当前 wiki", workspace_empty: "连接文件夹后开始", path_not_set: "尚未连接文件夹", change_folder: "更换文件夹",
    docs_title: "实验文档", docs_hint: "聚焦核心资料，可以更快比较结果。", docs_empty: "连接 wiki 文件夹后，文档会显示在这里。",
    doc_search: "搜索文档名称", filter_all: "全部", filter_selected: "已选择", experiment_friendly_desc: "只需选择目标，其余使用推荐设置。",
    mode_summary_title: "A · 改进摘要", mode_summary_desc: "为每篇文档进化摘要策略", mode_structure_title: "B · 改进结构", mode_structure_desc: "重新设计文件夹与文档组织",
    advanced_settings: "打开高级设置", safe_note: "原文和现有 wiki 文件保持不变。", next_actions_desc: "诊断当前状态，或用验证过的策略生成新文件。",
    mobile_menu: "移动菜单", new_wiki_eyebrow: "New wiki", prop_friendly_title: "设计新的 wiki 结构", prop_friendly_desc: "根据资料和使用目的提出文件夹与文档结构。",
    runs_eyebrow: "Experiment history", runs_desc: "比较分数和各代策略变化，只应用需要的结果。",
    score_improved: n => `提升 ${n}%`, evolution_trail: "Evolution Trail", baseline: "基准", best_label: "最高",
    action_running: "运行中…", progress_label: "任务进度", progress_wait: "等待", progress_run: "运行", progress_result: "生成结果", progress_done: "完成",
    toast_started: m => `${m}已开始。`, toast_done: m => `${m}已完成。`, toast_error: m => `${m}未能完成。`, toast_cancelled: m => `${m}已停止。`,
    subtitle: "指定 wiki 文件夹，运行摘要（A）/ 结构（B）优化，查看结果如何演化",
    theme: "主题", theme_system: "跟随系统", theme_light: "浅色", theme_dark: "深色",
    language: "语言", language_tip: "同时切换界面语言和 LLM 输出（摘要、问题、答案）",
    setup_title: "加载 wiki", setup_desc: "选择包含待优化 Markdown 原文的文件夹。",
    experiment_title: "配置实验", experiment_desc: "选择进化模式和迭代次数，然后对所选文档运行实验。",
    folder_label: "wiki 文件夹路径（.md 文件 — 如有 raw/ 子文件夹则读取该目录）",
    folder_ph: "~/dev/llm_wiki  或  data/raw",
    pick_dir: "选择文件夹…", load_docs: "加载文档",
    mode: "模式", mode_a: "A — 摘要策略进化（按文档）", mode_b: "B — 文件夹结构进化（所选全部文档）",
    gens: "代数", gens_help: "每代每篇文档约 1–2 分钟",
    nqa: "问题数", nqa_help: "准确率评估题数（2–12）",
    backend: "后端", backend_claude: "claude（Claude Code 订阅）", backend_codex: "codex（ChatGPT 订阅）",
    run_exp: "运行实验", starting: "正在启动…", starting_job: "正在启动任务…", job_in_progress: "任务运行中 · 完成后可启动新任务",
    wiki_title: "诊断并优化我的 wiki",
    wiki_desc: "以上方文件夹（raw/ + wiki/）为基准给当前摘要评分（Before），再用最佳策略重新摘要（After）进行对比。原文和现有 wiki 文件不会被修改。",
    btn_audit: "诊断当前 wiki（Before）", btn_apply: "生成优化版（After）",
    strategy_custom: "自定义策略（留空则使用实验得出的最佳策略）",
    strategy_label: "自定义优化策略",
    docs_selected: (n,t) => `已选择 ${n}/${t} 篇文档`, select_all: "全选", clear_all: "清除选择",
    runs_title: "运行记录", runs_count: n => `共 ${n} 条`,
    need_dir: "请输入文件夹路径", need_dir_first: "请先在上方指定文件夹",
    no_md: "未找到 md 文件", need_docs: "请至少选择一个文档", load_failed: "无法加载文档。请检查路径和服务器状态。",
    request_failed: "请求失败，请稍后重试。",
    no_jobs: "还没有运行过实验", no_jobs_hint: "加载文档并运行实验后，这里将显示各代评分和策略变化。", no_propose_hint: "添加来源和任务并运行后，这里将显示提案的文件夹结构。",
    waiting_gen: "等待第一代结果…",
    filter_all: "全部", filter_mode: "按模式筛选", filter_status: "按状态筛选", filter_active: "进行中", runs_search: "按文档名搜索",
    runs_filtered: (n, total) => `${n} / ${total} 条`, chip_best: "best", chip_failed: n => `${n} 个判定失败`,
    stop: "停止", stopping: "已请求停止…",
    preparing_q: "正在准备问题集…", failed: "失败",
    status_running: "运行中", status_done: "已完成", status_error: "错误", status_queued: "等待中", status_cancelled: "已停止", status_interrupted: "已中断",
    run_meta: (mode,backend,gens,time) => [backend, ...(mode === "summary" || mode === "structure" ? [`${gens}代`] : []), time].join(" · "),
    best_gen: n => `最高分（第 ${n} 代）`, held_out: "验证", train: "训练", gen_short: n => `第 ${n} 代`,
    generation_label: "代", ratio: "比例", total: "总分", acc_short: "准确率", eff_short: "效率",
    chart_label: n => `各代验证分数趋势${n == null ? "" : `，最高分在第 ${n} 代`}`,
    mode_summary: "A 摘要", mode_structure: "B 结构", mode_audit: "诊断", mode_apply: "优化",
    audit_title: "当前 wiki 诊断", scoring_docs: (d,t) => ` — 共 ${t} 篇文档，已评 ${d} 篇…`,
    r_kpi_acc: "准确率", r_kpi_eff: "效率", r_kpi_read: "平均阅读字数",
    r_kpi_cov: "页面覆盖率",
    r_graph: "反向链接图 — 颜色 = 正确率 · 大小 = 入链数 · 灰色描边 = 未使用",
    r_pages: "按页面诊断", th_page: "页面", th_inlinks: "入链",
    th_uses: "被读", th_rate: "正确率", r_unused: "未使用",
    r_questions: "逐题路由详情", th_src_doc: "来源文档",
    r_parse_warn: d => `judge 解析失败的文档：${d} — 相关分数不可信`,
    avg_score: n => `平均分（已评 ${n} 篇）`,
    th_doc: "文档", th_raw: "raw", th_summary: "摘要", th_score: "分数（总分 · 准确率 · 效率）", none: "无",
    apply_title: "优化 Before → After", generating: (d,t) => ` — 正在生成 ${d}/${t}…`,
    before_avg: "Before 平均", after_avg: "After 平均",
    used_strategy: s => `所用策略（${s}）`,
    after_files: p => `After 文件：${p}/（原文与现有 wiki 保持不变）`,
    diff_legend: (b,a) => `相对现有摘要的变化（<del>删除</del> / <ins>新增</ins>）· ${b} → ${a} 字`,
    new_summary: "无现有摘要 — 新生成",
    gen_progress: (d,t) => `（第 ${d}/${t} 代）`, accuracy: "准确率", efficiency: "效率",
    "arm_evolve": "进化", "arm_control": "对照组 · 不进化", "arm_evolve-nohist": "进化 · 无历史", "arm_evolve-wiki": "进化 · 模式 wiki",
    parse_failed_note: n => `${n} 代的评分无效（判定解析失败）— 已从汇总中排除`, strategy_unchanged: "无变化",
    vs_baseline: "vs 基准", delta_flat: "= 基准", tile_best: "best held-out", tile_gain: "提升", tile_gens: "代数", tile_judge: "判定",
    tile_failed: n => `${n} 次失败`, tile_valid: "全部有效", tile_excluded: "已排除", tile_parse_ok: "判定正常", baseline_val: n => `基准 ${n}`, best_gen_short: n => `最佳第 ${n} 代`, and_more: n => `等 ${n} 个`,
    th_elapsed: "耗时", provenance_title: "可复现性 — 后端 · 模型 · 代码", strategy_diff_hint: "高亮相对上一代的变化",
    strategy_per_gen: "各代策略提示词", th_strategy: "策略",
    best_summary: "查看最佳摘要", len_vs_raw: "相对原文长度",
    structure_title_n: n => `结构进化 · ${n} 篇文档`, doc_list: "文档列表",
    structure_title: d => `结构进化 — ${d}`,
    files_per_gen: "各代文件构成", th_files: "文件",
    structure_head: (d, f) => `结构重组提案 · ${d} 篇文档 → ${f} 个文件`,
    structure_intro: (n, b) => `AI 将"如何拆分文档"的规则改写并尝试了 ${n} 次，每次都用同一问题集评分。得分最高的第 ${b} 次尝试即为提案。`,
    structure_intro_running: (d, n) => `AI 正在改写"如何拆分"的规则并再次尝试 · 第 ${d}/${n} 次`,
    structure_hero: n => `第 ${n} 次尝试综合`, attempt_n: n => `第 ${n} 次尝试`, attempt_meta: (f, a) => `${f} 个文件 · 准确率 ${a}`,
    rule_diff: (n, p) => `第 ${n} 次尝试的拆分规则 — 相对第 ${p} 次的变化`, rule_seed: n => `第 ${n} 次尝试的拆分规则 — 默认`, rule_rewritten: (n, p) => `第 ${n} 次尝试的拆分规则 — 参考第 ${p} 次结果后基本重写`, rule_prev: p => `查看第 ${p} 次规则`,
    map_now: "当前文档", map_from: "来源", map_proposed: "提案结构", map_tip: "悬停或点击文件，可查看它来自哪些原始文档。",
    map_no_sources: "此次运行早于来源记录功能，因此没有文档→文件的连线。重新运行即可显示。",
    sb_formula: (a, e) => `综合 = 准确率 ${a} × 效率 ${e}`, sb_acc: (c, n) => `${n} 个问题中答对 ${c} 个。`, sb_acc_how: "问题基于全部原始文档预先生成，所有尝试使用同一组问题。仅阅读提案结构中的文件作答，与标准答案实质一致得 1 分（LLM 判定）。",
    sb_eff: (r, t) => `每个问题平均只读 ${r} 字 / 原文共 ${t} 字。`, sb_eff_how: "效率 = 1 −（平均阅读字数 ÷ 原文总字数）。读得越少且答对，分数越高。",
    sb_heldout_tag: "基于验证问题", sb_split_label: "问题拆分", sb_split: (h, tr) => `是否采用仅由 ${h} 个验证（held-out）问题决定。${tr} 个训练问题只提供给规则改写步骤，以检验改后的规则对未见过的问题是否同样有效。`, sb_train_score: (t, a) => `训练问题得分：${t}（准确率 ${a}）。`, sb_acc_heldout: (c, n) => `${n} 个验证问题中答对 ${c} 个。`, sb_train_questions: n => `训练问题判定 · ${n} 个（用于改写规则）`,
    sb_questions: n => `逐题判定 · ${n} 个`, th_expected: "期望答案", th_answer: "按结构给出的答案",
    n_chars: n => `${n} 字`, n_sources: n => `${n} 个来源`, file_previews: "预览提案文件内容", score_trend: "各次尝试的分数走势",
    routing: "最佳结构的逐题路由", th_q: "问题", th_picked: "读取的文件", th_chars: "字数", th_correct: "正确",
    prop_title: "结构提案", mode_propose: "结构提案", prop_rail_desc: "添加素材来源并描述任务。", runs_list_title: "记录",
    rail_title: "设置", rail_desc: "选择文档与实验方式。",
    timeline_title: "运行", timeline_desc: "这里显示最近一次运行。",
    nav_opt: "Wiki 优化", nav_propose: "结构提案", nav_runs: "运行记录",
    src_add_dir: "＋ 选择文件夹", src_add_files: "＋ 上传文件",
    src_manual_ph: "输入路径后回车（例：~/repo/docs）",
    src_kind_dir: "目录", src_kind_file: "文件", src_remove: "移除源",
    src_upload_label: (first, n) => n > 1 ? `${first} 等 ${n} 个（已上传）` : `${first}（已上传）`,
    prop_desc: "用于还没有 wiki 的情况。根据来源与任务提出文件夹结构，并标出缺乏依据的轴（gap）。",
    prop_sources: "来源", prop_sources_hint: "添加将作为 wiki 素材的仓库与文档文件夹。", src_empty: "选择文件夹或输入路径后，来源会显示在这里。",
    prop_task: "任务描述", prop_task_ph: "该 wiki 的使用者与使用时机、2~3 个示例问题、数据的性质、产出用途",
    btn_propose: "运行结构提案", prop_seed: "继续改进", prop_seed_tip: "以之前提案 run 的最佳拆分策略作为 seed（warm start）",
    need_sources_task: "请输入源文件夹和任务描述",
    prop_run_title: "结构提案", prop_unscored: "无法评分 — 源中没有任何问题有依据答案（纯从零开始）",
    prop_grounded: "有依据的问题", prop_gapq: "缺乏依据的问题（gap）", prop_gapq_desc: "需要但源中没有依据的轴 — 即需额外获取的数据清单",
    prop_tree: "提案结构", prop_pages_per_gen: "每代页面",
    prop_export: "导出骨架", prop_export_ph: "写入骨架的文件夹路径", prop_export_done: (w,s) => `生成 ${w} 个，保留已有 ${s} 个`,
    th_page: "页面", th_purpose: "purpose", th_sources: "sources",
    chart_aria: (n, best) => `各代验证分数走势，共 ${n} 代` + (best == null ? "" : `，最高分在第 ${best} 代`),
  },
};

let LANG = "ko";
try { LANG = localStorage.getItem("wikiopt_lang") || "ko"; } catch (e) {}
if (!I18N[LANG]) LANG = "ko";

function t(key, ...args) {
  const v = (I18N[LANG] || I18N.ko)[key] ?? I18N.ko[key] ?? key;
  return typeof v === "function" ? v(...args) : v;
}

function applyLang() {
  if (typeof srcState !== "undefined") try { renderSrcList(); } catch (e) {}
  document.documentElement.lang = LANG;
  $("lang").value = LANG;
  $("mobileLang").value = LANG;
  document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
  document.querySelectorAll("[data-i18n-aria-label]").forEach(el => { el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel)); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  syncDocSelection();
  syncModeCards();
  syncExperimentSummary();
  syncWorkspaceSummary();
  $("topCurrent").textContent = t("nav_" + curView);
}

function setLang(lang) {
  LANG = I18N[lang] ? lang : "ko";
  try { localStorage.setItem("wikiopt_lang", LANG); } catch (e) {}
  applyLang();
  lastHtml = "";   // job 카드도 새 언어로 다시 그린다
  poll();
}

// ---------- 테마 (system | light | dark) — <head> 부트스트랩 스크립트와 짝 ----------
function setTheme(mode) {
  var root = document.documentElement;
  root.classList.add("theme-switching");
  if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode);
  else { mode = "system"; root.removeAttribute("data-theme"); }
  try { localStorage.setItem("wikiopt_theme", mode); } catch (e) {}
  var sel = document.getElementById("theme"); if (sel) sel.value = mode;
  requestAnimationFrame(function () { requestAnimationFrame(function () { root.classList.remove("theme-switching"); }); });
}
(function () {
  var t = "system";
  try { t = localStorage.getItem("wikiopt_theme") || "system"; } catch (e) {}
  var sel = document.getElementById("theme"); if (sel) sel.value = t;
})();

// ---------- 폼 값 저장/복원 (wikiopt_lang 저장 로직의 확장) ----------
function savePrefs() {
  try {
    localStorage.setItem("wikiopt_prefs", JSON.stringify({
      dir: $("dir").value.trim(), backend: $("backend").value,
      gens: $("gens").value, nqa: $("nqa").value,
      mode: $("mode").value, srcState, propTask: $("propTask").value, view: curView,
    }));
  } catch (e) {}
}

function loadPrefs() {
  let p = {};
  try { p = JSON.parse(localStorage.getItem("wikiopt_prefs") || "{}"); } catch (e) {}
  if (p.dir) $("dir").value = p.dir;
  if (p.backend) $("backend").value = p.backend;
  if (p.gens) $("gens").value = p.gens;
  if (p.nqa) $("nqa").value = p.nqa;
  if (p.mode) $("mode").value = p.mode;
  if (Array.isArray(p.srcState)) { srcState = p.srcState; renderSrcList(); }
  if (p.view) showView(p.view, false);
  if (p.propTask) $("propTask").value = p.propTask;
  syncModeCards();
  syncExperimentSummary();
  syncWorkspaceSummary();
  // 저장된 폴더가 있으면 문서 목록도 바로 복원 — 새로고침마다 다시 불러오지 않게
  if (p.dir) loadDocs();
}

// ---------- 데이터 로드/실행 ----------
// ---------- 실행 피드백 — 아이콘 버튼 · 스피너 · 토스트 (PR #47 포팅) ----------
const ACTION_ICONS = {
  run: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>',
  audit: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4M8 11h6M11 8v6"/></svg>',
  apply: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6V3Z"/><path d="M14 3v5h5M12 11v6M9 14h6"/></svg>',
  propose: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3Z"/></svg>',
};
// summary/structure 실험은 같은 "실험 실행" 버튼이므로 하나의 액션(run)으로 본다
function normalizeActionMode(mode) {
  return mode === "summary" || mode === "structure" ? "run" : mode;
}
function actionButtonContent(mode, label, running) {
  const icon = running ? '<span class="action-spinner" aria-hidden="true"></span>' : ACTION_ICONS[mode];
  return `${icon}<span>${esc(label)}</span>`;
}
function showToast(message, tone = "info") {
  const region = $("toastRegion");
  if (!region) return;
  const toast = document.createElement("div");
  const mark = document.createElement("span");
  const copy = document.createElement("span");
  toast.className = `toast ${tone}`;
  mark.className = "toast-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = tone === "success" ? "✓" : tone === "error" ? "!" : "→";
  copy.textContent = message;
  toast.append(mark, copy);
  region.append(toast);
  setTimeout(() => toast.remove(), 4200);
}

function syncActionStates() {
  const ready = Boolean(loadedDir) && $("dir").value.trim() === loadedDir;
  const selected = document.querySelectorAll("#docs input[type=checkbox]:checked").length;
  const busy = requestPending || hasActiveJob;
  $("auditBtn").disabled = !ready || busy;
  $("applyBtn").disabled = !ready || busy;
  $("go").disabled = selected === 0 || busy;
  $("propGo").disabled = busy;
  const actions = [["go", "run", "run_exp"], ["auditBtn", "audit", "btn_audit"], ["applyBtn", "apply", "btn_apply"], ["propGo", "propose", "btn_propose"]];
  actions.forEach(([id, mode, label]) => {
    const button = $(id);
    const starting = requestPending && submittingMode === mode;
    const running = starting || (hasActiveJob && activeJobMode === mode);
    const buttonLabel = starting ? t("starting") : running ? t("action_running") : t(label);
    button.classList.add("action-button");
    button.innerHTML = actionButtonContent(mode, buttonLabel, running);
    if (running) button.setAttribute("aria-busy", "true");
    else button.removeAttribute("aria-busy");
  });
  $("workStatus").textContent = requestPending ? t("starting_job") : hasActiveJob ? t("job_in_progress") : "";
}

function syncDirActions() {
  syncActionStates();
}

function clearDocs() {
  loadedDir = "";
  $("docs").innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "docs-empty";
  empty.textContent = t("docs_empty");
  $("docs").append(empty);
  syncDocSelection();
  syncDirActions();
}

function handleDirInput() {
  docsController?.abort();
  if ($("dir").value.trim() !== loadedDir) clearDocs();
  else syncDirActions();
  syncWorkspaceSummary();
}

function syncDocSelection() {
  const boxes = [...document.querySelectorAll("#docs input[type=checkbox]")];
  const selected = boxes.filter(x => x.checked).length;
  $("docsToolbar").hidden = boxes.length === 0;
  $("docsStatus").textContent = boxes.length ? t("docs_selected", selected, boxes.length) : "";
  $("toggleDocs").textContent = selected === boxes.length ? t("clear_all") : t("select_all");
  $("docCount").textContent = `${selected} / ${boxes.length}`;
  $("filterSelected").textContent = `${t("filter_selected")} ${selected}`;
  filterDocs();
  syncWorkspaceSummary();
  syncActionStates();
}

function toggleAllDocs() {
  const boxes = [...document.querySelectorAll("#docs input[type=checkbox]")];
  const shouldSelect = boxes.some(x => !x.checked);
  boxes.forEach(x => { x.checked = shouldSelect; });
  syncDocSelection();
}

function renderDocs(docs) {
  const fragment = document.createDocumentFragment();
  docs.forEach(d => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    const hint = document.createElement("small");
    const size = document.createElement("span");
    input.type = "checkbox";
    input.value = d.path;
    input.addEventListener("change", syncDocSelection);
    label.dataset.name = d.name.toLocaleLowerCase(LANG);
    copy.className = "doc-copy";
    name.append(document.createTextNode(d.name));
    hint.textContent = d.path.endsWith(".md") ? "Markdown" : d.path.split(".").pop().toUpperCase();
    copy.append(name, hint);
    size.className = "sz";
    size.textContent = `${(d.size/1000).toFixed(1)}k`;
    label.append(input, copy, size);
    fragment.append(label);
  });
  $("docs").replaceChildren(fragment);
}

async function loadDocs() {
  $("msg").textContent = "";
  const dir = $("dir").value.trim();
  if (!dir) { $("msg").textContent = t("need_dir"); return; }
  const requestId = ++docsRequest;
  docsController?.abort();
  docsController = new AbortController();
  clearDocs();
  $("loadDocsBtn").disabled = true;
  $("loadDocsBtn").setAttribute("aria-busy", "true");
  try {
    const r = await fetch(`/api/docs?dir=${encodeURIComponent(dir)}`, {signal: docsController.signal});
    const j = await r.json();
    if (requestId !== docsRequest || $("dir").value.trim() !== dir) return;
    if (!r.ok || j.error) { $("msg").textContent = j.error || t("load_failed"); return; }
    if (!j.docs.length) { $("msg").textContent = t("no_md"); return; }
    renderDocs(j.docs);
    loadedDir = dir;
    syncDocSelection();
    syncDirActions();
    syncWorkspaceSummary();
  } catch (e) {
    if (requestId === docsRequest && e.name !== "AbortError") $("msg").textContent = t("load_failed");
  } finally {
    if (requestId === docsRequest) {
      $("loadDocsBtn").disabled = false;
      $("loadDocsBtn").removeAttribute("aria-busy");
    }
  }
}

async function pickDir() {
  $("msg").textContent = "";
  const r = await fetch("/api/pick-dir");
  const j = await r.json();
  if (j.error) { $("msg").textContent = j.error; return; }
  if (j.cancelled) return;
  $("dir").value = j.dir;
  syncDirActions();
  savePrefs();
  loadDocs();
}

async function startJobRequest(body, msgEl) {
  if (requestPending || hasActiveJob) return;
  $(msgEl).textContent = "";
  const modeLabel = t("mode_" + body.mode);
  submittingMode = normalizeActionMode(body.mode);
  requestPending = true;
  syncActionStates();
  try {
    const r = await fetch("/api/runs", { method:"POST",
      headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok || j.error) {
      $(msgEl).textContent = j.error || t("request_failed");
      showToast(t("toast_error", modeLabel), "error");
      return;
    }
    open_.add(j.id);
    jobStatusSnapshot.set(j.id, j.status || "queued");
    hasActiveJob = true;
    activeJobMode = normalizeActionMode(body.mode);
    showToast(t("toast_started", modeLabel));
    // 실행 기록으로 이동하지 않는다 — 결과는 이 화면의 타임라인에 바로 쌓인다
    const target = body.mode === "propose" ? "propose" : "opt";
    if (curView !== target) showView(target);
    $(target === "propose" ? "propose-timeline-panel" : "timeline-panel").scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (e) {
    $(msgEl).textContent = t("request_failed");
    showToast(t("toast_error", modeLabel), "error");
  } finally {
    requestPending = false;
    submittingMode = null;
    syncActionStates();
    poll();
  }
}

function startAudit() {
  const dir = $("dir").value.trim();
  if (!dir) { $("msg2").textContent = t("need_dir_first"); return; }
  startJobRequest({ mode:"audit", dir, n_qa:6, backend:$("backend").value,
    language:LANG }, "msg2");
}

function startApply() {
  const dir = $("dir").value.trim();
  if (!dir) { $("msg2").textContent = t("need_dir_first"); return; }
  startJobRequest({ mode:"apply", dir, n_qa:6, backend:$("backend").value,
    language:LANG, strategy: $("strategy").value.trim() }, "msg2");
}

function startPropose() {
  const sources = srcState.map(x => x.value);
  const task = $("propTask").value.trim();
  if (!sources.length || !task) { $("msg3").textContent = t("need_sources_task"); return; }
  startJobRequest({ mode:"propose", sources, task,
    generations: +$("gens").value, n_qa: +$("nqa").value,
    seed_from_runs: $("propSeed").checked,
    backend: $("backend").value, language: LANG }, "msg3");
}

async function exportSkeleton(jobId, inputId, msgId) {
  const write_dir = $(inputId).value.trim();
  const el = $(msgId);
  el.textContent = "";
  if (!write_dir) { el.textContent = t("prop_export_ph"); return; }
  const r = await fetch("/api/skeleton", { method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ job_id: jobId, write_dir }) });
  const j = await r.json();
  el.textContent = j.error ? j.error : t("prop_export_done", j.written.length, j.skipped.length);
  el.className = j.error ? "err" : "muted";
}

function startRun() {
  if (requestPending || hasActiveJob) return;
  $("msg").textContent = "";
  const files = [...document.querySelectorAll("#docs input:checked")].map(x => x.value);
  if (!files.length) { $("msg").textContent = t("need_docs"); return; }
  startJobRequest({ files, mode: $("mode").value,
    generations: +$("gens").value, n_qa: +$("nqa").value,
    backend: $("backend").value, language: LANG }, "msg");
}

// ---------- 뷰 ----------
function wrapTable(inner) { return `<div class="table-wrap"><table>${inner}</table></div>`; }

// 단어 단위 unified diff (LCS). 너무 길면 하이라이트 생략.
function wordDiff(a, b) {
  const A = a.split(/(\s+)/), B = b.split(/(\s+)/);
  if (A.length * B.length > 500000)
    return `<div class="diff">${esc(b)}</div>`;
  const n = A.length, m = B.length;
  const dp = Array.from({length: n+1}, () => new Uint16Array(m+1));
  for (let i = n-1; i >= 0; i--)
    for (let j = m-1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i+1][j+1] + 1 : Math.max(dp[i+1][j], dp[i][j+1]);
  let i = 0, j = 0, out = "";
  while (i < n && j < m) {
    if (A[i] === B[j]) { out += esc(A[i]); i++; j++; }
    else if (dp[i+1][j] >= dp[i][j+1]) { if (A[i].trim()) out += `<del>${esc(A[i])}</del>`; i++; }
    else { if (B[j].trim()) out += `<ins>${esc(B[j])}</ins>`; else out += esc(B[j]); j++; }
  }
  while (i < n) { if (A[i].trim()) out += `<del>${esc(A[i])}</del>`; i++; }
  while (j < m) { out += B[j].trim() ? `<ins>${esc(B[j])}</ins>` : esc(B[j]); j++; }
  return `<div class="diff">${out}</div>`;
}

function fmtScore(s) {
  return s ? `${s.total} <span style="color:var(--dim)">(${t("acc_short")} ${s.accuracy} · ${t("eff_short")} ${s.efficiency})</span>` : "-";
}

// ---------- 백링크 그래프 (결정적 force layout — poll 재렌더 캐시 안정성) ----------
function layoutGraph(nodes, edges, W, H) {
  const n = nodes.length;
  if (!n) return [];
  const pos = nodes.map((nd, i) => {
    const a = (2 * Math.PI * i) / n;
    return { id: nd.id, x: W/2 + Math.cos(a) * W * 0.34, y: H/2 + Math.sin(a) * H * 0.34 };
  });
  const idx = Object.fromEntries(pos.map((p, i) => [p.id, i]));
  const k = Math.sqrt((W * H) / n) * 0.8;
  for (let it = 0; it < 160; it++) {
    const fx = new Float64Array(n), fy = new Float64Array(n);
    for (let i = 0; i < n; i++) for (let j = i+1; j < n; j++) {
      let dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y;
      let d2 = dx*dx + dy*dy || 1, d = Math.sqrt(d2);
      const rep = (k*k) / d2;
      fx[i] += dx/d * rep * d; fy[i] += dy/d * rep * d;
      fx[j] -= dx/d * rep * d; fy[j] -= dy/d * rep * d;
    }
    for (const e of edges) {
      const a = idx[e.source], b = idx[e.target];
      if (a == null || b == null) continue;
      let dx = pos[a].x - pos[b].x, dy = pos[a].y - pos[b].y;
      const d = Math.sqrt(dx*dx + dy*dy) || 1;
      const att = (d*d) / k * 0.02;
      fx[a] -= dx/d * att; fy[a] -= dy/d * att;
      fx[b] += dx/d * att; fy[b] += dy/d * att;
    }
    const cool = 1 - it/160;
    for (let i = 0; i < n; i++) {
      const disp = Math.sqrt(fx[i]*fx[i] + fy[i]*fy[i]) || 1;
      const step = Math.min(disp, 14 * cool);
      pos[i].x += fx[i]/disp * step + (W/2 - pos[i].x) * 0.012;
      pos[i].y += fy[i]/disp * step + (H/2 - pos[i].y) * 0.012;
      pos[i].x = Math.max(50, Math.min(W-50, pos[i].x));
      pos[i].y = Math.max(26, Math.min(H-16, pos[i].y));
    }
  }
  return pos;
}

function nodeColor(rate, uses) {
  if (!uses) return null;                       // 미사용 — 회색 테두리만
  if (rate >= 0.75) return "var(--ok)";
  if (rate >= 0.4) return "var(--warn)";
  return "var(--bad)";
}

function graphSVG(graph, pageRows) {
  const W = 940, H = 560;
  const byName = Object.fromEntries(pageRows.map(r => [r.name, r]));
  const pos = layoutGraph(graph.nodes, graph.edges, W, H);
  const at = Object.fromEntries(pos.map(p => [p.id, p]));
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t("r_graph"))}">`;
  for (const e of graph.edges) {
    const a = at[e.source], b = at[e.target];
    if (!a || !b) continue;
    svg += `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}" stroke="var(--line)" stroke-width="1"/>`;
  }
  for (const nd of graph.nodes) {
    const p = at[nd.id], row = byName[nd.id] || {};
    if (!p) continue;
    const r = 6 + Math.min(10, nd.inlinks * 1.4);
    const fill = nodeColor(row.correct_rate ?? 0, row.uses);
    const circle = fill
      ? `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r}" fill="${fill}" fill-opacity="0.85"/>`
      : `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r}" fill="var(--control)" stroke="var(--line-strong)" stroke-width="1.5"/>`;
    const label = nd.id.length > 22 ? nd.id.slice(0, 21) + "…" : nd.id;
    svg += `<g>${circle}<text x="${p.x.toFixed(1)}" y="${(p.y + r + 11).toFixed(1)}" text-anchor="middle" font-size="10">${esc(label)}</text>
      <title>${esc(nd.id)} — inlinks ${nd.inlinks}, ${row.uses ? `reads ${row.uses}, correct ${row.correct_rate}` : t("r_unused")}</title></g>`;
  }
  svg += "</svg>";
  return `<div class="chart"><div class="chart-head">${t("r_graph")}</div>${svg}</div>`;
}

function routerAuditView(res, status) {
  const done = res.done ?? res.n_docs, total = res.total ?? res.n_docs;
  const prog = done < total || status === "running"
    ? `<span class="muted">${t("scoring_docs", done ?? 0, total ?? "?")}</span>` : "";
  let html = `<div class="runbox"><h3>${t("audit_title")}${prog}</h3>`;
  if (res.accuracy != null)
    html += `<div class="kpis">
      <div class="kpi"><b>${res.accuracy}</b><span>${t("r_kpi_acc")} (${res.n_questions ?? "-"} Q)</span></div>
      <div class="kpi"><b>${res.efficiency ?? "-"}</b><span>${t("r_kpi_eff")}</span></div>
      <div class="kpi"><b>${res.avg_read ?? "-"}</b><span>${t("r_kpi_read")}</span></div>
      <div class="kpi"><b>${res.n_pages_used ?? "-"}/${res.n_pages ?? "-"}</b><span>${t("r_kpi_cov")}</span></div>
    </div>`;
  if (res.graph?.nodes?.length && res.pages)
    html += graphSVG(res.graph, res.pages);
  if (res.parse_failed_docs?.length)
    html += `<div class="err" style="margin-top:8px">${t("r_parse_warn", res.parse_failed_docs.join(", "))}</div>`;
  if (res.pages?.length) {
    html += `<details open><summary>${t("r_pages")}</summary>${wrapTable(
      `<tr><th>${t("th_page")}</th><th>chars</th><th>${t("th_inlinks")}</th><th>${t("th_uses")}</th><th>${t("th_rate")}</th></tr>` +
      res.pages.map(r => `<tr>
        <td>${esc(r.name)}</td><td>${(r.chars/1000).toFixed(1)}k</td>
        <td>${r.inlinks}</td><td>${r.uses}</td>
        <td>${r.uses ? `<span class="${r.correct_rate >= 0.75 ? "up" : r.correct_rate < 0.4 ? "down" : ""}">${r.correct_rate}</span>` : `<span class="muted">${t("r_unused")}</span>`}</td>
      </tr>`).join(""))}</details>`;
  }
  if (res.questions?.length) {
    html += `<details><summary>${t("r_questions")}</summary>${wrapTable(
      `<tr><th>${t("th_src_doc")}</th><th>${t("th_q")}</th><th>${t("th_picked")}</th><th>${t("th_chars")}</th><th>${t("th_correct")}</th></tr>` +
      res.questions.map(d => `<tr><td>${esc(d.doc)}</td><td>${esc(d.q)}</td>
        <td>${(d.picked||[]).map(esc).join(", ")}</td><td>${d.read_chars}</td>
        <td>${d.score != null ? (d.score ? "⭕" : "❌") : "-"}</td></tr>`).join(""))}</details>`;
  }
  return html + "</div>";
}

function auditView(res, status) {
  if (res.variant === "router") return routerAuditView(res, status);
  const prog = res.done < res.total ? `<span class="muted">${t("scoring_docs", res.done, res.total)}</span>` : "";
  let html = `<div class="runbox"><h3>${t("audit_title")}${prog}</h3>`;
  if (res.avg_total != null)
    html += `<div class="kpis"><div class="kpi"><b>${res.avg_total}</b><span>${t("avg_score", res.n_scored)}</span></div></div>`;
  html += wrapTable(`<tr><th>${t("th_doc")}</th><th>${t("th_raw")}</th><th>${t("th_summary")}</th><th>${t("th_score")}</th></tr>` +
    (res.docs||[]).map(d => `<tr><td>${esc(d.name)}</td>
      <td>${(d.raw_chars/1000).toFixed(1)}k</td>
      <td>${d.has_summary ? `${((d.wiki_chars||0)/1000).toFixed(1)}k` : `<span class="err">${t("none")}</span>`}</td>
      <td>${fmtScore(d.score)}</td></tr>`).join(""));
  return html + "</div>";
}

function applyView(res, status) {
  const prog = res.done < res.total || status === "running" ? `<span class="muted">${t("generating", res.done||0, res.total||"?")}</span>` : "";
  let html = `<div class="runbox"><h3>${t("apply_title")}${prog}</h3>`;
  if (res.avg_after != null) {
    const b = res.avg_before, a = res.avg_after;
    const dir_ = b == null ? "" : a > b ? "up" : a < b ? "down" : "";
    html += `<div class="kpis">
      <div class="kpi"><b>${b ?? "-"}</b><span>${t("before_avg")}</span></div>
      <div class="kpi"><b class="${dir_}">${a}</b><span>${t("after_avg")}</span></div></div>`;
  }
  if (res.strategy)
    html += `<details><summary>${t("used_strategy", esc(res.strategy_source||""))}</summary>
      <pre>${esc(res.strategy)}</pre></details>`;
  if (res.out_dir)
    html += `<div class="note">${t("after_files", esc(res.out_dir))}</div>`;
  html += (res.docs||[]).map(d => {
    const bs = d.before?.score, as_ = d.after?.score;
    const head = `${esc(d.name)} &nbsp; ${fmtScore(bs)}<span class="arrow">→</span>${fmtScore(as_)}`;
    let body = "";
    if (d.before?.content && d.after?.content)
      body = `<div class="note">${t("diff_legend", d.before.content.length, d.after.content.length)}</div>` +
        wordDiff(d.before.content, d.after.content);
    else if (d.after?.content)
      body = `<div class="note">${t("new_summary")}</div>
        <pre>${esc(d.after.content)}</pre>`;
    return `<details><summary>${head}</summary>${body}</details>`;
  }).join("");
  return html + "</div>";
}

function chart(hist, key, bestGen, failed = new Set(), xLabel = g => t("gen_short", g)) {
  const W = 640, H = 180, P = 32;
  const xs = hist.map(h => h.generation);
  const X = i => P + (W - 2*P) * (xs.length === 1 ? 0.5 : i / (xs.length - 1));
  const Y = v => H - P - (H - 2*P) * Math.max(0, Math.min(1, v));
  const line = (get, stroke, extra="") => {
    const pts = hist.map((h, i) => `${X(i)},${Y(get(h))}`);
    return `<polyline points="${pts.join(" ")}" fill="none" stroke="${stroke}" ${extra}/>` +
      hist.map((h, i) => `<circle cx="${X(i)}" cy="${Y(get(h))}" r="3" fill="${stroke}"/>`).join("");
  };
  const ariaBest = bestGen != null && xs.indexOf(bestGen) >= 0 ? bestGen : null;
  const chartLabel = t("chart_aria", xs.length, ariaBest);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(chartLabel)}"><title>${esc(chartLabel)}</title>`;
  for (const v of [0, .5, 1]) {
    svg += `<line x1="${P}" y1="${Y(v)}" x2="${W-P}" y2="${Y(v)}" stroke="var(--line-subtle)" stroke-dasharray="3 5"/>
            <text x="4" y="${Y(v)+4}">${v}</text>`;
  }
  if (hist[0][key])
    svg += line(h => (h[key]||{}).total ?? 0, "var(--line-strong)", 'stroke-width="1.5" stroke-dasharray="4 5"');
  {
    const gid = "g" + Math.random().toString(36).slice(2, 8);
    const pts = hist.map((h, i) => `${X(i)},${Y(h.score.total)}`).join(" ");
    svg += `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--acc)" stop-opacity=".28"/><stop offset="1" stop-color="var(--acc)" stop-opacity="0"/></linearGradient></defs>
      <polygon points="${X(0)},${H-P} ${pts} ${X(xs.length-1)},${H-P}" fill="url(#${gid})"/>`;
  }
  svg += line(h => h.score.total, "var(--acc)", 'stroke-width="2.5"');
  hist.forEach((h, i) => { if (failed.has(h.generation))
    svg += `<circle cx="${X(i)}" cy="${Y(h.score.total)}" r="5" fill="var(--bg)" stroke="var(--line-strong)" stroke-width="1.5"/>`; });
  if (bestGen != null) {
    const bi = xs.indexOf(bestGen);
    if (bi >= 0) {
      svg += `<circle cx="${X(bi)}" cy="${Y(hist[bi].score.total)}" r="6" fill="none" stroke="var(--acc)" stroke-width="1.5"/>`;
    }
  }
  xs.forEach((g, i) => { svg += `<text x="${X(i)-12}" y="${H-8}">${xLabel(g)}</text>`; });
  svg += `</svg>`;
  const legend = `<div class="chart-head"><span><i></i>${t("held_out")}</span>` +
    (hist[0][key] ? `<span><i class="train"></i>${t("train")}</span>` : "") + `</div>`;
  return `<div class="chart">${legend}${svg}</div>`;
}


// 결과 카드 공용 — arm 뱃지 + provenance 칩 (report가 있을 때). 신뢰성 장치를 화면에 드러낸다.
function resultMeta(p, rep) {
  const arm = (rep && rep.arm) || (p && p.arm) || "";
  const prov = rep && rep.provenance;
  let html = "";
  if (arm) html += `<span class="arm arm-${esc(arm)}">${t("arm_" + arm) || esc(arm)}</span>`;
  if (prov) {
    const bits = [prov.backend, prov.model, prov.code_sha].filter(Boolean).map(esc).join(" · ");
    html += `<span class="prov" title="${esc(t("provenance_title"))}${prov.question_set_sha ? " · qs " + esc(prov.question_set_sha) : ""}">${bits}</span>`;
  }
  return html ? `<div class="result-meta">${html}</div>` : "";
}
function failedGens(rep) { return new Set((rep && rep.parse_failed_generations) || []); }
function parseFailedNote(failed) {
  return failed.size ? `<div class="parse-failed-note">${t("parse_failed_note", failed.size)}</div>` : "";
}
// 스파크라인 — KPI 카드 안의 세대 추이 (그라데이션 영역 + 마지막 점)
function spark(values, best = -1) {
  const W = 96, H = 28, n = values.length;
  if (!n) return "";
  const X = i => n === 1 ? W / 2 : 4 + (W - 8) * i / (n - 1);
  const Y = v => H - 3 - (H - 8) * Math.max(0, Math.min(1, v));
  const pts = values.map((v, i) => `${X(i)},${Y(v)}`).join(" ");
  const gid = "s" + Math.random().toString(36).slice(2, 8);
  const bi = best >= 0 && best < n ? best : n - 1;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--acc)" stop-opacity=".35"/><stop offset="1" stop-color="var(--acc)" stop-opacity="0"/></linearGradient></defs>
    <polygon points="${X(0)},${H} ${pts} ${X(n-1)},${H}" fill="url(#${gid})"/><polyline points="${pts}" fill="none" stroke="var(--acc)" stroke-width="1.5"/>
    <circle cx="${X(bi)}" cy="${Y(values[bi])}" r="2.5" fill="var(--acc)"/></svg>`;
}
function deltaChip(cur, base, digits = 2) {
  if (!Number.isFinite(cur) || !Number.isFinite(base)) return "";
  const d = cur - base; if (Math.abs(d) < 1e-9) return `<span class="delta flat">${t("delta_flat")}</span>`;
  return `<span class="delta ${d > 0 ? "up" : "down"}">${d > 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(digits)} <em>${t("vs_baseline")}</em></span>`;
}
function summaryRun(run) {
  const p = run.progress, rep = run.report;
  if (!p) return "";
  const failed = failedGens(rep);
  const best = p.history[p.best_gen >= 0 ? p.best_gen : 0] || {};
  const baseline = Number(p.history[0]?.score?.total);
  const bestTotal = Number(p.best_total);
  const improvementValue = baseline > 0 && Number.isFinite(bestTotal)
    ? (bestTotal - baseline) / baseline * 100 : null;
  const improvement = improvementValue > 0 ? improvementValue.toFixed(1) : null;
  const trail = p.history.map((h, index) => {
    const cls = h.generation===p.best_gen ? " best" : failed.has(h.generation) ? " failed" : "";
    return `<div class="evolution-step${cls}"><i></i><b>${h.score?.total ?? "-"}</b>
      <span>${index === 0 ? t("baseline") : h.generation===p.best_gen ? t("best_label") : t("gen_short", h.generation)}</span></div>`;
  }).join("");
  // 세대별 전략: 이전 세대 대비 diff — 무엇이 바뀌어서 점수가 움직였는지
  const rows = p.history.map((h, i) => {
    const prev = i > 0 ? p.history[i-1].strategy : null;
    const cell = prev == null ? esc(h.strategy)
      : prev === h.strategy ? `<span class="muted">${t("strategy_unchanged")}</span>`
      : wordDiff(prev, h.strategy);
    const cls = h.generation===p.best_gen ? ' class="is-best"' : failed.has(h.generation) ? ' class="is-failed"' : "";
    return `<tr${cls}><td>${h.generation}${h.generation===p.best_gen?" ★":""}</td>
      <td>${h.score.total}</td><td>${h.score.length_ratio}</td><td>${h.elapsed_sec != null ? h.elapsed_sec + "s" : "-"}</td>
      <td class="strategy-cell">${cell}</td></tr>`;
  }).join("");
  let html = `<div class="runbox evolution-run"><div class="result-heading"><div><h3>${esc(p.doc)}
      <span class="muted">${t("gen_progress", p.done_generations, p.generations)}</span></h3>${resultMeta(p, rep)}</div>
      <div class="result-score"><b>${p.best_total}</b>${improvement == null ? "" : `<span>↑ ${t("score_improved", improvement)}</span>`}</div></div>
    ${parseFailedNote(failed)}
    <div class="bento">
      <div class="tile tile-hero"><span>${t("tile_best")}</span><b>${p.best_total}</b>${deltaChip(bestTotal, baseline, 3)}${spark(p.history.map(h => h.score?.total ?? 0), p.best_gen)}</div>
      <div class="tile"><span>${t("tile_gain")}</span><b>${improvement == null ? "—" : "+" + improvement + "%"}</b><small>${t("baseline_val", baseline)}</small></div>
      <div class="tile"><span>${t("tile_gens")}</span><b>${p.done_generations}<small>/${p.generations}</small></b><small>${t("best_gen_short", p.best_gen)}</small></div>
      <div class="tile ${failed.size ? "tile-warn" : "tile-ok"}"><span>${t("tile_judge")}</span><b>${failed.size ? t("tile_failed", failed.size) : t("tile_valid")}</b><small>${failed.size ? t("tile_excluded") : t("tile_parse_ok")}</small></div>
    </div>
    <div class="kpis">
      ${[["length_ratio", t("ratio")], ["accuracy", t("accuracy")], ["efficiency", t("efficiency")]].map(([k, label]) => {
        const vals = p.history.map(h => Number((h.score||{})[k] ?? 0));
        const cur = Number((best.score||{})[k]), base = vals[0];
        return `<div class="kpi"><div class="kpi-top"><span>${label}</span>${deltaChip(cur, base)}</div><b>${(best.score||{})[k] ?? "-"}</b>${spark(vals, p.best_gen)}</div>`;
      }).join("")}
    </div>
    <div class="evolution-label">${t("evolution_trail")}</div>
    <div class="evolution-steps" style="--step-count:${Math.max(1,p.history.length)}">${trail}</div>
    ${chart(p.history, "train_score", p.best_gen, failed)}
    <details><summary>${t("strategy_per_gen")} <span class="muted">· ${t("strategy_diff_hint")}</span></summary>${wrapTable(
      `<tr><th>${t("generation_label")}</th><th>${t("held_out")}</th><th>${t("ratio")}</th><th>${t("th_elapsed")}</th><th>${t("th_strategy")}</th></tr>` + rows)}
    </details>`;
  if (rep && rep.best && rep.best.summary) {
    const ratio = (rep.history[rep.best.generation]||{}).score?.length_ratio;
    html += `<details><summary>${t("best_summary")}</summary>
      <div class="note">${t("len_vs_raw")}
        <span class="lenbar"><i style="width:${Math.min(100,(ratio||0)*100)}%"></i></span>
        ${ratio!=null ? (ratio*100).toFixed(0)+"%" : ""}</div>
      <pre>${esc(rep.best.summary)}</pre></details>`;
  }
  return html + "</div>";
}
// ---------- B 구조 결과 카드 — "N차 시도" · 분할 규칙 · 문서→파일 매핑 다이어그램 ----------
const selectedAttempt = {};   // runKey -> generation (기본은 best)
function selectAttempt(key, gen) { selectedAttempt[key] = gen; poll(); }
// 파일 제목 "주제: 부제" → 파일명(01-주제.md)과 부제
function structFileName(title, j) {
  const i = title.indexOf(":");
  const head = (i < 0 ? title : title.slice(0, i)).trim(), tail = i < 0 ? "" : title.slice(i + 1).trim();
  return { name: `${String(j + 1).padStart(2, "0")}-${head}.md`, desc: tail };
}
// 매핑 다이어그램 — 왼쪽 원본 문서, 오른쪽 제안 파일, 사이에 출처 선. 출처 정보가 없는(구버전) 실행이면 선 없이 목록만
function structureMap(docs, files) {
  const RH = 26, FH = 44, SW = 96, n = docs.length, m = files.length;
  const H = Math.max(n * RH, m * FH), lt = (H - n * RH) / 2, rt = (H - m * FH) / 2;
  const hasSources = files.some(f => (f.sources || []).length);
  const di = Object.fromEntries(docs.map((d, i) => [d, i]));
  const ly = i => lt + i * RH + RH / 2, ry = j => rt + j * FH + FH / 2;
  const wires = files.flatMap((f, j) => (f.sources || []).filter(d => d in di).map(d =>
    `<path class="w f${j} d${di[d]}" d="M0 ${ly(di[d]).toFixed(0)} C ${SW / 2} ${ly(di[d]).toFixed(0)}, ${SW / 2} ${ry(j).toFixed(0)}, ${SW} ${ry(j).toFixed(0)}"/>`)).join("");
  const ico = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 1.5h5l3 3v10H4z"/><path d="M9 1.5v3h3"/></svg>';
  const left = docs.map((d, i) => `<div class="src d${i}" data-d="${i}" style="height:${RH}px" title="${esc(d)}">${ico}<span>${esc(d)}.md</span></div>`).join("");
  const right = files.map((f, j) => {
    const { name, desc } = structFileName(f.title, j);
    const src = (f.sources || []).filter(d => d in di);
    const chars = f.n_chars ?? (f.content ? f.content.length : null);
    const meta = [desc, chars != null ? t("n_chars", chars) : "", hasSources ? t("n_sources", src.length) : ""].filter(Boolean).join(" · ");
    return `<div class="dst f${j}" data-f="${j}" data-src="${src.map(d => di[d]).join(",")}" style="height:${FH - 6}px">${ico}<div><b>${esc(name)}</b><small>${esc(meta)}</small>${src.length ? `<small class="src-names">${t("map_from")}: ${src.map(esc).join(", ")}</small>` : ""}</div></div>`;
  }).join("");
  return `<div class="smap${hasSources ? "" : " no-wires"}" onmouseover="smapHover(event)" onmouseout="smapClear(event)" onclick="smapPin(event)">
    <div class="col"><h4>${t("map_now")} <b>${n}</b></h4><div style="padding-top:${lt.toFixed(0)}px">${left}</div></div>
    <svg class="wires" width="${SW}" height="${H}" aria-hidden="true">${wires}</svg>
    <div class="col"><h4>${t("map_proposed")} <b>${m}</b></h4><div style="padding-top:${rt.toFixed(0)}px">${right}</div></div>
  </div><div class="smap-tip">${hasSources ? t("map_tip") : t("map_no_sources")}</div>`;
}
function _smapRoot(ev) { return ev.target.closest(".smap"); }
function smapHover(ev) {
  const root = _smapRoot(ev); if (!root || root.dataset.pinned) return;
  const el = ev.target.closest(".dst, .src"); if (!el) return;
  smapLight(root, el);
}
function smapLight(root, el) {
  root.querySelectorAll(".hot").forEach(x => x.classList.remove("hot"));
  if (el.classList.contains("dst")) {
    const j = el.dataset.f;
    root.querySelectorAll(`.w.f${j}, .dst.f${j}`).forEach(x => x.classList.add("hot"));
    (el.dataset.src || "").split(",").filter(Boolean).forEach(i => root.querySelector(`.src.d${i}`)?.classList.add("hot"));
  } else {
    const i = el.dataset.d;
    root.querySelectorAll(`.w.d${i}, .src.d${i}`).forEach(x => x.classList.add("hot"));
    root.querySelectorAll(`.w.d${i}`).forEach(w => { const f = [...w.classList].find(c => /^f\d+$/.test(c)); root.querySelector(`.dst.${f}`)?.classList.add("hot"); });
  }
}
function smapClear(ev) {
  const root = _smapRoot(ev); if (!root || root.dataset.pinned) return;
  if (ev.relatedTarget && root.contains(ev.relatedTarget) && ev.relatedTarget.closest(".dst, .src")) return;
  root.querySelectorAll(".hot").forEach(x => x.classList.remove("hot"));
}
// 클릭하면 고정(모바일·키보드용) — 같은 항목 다시 클릭하면 해제
function smapPin(ev) {
  const root = _smapRoot(ev); if (!root) return;
  const el = ev.target.closest(".dst, .src"); if (!el) return;
  const key = el.dataset.f != null ? "f" + el.dataset.f : "d" + el.dataset.d;
  if (root.dataset.pinned === key) { delete root.dataset.pinned; root.querySelectorAll(".hot").forEach(x => x.classList.remove("hot")); return; }
  root.dataset.pinned = key; smapLight(root, el);
}

// 분할 규칙 박스 — 이전 시도와 겹치는 부분이 충분하면 단어 diff, 거의 다시 썼으면 원문 + 이전 규칙 접기
function ruleBlock(prev, cur) {
  const n = cur.generation + 1;
  if (!prev) return `<div class="rule"><em>${t("rule_seed", n)}</em><div class="diff">${esc(cur.strategy)}</div></div>`;
  const diff = wordDiff(prev.strategy, cur.strategy);
  const changed = (diff.match(/<(ins|del)>/g) || []).length;
  const tokens = cur.strategy.split(/\s+/).filter(Boolean).length || 1;
  if (changed / tokens <= 0.5)
    return `<div class="rule"><em>${t("rule_diff", n, prev.generation + 1)}</em>${diff}</div>`;
  return `<div class="rule"><em>${t("rule_rewritten", n, prev.generation + 1)}</em><div class="diff">${esc(cur.strategy)}</div>
    <details class="rule-prev"><summary>${t("rule_prev", prev.generation + 1)}</summary><div class="diff">${esc(prev.strategy)}</div></details></div>`;
}

function structureRun(run, jobId) {
  const p = run.progress, rep = run.report;
  if (!p) return "";
  const failed = failedGens(rep);
  const key = `${jobId}/${run.run_dir}`;
  const hist = p.history || [];
  const bestGen = p.best_gen;
  const sel = selectedAttempt[key] ?? bestGen;
  const cur = hist.find(h => h.generation === sel) || hist[hist.length - 1];
  const prev = cur ? hist.find(h => h.generation === cur.generation - 1) : null;
  // 파일 목록: 선택한 시도의 history.files(제목·출처·글자수). best면 report.best.struct(본문 포함)를 우선
  const bestStruct = rep?.best?.struct?.files;
  const files = (cur && cur.generation === bestGen && bestStruct) ? bestStruct : (cur?.files || (cur?.file_titles || []).map(x => ({ title: x, sources: [] })));
  const running = p.done_generations < p.generations;
  const bestH = hist.find(h => h.generation === bestGen);
  const bestCount = bestStruct ? bestStruct.length : (bestH?.n_files ?? files.length);
  let html = `<div class="runbox structure-run">
    <div class="sr-head"><div><h3>${t("structure_head", p.docs.length, bestCount)}</h3>
      <p>${running ? t("structure_intro_running", p.done_generations, p.generations) : t("structure_intro", hist.length, bestGen + 1)}</p></div>
      ${p.best_total != null && bestGen >= 0 ? `<div class="sr-hero"><b>${p.best_total}</b><small>${t("structure_hero", bestGen + 1)}${bestH?.score ? ` · ${t("acc_short")} ${bestH.score.accuracy} × ${t("eff_short")} ${bestH.score.efficiency}` : ""}</small></div>` : ""}
    </div>
    ${resultMeta(p, rep)}${parseFailedNote(failed)}
    <div class="attempts" role="tablist">${hist.map(h => `<button type="button" role="tab" aria-selected="${h.generation === sel}" class="attempt${h.generation === sel ? " on" : ""}${failed.has(h.generation) ? " failed" : ""}" onclick="selectAttempt('${esc(key)}', ${h.generation})">
        <span>${t("attempt_n", h.generation + 1)}${h.generation === bestGen ? " ★" : ""}</span><b>${failed.has(h.generation) ? "—" : h.score.total}</b><small>${t("attempt_meta", h.n_files, h.score.accuracy)}</small></button>`).join("")}</div>`;
  if (cur) {
    html += ruleBlock(prev, cur);
    html += structureMap(p.docs, files);
    if (cur.generation === bestGen && bestStruct) {
      html += `<details class="file-previews"><summary>${t("file_previews")}</summary>${bestStruct.map((f, j) => `<div class="preview"><b>${esc(structFileName(f.title, j).name)}</b> <span class="muted">· ${t("n_chars", f.content.length)}</span><p>${esc(f.content)}</p></div>`).join("")}</details>`;
    }
  }
  if (cur) html += scoreBlock(cur, p, rep, cur.generation === bestGen);
  html += `<details><summary>${t("score_trend")}</summary>${chart(p.history, "", bestGen, failed, g => t("attempt_n", g + 1))}</details>`;
  return html + "</div>";
}

// 점수 근거 — 종합 = 정확도 × 효율 산식과, 그 시도의 질문별 판정(읽은 파일 · 답 · 정답 여부)
function scoreBlock(cur, p, rep, isBest) {
  const sc = cur.score || {};
  if (sc.total == null) return "";
  const qs = rep?.question_set || p.question_set || [];
  const det = cur.details || (isBest ? rep?.best?.result?.details : null) || [];
  // 분리된 실행이면 score/details는 held-out(검증) 것 — 질문 수도 검증 질문 수로
  const split = rep?.question_split || p.question_split;
  const hasSplit = Boolean(split && !split.degenerate);
  const nQ = hasSplit ? split.heldout : (qs.length || det.length || null);
  const raw = rep?.total_raw_chars ?? p.total_raw_chars;
  const correct = det.length ? det.filter(d => d.score).length : (nQ != null ? Math.round(sc.accuracy * nQ) : null);
  const ans = Object.fromEntries(qs.map(q => [q.q, q.a]));
  const fmt = n => Number(n).toLocaleString(LOCALES[LANG]);
  const tr = cur.train_score;
  let html = `<div class="score-basis"><div class="sb-formula"><b>${sc.total}</b><span>${t("sb_formula", sc.accuracy, sc.efficiency)}${hasSplit ? ` <em class="sb-tag">${t("sb_heldout_tag")}</em>` : ""}</span></div>
    <div class="sb-rows">
      ${hasSplit ? `<div class="sb-row"><b>${t("sb_split_label")}</b><span>${t("sb_split", split.heldout, split.train)}${tr ? " " + t("sb_train_score", tr.total, tr.accuracy) : ""}</span></div>` : ""}
      <div class="sb-row"><b>${t("acc_short")} ${sc.accuracy}</b><span>${nQ != null && correct != null ? t(hasSplit ? "sb_acc_heldout" : "sb_acc", correct, nQ) + " " : ""}${t("sb_acc_how")}</span></div>
      <div class="sb-row"><b>${t("eff_short")} ${sc.efficiency}</b><span>${sc.avg_read != null && raw ? t("sb_eff", fmt(Math.round(sc.avg_read)), fmt(raw)) + " " : ""}${t("sb_eff_how")}</span></div>
    </div>`;
  if (det.length) {
    html += `<details class="sb-table" open><summary>${t("sb_questions", det.length)}</summary>${wrapTable(
      `<tr><th>${t("th_q")}</th><th>${t("th_expected")}</th><th>${t("th_picked")}</th><th>${t("th_answer")}</th><th>${t("th_chars")}</th><th>${t("th_correct")}</th></tr>` +
      det.map(d => `<tr class="${d.score ? "q-ok" : "q-bad"}"><td>${esc(d.q)}</td><td>${esc(ans[d.q] || "")}</td><td>${(d.picked || []).map(esc).join(", ")}</td>
        <td>${esc(d.pred || "")}</td><td>${fmt(d.read_chars)}</td><td>${d.score ? "⭕" : "❌"}</td></tr>`).join(""))}</details>`;
  }
  const trd = cur.train_details || [];
  if (hasSplit && trd.length) {
    html += `<details class="sb-table"><summary>${t("sb_train_questions", trd.length)}</summary>${wrapTable(
      `<tr><th>${t("th_q")}</th><th>${t("th_expected")}</th><th>${t("th_picked")}</th><th>${t("th_answer")}</th><th>${t("th_chars")}</th><th>${t("th_correct")}</th></tr>` +
      trd.map(d => `<tr class="${d.score ? "q-ok" : "q-bad"}"><td>${esc(d.q)}</td><td>${esc(ans[d.q] || "")}</td><td>${(d.picked || []).map(esc).join(", ")}</td>
        <td>${esc(d.pred || "")}</td><td>${fmt(d.read_chars)}</td><td>${d.score ? "⭕" : "❌"}</td></tr>`).join(""))}</details>`;
  }
  return html + "</div>";
}

function proposalTree(pages) {
  let html = "", lastDir = null;
  for (const p of [...pages].sort((a,b) => a.path.localeCompare(b.path))) {
    const i = p.path.lastIndexOf("/");
    const dir = i < 0 ? "." : p.path.slice(0, i), name = i < 0 ? p.path : p.path.slice(i+1);
    if (dir !== lastDir) { html += `<div style="margin-top:6px"><b>${esc(dir)}/</b></div>`; lastDir = dir; }
    const gap = p.status === "gap" ? ` <span class="badge b-error" style="height:18px">gap</span>` : "";
    html += `<div style="padding-left:16px">${esc(name)} — <span class="muted">${esc(p.title)}</span>${gap}</div>`;
  }
  return html;
}

function proposalRun(run, jobId) {
  const p = run.progress, rep = run.report;
  if (!p && !rep) return "";
  const done = p ? t("gen_progress", p.done_generations, p.generations) : "";
  let html = `<div class="runbox"><h3>${t("prop_run_title")} <span class="muted">${done}</span></h3>`;
  const best = rep?.best;
  if (rep && !rep.scoreable) {
    html += `<div class="note" style="color:var(--warn)">${t("prop_unscored")}</div>`;
  } else if (p) {
    const nG = rep ? rep.question_set.filter(x => x.a).length : null;
    html += `<div class="kpis">
      <div class="kpi"><b>${p.best_total ?? "—"}</b><span>best (gen ${p.best_gen})</span></div>
      ${nG != null ? `<div class="kpi"><b>${nG}/${rep.question_set.length}</b><span>${t("prop_grounded")}</span></div>` : ""}
    </div>`;
    const hist = p.history.filter(h => h.score && h.score.total != null);
    if (hist.length) html += chart(hist, "", p.best_gen);
  }
  if (best?.pages) {
    html += `<details open><summary>${t("prop_tree")} (${best.pages.length})</summary>
      <div style="font-size:13px;margin-top:8px">${proposalTree(best.pages)}</div></details>`;
    html += `<details><summary>${t("th_page")} · ${t("th_purpose")} · ${t("th_sources")}</summary>${wrapTable(
      `<tr><th>${t("th_page")}</th><th>${t("th_purpose")}</th><th>${t("th_sources")}</th></tr>` +
      best.pages.map(x => `<tr><td>${esc(x.path)}</td><td>${esc(x.purpose)}</td>
        <td>${(x.sources||[]).map(esc).join("<br>") || "—"}</td></tr>`).join(""))}
    </details>`;
  }
  if (rep?.gap_questions?.length) {
    html += `<details open><summary>${t("prop_gapq")} (${rep.gap_questions.length})</summary>
      <div class="note">${t("prop_gapq_desc")}</div>
      <ul style="margin:8px 0">${rep.gap_questions.map(q => `<li>${esc(q)}</li>`).join("")}</ul></details>`;
  }
  if (p?.history?.length) {
    html += `<details><summary>${t("prop_pages_per_gen")}</summary>${wrapTable(
      `<tr><th>gen</th><th>total</th><th>acc</th><th>eff</th><th>${t("th_files")}</th></tr>` +
      p.history.map(h => `<tr${h.generation===p.best_gen?' class="is-best"':''}>
        <td>${h.generation}${h.generation===p.best_gen?" ★":""}</td>
        <td${heat(h.score?.total)}>${h.score?.total ?? "—"}</td><td${heat(h.score?.accuracy)}>${h.score?.accuracy ?? "—"}</td>
        <td${heat(h.score?.efficiency)}>${h.score?.efficiency ?? "—"}</td>
        <td>${(h.page_paths||[]).map(esc).join("<br>")}</td></tr>`).join(""))}
    </details>`;
  }
  if (rep && jobId) {
    html += `<div style="display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap">
      <input type="text" id="exp-${jobId}" placeholder="${t("prop_export_ph")}" style="max-width:340px">
      <button class="ghost" onclick="exportSkeleton('${jobId}','exp-${jobId}','expmsg-${jobId}')">${t("prop_export")}</button>
      <span id="expmsg-${jobId}" class="muted"></span></div>`;
  }
  return html + "</div>";
}

function esc(s) { return String(s ?? "").replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

// 목록 레벨 칩 — 서버가 완료 시 요약한 result_summary (열지 않아도 점수·출처가 보인다)
function jobChips(j) {
  const r = j.result_summary;
  if (!r) return "";
  let html = "";
  if (r.best_total != null) html += `<span class="chip chip-score"><b>${r.best_total}</b>${t("chip_best")}</span>`;
  if (r.parse_failed_runs) html += `<span class="chip chip-warn">${t("chip_failed", r.parse_failed_runs)}</span>`;
  const prov = [r.model, r.code_sha].filter(Boolean).map(esc).join(" · ");
  if (prov) html += `<span class="chip chip-prov" title="${esc(t("provenance_title"))}">${prov}</span>`;
  return html ? `<span class="job-chips">${html}</span>` : "";
}

// 실행 기록 필터 — 모드/상태/문서명. 타임라인(최신 1건)에는 적용하지 않는다
const runsFilter = { mode: "all", status: "all", q: "" };
function setRunsFilter(key, value, btn) {
  runsFilter[key] = key === "q" ? value.trim().toLowerCase() : value;
  if (btn) {
    for (const b of btn.parentElement.querySelectorAll("button")) {
      const on = b === btn; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on));
    }
  }
  lastHtml = ""; poll();
}
function jobMatches(j) {
  if (runsFilter.mode !== "all" && j.mode !== runsFilter.mode) return false;
  if (runsFilter.status === "active" && !["running", "queued"].includes(j.status)) return false;
  if (runsFilter.status !== "all" && runsFilter.status !== "active" && j.status !== runsFilter.status) return false;
  if (runsFilter.q && !j.doc_names.some(n => n.toLowerCase().includes(runsFilter.q))) return false;
  return true;
}

// 펼친 카드의 진행 단계 — 대기 → 실행 → 결과 생성 → 완료. 부분 결과가 있어야 '결과 생성'으로 넘어간다.
// 끝난 실행에는 붙이지 않는다 — 완료 뱃지와 결과 카드가 이미 그 말을 하고 있다
function jobProgress(j, detail) {
  if (!["queued", "running"].includes(j.status)) return "";
  const hasPartial = Boolean(detail?.result) || (detail?.runs || []).some(run =>
    run.report || run.progress?.history?.length || run.progress?.done_generations > 0);
  const current = j.status === "done" ? 3 : j.status === "queued" ? 0 : hasPartial ? 2 : 1;
  const labels = [t("progress_wait"), t("progress_run"), t("progress_result"), t("progress_done")];
  const steps = labels.map((label, index) => {
    const complete = j.status === "done" || index < current;
    const active = j.status !== "done" && index === current;
    const state = complete ? " complete" : active ? " active" : "";
    return `<span class="progress-step${state}"><i aria-hidden="true">${complete ? "✓" : active ? "•" : ""}</i>${esc(label)}</span>`;
  }).join("");
  return `<div class="run-progress" role="group" aria-label="${esc(t("progress_label"))}">${steps}</div>`;
}

async function renderJob(j) {
  const isOpen = open_.has(j.id);
  const statusLabel = t("status_" + j.status);
  const badge = `<span class="badge b-${j.status}" aria-label="${esc(statusLabel)}">${esc(statusLabel)}</span>`;
  const when = new Date(j.created_at*1000).toLocaleTimeString(LOCALES[LANG]);
  let inner = `<div class="job-body" id="job-${j.id}-body" hidden></div>`;
  if (isOpen) {
    const r = await fetch(`/api/runs/${j.id}`);
    const d = await r.json();
    let body = jobProgress(j, d);
    if (d.error && !d.result && !d.runs?.length) body = `<div class="err">${esc(d.error)}</div>`;
    else {
      if (d.status === "error") body += `<div class="err runbox">${t("failed")}: ${esc(d.error)}</div>`;
      if (j.mode === "audit" || j.mode === "apply") {
        body += d.result
          ? (j.mode === "audit" ? auditView(d.result, d.status) : applyView(d.result, d.status))
          : `<div class="runbox" style="color:var(--dim)">${t("preparing_q")}</div>`;
      } else {
        body += d.runs.map(run =>
          (j.mode === "propose" ? proposalRun(run, j.id)
           : run.progress?.mode === "structure" ? structureRun(run, j.id) : summaryRun(run))).join("")
          || `<div class="runbox" style="color:var(--dim)">${t("waiting_gen")}</div>`;
      }
    }
    inner = `<div class="job-body" id="job-${j.id}-body">${body}</div>`;
  }
  // summary만 실행 중 취소 지점이 있다 — 다른 모드는 대기 중(queued)에만 중지 가능
  const stoppable = j.status === "queued" ||
    (j.status === "running" && j.mode === "summary");
  const stopUi = !stoppable ? ""
    : j.cancel_requested
      ? `<span class="job-stop">${t("stopping")}</span>`
      : `<button type="button" class="ghost job-stop" onclick="cancelRun('${j.id}')">${t("stop")}</button>`;
  return `<div class="card">
    <div class="job-actions">
    <button type="button" class="job-head" data-job-id="${j.id}" onclick="toggle('${j.id}')"
      aria-expanded="${isOpen}" aria-controls="job-${j.id}-body">
      ${badge} <span class="mode">${t("mode_" + j.mode)}</span>
      <span class="docs-list">${j.doc_names.map(esc).join(", ")}</span>
      ${jobChips(j)}
      <span class="meta">${t("run_meta", j.mode, j.backend, j.generations, when)}</span>
      <span class="chev${isOpen ? " open" : ""}" aria-hidden="true">▾</span>
    </button>${stopUi}</div>${inner}</div>`;
}

function toggle(id) { open_.has(id) ? open_.delete(id) : open_.add(id); poll(); }

async function cancelRun(id) {
  await fetch(`/api/runs/${id}/cancel`, { method: "POST" });
  poll();
}

// 상태 전이(완료·실패·중지)를 토스트로 알린다 — 첫 폴링은 스냅샷만 잡고 조용히 지나간다
function announceJobTransitions(jobs) {
  const next = new Map(jobs.map(job => [job.id, job.status]));
  if (!jobStatusesReady) {
    jobStatusSnapshot = next;
    jobStatusesReady = true;
    return;
  }
  for (const job of jobs) {
    const previous = jobStatusSnapshot.get(job.id);
    if (!previous || previous === job.status) continue;
    const label = t("mode_" + job.mode);
    if (job.status === "done") showToast(t("toast_done", label), "success");
    else if (job.status === "error") showToast(t("toast_error", label), "error");
    else if (["cancelled", "interrupted"].includes(job.status)) showToast(t("toast_cancelled", label));
  }
  jobStatusSnapshot = next;
}

let timer = null, lastHtml = "";
let autoOpenedLatest = false;
// 위키 최적화 화면의 실행 타임라인 — 가장 최근 실행 1건. 실행 기록과 같은 카드를 쓰되 id는 tl- 접두로 분리
function renderTimeline(jobs, parts) {
  // 첫 로드에 최신 실행은 펼쳐진 채로 — 결과가 주인공이라는 레이아웃 원칙
  if (jobs.length && !autoOpenedLatest) { autoOpenedLatest = true; if (!open_.has(jobs[0].id)) { open_.add(jobs[0].id); setTimeout(poll, 50); } }
  _renderTimelineInto("optTimeline", jobs, parts, 0);
  _renderTimelineInto("proposeTimeline", jobs, parts, jobs.findIndex(j => j.mode === "propose"));
  _renderLiveStatus("liveStatus", jobs[0]);
  _renderLiveStatus("proposeLiveStatus", jobs.find(j => j.mode === "propose"));
}
// 화면 머리글의 상태 알약 — 위키 최적화는 최신 실행, 구조 제안은 최신 제안 실행
function _renderLiveStatus(elId, j) {
  const ls = $(elId);
  if (!ls) return;
  ls.hidden = !j;
  ls.innerHTML = !j ? "" : `<span class="dot ${j.status}"></span><span class="ls-text">${t("status_" + j.status)} · ${t("mode_" + j.mode)} · ${esc(j.doc_names[0] || "")}${j.doc_names.length > 1 ? " " + t("and_more", j.doc_names.length - 1) : ""} · ${j.backend}` +
    (j.result_summary && j.result_summary.best_total != null ? ` · best <b>${j.result_summary.best_total}</b>` : "") + `</span>`;
}
const _timelineCache = {};
// 특정 화면의 타임라인에 job 1건 렌더 — 실행 기록과 같은 카드, id는 tl-<컨테이너> 접두로 충돌 방지
function _renderTimelineInto(elId, jobs, parts, idx) {
  const el = $(elId);
  if (!el) return;
  const expanded = [...el.querySelectorAll("details")].flatMap((d, i) => d.open ? [i] : []);
  const j = idx >= 0 ? jobs[idx] : null;
  const html = j
    ? parts[idx].replaceAll(`id="job-${j.id}-body"`, `id="tl-${elId}-${j.id}-body"`)
                .replaceAll(`aria-controls="job-${j.id}-body"`, `aria-controls="tl-${elId}-${j.id}-body"`)
    : `<div class="card empty-state timeline-empty"><strong>${t("no_jobs")}</strong><span>${t(elId === "proposeTimeline" ? "no_propose_hint" : "no_jobs_hint")}</span></div>`;
  if (html === _timelineCache[elId]) return;
  el.innerHTML = html; _timelineCache[elId] = html;
  const details = el.querySelectorAll("details");
  expanded.forEach(i => { if (details[i]) details[i].open = true; });
}
async function poll() {
  clearTimeout(timer);
  const focusedJobId = document.activeElement?.dataset?.jobId;
  const expandedDetails = [...document.querySelectorAll("#jobs details")]
    .flatMap((el, index) => el.open ? [index] : []);
  let delay = 10000;
  try {
    const r = await fetch("/api/runs");
    if (!r.ok) throw new Error(`poll failed: ${r.status}`);
    const { jobs } = await r.json();
    announceJobTransitions(jobs);
    $("runsCountNav").textContent = jobs.length || "";
    const parts = await Promise.all(jobs.map(renderJob));
    const shown = parts.filter((_, i) => jobMatches(jobs[i]));
    const html = shown.join("") ||
      `<div class="card empty-state"><strong>${t("no_jobs")}</strong>${t("no_jobs_hint")}</div>`;
    $("runsCount").textContent = !jobs.length ? ""
      : shown.length === jobs.length ? t("runs_count", jobs.length) : t("runs_filtered", shown.length, jobs.length);
    const activeJob = jobs.find(j => j.status === "running" || j.status === "queued");
    hasActiveJob = Boolean(activeJob);
    activeJobMode = activeJob ? normalizeActionMode(activeJob.mode) : null;
    delay = hasActiveJob ? 2000 : 10000;
    renderTimeline(jobs, parts);
    if (html !== lastHtml) {
      $("jobs").innerHTML = html;
      lastHtml = html;
      const details = document.querySelectorAll("#jobs details");
      expandedDetails.forEach(index => { if (details[index]) details[index].open = true; });
      if (focusedJobId)
        document.querySelector(`[data-job-id="${focusedJobId}"]`)?.focus();
    }
  } catch (e) {
    delay = 3000;
  } finally {
    syncActionStates();
    timer = setTimeout(poll, delay);
  }
}

applyLang();
loadPrefs();
syncDirActions();
poll();
