# wiki-optimizer

Self-evolving optimizer for markdown knowledge bases. It automatically discovers
**how to summarize documents and how to organize them into folders** so that the
wiki actually answers questions well — instead of a human hand-tuning prompts and
structure, the system runs a generate → evaluate → reflect → improve loop.

No API keys. All LLM calls go through the CLI you are already logged into:

| Backend | CLI | Auth |
|---|---|---|
| `claude` (default) | `claude -p` | Claude Code subscription |
| `codex` | `codex exec` | ChatGPT subscription |

## Core principle — query-based evaluation

A summary or folder structure is never judged on its own. The real job of a wiki
is *"ask a question, read the relevant part, get the answer"* — so that is exactly
how quality is measured:

> Ask questions against the summary/structure. Do correct answers come out?

- **Ground truth always comes from the raw source** — never from another summary
  (avoids circular grading).
- **Two axes at once** — accuracy (does it answer correctly?) × efficiency
  (how little do you have to read?).
- **Multiplicative scoring resists gaming** — dump-everything (high accuracy, low
  efficiency) and over-compression (high efficiency, low accuracy) both score low.

Two experimental safeguards on top:

- **Train / held-out question split** — the reflector only sees misses on train
  questions; best-selection and reporting use held-out scores only. This separates
  genuine improvement from memorizing a fixed question set.
- **No-evolution control arm** — `batch.py --with-control` runs a
  seed-strategy-resampling arm alongside. Since "best of N noisy runs" is biased
  upward even with zero real improvement, the true effect is
  `evolve gain − control gain`. Both arms share the same question set per document.
- **Significance testing** — the net effect is reported with a 95% confidence
  interval and a p-value from a paired bootstrap (1000 resamples, fixed seed).
  Documents are the resampling unit, not runs: runs on the same document share a
  question set, so treating them as independent would understate the p-value.
  With fewer than two paired documents the summary says the effect cannot be
  judged instead of claiming an improvement.

## Two stages

**Stage A — summary strategy** (`evolve.py`, `scoring.py`)
- Evolution knob: the summarization prompt
- Loop: summarize → answer the question set from the summary alone → score
  accuracy + efficiency → reflect → improve the strategy

**Stage B — folder structure** (`evolve_structure.py`, `structure.py`)
- Evolution knob: the splitting strategy (how many files, along which axis)
- Loop: organize documents into files → a router picks which files to read per
  question → score accuracy + chars read → reflect → improve the structure

## Run

```bash
python3 src/web.py            # → http://localhost:8765
```

Everything happens in the dashboard: point it at a wiki folder, pick documents,
choose Stage A or B and the backend (claude / codex), run, and watch scores,
strategies, and structures evolve per generation.

The dashboard keeps the two output actions explicit:

- Select **Use this summary strategy** from a completed Stage A result (or enter
  a strategy), select source documents, then generate their new summaries.
  Dashboard generation never silently chooses a strategy from another experiment.
- Stage B results export the **best structure files**, including their content.
  Structure proposals export skeletons. Each result has its own destination form;
  existing files are preserved.
- Before/After averages compare only documents with valid scores on both sides.
  New summaries and the average across all generated documents are shown separately.
- Nested documents keep their relative paths. Diagnostic question caches are keyed
  by source content, document identity, question count, language, backend and model.

The only CLI-exclusive feature is the statistical batch with a control arm:

```bash
python3 src/batch.py --docs 5 --runs 2 --generations 3 --with-control
```

`--arms` picks arms explicitly. The `evolve-wiki` arm (WikiSkill-style,
arxiv 2608.27454) feeds the reflector a structured pattern wiki
(`runs/wiki/patterns/*.md` + `index.md`, maintained by one extra LLM call per
generation) instead of the flat strategy history, testing whether the
*structure* of accumulated history matters — not just its presence:

```bash
python3 src/batch.py --arms evolve,evolve-wiki,control --docs 5 --runs 2
```

## Add one document and update an existing wiki

Open **새 문서로 위키 갱신 / Update wiki with a new document** on the dashboard.
Choose a wiki root containing `raw/` and `wiki/`, then enter or upload one new
Markdown source. Optionally describe what you use the wiki for.

The update flow:

1. Reuses questions from previous accepted updates and generates questions for
   new sources. Each generated question must cite a passage present in the raw source.
