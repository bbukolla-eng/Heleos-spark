# Explicit duct connection diagnostics

Resume DUCT-TOPOLOGY-1 from main e88fc9bb99b931d69cbd1a3df59c4f783614dd31.
Preserve the accepted curve evaluation and all uncommitted product work.
The owner authorized implementation, NotebookLM research and bounded Claude work.
Use the accompanying committed duct-topology findings packet. Its verified
Digitize-PID sections distinguish geometry, graph association, domain validation
and adjacency evaluation. No paper establishes our HVAC rules or thresholds.

## Connected capability

Introduce a versioned local image adapter retaining explicit undirected
same-sheet path-pair assertions: connected, not_connected, or uncertain.
Every assertion cites original image evidence. Version 1 and 2 raw responses,
records and sealed evaluation reports remain readable without rewriting bytes.
Add optional versioned independent topology truth to source-bound evaluation
samples. Only explicitly annotated pairs are scored; absent pairs are unknown.
No port direction, cross-page joining, transitive closure, snapping, fitting
quantity or measured-length change is authorized by an assertion.
Report these diagnostics separately from existing geometric acceptance criteria.
Expose retained claims, evidence, truth coverage and limits in the application
and draft exports. Include new modules in the relocatable package.

## Claude assignment: pure helper, two paths only

Claude is sole writer of scripts/duct_topology.py and
tests/drawing-workspace/test_duct_topology.py in its exact-base candidate.
Codex owns all other integration, independent checks, review and commits.
The worker is not alone and must not revert or alter other files.
Implement a Python 3.9+ standard-library-only module with no local imports, I/O,
model calls, geometry calculations, quantity calculations or inferred edges.

Public API:

1. TopologyError(ValueError) exposes code and message.
2. normalize_assertions(assertions, object_ids, evidence_ids,
   allow_uncertain=True) returns a deep independent canonical list.
   IDs are exact nonempty stripped Unicode strings, no surrogates, max160 chars.
   object_ids and evidence_ids must be lists of unique IDs, bounded to 250 and
   2000 respectively. assertions is a list of at most1000 exact-field dictionaries
   with id, members, relation, evidence_ids. Each members list has exactly two
   distinct known object IDs. Each evidence_ids list has 1..128 distinct known
   evidence IDs. relation is connected, not_connected, or uncertain; uncertain
   is forbidden when allow_uncertain=False. Reject duplicate assertion IDs and
   duplicate undirected member pairs, including conflicting relation claims.
   Reject wrong types, bools, extra fields and dangling references before use.
   Sort members, evidence_ids and result by assertion id. Never mutate inputs.
3. compare_assertions(truth, prediction, assignment, matching_state,
   assignment_ambiguous) compares two structurally valid assertion lists.
   It must validate their structure itself using the IDs they contain (truth
   forbids uncertain), not assume callers did so. External identity binding is
   the responsibility of normalize_assertions at the caller boundary.
   assignment is a list of at most250 two-element lists [truth_id,prediction_id],
   exact strings as above, one-to-one on both sides. Reject duplicate endpoints.
   matching_state is definite or indeterminate; assignment_ambiguous is bool.
   Only a definite non-ambiguous assignment allows comparison. For each truth
   pair in id order, map both members through assignment. If either is absent,
   row state is unmatched_geometry. If the correspondence is indeterminate or
   ambiguous, every truth row state is indeterminate. Otherwise find an explicit
   prediction of that exact undirected pair. Missing or uncertain predictions
   give unresolved; matching relation gives correct; opposite gives incorrect.
   Do not infer not_connected for an absent prediction or truth pair.

Return an exact JSON-compatible dictionary with state (evaluated or
indeterminate), scope='explicit_same_sheet_path_pairs', truth_count, correct,
incorrect, unresolved, unmatched_geometry, known_pair_accuracy, coverage, rows,
and unscored_prediction_ids. Indeterminate rows have all four counts zero and
accuracy/coverage null. In evaluated mode accuracy=correct/truth_count and
coverage=(correct+incorrect)/truth_count; both null for zero truth. Every row has
truth_id, truth_members, expected, prediction_id (null if none),
prediction_members (null if no mapped pair), observed (null if none), and state.
Use canonical sorted members. An uncertain prediction still has its id/observed
value. All prediction IDs not associated with a truth row are unscored, sorted;
under ambiguous/indeterminate correspondence every prediction is unscored.
No derived topology pass/fail threshold or quantity authority is added.

Tests must exercise true/false relation cases, omission vs explicit negative,
uncertain predictions, partial truth/unscored predictions, no truth, unmatched
geometry, ambiguous/indeterminate assignments, permutation invariance, immutability,
self-pairs, conflicting/duplicate pairs, duplicate IDs, dangling evidence/objects,
wrong types, Unicode, upper bounds and one-over bounds. Keep the implementation
small; do not add graph libraries or build a generic graph framework.

Codex independently runs the named unittest on Python 3.9, 3.12 and 3.14 and
git diff --check; records actual exits and hashes; reviews complete candidate
inventory; and adds connected producer, scorer, historical replay, UI, export
and package checks on its own paths. Claude uses Read/Glob/Grep/Edit/Write only,
does not execute tests or claim to have run them, and returns a concise report.
Stop on a missing dependency or conflicting task requirement; do not expand scope.

## Continuity and acceptance

Record all research submissions and provider process state under
.heleos/duct-topology-2026-09-15/. Preserve failure evidence. Update root
CURRENT_STATUS.md before a completion checkpoint. Connected tests must prove
that invalid topology leaves workflow and SQLite history unchanged, old reports
retain bytes and stale scorer status, source and response drift is rejected,
and quantities stay unchanged. Independent fixtures test assertions; they do
not establish representative-project accuracy or native Windows acceptance.
