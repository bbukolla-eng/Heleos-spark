# Storage

The evidence vault is immutable by application contract on an ordinary local filesystem. It is not regulatory WORM storage. Objects are written atomically.

SQLite alone is not claimed to prevent privileged alteration.

Mutable review and lifecycle state stays in separate records from immutable content, evidence, and audit rows.

This is the Phase 1 storage statement named in `ROADMAP.md`. The same non-claim is in spec section 4.1 of `docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`.
