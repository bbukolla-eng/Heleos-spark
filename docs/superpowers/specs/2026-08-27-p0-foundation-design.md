# HELIOS Takeoff Core — P0 Foundation Design

## Goal

Build the local-first, auditable data and control plane for an evidence-backed Division 23 takeoff system. P0 stores immutable source revisions, extracted evidence, quantity assertions, review decisions, takeoff snapshots, quote inputs, and bid releases. It intentionally does not perform OCR, drawing vision, topology inference, estimating formulas, or external vendor synchronization.

## Chosen approach

Use a Python 3.12 standard-library package backed by SQLite. SQLite is the local authoritative store in P0; the schema, UUID identifiers, append-only records, decimal serialization, and explicit foreign-key relationships are designed to migrate cleanly to PostgreSQL later. No external runtime dependency is required.

The public boundary is a small local JSON-over-HTTP WSGI API plus a Python service layer. Future desktop, mobile, vision, pricing, and cloud connectors consume versioned resources; they do not read or write database tables directly.

## Architecture

```text
immutable source files
  → DocumentRevision / RevisionSet
  → EvidenceItem / ExtractionClaim
  → QuantityAssertion / ReviewDecision
  → TakeoffVersion / TakeoffLine
  → EstimateVersion / QuoteRevision
  → BidRelease
```

- `DocumentRevision` is an immutable issued artifact. A new drawing, schedule, specification, addendum, or formal RFI response creates a new record with a SHA-256 hash.
- `RevisionSet` is the controlled bid baseline. It lists the exact document revisions used for a takeoff. Freezing it prevents in-place changes.
- `EvidenceItem` is an exact location in a document revision. It carries a page/sheet, geometry or text span, and optional content digest.
- `RoleGrant` is an immutable authorization record. Every review, approval, release, or submission decision references a grant that was valid for the relevant project at the decision time.
- `ExtractionClaim` records what a specific plugin/run inferred from evidence. Claims never overwrite evidence or each other.
- `QuantityAssertion` records a candidate, correction, or replacement measurement. Status is derived from append-only review decisions; corrections supersede rather than mutate the original assertion.
- `TakeoffVersion` snapshots approved assertions into immutable, exportable lines. Approval is role-based and records the actor, rationale, and time.
- `QuoteRevision` and `QuoteLine` preserve supplier pricing inputs with UOM, currency, validity, terms, and source document lineage.
- `EstimateVersion` is an immutable cost snapshot based on one approved takeoff. `BidRelease` may only reference an approved takeoff and approved estimate and requires an authorized bidder decision.

## Non-negotiable rules

1. P0 never overwrites a source revision, evidence item, extraction claim, quantity assertion, takeoff line, quote line, estimate line, approval, or bid release.
2. Every takeoff line must resolve to at least one evidence item on an immutable document revision.
3. Every measurement is scoped to one `RevisionSet`; an addendum or RFI response creates a new baseline and cannot alter an approved/as-bid baseline in place.
4. Conflicting plan, schedule, spec, or RFI claims remain conflicts until explicitly dispositioned. P0 does not assume a universal order of precedence.
5. Roles, not a hard-coded personal name, control `ESTIMATOR_REVIEWER`, `AUTHORIZED_BIDDER`, and `SUBMITTER` decisions.
6. Currency and quantities use exact decimal strings; no binary floating point is persisted.
7. Plugin workers can write proposed evidence and claims only. The service layer alone creates canonical snapshots, approvals, estimates, and releases.
8. Any future rule/model self-improvement must write a proposed change record, pass a frozen regression corpus, receive approval, and be reversible. P0 records the hooks; it does not activate autonomous promotion.
9. Retriable mutation commands use an idempotency key and request fingerprint. Reusing a key with a different request is a conflict; retrying the same request returns the original result.
10. Migration files are append-only after commit. New schema work is delivered as a later numbered migration; no deployed migration is edited.

## P0 resource contract

| Resource | Purpose | Immutable fields / linkage |
|---|---|---|
| Project | Work container | Project code and metadata |
| Actor / RoleGrant | Authenticated decision authority | Actor, project scope, role, valid interval, revocation event |
| DocumentRevision | Issued source artifact | SHA-256, type, number, dates, optional superseded revision |
| RevisionSet | Bid baseline | Included document revisions and freeze status |
| EvidenceItem | Source locator | Document revision, sheet/page, geometry/text span, digest |
| ExtractionRun | Reproducible worker execution | Plugin/version/config/input hashes |
| ExtractionClaim | Machine or human interpretation | Evidence, run, subject, payload, confidence |
| QuantityAssertion | Candidate/replacement measurement | Revision set, UOM/decimal, scope state, evidence allocations |
| ReviewDecision | Append-only evaluation | Subject, actor role, outcome, rationale |
| TakeoffVersion / Line | Frozen quantity package | Revision set, source assertions, approval |
| Conflict / RFI | Unresolved design discrepancy | Revision set, affected objects, formal answer lineage |
| QuoteRevision / Line | Pricing input | Supplier, source, UOM, currency, valid dates, terms |
| EstimateVersion / Line | Frozen priced output | Approved takeoff, quote/cost basis, approval |
| BidRelease | Final as-bid artifact | Approved takeoff, estimate, authorized approvals |
| IdempotencyRequest | Safe retriable mutation ledger | Operation scope, caller key, request fingerprint, result resource |

## State transitions

```text
RevisionSet: OPEN → FROZEN
QuantityAssertion: CANDIDATE → VERIFIED → APPROVED | REJECTED
TakeoffVersion: DRAFT → REVIEWED → APPROVED | SUPERSEDED
EstimateVersion: DRAFT → APPROVED | SUPERSEDED
BidRelease: PENDING → RELEASED | VOIDED
```

Transitions are represented by new decision rows; source records stay immutable. Adding a replacement document creates a successor revision set and impact record rather than reopening an older frozen set. A mutable `status` is only a read projection; the relevant immutable event remains authoritative.

## API scope

P0 exposes `/health`, project creation, document revision registration, revision-set creation/freezing, evidence/claim recording, quantity assertion and review, takeoff creation/approval, quote and estimate creation, and bid release. Mutation routes live beneath `/v1`, accept JSON only, and use an `Idempotency-Key` where retries are supported. JSON responses contain only the public resource IDs and data required by app clients; database tables remain private.

## Validation and acceptance criteria

- A local database can initialize from blank state using the migration runner.
- An approved/as-bid takeoff cannot change when a new document revision arrives.
- Every exported takeoff line has linked evidence, a document hash, a sheet/page, and an extraction or reviewer provenance record.
- A bid release fails without an approved takeoff, approved estimate, non-expired quote inputs, and authorized bidder approval.
- Re-running the same commands against the same database is idempotent where an idempotency key is supplied.
- Tests cover positive and negative lifecycle paths, immutable-write rejection, revision impacts, and API error responses.

## Explicitly deferred

- OCR/PDF/vector/CAD extraction and rendering
- Duct/piping topology as a bid-driving dependency
- Assembly/labor formulas and live vendor pricing
- Graph database, microservices, cloud sync, mobile UI, and unattended submission
- Automated rule/model promotion
