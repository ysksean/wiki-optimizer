/* One-source update flow; shares the dashboard's request and job history UI. */
const UPDATE_I18N = {
  ko: {
    mode_incremental: "문서 추가 · 위키 갱신", update_title: "새 문서로 위키 갱신",
    update_intro: "영향받는 페이지만 수정하고 기존·새 질문으로 검증합니다. 변경안을 확인한 뒤 위키에 반영할 수 있습니다.",
    update_root: "위키 루트 폴더 (raw/와 wiki/ 포함)", update_source: "추가할 원본 문서 (.md)",
    update_task: "이 위키의 사용 목적 (선택)", update_prepare: "변경안 만들고 검증",
    update_questions: "원본 근거로 기존·새 질문을 준비하고 있습니다.", update_updating: "영향받는 페이지를 찾고 수정하고 있습니다.",
    update_verifying: "동일한 질문으로 변경 전후를 검증하고 있습니다.", update_ready: "검증 통과 · 반영 가능",
    update_blocked: "검증 미통과 · 반영 차단", update_applied: "위키에 반영됨", update_rolled_back: "반영 취소됨",
    update_apply: "검증된 변경안 반영", update_undo: "이번 반영 되돌리기", update_existing: "기존 질문", update_new: "새 질문",
    update_rule: "기존에 맞힌 질문의 퇴행 0건, 새 질문 전부 통과, 보고된 충돌 0건일 때 반영할 수 있습니다. 표본 질문에 대한 LLM 판정이며 전체 지식 보존을 보장하지는 않습니다.",
    update_counts: (c, u, r, n, total) => `수정 ${c}개 · 유지 ${u}개 · 기존 질문 퇴행 ${r}건 · 새 질문 ${n}/${total} 통과`,
    update_before: "변경 전", update_after: "변경 후", update_evidence: "정답과 원본 근거", update_diff: "파일 변경 내용",
    update_regression: "퇴행", update_conflicts: "원본 간 충돌", update_no_old: "기존 원본이 없어 기존 질문 검증은 수행하지 않았습니다.",
    update_read: (p, b, i) => `읽은 페이지: ${p || '없음'} · 본문 ${b}자 + 인덱스 ${i}자`,
    update_result_path: "변경안과 백업 위치", update_uploading: "문서를 업로드하고 있습니다.",
  },
  en: {
    mode_incremental: "Add document · Update wiki", update_title: "Update wiki with a new document",
    update_intro: "Update affected pages and verify existing and new questions. Review the candidate before applying it to your wiki.",
    update_root: "Wiki root (containing raw/ and wiki/)", update_source: "New original document (.md)",
    update_task: "What this wiki is used for (optional)", update_prepare: "Prepare and verify update",
    update_questions: "Preparing questions grounded in original sources.", update_updating: "Finding and updating affected pages.",
    update_verifying: "Comparing the original and candidate with the same questions.", update_ready: "Checks passed · Ready to apply",
    update_blocked: "Checks failed · Apply blocked", update_applied: "Applied to wiki", update_rolled_back: "Update undone",
    update_apply: "Apply verified update", update_undo: "Undo this update", update_existing: "Existing question", update_new: "New question",
    update_rule: "Apply requires zero regressions on previously correct questions, all new questions passing, and no reported conflicts. These are sampled LLM checks, not a guarantee that all knowledge is preserved.",
    update_counts: (c, u, r, n, total) => `${c} changed · ${u} unchanged · ${r} regressions · ${n}/${total} new questions passed`,
    update_before: "Before", update_after: "After", update_evidence: "Expected answer and source evidence", update_diff: "File changes",
    update_regression: "Regression", update_conflicts: "Source conflicts", update_no_old: "No existing raw sources; existing-question checks were not performed.",
    update_read: (p, b, i) => `Pages: ${p || 'none'} · body ${b} chars + index ${i} chars`,
    update_result_path: "Candidate and backup location", update_uploading: "Uploading document.",
  },
  zh: {
    mode_incremental: "添加文档 · 更新 Wiki", update_title: "用新文档更新 Wiki",
    update_intro: "仅更新受影响的页面，用新旧问题验证。查看变更后可应用到 Wiki。",
    update_root: "Wiki 根目录（包含 raw/ 和 wiki/）", update_source: "新增原始文档（.md）",
    update_task: "Wiki 用途（可选）", update_prepare: "生成并验证变更",
    update_questions: "正在根据原始资料准备问题。", update_updating: "正在查找并修改受影响的页面。",
    update_verifying: "正在使用相同问题比较变更前后。", update_ready: "验证通过 · 可以应用",
    update_blocked: "验证未通过 · 禁止应用", update_applied: "已应用到 Wiki", update_rolled_back: "已撤销",
    update_apply: "应用已验证的变更", update_undo: "撤销本次变更", update_existing: "已有问题", update_new: "新问题",
    update_rule: "原先答对的问题不能退步，新问题必须全部通过，且无报告的冲突。这是对抽样问题的 LLM 验证，不保证保留全部知识。",
    update_counts: (c, u, r, n, total) => `修改 ${c} 页 · 保留 ${u} 页 · 退步 ${r} 项 · 新问题通过 ${n}/${total}`,
    update_before: "变更前", update_after: "变更后", update_evidence: "预期答案与原始依据", update_diff: "文件变更",
    update_regression: "退步", update_conflicts: "资料冲突", update_no_old: "没有已有原始资料，未验证已有问题。",
    update_read: (p, b, i) => `页面：${p || '无'} · 正文 ${b} 字 + 索引 ${i} 字`,
    update_result_path: "变更与备份位置", update_uploading: "正在上传文档。",
  },
};

