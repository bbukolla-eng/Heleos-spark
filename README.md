# Heleos-spark

Heleos-spark is a clean-room, evidence-first HVAC and Division 23 takeoff intelligence system for native Windows and macOS workflows, with a controlled iPhone companion.

This repository is private and starts from a new history. No code, configuration, data, tests, prompts, artifacts, issues, or Git history from the quarantined predecessor repository may be imported, inspected, or reused.

## Current phase

The repository is in foundation design. Production implementation begins only after the owner approves the written design in [`docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`](docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md).

The first implementation milestone will establish the operational database, immutable evidence vault, deterministic PDF intake, and tests before broader AI, research, or interface work.

## Authority rule

Research systems and AI workers may propose findings and patches. Deterministic code, governed data, cited evidence, automated tests, and explicit human approval determine production truth.

## Working with Claude Code

Claude Code operates in one designated branch, named in [`.claude/work-branch`](.claude/work-branch), and treats every other branch as read-only. The rule, the enforcement hooks, and the day-to-day flow are described in [`CLAUDE.md`](CLAUDE.md) and [`docs/workspace/claude-code-lane.md`](docs/workspace/claude-code-lane.md). Hook tests run with the standard library only:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```
