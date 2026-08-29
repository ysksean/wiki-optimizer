---
name: wiki-optimize
description: >
  Run and interpret wiki-optimizer experiments from chat: point at a wiki folder,
  evolve summary strategies (stage A) or folder structures (stage B), judge whether
  the improvement is real (held-out + control arm), and apply the best strategy.
  Use when the user says "위키 최적화", "optimize my wiki", "audit my wiki",
  "요약 전략 실험", "구조 실험 돌려", "실험 결과 해석해줘", "best 전략 적용해줘",
  or asks whether their wiki summaries/structure are any good.
---

# wiki-optimize — run, interpret, apply

Drive this repo's experiment tooling from chat. Do not modify the code; use only
the entry points below.

## 0. Preconditions

- The backend is a logged-in CLI: `LLM_BACKEND=claude|codex` (default claude). No API keys.
- Output language: `LLM_LANG=ko|en|zh` (default ko).
- Never modify source documents. All artifacts go under `runs/` only.
- If you don't know the user's wiki folder path, ask first (e.g. `~/dev/llm_wiki`).

## 1. Running experiments

**Interactive / small scale** — launch the web dashboard for the user:

```bash
python3 src/web.py   # http://localhost:8765
```

**When statistical evidence is needed** — always batch with a control arm:

```bash
python3 src/batch.py --files <md files> --runs 2 --generations 3 --with-control
```

Quick single-document run: `python3 src/evolve.py <md file> --generations 3`
Structure (stage B): `python3 src/evolve_structure.py --docs 3 --generations 2 --n-qa 4`

Runs take minutes per document. Run in the background, poll
`runs/**/progress.json`, and relay progress to the user.

## 2. Interpreting results — never read the numbers naively

Read `runs/batch-*/summary.md` and `report.json`, then judge in this order:

1. **The net effect is the criterion**: evolution effect = mean evolve gain −
   mean control gain. The evolve gain alone is biased upward (max of noisy
   samples) and is not evidence.
2. **Quote held-out scores only.** Train scores feed the reflector and are
   overfit by construction.
3. Verdict guide: net > +0.05 with a majority of improved runs → recommend
   adoption / 0 to +0.05 → "increase runs/generations and re-check" /
   ≤ 0 → say honestly that there is no effect under the current setup.
4. Accuracy is quantized per question (6 questions → 0.167 steps) — never call
   a difference smaller than one step an improvement.

Report to the user as "adopt or not + two lines of evidence", not a table dump.

## 3. Applying the best strategy

Read `best.strategy` (summary prompt) or `best.struct` (file layout) from report.json.

- **Stage A apply**: summarize the target raw documents with the best strategy
  into a user-chosen output folder (default `runs/apply-<date>/`), one
  `<docname>.md` each. To overwrite existing wiki files, always show a diff and
  get confirmation first.
- **Stage B apply**: export `best.struct.files` (title/content) as files verbatim.
- Append the strategy used to `runs/strategies.json` as
  `{doc_type, strategy, score, date}` for reuse (try the stored strategy first
  for same-type documents before re-evolving).

## 4. Never do

- Conclude "it improved" without a control arm
- Adopt a strategy based on train scores
- Overwrite the user's wiki without confirmation
- Modify the experiment scripts (unless asked)
