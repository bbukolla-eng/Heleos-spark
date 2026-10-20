# Research source candidate preparation

`governance/agents/validate-sources.py` checks declared research metadata offline.
It implements the source-entry preparation slice of the
[engineering-research plan](../superpowers/plans/2026-08-28-heleos-engineering-research.md#task-1-freeze-research-contracts-validation-readiness-and-ecc-inventory).
This is not completion of that plan's Task 1, source admission, a Division 23
taxonomy, or Foundation 0.2. The [0.2 entry gate](../roadmap.md#02--source-registry-division-23-taxonomy-and-evaluation-baseline)
still requires the preceding acceptance and owner decisions.

Run with Python 3.11 or newer and explicit candidate TOML paths:

```sh
python3 -B governance/agents/validate-sources.py /path/to/candidates.toml
python3 -B -m unittest discover -s tests/research -p 'test_source_candidates.py'
```

Only explicitly named manifest files are read. There are no network calls,
source-cache reads, database operations, file writes, or promotion commands.
Exit 0 means `CANDIDATE_METADATA_VALID`; exit 1 returns a fixed rejection code.
Successful JSON reports contain sorted SHA-256 hashes of the exact input
manifest bytes, the source count, and all four of these flags set to `false`:
`production_authority`, `source_bytes_verified`, `citations_verified`, and
`rights_verified`. Errors do not echo manifest contents or local paths.

Each manifest has exactly `schema_version = 1`, `status = "research_only"`, and
one or more `[[source]]` tables. Each source implements `SourceEntryV1` with the
following required fields; unknown fields fail closed:

| Fields | Candidate syntax |
| --- | --- |
| `id` | Unique across all supplied manifests; lowercase ASCII letters, digits, `.`, `_`, or `-`, beginning with a letter or digit; at most 128 characters. |
| `canonical_url` | HTTP(S) DNS source URL with lowercase host; no credentials, port, query, fragment, percent-encoding, whitespace, or dot-segment traversal. Literal IPs, local/internal suffixes, and the enumerated common search-engine hosts are rejected. Put section anchors in `locators`. |
| `publisher`, `title`, `edition_or_version` | Nonempty declared source identity. |
| `effective_date`, `retrieved_at` | Quoted calendar date `YYYY-MM-DD` and quoted UTC timestamp `YYYY-MM-DDTHH:MM:SSZ`. A source without a known effective date cannot yet satisfy this preparation contract; record that gap outside a valid manifest rather than inventing a date. |
| `data_class` | Exactly `PUBLIC`; this is a declaration, not automatic classification. |
| `license_or_rights` | Nonempty declared rights basis. |
| `locators`, `claims_supported` | Nonempty lists of nonempty precise locator and claim strings. Precision and support require independent inspection. |
| `applicability` | Nonempty declared scope, including jurisdiction and edition limits where relevant. |
| `supersession_state` | `current`, `unknown`, `superseded`, or `retired`. Unresolved and retired entries may remain research records; validation grants none of them product applicability. |
| `cache_status` | `not_cached` or `cached_verified`. The latter requires exactly one `cache_sha256` string of 64 lowercase hexadecimal characters; the former forbids that field. This checks the declaration, not cached bytes. |
| `notebooklm_permission`, `notebooklm_rights_basis` | `permitted`, `reference_only`, or `denied`, plus a nonempty declared basis. Even `permitted` grants no upload or egress permission through this tool. An affirmative, applicable rights basis still needs independent verification. |
| `contradictions`, `gaps` | Lists of nonempty strings; empty lists are allowed. |

Strings are bounded to 4,096 characters with no surrounding whitespace or
control characters. Lists and manifest source counts are bounded to 1,000;
each manifest is bounded to 1 MiB and must be a regular file. Duplicate TOML
keys, ambiguous types, unsupported states, and familiar host/private-path
patterns are rejected. URL and text checks are conservative syntax checks;
they are not a general secret scanner, URL resolver, rights adjudicator, or
proof that arbitrary free text is public. No candidate should be published or
sent to a provider on the basis of this validator's result.

The synthetic tests contain no real standard text or source admission. No
quarantined Athena artifacts were inspected, ingested, or used to author this
contract. `governance/sources.toml` remains outside this tool's write authority.
Aggregate admission, cache-content verification, source/rights adjudication,
the research packet/jq self-test, taxonomy and supersession replay, and
candidate-rule promotion remain separate unfinished work under their gates.
