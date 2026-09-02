# HELIOS Agentic Intelligence Layer Boundary

## Decision

P0 remains framework-agnostic. LangGraph, LangChain adapters, and retrieval-augmented generation are separate consumers of P0’s public API and append-only evidence contracts—not owners of the bid record.

Configured P1A host workers are trusted local executables. A private working
directory, constructed environment, output cap, and process-group timeout are
containment and cleanup controls; they are not an OS sandbox or a security
boundary against malicious code.

## Platform boundary

P0 remains cross-platform. The security-hardened local P1A artifact runner is intentionally narrower: it requires macOS or Linux POSIX directory-descriptor and no-follow semantics to preserve content-addressed artifact containment under concurrent filesystem changes. Unsupported platforms must report the secure-storage-unavailable blocker rather than falling back to pathname-based I/O.

## LangGraph placement

Use LangGraph after P0 for long-running, resumable workflow orchestration:

```text
document packet → scope/scale gate → per-sheet extraction workers
→ V1–V4 verification → deterministic merge → conflict queue → reviewer task
```

Each LangGraph state carries only P0 resource IDs, artifact hashes, task state, retry budget, and a bounded error reason. Its nodes must write evidence/claims through the P0 API; only the deterministic P0 service creates approvals, snapshots, estimates, and releases.

## LangChain placement

Use LangChain selectively as a provider, tools, embedding, and retriever adapter. The P0 domain model, SQL migrations, lifecycle logic, pricing math, and authorization checks must remain plain Python so model/provider changes do not risk permanent bid data.

## RAG placement

RAG is a source-grounded knowledge service. It should retrieve:

- project specs, schedules, addenda, RFIs, and approved manufacturer literature;
- governed Division 23 rules and company policies;
- historical corrections only after their promotion records are approved.

RAG answers return citations to `DocumentRevision` and `EvidenceItem` IDs. It may propose an extraction claim, conflict, RFI, or reviewer task; it cannot write an approved quantity, price, rule, or bid release.

## Promotion and privacy

Retrieval indexes are derived data. They may be rebuilt from source files and must retain document revision hash, access scope, source class, and chunk/citation locators. Any LangGraph/LangChain prompt, model, or retrieval change first runs against the frozen Division 23 regression corpus, then shadow mode, then an approved rollback-capable release.
