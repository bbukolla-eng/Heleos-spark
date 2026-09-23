# Audit local mechanical dataset annotations

`scripts/mechanical_dataset_audit.py` inspects explicitly named local COCO exports
before adaptation to Heleos's saved-output baseline. It reports source duplicates,
split conflicts, unknown ancestry, malformed records and current size-limit issues.
It preserves the files and original labels. It does not download/extract archives,
train a model, import records into the knowledge store or approve dataset use.

## Prepare the specification

Place a JSON specification alongside the local export directories. Name each
export explicitly; paths stay within the specification directory. The split is a
declaration about that export, not inferred from a directory name.

```json
{
  "schema_version": 1,
  "exports": [
    {"id": "train-export", "split": "train", "annotations": "train/_annotations.coco.json", "images": "train"},
    {"id": "test-export", "split": "test", "annotations": "test/_annotations.coco.json", "images": "test"}
  ],
  "source_groups": [
    {"export_id": "train-export", "image_id": 1, "group_id": "original-drawing-project-a"},
    {"export_id": "test-export", "image_id": 2, "group_id": "original-drawing-project-b"}
  ]
}
```

Use known original project/drawing ancestry for `group_id`, including related
sheets, revisions, crops and augmentations. A candidate/dataset name alone does
not establish independent ancestry. Use an empty `source_groups` list when ancestry
is unknown; the report retains that gap instead of inventing assignments.

Image, category and annotation IDs are scoped to their own export. Reusing image
ID 1 in two different exports is valid. Reusing the same image bytes under different
IDs, filenames or group claims is still reported as a duplicate. Identical bytes
are only one form of duplication; transformed crops and related revisions require
additional source review.

## Run the audit

From the repository root, using Python 3.9 or newer:

```sh
python3 scripts/mechanical_dataset_audit.py /path/to/local-export/audit-spec.json
```

The CLI writes JSON to standard output. Exit 0 means the audit ran and emitted its
findings; it does not mean the dataset is ready for training. Exit 2 means the
specification/input could not be safely read or interpreted. A caller can redirect
the report to a new file outside the source image/annotation files.

The Python entrypoint is `audit(spec, base_dir)`. It reads only explicitly named
annotation/image files under the selected base directory. It performs no network
calls or input writes.

## Interpret the result

- Per-export categories retain their original IDs, names and annotation counts.
  Cross-export label differences are visible; no class mapping is assumed.
- Per-image hashes and duplicate groups reveal identical source bytes, including
  duplicates spanning different declared splits or group IDs.
- Source-group conflicts report one supplied original group spanning splits.
  Unknown ancestry remains explicit. Group declarations are not independently
  authenticated by this tool.
- Annotation findings identify malformed/duplicate IDs, missing references and
  invalid bounding-box extents. Segmentation presence/type is recorded; polygon
  geometry, masks and semantic annotation completeness are not validated.
- Images are hashed as raw bytes. The audit checks the dimensions declared in
  COCO, without decoding images or verifying their actual pixel dimensions.
- Baseline size findings compare the complete inventory with the current 200
  sample / 250 object limits. Numeric fit alone does not establish class/schema
  compatibility. An over-limit image retains its full annotation count.
- `training_admitted` remains false and `quantity_authority` remains `none`.

For the supplied Roboflow candidates, use the
[preparation manifest](../research/engineering/roboflow-preparation-manifest-2026-09-13.json)
to track source definitions and the observed HVAC overlap. Acquire an exact export
only after its use basis is established, then audit its actual local bytes. Public
gallery observations are not frozen-archive findings.

Resolve the reported issues before preparing a bounded baseline experiment.
Do not truncate annotations, invent test assignments or force wall/room/generic
symbol labels into mechanical classes to make an import fit. Source rights,
semantic class review, path/topology supervision and target-project accuracy are
separate requirements; this structural audit does not close them.
