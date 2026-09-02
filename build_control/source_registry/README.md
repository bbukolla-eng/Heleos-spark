# Build-time source registry

This directory stores canonical, sanitized metadata for research used to build HELIOS. It is not a research database and it grants no runtime, engineering, quantity, pricing, approval, or bid authority.

- The notebook manifest defines logical research domains. It contains no live NotebookLM URL, provider notebook ID, account identity, credential, or session state.
- Source bodies, copyrighted documents, private project files, prompts, responses, and browser artifacts are never stored here.
- One canonical source identity can be cited from many packets and logical notebooks. The source object is stored once; packet and notebook memberships remain visible in the append-only import ledger.
- Packet and source objects are immutable and content-addressed. A corrected packet uses a new packet ID and an explicit predecessor. One predecessor cannot fork into several successors.
- Packet import validates the entire requested batch before publishing objects and appends one final authoritative import event only after every object is safely published. Objects left without that final event after a storage interruption are unreachable and do not count as imported.
- Exact replay is idempotent. Reuse of an identity with different bytes fails closed.
- Provider removal or later correction never erases prior packet, source, membership, conflict, limitation, or gap history.

Codex and Claude consume only a deterministic BuilderSourceBundle tied to a frozen BuildTask's declared, integrated `SourcePacket` graph nodes. The bundle remains advisory input to later reviewed implementation work.
