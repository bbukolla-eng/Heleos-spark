# Foundation Task 1 implementation report

## Revisions

- Base SHA: `a2321d4943a562bc7880c2adfc1f1fc358ef9a53`
- Initial Task 1 commit: `68f5c725adc85bb1340c1e741bd120cc8bd01f4e` (`docs: approve foundation and record governance`)
- Current head SHA: `0d7180d12b4e55cf00d80324c049bc805ff313c9`

## Changed paths

- `README.md`
- `docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`
- `SECURITY.md`
- `docs/architecture/decisions/0001-foundation-runtime.md`
- `docs/architecture/task-graph.md`
- `docs/roadmap.md`
- `governance/tools.toml`
- `governance/sources.toml`
- `governance/fixtures.toml`
- `governance/github-apps.toml`

## Commands and results

- Pre-change: `rg -n "Proposed for owner review|Production implementation begins only" README.md docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md` found both required stale statements.
- Verification: `rg -n "Approved by owner on 2026-08-28|Rust 1.96.1|owner_decision_required|Foundation 0.1|Production pilot" README.md SECURITY.md docs governance` found the required approval, runtime, app-gate, and roadmap terms.
- Verification: `rg -n "Proposed for owner review|Production implementation begins only" README.md docs/superpowers/specs` returned no matches (exit 1).
- Staged verification: `git diff --cached --check` passed.
- Staged-path inspection: `git diff --cached --name-only` listed exactly the ten declared Task 1 paths.
- Staged-diff inspection: `git diff --cached -- ...` was reviewed before commit.

## Limitations

This task records, rather than creates, the owner's external approval. The four GitHub Apps remain `owner_decision_required`; no workflow, repository secret, app permission, push, merge, publish, deploy, or destructive action was performed. Foundation 0.1 remains subject to later acceptance, Windows, and owner gates.

## Review round 1

Independent review requested a provenance, rights, and egress correction limited to `governance/tools.toml`.

- Superpowers now records upstream `https://github.com/obra/superpowers`, installed curated-cache version `6.3.0`, MIT rights, and the optional visual companion's outbound logo/version request. Admission is restricted to local planning/TDD, with the visual companion prohibited unless `SUPERPOWERS_DISABLE_TELEMETRY=true` or an equivalent documented opt-out is set before use.
- graph-engineering now records upstream `https://github.com/codejunkie99/graph-engineering` at `cfacb56a05a31ba69bf84d0b8b00f5ce463127ef` and its declared MIT license. The registry records that `npubird/KnowledgeGraphCourse` has no visible repository license; translated/distilled course material is not admitted pending rights clarification. The limited task-graph orchestration material used here remains the only admitted scope.
- `python3 -c 'import tomllib; tomllib.load(open("governance/tools.toml", "rb"))'` passed.
- Both Task 1 `rg` acceptance checks retained their expected results, and the stale-language check returned no matches (exit 1).
- Only `governance/tools.toml` was staged; `git diff --cached --check` passed before commit.
- Focused follow-up commit: `0d7180d12b4e55cf00d80324c049bc805ff313c9` (`docs: tighten tool admission governance`).
