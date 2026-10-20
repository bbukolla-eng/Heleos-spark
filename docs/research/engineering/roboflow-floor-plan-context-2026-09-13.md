# Floor Plans: architectural context candidate

Observed 2026-09-14 02:18:26 UTC. Status: research candidate; no training or product admission.

The owner's proposed use is plausible: recognizing architectural features could
help a later mechanical model avoid following wall lines while tracing pipes and
ducts. This is an experiment hypothesis; no improvement has been measured.

The [Reviuer project](https://universe.roboflow.com/reviuer/floor-plans-zeb7z)
reports 3,716 images and two instance-segmentation labels, wall and room.
There are no dedicated door, pipe or duct classes. No license declaration was
visible on the checked public overview, gallery header or version page, and no
project description was supplied. License and original drawing provenance
remain unresolved.

The [inspected sample](https://universe.roboflow.com/reviuer/floor-plans-zeb7z/images/6T75E5YjQ7u99SyAj7Gn?queryText=&pageSize=50&startingIndex=0&browseQuery=true)
shows an architectural plan with 61 polygons: 41 wall segments and 20 room areas.
The UI name is 174.png; the rendered annotation JSON uses key 174.jpg and
dimensions 1922 by 1070 pixels. Both identities are retained in the candidate
record. This is one current-gallery sample, not a complete annotation audit or
a sample bound to a frozen downloaded version.

[Version 8](https://universe.roboflow.com/reviuer/floor-plans-zeb7z/dataset/8)
reports 6,318 images, split 5,204/744/370 for training/validation/test.
Training augmentation produces two outputs per example using flips and rotation.
Preprocessing fits images within 1024 by 1024 pixels with white padding and applies
grayscale and contrast stretching. The augmented total is not an independent
drawing count.

A suitable first experiment would train architectural recognition with these
wall/room labels, then provide its predictions as uncertain context for a
mechanical model trained on explicitly labeled MEP drawings. Preserve the
original pixels and allow overlapping predictions: real pipe or duct traces may
cross or overlap walls, and room masks cover areas where services can appear.
Do not turn missing pipe/duct annotations into confirmed negative examples.
Negative examples require verified absence of the target class.

Evaluate the same held-out mechanical projects with and without architectural
context. Keep related sheets, crops, augmentations and revisions in one split.
Compare false traces along architecture, missed mechanical segments, connectivity
at crossings and centerline accuracy. Set class-specific acceptance criteria from
the intended takeoff task before interpreting results.

Next action: establish the license and original source, then audit label coverage
and source-group separation before admitting an exact version for this experiment.
No dataset archive or model was downloaded, and no training or inference ran.

[Machine-readable candidate](dataset-candidates/roboflow-floor-plans-zeb7z.json).
