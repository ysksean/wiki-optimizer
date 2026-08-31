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

The only CLI-exclusive feature is the statistical batch with a control arm:

```bash
python3 src/batch.py --docs 5 --runs 2 --generations 3 --with-control
```

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