let incrementalUploading = false;
async function uploadIncremental(files) {
  if (!files?.length) return;
  const file = files[0];
  const msg = $("updateMessage");
  incrementalUploading = true;
  $("updateGo").disabled = true;
  msg.textContent = t("update_uploading");
  try {
    if (!file.name.toLowerCase().endsWith(".md") || file.size > 2 * 1024 * 1024) throw new Error("Choose a Markdown file under 2 MB.");
    const response = await fetch("/api/upload", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({files: [{name: file.name, content: await file.text()}]})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    $("updateSource").value = `${result.dir}/${result.saved[0]}`;
    msg.textContent = "";
  } catch (error) { msg.textContent = error.message; }
  finally { incrementalUploading = false; syncActionStates(); }
}

function startIncremental() {
  savePrefs();
  startJobRequest({mode: "incremental", dir: $("updateRoot").value.trim(),
    source_file: $("updateSource").value.trim(), task: $("updateTask").value.trim(),
    backend: $("updateBackend").value, language: LANG, n_qa: 6}, "updateMessage");
}

async function commitIncremental(button, id, undo = false) {
  const box = button.closest(".incremental-result");
  const message = box.querySelector("[role=status]");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  message.textContent = "";
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(id)}/${undo ? 'undo-update' : 'apply-update'}`,
      {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || t("request_failed"));
    showToast(t("update_" + result.status), "success");
    lastHtml = "";
    await poll();
  } catch (error) { message.textContent = error.message; button.disabled = false; }
  finally { button.removeAttribute("aria-busy"); }
}

function incrementalView(result, id) {
  if (result.stage !== "complete") return `<div class="runbox">${esc(t("update_" + result.stage))}</div>`;
  const answer = row => `<p>${row.score === 1 ? '✓' : '✗'} ${esc(row.pred)}</p><small>${esc(t("update_read", row.picked.join(', '), row.read_chars, row.index_chars))}</small>`;
  const checks = result.checks.map(row => `<details class="incremental-check${row.regression ? ' regression' : ''}"><summary>${row.before.score === 1 ? '✓' : '✗'} → ${row.after.score === 1 ? '✓' : '✗'} · ${esc(t('update_' + row.kind))} · ${esc(row.q)} ${row.regression ? '— ' + esc(t('update_regression')) : ''}</summary>
    <div class="incremental-answers"><div><b>${t('update_before')}</b>${answer(row.before)}</div><div><b>${t('update_after')}</b>${answer(row.after)}</div></div>
    <p><b>${t('update_evidence')}</b>: ${esc(row.a)}</p><blockquote>${esc(row.evidence)}</blockquote><code>${esc(row.source)}</code></details>`).join('');
  const changes = result.changes.map(change => `<details><summary>${esc(change.path)}</summary><p>${esc(change.reason)}</p><pre class="incremental-diff">${change.diff.split('\n').map(line => `<span class="${line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : ''}">${esc(line)}\n</span>`).join('')}</pre></details>`).join('');
  const action = result.status === 'ready'
    ? `<button type="button" onclick="commitIncremental(this, '${id}')">${t('update_apply')}</button>`
    : result.status === 'applied' ? `<button type="button" class="ghost" onclick="commitIncremental(this, '${id}', true)">${t('update_undo')}</button>` : '';
  return `<div class="runbox incremental-result"><h3>${esc(t('update_' + result.status))}</h3>
    <p>${esc(t('update_counts', result.changes.length, result.unchanged_pages, result.regressions, result.new_passed, result.new_total))}</p>
    <p>${esc(t('update_rule'))}</p>${!result.existing_total ? `<p>${t('update_no_old')}</p>` : ''}
    ${result.conflicts.length ? `<p class="err">${t('update_conflicts')}: ${result.conflicts.map(esc).join('; ')}</p>` : ''}
    <h4>${t('update_diff')}</h4>${changes}<h4>${t('update_existing')} / ${t('update_new')}</h4>${checks}
    <p>${t('update_result_path')}: <code>${esc(result.run_dir)}</code></p>
    ${action}<span class="err view-message" role="status" aria-live="polite"></span></div>`;
}
