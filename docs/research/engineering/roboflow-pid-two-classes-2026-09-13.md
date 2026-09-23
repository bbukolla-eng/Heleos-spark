# Roboflow candidate: P&ID 2 Classes

Assessment date: 2026-09-13, America/New_York. Status: research only; no training,
model, dataset, or quantity admission. Candidate metadata is in
[dataset-candidates/roboflow-p-id-2-classes-er8vf.json](dataset-candidates/roboflow-p-id-2-classes-er8vf.json).

This is a possible generic symbol/localization research source. Its current
annotation semantics need resolution before assigning a specific task to it.

## Verified public metadata

The [project overview](https://universe.roboflow.com/pid-dataset/p-id-2-classes-er8vf)
lists 4,250 images, two object-detection labels (`page connection`, `tag`), three
versions, no dataset-specific model, and a CC BY 4.0 license. The publisher display
name is PID dataset; the overview supplies no dataset description or original
drawing provenance. The hosted demonstration uses a general detection model and
does not establish a trained model or measured accuracy for this dataset.

The live [v3 page](https://universe.roboflow.com/pid-dataset/p-id-2-classes-er8vf/dataset/3)
shows generation on January 29, 2026, 3,384 training / 638 validation / 228 test
images, grayscale preprocessing, and no version-level augmentation. These counts
sum to 4,250. COCO JSON and YOLO exports are advertised. The older
[v1 page](https://universe.roboflow.com/pid-dataset/p-id-2-classes-er8vf/dataset/1)
has a different split and preprocessing; any future acquisition must name the
exact version. A project image count does not establish unique original drawings.

## Concrete sample finding

The current gallery's [13.jpg sample](https://universe.roboflow.com/pid-dataset/p-id-2-classes-er8vf/images/BUMQkNcWRI2LlKK2IOaq)
is a 7,168 by 4,561 pixel schematic. Its visible annotation panel reports 112
objects, all labeled `tag`, with no `page connection` in that image.

The public Raw Data panel retains two annotation groups. Comparing their original
YOLO rows found 112 boxes in each and identical coordinate sequences. The
`symbols` group uses only class 0, converted to `tag`; the other group's original
rows contain every class ID from 0 through 31. This supports a sample-specific
finding that detailed distinctions have been collapsed. The numeric class-name
mapping was not established. The alternate group's converted data also differs
from its original rows, so it is not an authoritative repair source.

These observations concern one current gallery record, not a downloaded frozen
v3 export. They do not prove dataset-wide error rates, class frequencies, or
whether v3 contains the same annotations. No transcription or physical geometry
ground truth was established.

## Fit with Heleos

My assessment is to retain this as a candidate for generic region proposals,
pending annotation and provenance inspection. The label name alone does not
establish an OCR tag target. Page-reference localization is a possible later use;
it still needs positive examples and an explicit definition of the annotation.

The existing `mechanical_model_baseline.py` and `mechanical_model_scoring.py`
support source-pinned bounding-box experiments across mechanical categories.
Neither source class has a justified automatic mapping into those categories.
Mapping `tag` to equipment or `page connection` to piping would invent semantics.
Source-specific review and an appropriate localization task contract must precede
such an experiment. No baseline, taxonomy, or production code changed here.

For physical takeoff, this assessment establishes no duct or pipe centerlines,
scale, dimensions, connectivity, equipment types, or accepted quantities. Testing
on independently adjudicated target drawings remains necessary to determine
transfer from schematics to the building plans used by Heleos.

## Rights and next action

[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) permits commercial reuse
subject to its terms, including attribution and change notices, but does not
warrant every underlying permission. The displayed license is not independent
proof that the uploader can license all drawings. Separately,
[Roboflow's terms](https://roboflow.com/terms) contain Public-plan commercial-use
restrictions. The owner's plan and applicable acquisition rights were not
established; this note makes no commercial-use clearance decision.

Next action: identify the original class mapping and drawing source, then inspect
an exact authorized version export in local quarantine. Audit class semantics,
source-level duplicates and split separation before choosing a narrowly defined
experiment. The public gallery inspection is sufficient for this initial
assessment, but it does not replace an archive audit.

## Execution record

Public-only website and search reads were performed through web and browser tools.
Only public URLs and the dataset slug were submitted. No private drawings, repo
content, credentials, hosted inference input, or messages were sent. No dataset
archive, model, or package was downloaded or executed. No account, remote dataset,
commit, branch, index, workflow, or release state changed. Existing local work is
preserved.

The web reader could read the overview and v1 but failed on the supplied browse,
v3, and image-detail URLs; the public browser UI supplied those observations. The
old Roboflow dataset-license documentation URL returned a missing-page notice;
the current license deed and terms were read directly. Browser content export was
unsupported. The evidence JSON therefore contains investigator-derived metadata,
not archived page or dataset bytes. All research calls are terminal.
