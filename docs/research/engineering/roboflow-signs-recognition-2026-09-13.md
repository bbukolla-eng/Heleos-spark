# Signs recongnition: dataset candidate

Observed 2026-09-14 01:49:39 UTC. Status: research candidate; no training or product admission.

This candidate could help locate drawing references before OCR and sheet matching.
Its [project listing](https://universe.roboflow.com/plan-training/signs-recongnition)
reports 27 images and 15 instance-segmentation classes, including detail, section,
grid, level, elevation, door and wall labels. The publisher declares CC BY 4.0;
original drawing provenance and permission authority have not been verified.

The [inspected plan](https://universe.roboflow.com/plan-training/signs-recongnition/images/OiuHDF0sSC8F741qu04r?queryText=&pageSize=50&startingIndex=0&browseQuery=true),
11-pdf_page_1.png, is 2448 by 1584 pixels. Its current gallery annotation group has
13 polygons: eight grid markers, one detail callout, three SectionL markers and
one SectionU marker. Door and wall features are visible, but Door and Wall1 are
unused classes in this sample. Their presence in the project class list therefore
does not establish complete door or wall annotation coverage.

[Version 3](https://universe.roboflow.com/plan-training/signs-recongnition/dataset/3),
linked by the overview's displayed model, reports 107 images: 100 training,
five validation and two test. It produces five outputs per training example,
applies cutout augmentation, and stretches images to 640 by 640 pixels.
The 107 total includes augmentation; it is not evidence of 107 independent
drawings. Two test images cannot establish broad transfer performance.
This inspected version is one of four reported versions.

The proposed Heleos use is marker localization feeding source text extraction
and verified drawing-reference links. Actual reference text, target-sheet identity
and matching require separate evidence. The inspected annotations do not establish
duct or pipe quantities.

Next action: verify original drawing provenance, class definitions, annotation
completeness and document-group split independence, then select an exact version
for a bounded experiment. The current gallery sample has not been bound to a
downloaded version archive. No model was run or downloaded.

Machine-readable observations:
[Candidate metadata](dataset-candidates/roboflow-signs-recongnition.json).
