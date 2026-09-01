# HELIOS P0 Operator Guide

## Purpose

P0 is the controlled system of record. It stores verified project facts and their audit trail; it is not the PDF/vision or LLM worker itself.

## Platform support

P0 remains cross-platform. Its local database, API, and operator workflow can run on supported Windows, macOS, and Linux hosts. The security-hardened P1A artifact runner is separate from P0 and currently requires macOS or Linux POSIX directory-descriptor and no-follow semantics. On an unsupported platform it blocks with an explicit secure-storage-unavailable error; do not substitute a pathname-based artifact-store fallback.

## Safe job flow

1. Create the project and bootstrap trusted actors/role grants through the local administrator/integration. Role grants and revocations are deliberately not HTTP endpoints in P0.
2. Register every issued drawing, spec, schedule, addendum, quote, and formal RFI response as a hashed `DocumentRevision`.
3. Build and freeze the revision set that is actually being priced. Do not use a mixed or open packet for quantities.
4. Let extractors create evidence and extraction claims only. Each candidate must cite sheet/page and geometry or text span.
5. Create quantity assertions from evidence in the frozen baseline. Review them `CANDIDATE → VERIFIED → APPROVED` or `REJECTED`.
6. Record plan/spec/schedule conflicts. A formal RFI response plus a documented disposition is required before a conflict can stop blocking a takeoff.
7. Create and approve the immutable takeoff snapshot.
8. Register dated quote revisions and select a quote line for every takeoff line to form an estimate. Approve the estimate.
9. Create the as-bid release only when an authorized bidder and submitter both decide on the same approved takeoff/estimate pair. The service rejects expired quote inputs.
10. If an addendum or formal response arrives, create a successor revision set. Do not edit a frozen or approved baseline.

## API rules

- All mutations use `POST /v1/...` with `Content-Type: application/json` and an `Idempotency-Key` header.
- Never call `PUT`, `PATCH`, or `DELETE` on P0 resources.
- The same idempotency key and body returns the original result; the same key with changed body returns `409`.
- Actor role is never supplied with a quantity/takeoff/estimate/bid decision. The service resolves an active role grant for the actor and project.
- The listener is loopback-only. Do not proxy P0 to a network until an authenticated identity/administration layer is added.

## Incident handling

If a document, quote, or role grant was entered incorrectly, preserve it. Add a replacement document, successor assertion, new quote revision, role revocation event, successor estimate, or void event as appropriate. Do not delete history.
