# Compare layouts and summaries for a workload

This is an evaluation design and manual review protocol. The current CLI does
not implement the complete protocol. Use tools.md for what it actually measures.
Do not claim a global optimum: report the best supported candidate among those
tested for a specified workload, reader, source snapshot and budget.

## Freeze the task before generating candidates

Record the user's purpose, reader (human or agent), available reading tools,
query frequency, update frequency and constraints. A question-answer workload
can use answer checks; a workflow such as onboarding or incident response needs
completion criteria for the actual task as well. Human usability requires human
task trials; an agent score is not a substitute.

Build representative cases from user questions and source-grounded tasks. Each
case records an ID, task/question, task family, importance, must-answer flag,
required answer facts, original evidence, answerability and a success rubric.
Use actual query frequencies when available; otherwise declare provisional
weights and show category-level results. Include cross-page questions, exceptions,
unanswerable cases and conflicting/versioned sources when relevant. Do not invent
a policy for resolving source conflicts.

Freeze cases before candidate writing; do not derive the test only from the
generated summaries. Review important reference answers against raw passages.
Split by task family or source cluster so paraphrases of the same fact do not
cross development, selection and final-test boundaries. Development feedback
may improve candidates; selection chooses finalists; reserve the final test until
the exact candidate and selection rule are locked. The writer receives the user
brief and sources, not hidden questions, rubrics or answers. Do not leak final
results through persistent strategy history. If final results inform a revision,
retire that set as a final test. Small case sets support a pilot, not a reliable
generalization claim; retain must-answer checks as explicit acceptance tests.

## Search a bounded set of actual wikis

Keep the default raw/wiki separation stable. Vary internal page boundaries,
folder grouping, index descriptions, links, section granularity and summary policy.
For an initial pilot, consider three meaningful layouts (flat concepts, task flow,
or a shallow hybrid) and two summary policies (concise or detailed with explicit
preconditions/exceptions): six candidates, only if the workload justifies them.
These counts and layouts are a starting budget, not prescribed templates.

Compile actual page contents and navigation for each candidate. Keep source hashes,
model/version, reader tools, context limits, evaluation cases and generation
budgets comparable. Preserve candidate files and record configuration/hashes.
Test the existing wiki or a simple source-preserving layout as a baseline. A
fixed-strategy regeneration control helps distinguish strategy improvement from
the benefit of sampling more candidates; give it a comparable generation budget.

After initial comparisons, change one factor at a time to diagnose effects: hold
summary policy fixed while editing boundaries/navigation, then hold layout fixed
while editing summaries. Recheck the combined result because these factors interact.
Stop at the declared run/token budget or when feedback gives no actionable change.

## Separate navigation, content and answering failures

Run the actual reader from wiki/index.md with its normal file-listing, search,
section-reading and link-following tools. Capture paths, searches, visited sections,
answer and citations. Isolate it from raw sources, reference answers, other
candidates and previous sessions when testing wiki-only quality. If production
permits raw fallback, run that mode separately and report its use and cost.

For failures, compare three paths with the same answerer and a fixed context budget:

| Path | What it helps diagnose |
|---|---|
| Verified original evidence directly supplied | Whether the task/reference/answerer works at all |
| Independently located candidate passages directly supplied | Whether the summary retained sufficient evidence |
| Normal entry-index navigation | Whether the reader can find that evidence |

Raw succeeds but no sufficient candidate passage exists suggests content loss;
candidate evidence succeeds when supplied but ordinary navigation fails suggests
a navigation problem. If sufficient evidence is reached and the answer fails,
inspect answer generation. Mixed failures remain possible. These are diagnostics,
not automatic causal proofs. The oracle passage selector must not see the expected
answer, and its failure to locate evidence alone is not proof of absence; inspect
the relevant source-to-page mappings before labeling content loss.

## Measure quality and cost separately

| Dimension | Evidence to retain |
|---|---|
| Task success | Required facts/steps satisfied; must-answer and category results |
| Faithfulness | Claims supported by original evidence; unsupported claims, conflicts and stale facts |
| Citation quality | Whether citations support claims and cover claims requiring evidence |
| Navigation | Sufficient evidence reached within budget, failed routes and fallback rate |
| Reading cost | Index/search output + page/section text, repeated reads, calls and end-to-end latency |
| Update cost | Edits and generation cost for the same source addition, plus old/new task checks |

Count actual input/output tokens where available; label character counts as a
proxy. Use the same fixed corpus if reporting normalized efficiency. Never let a
candidate enlarge its own denominator by citing more sources. Show average and
tail costs for successes as well as failures: a cheap incorrect answer is not an
efficiency win. Track full request token costs separately from unique text read.
Folder depth, page count, broken links and duplicated facts are diagnostics;
page count or shallowness alone is not a quality objective.

Use claim-level checks for completeness and faithfulness, with a calibrated LLM
judge and manual review of must-answer cases or disputed judgments. Parsing errors
are failed evaluations, not ordinary incorrect answers or silent exclusions.
The dimension separation is informed by [RAGAS](https://arxiv.org/abs/2309.15217)
and citation evaluation by [ALCE](https://aclanthology.org/2023.emnlp-main.398/).
These are evaluation references, not a requirement to introduce a vector database.

## Select a candidate without trading away required knowledge

Declare acceptance rules before looking at results: passing must-answer cases,
no unacceptable unsupported claims/conflicts, no prohibited baseline regressions,
and any user-specified cost limits. Do not use a weighted average to compensate
for failing these rules. If none qualify, report no acceptable candidate.

Among qualifying candidates, show the tradeoffs between quality, query cost and
maintenance cost. Prefer a cheaper candidate only when its quality is adequate
under a predeclared tolerance; otherwise retain multiple nondominated options.
Expected operating cost over a stated horizon can be estimated as build cost +
query count × query cost + update count × update cost, using consistent units
and explicit workload assumptions. Test sensitivity when frequencies are unknown.

Compare candidates on identical cases and report paired uncertainty at the task
family/source-cluster level, plus repetitions for model variation. Repeated runs
on one wiki do not establish performance across wikis. Lock the selected artifact
before final evaluation and retain source/candidate/case hashes and reader config.
Expose final results for confirmation, not another round of winner selection.

## Current implementation gaps

- Stage 0 scores original source passages linked by a skeleton, not compiled summaries.
- B evaluates generated contents through a flat whole-page router, not folder traversal.
- The current aggregate mixes accuracy and read efficiency and lacks the above gates.
- Stage 0 has no split; A/B selection scores are not an untouched final test.
- Incremental updates have useful before/after checks, but no complete workload
  benchmark, claim-level judge or navigation trace.

Implement the frozen case/reader-trace evaluation first, then bounded joint
candidate search. Optimizing a larger search space against the existing score
alone would leave these measurement gaps unresolved.
