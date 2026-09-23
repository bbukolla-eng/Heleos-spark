# Verified GitHub automation research

Task: GITHUB-AUTOMATION-REPAIR-2026-09-22. NotebookLM disposition: new verified research.

Reused inventory N07 (Software Engineering), notebook `f0404db5-8c1e-4591-9c9d-2727bd668cbe`. Two public GitHub references were added after recording the submission and confirming two available source slots. Query `3d05c6560b14` completed; raw result is retained beside this record. Codex separately read both supporting source bodies with source_get_content. No repository code, private drawings or secrets were uploaded.

## Verified findings and intended bindings

| ID | Verified finding | Source and passage locator | Required behavior / meaningful verification |
| --- | --- | --- | --- |
| GH01 | Default workflow credentials should be read-only; increase permissions per job only as needed. | Source `8765e3a9-3e51-407f-8c38-5357aecb396e`, Secure use reference, Writing workflows / Principle of least privilege. Short passage: "read access only". | Inspect default workflow permission after setting; explicitly bounded job permissions. |
| GH02 | Privileged workflow_run / pull_request_target execution must not run untrusted PR code; artifacts are untrusted inputs. | Same source, Mitigating risks of untrusted code checkout / Good practices. Short passage: "must not explicitly check out untrusted code". | Trusted default-branch controller; separate credential-free candidate tests; reject malicious paths and stale artifact identities. |
| GH03 | Full commit SHA action pins bind the action version, and the pin must originate from the action's own repository. | Same source, Using third-party actions / Pin actions. Short passage: "full-length commit SHA". | Check every external action pin and its upstream origin; reject floating tags. |
| GH04 | A GITHUB_TOKEN push does not start a new push-triggered run. PR opened/synchronize/reopened events can create runs awaiting approval; workflow_dispatch and repository_dispatch are exceptions. GitHub recommends an App installation token when automatic subsequent PR runs are required. | Source `fc1c5a82-9a35-4ddd-9f1c-d2f9727b2dc1`, GITHUB_TOKEN / When GITHUB_TOKEN triggers workflow runs. Short passage: "approval-required". | Use the repository-scoped App to publish accepted updates; verify downstream CI actually starts and completes. Do not assert that GITHUB_TOKEN never produces any PR workflow. |

Sources:
- <https://docs.github.com/en/actions/reference/security/secure-use>
- <https://docs.github.com/en/actions/concepts/security/github_token>

## Boundaries, conflicts and unfinished work

NotebookLM explicitly lacked support in these two references for exact required-check publisher mapping, merge-commit selection and auto-merge eligibility. Those claims need the relevant GitHub ruleset/PR documentation and live API evidence; they are not verified by this query.

The source makes broad statements about job compromise. This record adopts the concrete least-privilege and untrusted-execution constraints; it does not claim that every isolated job can access every secret.

No model answer overrides approved merge policy, checkpoint validation or deterministic gate evaluation. Source findings above are verified research; implementation bindings remain planned until code and independent tests exist. The app installation is administrative and reuses GH01-GH02; it does not add mechanical behavior.

Known live defects from separate API/code inspection: the ai-reviewers required-check publisher differs from its observed publisher; an acknowledgement checker accepts non-approval reviews; auto-merge requests squash; fork approval and workflow default permissions are too permissive for the intended public-repository policy. Snapshots preserve the exact earlier state.