2. Selects up to five affected pages from the wiki index and proposes changes to
   those pages only. If none match, it creates a page under `wiki/updates/`.
3. Evaluates the existing wiki and the candidate with the same old and new
   questions. The page writer never sees the evaluation questions or their answers;
   this is one candidate and one comparison, with no score-driven retry loop.
4. Shows affected-page reasons, file diffs, expected answers, source evidence,
   answers before/after, and the body/index characters read.
5. Enables **Apply verified update** only when previously correct old questions
   do not regress, all new questions pass, and the writer reports no source conflicts.

Preparation writes only to the run directory. Applying copies the source into
`raw/`, updates the proposed pages, and persists the question suite in
`.wiki-optimizer/questions.json`. The next addition starts from that state.
Original versions are saved under the run's `backup/`; **Undo this update** restores
them, removes files created by that update, and restores the previous question suite.
Apply and undo reject changes to any raw/wiki file or question baseline since
verification/application, so a later edit or another update is never silently replaced.

CLI equivalents (no extra runtime dependencies):

```bash
python3 src/incremental.py --wiki ~/dev/llm_wiki --source ~/Downloads/new.md \
  --out runs/update-001 --task "Questions I need this wiki to answer" --backend claude
python3 src/incremental.py --out runs/update-001 --apply
python3 src/incremental.py --out runs/update-001 --undo
```

Use a fresh `--out` directory for each preparation. Read `report.json` and
`candidate/wiki/` there before applying. The new document can already be in `raw/`
if it has not been incorporated by this flow; a different file at the import
destination is a conflict. Existing incorporated sources cannot be replaced yet.

This first version handles small local Markdown wikis: at most 400 files and
240,000 characters per raw/wiki tree, and a 60,000-character incoming document.
It uses sampled LLM judgments, not exhaustive knowledge-preservation proofs.
Claude calls for this flow disable built-in tools, MCP servers and skills;
Codex uses the existing read-only CLI sandbox.
Question evidence is checked for presence in the raw text, not independently
fact-checked. Impact selection uses a 600-character excerpt per page; reported
conflicts and non-regression checks cannot guarantee that all omissions are caught.
There is no file watcher or automatic scheduling. Ordinary write failures roll
back touched files. A process crash can leave `.wiki-optimizer/pending.json`;
further apply/undo operations stop until the recorded transaction is recovered
using its `report.json` mutation list and `backup/` originals. Multi-file writes
are not atomic across a process crash.

## Requirements

- Python 3 — standard library only, nothing to install
- One logged-in CLI:
  - `claude` (default): Claude Code CLI. Override the model with `CLAUDE_MODEL`
    (defaults to Haiku 4.5)
  - `codex`: Codex CLI. Override the model with `CODEX_MODEL`
    (defaults to your codex config)

## Development / Tests

Runtime needs nothing installed, but the checks do. Install the two tools once:

```bash
pip install ruff pytest
```

Then run exactly what CI runs (`.github/workflows/ci.yml`, Python 3.12):

```bash
ruff check --select E9,F src tests   # lint — syntax errors + pyflakes only
python -m py_compile src/*.py        # compile every module
pytest tests -q                      # unit tests (pure logic, no LLM calls)
```

Browser regressions (requires an installed Chromium; optionally set `CHROMIUM_PATH`):

```bash
uv run --with playwright --no-project python tests/browser_review.py
```

This uses temporary documents and results, performs real local exports, and checks
mobile layouts and keyboard controls without LLM calls.

Tests live in `tests/` (`conftest.py` puts `src/` on `sys.path`, so no package
install is needed). None of them call `claude` / `codex` — they run offline.

## Layout

```
src/
  llm.py               LLM client (claude / codex CLI sessions, stdlib only)
  scoring.py           Stage A: query-based summary scoring (accuracy x efficiency)
  evolve.py            Stage A: self-evolving summary loop
  structure.py         Stage B: organizer + router + structure scoring
  evolve_structure.py  Stage B: self-evolving structure loop
  batch.py             batch runner (evolve vs control arms) + aggregation
  web.py               local web dashboard (run experiments, inspect results)
data/raw/              sample raw documents (copied from a real wiki; originals untouched)
data/questions/        optional manual question sets: <docname>.json
runs/                  per-generation / batch outputs (gitignored)
```

The dashboard binds to localhost and has no auth — it is a personal, local tool.
Source documents are never modified; all outputs go to `runs/`.
