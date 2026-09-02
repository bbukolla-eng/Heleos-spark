# HELIOS P0 Acceptance Record

Verified on 2026-08-27 against commit `10b2442`.

## Regression suite

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Result: **31 tests passed**. The suite exercises immutable identity/document/takeoff rows, migration idempotence, evidence/claim provenance, review authority and state transitions, direct-SQL snapshot provenance/completeness gates, post-approval insert locks, frozen-baseline enforcement, addendum isolation, RFI conflict disposition, quote-expiry blocking, API idempotency/rollback, CLI initialization, package metadata, and the end-to-end workflow.

## Installed-package check

```bash
python3 -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir /tmp/helios-wheel .
python3 -m venv /tmp/helios-venv
/tmp/helios-venv/bin/pip install --no-index --no-deps /tmp/helios-wheel/*.whl
/tmp/helios-venv/bin/helios-p0 init --database /tmp/helios-installed.sqlite3
```

Result: the wheel installed without runtime dependencies, `helios-p0 init` completed, and the installed database applied migration **8**. This confirms SQL migrations are included in the distributable package rather than relying on an editable checkout.

## Fresh-database demonstration

```bash
PYTHONPATH=src python3 examples/p0_demo.py --database /tmp/helios-p0-demo.sqlite3
```

Result: a newly created database produced:

- frozen `M-101` drawing and `M-601` schedule baseline;
- sheet/text-located evidence and two extraction claims;
- reviewed/approved duct and equipment quantities;
- an approved takeoff with 2 lines;
- two supplier quote selections, including `42 LF × $88.00 = $3,696.00`;
- an approved estimate and a `RELEASED` bid with both `AUTHORIZED_BIDDER` and `SUBMITTER` approval events.

The demo refuses an existing database path. It is therefore safe to use as a repeatable quick-start check without overwriting an existing project record.

## Scope boundary

This acceptance record verifies the P0 system of record and its local API. It does not claim that PDF/CAD extraction, computer vision, RAG retrieval quality, topology inference, labor costing, live supplier pricing, code compliance, or unattended bid submission are implemented or production-approved.
