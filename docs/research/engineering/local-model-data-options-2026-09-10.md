# Local models and data sources — 2026-09-10

Status: public-source verification and candidate discovery only. No model or dataset
is admitted, downloaded in bulk, trained, embedded in Heleos, or proven accurate
by this report. No private drawing was sent to a provider. The owner clarified
that “Juniper” meant Jupyter notebooks.

## Owner direction and verified roles

The owner wants pretrained downloadable models integrated into Heleos, with the
installed model files and project information retained locally. Existing datasets
should reduce the need for the owner to prepare training examples. This agrees
with sections 3 and 8 of the approved Foundation design; it does not establish
that every model or dataset on a hosting platform is suitable for the product.

| Resource | Verified role | Implication for Heleos |
| --- | --- | --- |
| [Hugging Face Hub downloads](https://huggingface.co/docs/huggingface_hub/guides/download) | Download model or dataset files, including specific repository revisions, to local storage. | Select and preserve a tested model version and its required files. |
| [Hugging Face offline settings](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables) | Hub offline mode uses existing cached files and disables Hub HTTP requests; telemetry has a separate control. | Verify the complete selected runtime with network access denied before claiming offline operation. One library setting alone does not prove application-wide isolation. |
| [Kaggle's official kagglehub library](https://github.com/Kaggle/kagglehub) | Download versioned datasets locally; load tabular data and SQLite database files; retrieve notebook outputs. | Kaggle is an additional source of candidate data. Hosted Kaggle execution is optional and separate from local use. |
| [Project Jupyter](https://jupyter.org/) | Interactive notebooks for code, data inspection, scientific computing, and machine learning. | Use local notebooks to inspect examples and run experiments with appropriate libraries. Jupyter is the workbench; a notebook still needs a dataset and training software. |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Local language/vision-language inference with Apple Silicon acceleration and other hardware backends. | A runtime candidate for compatible models; no particular model/runtime combination has been tested here. |

Downloaded models remain subject to their original
[licenses](https://huggingface.co/docs/hub/repositories-licenses). Control over a
local installation is distinct from exclusive ownership of the original model.
The selected license must support the intended business use and any planned
modification or redistribution.

## Initial dataset findings

These are discovery findings, not permission to train or production selections.

| Candidate | Observed contents and terms | Assessment |
| --- | --- | --- |
| [PubTables-1M](https://huggingface.co/datasets/bsmock/pubtables-1m), [original Microsoft project](https://github.com/microsoft/table-transformer) | Annotated tables and associated pretrained table-extraction models; dataset card declares CDLA-Permissive-2.0. | Candidate for table structure work. Benefit on equipment schedules still requires measurement; general table data does not supply complete mechanical takeoff answers. |
| [DocLayNet v1.2](https://huggingface.co/datasets/docling-project/DocLayNet-v1.2) | Human-annotated document layouts, including text, titles, and tables; card declares CDLA-Permissive-1.0. | Candidate for locating document regions. Mechanical symbols, connectivity, and quantity logic remain separate tasks. |
| [FloorPlanCAD original project](https://floorplancad.github.io/) | Annotated architectural CAD drawings; original annotations are CC BY-NC 4.0, and authors state they do not own drawing copyrights. | Do not select for the commercial product on current evidence. The [Hugging Face mirror](https://huggingface.co/datasets/Voxel51/FloorPlanCAD) has inconsistent license metadata versus its text and upstream terms. |
| [DrawingVQA](https://huggingface.co/datasets/S2-MIND/DrawingVQA) | Construction-drawing questions; original drawing images are not fully public, and the public question set is CC BY-NC-SA 4.0. | Research reference only on current evidence; not an available complete commercial training set. |

No complete, suitably licensed HVAC/Division 23 drawing-to-takeoff training corpus
was verified in this bounded search. This is a search limitation, not a claim that
none exists. Unrelated HVAC telemetry, simplified engineering question/answer
data, and general floor plans must not be represented as mechanical takeoff labels.

## Next concrete evaluation

The owner explicitly requested coverage of the full mechanical scope. Discovery
includes ductwork and fittings; supply, return, and exhaust air devices; heating
and cooling equipment; schedules; hydronic and other mechanical piping; controls;
insulation; and specifications. Piping diagrams are one source category only.

First evaluate existing pretrained document readers on an approved, local equipment
schedule task. Measure field extraction and source-location errors before deciding
whether additional training is needed. If training is justified, select data for
the observed failure, freeze separate evaluation examples, and retain a local
model version that can be restored. Final quantities continue to come from tested
calculation code with source evidence. No implementation or training was started
by this verification request.

## Broader mechanical head-start findings

The follow-up search verified useful building blocks beyond piping. Candidate
usefulness below is an assessment of the published scope, not measured performance
in Heleos. A reference dictionary supplies names and relationships; it is different
from a collection of labelled drawing images used to train a detector.

| Area | Existing resource | Useful starting point and remaining gap |
| --- | --- | --- |
| Equipment schedules | [Microsoft Table Transformer](https://huggingface.co/microsoft/table-transformer-structure-recognition), alongside PubTables-1M | A downloadable model already trained to detect table rows and columns. Card declares MIT. Test the existing model before repeating training. Text reading, equipment field meanings, and plan-tag matching still need integration and testing. |
| Notes, specifications, and document layout | DocLayNet and the pretrained document-reader candidates in the approved design | Existing layout examples can help locate text and tables. They do not supply the project's contractual requirements, insulation decisions, or approved mechanical calculation rules. |
| HVAC equipment and system relationships | [Brick](https://brickschema.org/), [original repository](https://github.com/BrickSchema/Brick) | An extensible building-system dictionary and relationship model, with a BSD-3-Clause repository license. Candidate vocabulary/reference source for equipment and connected systems, rather than a trained drawing detector. Keep any mapping subordinate to Heleos's approved domain contract. |
| Controls, sensors, equipment, and HVAC processes | [Project Haystack](https://project-haystack.org/), [definition index](https://project-haystack.org/doc/index) | Definitions and relationships for building-system data; publisher states AFL 3.0. Candidate structured reference information. It does not teach symbol appearance or measure ductwork. |
| Labelled HVAC equipment images | [BIM-Speed HVAC dataset](https://depositonce.tu-berlin.de/items/94a20319-eec9-41a4-b4b8-aa83134eb854/full) | Publisher lists 107 boiler images, 123 radiator images, bounding-box labels, and 33 additional validation images. Metadata declares CC BY 4.0. The image style and suitability for recognizing symbols on plans have not been verified; README download failed and no archive was loaded. |
| HVAC and plumbing construction symbols | [Jamieson et al. construction-diagram study](https://link.springer.com/article/10.1007/s10032-024-00492-9) | Research uses 198 drawings, including 103 HVAC drawings. Its Data availability section explicitly says drawings are not public because of industrial-partner confidentiality. Methodological lead only; no accessible training corpus or pretrained specialist was verified from this study. |
| Piping and instrumentation symbols, lines, and text | [Digitize-PID / Dataset-P&ID](https://arxiv.org/abs/2109.03794), [author-published Kaggle conversion](https://www.kaggle.com/datasets/hristohristov21/pid-symbols) | Paper describes 500 annotated synthetic P&IDs. These are industrial schematics; usefulness on building mechanical plans and original data rights remain to be established. This source does not cover the full mechanical scope. |

For duct-network geometry, air-device symbol coverage, insulation, supports, and
specification-to-quantity decisions, this search has not yet verified a suitably
licensed complete labelled corpus. Existing pretrained readers, published symbol
methods, and reusable equipment dictionaries can still reduce development work.
The owner's real drawings can provide local evaluation cases, with targeted
annotations where required; they are not a prerequisite completed takeoff for
each future production job.

Follow-up lookup record: PUBLIC web searches and publisher-page inspection on
2026-09-10, requested by the owner to find a head start across mechanical systems.
Only generic topic queries and the linked public URLs were submitted. No private
files, dataset training jobs, model executions, or upstream contacts were involved.
All lookups are terminal. This update changes only this research report and does
not admit a source, model, dependency, or new product capability.

## Owner-supplied Division 23 workflow reference

The subsequent owner-supplied Sintra Division 23 Mechanical Takeoff Master Brain
is a useful functional specification for the product. Its 99 numbered sections
describe the estimator's workflow, records, decisions and outputs across equipment,
ductwork, piping, fittings, insulation, controls, demolition, specifications and
revisions. It provides a head start on domain requirements rather than labelled
drawing images or a pretrained model. Reading it does not by itself teach a model
to recognize every symbol, establish scale or calculate accurate quantities.

Source identity: original DOCX, 61,422 bytes; SHA-256
c202f447e17286dd33ccd93961c3e021ed81336cfbe69162b5fbe80b29fcd7a6.
A byte-identical copy is preserved under the ignored local reference-inputs
directory; the original and its private contents are not added to Git.

| Document content | Application in Heleos | Current extent |
| --- | --- | --- |
| Equipment-first baseline and register (14, 57, 78) | Record tags, locations, schedule references, quantities, supporting sources and review status. | Tag/source records, plan/schedule matching, corrections and draft CSV are implemented. Capacity, connection, accessory and other complete schedule fields remain to build. |
| No double counting (29) | Reconcile repeated plan, detail, riser and schedule appearances before deciding a physical quantity. | Exact-tag duplicate/unmatched flags are implemented. Physical-object and network reconciliation remain to build. |
| Evidence classes and unknown values (6, 7, 88) | Keep what was found, what was derived, assumptions and unresolved questions distinguishable; unknown is not zero. | Original extraction, review history and UNKNOWN counts are implemented. The complete evidence/confidence classification remains to build. |
| Scale and measurement (12, 13, 18, 72, 73) | Establish scale for the relevant view, retain original measurements, and keep adjustments separate. | The workflow now saves operator-confirmed uniform-scale view calibration, length geometry and linked draft quantities. Automatic scale verification, distorted scans, system tracing and area/weight calculations remain unfinished. |
| Full mechanical scope and requirements (15-28, 33-42) | Connect ductwork, piping, fittings, insulation, controls, specifications, demolition and revisions to the takeoff. | The workflow now saves classified source pages and item records across the full mechanical scope, including review, explicit allowances, unresolved issues and export. Automatic document interpretation, full mechanical recognition and estimating remain to build. A checklist entry alone does not authorize adding an item to a job. |
| Failure scenarios and truthful completion (43-51, 97) | Check duplicates, incorrect scale, missing items, revisions, allowances and unfinished work through specific product behavior. | Duplicate, missing-tag, correction and empty-reader behavior are covered in this slice. The rest accompany the corresponding feature implementation. |

Practical use has three parts: make the document's fields into saved records,
make its calculation and reconciliation rules into explicit code, and make its
relevant instructions available to the selected local reader when that capability
is implemented. This does not require another large model just for the document.
Future drawings supply the job-specific evidence; the owner should not need to
complete every new takeoff before Heleos can start. Checked examples during
development establish whether the resulting software is doing its job.

The owner has authorized product implementation since the research-only handoff
below. The [equipment workflow ledger](../../superpowers/plans/2026-09-10-equipment-workflow.md)
records that separate work and its limitations. No document bytes were submitted
to an external provider, no training occurred, and the document's agent-role text
was treated as reference content rather than runtime instructions. Project
requirements and applicable rules still need source-specific verification; the
manual is not a declaration of legal or code compliance.

## Earlier research-only handoff record

- Repository: visible main, base `15d3a0269904ffa0f7da905debc59a6a61c47fef`.
- Initial dirty path: untracked `.DS_Store`, preserved. Active-build discovery
  returned `PASS`, with no active builds declared.
- Instruction SHA-256: AGENTS.md
  `85a5890e6cc86c0455f53457c2b392645bd145b0c43e06c5ee01c8df4e932af8`;
  approved design
  `2b377b8e78ab1572c148766716809869e161cce3b164622b1e182b6a17c01464`;
  roadmap
  `e4ce51d99e0bf64734e32064bbfe41ff503b5cabeafe37318cb82cb665f93ded`.
- Providers/purpose: web search and primary publisher pages for verification;
  Hugging Face connector for public repository metadata. Data class: PUBLIC.
  The owner's requests to verify these services authorized public-source lookup.
  Submissions consisted of generic service/dataset search terms and public URLs
  or repository IDs; no project source bytes, drawing bytes, secrets, or internal
  file paths were submitted.
- Result references: the primary URLs above and the current task's tool results.
  Raw dataset/model artifacts were not downloaded, so no source-byte verification
  or artifact digest is claimed. Publisher pages and unpinned metadata can change.
- Hugging Face metadata lookup succeeded for the three named dataset repositories
  PubTables-1M, DocLayNet v1.2, and DrawingVQA. Its dataset search capability was
  disabled by server configuration and model search was unavailable; public web
  discovery supplied the fallback. No failed search was treated as an empty Hub.
- Verification completed on 2026-09-10; observed clock checkpoint
  `2026-09-10T14:18:26Z`. All research calls are terminal. No installation, training,
  remote compute job, upload, push, or release action was performed.
- Changed file: this report only. Next action: scope and perform the local
  equipment-schedule reader evaluation described above.
