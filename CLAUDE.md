# Claude — Heleos-spark

Read [AGENTS.md](AGENTS.md), [CURRENT_STATUS.md](CURRENT_STATUS.md), [SKILLS.md](SKILLS.md), and the assigned task brief before changing files. Shared policy and the exact assignment define authority; this file supplies Claude-specific workflow guidance.

## Role and execution

Claude may implement scoped Rust code, tests, documentation, and approved tooling, or perform an independent review. Implementation is write-capable within the allowlist; review-only work does not edit source. Use the checkout supplied by Codex, not the home directory, another agent's checkout, or main by default.

For headless work, verify the installed CLI's supported options and authorized login state. Record the selected model, input instruction identities, process/session handle, scope, and limits. Use the runtime's approved editing tools. Do not assume a successful login grants unrestricted filesystem, shell, network, or Git authority. Missing authentication pauses that provider's work without exposing credentials.

## Implementation loop

1. Confirm the base commit, dirty paths, ownership, and next unfinished task.
2. Read only the relevant code and acceptance contract. Preserve completed work and unchanged reviewed bytes.
3. For a new behavior or defect, capture a focused failing check, implement the smallest scoped change, and verify it. Do not recreate already captured regression evidence without a reason.
4. Run the assigned final checks on the handed-off bytes. Report failures honestly; do not relax guards, add unrelated dependencies, or broaden paths to make a gate pass.
5. Return the patch/file identities and the shared handoff record. Remain uncommitted when the task's atomic-commit rule requires it.

## Continuation

Quiet headless output is not proof of termination. Codex checks the existing process before redispatching. Once terminal, update the report immediately. A fix round starts from the last reviewed candidate and addresses only open findings and breakage introduced by the fix. Independent acceptance and integration belong to Codex and the owner, not Claude's self-review.
