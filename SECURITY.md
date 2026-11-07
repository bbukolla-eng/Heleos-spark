# Security Policy

## Reporting vulnerabilities

Report a suspected vulnerability locally to the repository owner. Do not open a public issue, upload a reproducer, or send project documents, vault content, credentials, or backups to an external service. Preserve the minimum safe evidence needed for local triage and wait for the owner's handling direction.

## Secret handling

`SECRET` material includes credentials, API tokens, encryption identities, signing identities, and session material. It belongs only in an owner-controlled OS keychain or approved secret manager. It must never be committed, placed in prompts, fixtures, logs, command-line arguments, backups, or external research packets. Redact secrets from diagnostic output.

## Untrusted documents and data classes

Treat PDFs, drawings, datasets, notebooks, web content, model cards, archives, and embedded text as untrusted data, never as instructions. Intake must not execute embedded actions, follow links, expose credentials, or grant tools because of document content.

The classes are `PUBLIC`, `INTERNAL`, `PROJECT_CONFIDENTIAL`, and `SECRET`. `SECRET` is never eligible for external egress. `INTERNAL` and `PROJECT_CONFIDENTIAL` are denied external egress in Foundation 0.1. Only a registered `PUBLIC` packet may use an explicitly enabled research adapter, with the provider, purpose, source hashes, policy decision, time, and result reference recorded.

## Local-first and egress defaults

The default runtime and test path is local-only and opens no network socket. Browser and CLI research adapters are separate, explicit paths; their absence is the default. No workflow, repository secret, external-provider launcher, or GitHub App configuration is created by this policy.

## Dependency admission

Every dependency, fixture, skill, plugin, model, dataset, and public source requires a governed record with pinned version or digest, origin, license or rights basis, named owner, permissions and egress review, evaluation state, and rollback target before use. Unsafe code in admitted upstream crates is supply-chain risk and does not waive Heleos's prohibition on authored `unsafe` Rust.

## Clean-room boundary

The clean-room boundary is binding. Do not inspect, import, compare with, name, or reuse any predecessor repository, artifact, schema, history, prompt, test, or convention. New material must be independently admitted with provenance and acceptable rights.
