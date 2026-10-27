# CI-RETAINED-LINKS-1: existing CI compatibility repair

The new guard passed190 top-level tests and independent review. The existing repository checker then reported60failures:56link resolutions in retained copies and4prose violations. Preserve the failed run in repository-checks.log.

Bounded R01/R02 repair: hash-pin four retained documents to their original link-resolution locations; continue checking everylink and reject changedbytes/unsafeorigin mappings. Preserve all archived/approved bytes. Use existing archive manifest and approved-rule binding as proof, with originalGit equality verified where true. No globalarchive exemption. Correct four roadmap prose lines and remove its obsolete immediate air-device assignment in favor of the verifiedlive status; do not change milestone acceptance or mechanical rules.

Root owns docs/operations/retained-document-origins.json and docs/roadmap.md; ci_retained_links owns tools/ci/checks.py, tests/test_ci_checks.py and retained-links-review.md. Checkpoint validator/hook/CI dispatcher remain accepted and unchanged. Root independently runs affectedCI checks and obtains scoped review. No remoteaction.
