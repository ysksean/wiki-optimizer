---
name: wiki-optimize
description: >
  Design and improve source-grounded Markdown wikis with wiki-optimizer: translate
  the user's work into questions, design pages and navigation, inspect knowledge
  gaps, evaluate summary/structure experiments, and prepare verified document
  additions. Use for "위키 설계", "위키 구조 잡아줘", "위키 최적화",
  "optimize my wiki", or "이 자료로 위키 만들어줘". For simply asking an existing
  wiki a question, use the user's normal wiki query workflow.
---

# wiki-optimize

Design the wiki around the work its reader needs to do. A useful result explains
which page answers each important question, what original evidence supports it,
how the reader finds it, and what happens when the source changes.

## Choose the requested outcome

| User needs | Read and use |
|---|---|
| A new wiki, task-specific structure, or redesign of an existing wiki | [Design workflow](references/design.md) |
| Audit, A/B experiments, interpretation, or exporting an existing result | [Tool recipes and evidence rules](references/tools.md) |
| Add one new document to an existing `raw/` + `wiki/` workspace | The incremental-update section in [Tool recipes](references/tools.md) |

Read only the relevant reference. A request for a sketch does not require running
an experiment; a request to build a wiki includes content and navigation, not
just a proposed folder tree. Do not expand a design request into changing the
optimizer's engine or configuring a scheduled watcher.

## Establish context once

- Reuse the conversation's purpose, source paths, target wiki and constraints.
  Ask only for missing information that changes the design: who reads the wiki,
  what they must accomplish, and where the source material is.
- Inspect the existing wiki and its local instructions before proposing changes.
  Preserve established page paths, links and curated content where practical.
- Locate this repository from the skill's location; verify `src/evolve_proposal.py`
  exists. Run recipes from the repo root. Use the user's source paths explicitly;
  commands defaulting to `data/raw/` would evaluate the repository's sample data.
- Use the selected logged-in CLI (`LLM_BACKEND=claude|codex`) and output language
  (`LLM_LANG=ko|en|zh`). Keep the selected backend consistent between Python
  recipes and CLI calls. Do not create API credentials for this workflow.

## Completion and evidence

For a design request, deliver the recommended structure, question-to-page routes,
source evidence, unresolved gaps and the next implementation step. Save a reusable
design artifact when the task calls for one. Clearly distinguish a sketch, a
source-reviewed design, a scored proposal, a generated wiki and an applied update.

For a build request, populate grounded pages, create a navigable index, check
links and exercise the agreed questions against the actual written pages. Keep
unsupported facts as explicit gaps. Stage 0 creates stubs and cannot by itself
complete a build request.

Treat scores according to what was actually measured:

- Stage 0 scores source routing through a proposed skeleton, not finished page
  contents. `grounded` means a source reference exists, not that every claim was
  established. Read the supporting passage before accepting it as evidence.
- A/B held-out scores participate in candidate selection; do not describe them as
  an untouched final test. Show uncertainty, parsing failures and missing coverage.
- A control comparison supports an improvement claim; a single run can still
  yield a useful draft. Do not block ordinary design work on statistical testing.
- Prioritize must-answer questions and preserving correct answers over shorter
  text or a larger aggregate score. Identify regressions by question.

## Writes and follow-through

Keep experiments and review candidates in a fresh task-specific `runs/` directory
unless the user chose another destination. Preserve original source documents.
Generating new files in an authorized output directory can proceed directly.

For replacing an existing wiki, prepare the exact candidate and diff first. Honor
authorization already given for that replacement; otherwise obtain approval for
that concrete change before applying it. Do not rerun generation between review
and apply. Verify source and destination snapshots before replacing files.

Reuse the accepted design and question baseline on the next update. Explain any
compatibility gap between a design's source layout and the incremental tool's
`raw/` + `wiki/` requirement instead of inventing an automatic handoff.
