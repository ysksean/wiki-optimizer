# Current tool recipes and their limits

Run these from the verified wiki-optimizer repository root. Replace example paths
with actual user-selected paths and use a fresh output directory. Check `--help`
if the installed checkout differs; do not invent flags or silently use sample data.

## Stage 0: propose a source-grounded skeleton

Write the task brief to a file, then start with one generation to inspect question
quality, source coverage and the proposed page boundaries:

```bash
LLM_BACKEND=claude LLM_LANG=ko python3 src/evolve_proposal.py \
  --source /absolute/path/source-root \
  --task-file /absolute/path/task.md \
  --generations 1 --n-qa 8 --out-dir /absolute/path/task-runs
```

The report is in `task-runs/proposal-*/report.json`; inspect `question_set`,
`gap_questions`, `best.pages`, `best.score`, `history[].validate_stats`,
`scoreable` and `parse_failed`. Never infer a best generation's warnings from a
different generation. All-gap proposals have no valid numerical score.

Stage 0 uses the same questions for reflection and best selection. It has no
held-out split. Its efficiency denominator depends on the sources the proposal
references; do not compare different source inventories by aggregate score alone.
Question-cache identity omits source content and model/language. After source
edits, model/language changes or a gap being resolved, use `--no-cache` and treat
the new questions as a different evaluation set. For a paired comparison, explicitly
reuse a frozen question set through Python rather than two independent regenerations.

`--seed-from-runs` selects by highest stored score. Use it only within an output
directory scoped to the same purpose and source set. Do not select a global “best”
from unrelated tasks. Stop additional generations when there is no concrete
diagnostic reason to run them; do not promise automatic convergence.

To export an already reviewed Stage 0 report, use the dashboard's export action
or load that exact `best.pages` and call `skeleton.write_skeleton(pages, destination,
write=True)`. Inspect destination paths for symlinks before invoking this writer.
It skips existing files and produces empty stubs. Re-running the generator with
`--write` creates a new proposal and does not export the previously reviewed one.
New default proposals use paths relative to `wiki/`; set their export destination
to `<workspace>/wiki`. Check older/custom reports before selecting a destination
to avoid a doubled `wiki/wiki/` prefix. Export does not create raw sources, a
functional index or an operation log. Complete those in the build workflow.

## Existing wiki: diagnose and experiment

For interactive diagnosis, open `python3 src/web.py` and use the dashboard. Reuse
a compatible running server where available; do not terminate unrelated sessions.
Current audit excludes `wiki/projects/`; disclose that scope if the user's task
depends on project pages. A page never selected by sampled questions is a coverage
candidate, not evidence that it should be deleted.

Use actual selected files for batch comparisons:

```bash
LLM_BACKEND=claude LLM_LANG=ko python3 src/batch.py --stage summary \
  --files /absolute/path/doc-a.md /absolute/path/doc-b.md \
  --runs 2 --generations 3 --n-qa 8 --with-control

LLM_BACKEND=claude LLM_LANG=ko python3 src/batch.py --stage structure \
  --files /absolute/path/doc-a.md /absolute/path/doc-b.md \
  --runs 2 --generations 3 --n-qa 8 --with-control
```

These are example budgets, not a requirement for every request. Structure batch
treats the selected files as one bundle; repeated runs of that single bundle are
not multiple independent document bundles. A bundled run alone cannot establish
generalization across different wikis.

Inspect `summary.md`, `results.csv` and the underlying reports:

- Exclude and disclose parsing-failed runs. Check backend/model, corpus, question
  identity and selection procedure before comparing results.
- Use evolution gain minus control gain, with the reported document-level paired
  bootstrap interval and sample count. A positive point estimate, `+0.05`, or a
  majority of improved runs is not a universal adoption threshold.
- Treat small samples and intervals overlapping zero as inconclusive. Report
  accuracy's question count and granularity, but do not impose that step size on
  the composite score: efficiency can change while accuracy stays constant.
- Separate train feedback, selection/validation scores and any genuinely untouched
  final evaluation. In A, strategy history can carry held-out scores back into
  reflection. A/B reports therefore do not establish an independent final test.
- Explain answer regressions and absolute reading costs as well as total scores.

For selected-summary application, pass explicit files and the explicitly selected
strategy to `apply.run_apply(...)` using the installed signature, or the dashboard.
Do not pick a strategy solely because it has the largest score across unrelated runs.
For B export, use the selected run's export action; it retains page contents and
source frontmatter. Do not claim a new evaluation for an unchanged exported result.
The B organizer treats its pages as the contents of the default workspace's
`wiki/` layer, but its title-based export and flat router do not implement or
measure nested folder navigation. Do not present B output as a complete vault.

For the proposed workload-based comparison of structure plus summary, see
[evaluation.md](evaluation.md). Its family-separated final test, navigation trace,
claim checks and update benchmark are a design for further work, not capabilities
of the current CLI.

## Add a new original document

For a wiki with `raw/` and `wiki/`, the incremental engine prepares a candidate,
checks old and new questions, stores diffs, and persists the question baseline on
application. It does not currently replace already incorporated raw documents.

```bash
python3 src/incremental.py --wiki /absolute/path/wiki-root \
  --source /absolute/path/new-document.md --out /absolute/path/update-run \
  --task "The user's actual wiki purpose" --backend claude
```

Inspect `report.json`: `status`, `changes`, `checks`, `conflicts`, `regressions`,
`new_passed` and `new_total`. Incomplete/failed evaluation is not a passing score.
Review the candidate before applying it within the user's authorization:

```bash
python3 src/incremental.py --out /absolute/path/update-run --apply
python3 src/incremental.py --out /absolute/path/update-run --undo
```

Apply requires no previously correct question regressions, every new question
passing, and no reported conflict. File snapshot checks guard both apply and undo.
If the source or wiki changed, prepare a fresh candidate instead of bypassing the
check. Keep the original run directory; it contains the backup and undo record.

This is sampled LLM verification of a one-document update, not a statistical claim
about an evolution algorithm; it does not require an A/B control arm. Stage 0's
arbitrary source roots and empty skeletons are not automatically compatible with
this workflow. Do not manufacture a raw baseline from generated summaries.
