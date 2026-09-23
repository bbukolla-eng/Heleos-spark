# Owner action required

This visible directory holds ignored, local GitHub App decision drafts in the
unique visible-main checkout. Its tracked README keeps the directory discoverable;
direct `.json` children are ignored by `/OWNER_ACTION_REQUIRED/*.json`.

Follow the [decision guide](../docs/operations/github-app-decisions.md#prepare-an-unresolved-draft)
to generate a new draft bound to the current main HEAD and committed registry.
The draft has exactly 29 unresolved `null` fields for Bekim Bukolla to complete
with actual decisions and supporting evidence. It is deliberately invalid for
application until completed; the named recipient is not an authenticated signature.

Preparation never overwrites an existing path. If main or the registry changes,
inspect the new state and prepare a fresh filename; do not update hashes merely
to bypass staleness. Keep secrets and credentials out of drafts and evidence.
An ignored draft is not a decision, evidence validation, an account or workflow
change, a Git change, or release authority.
