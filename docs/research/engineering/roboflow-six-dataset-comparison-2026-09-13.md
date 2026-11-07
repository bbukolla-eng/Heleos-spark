# Six Roboflow candidates for mechanical drawing work

Observed 2026-09-14 02:37:48 UTC. All six are research candidates; none is admitted for training.
The comparison uses public project/version metadata and one inspected gallery
example per dataset. Gallery examples are not bound to downloaded version archives.

## Relevance and next audit

| Candidate | Project images / listed classes | Proposed use and concrete concern |
| --- | --- | --- |
| [BluePrint hvac](https://universe.roboflow.com/blueprint-hu8pr/hvac-hkgny) | 754 / 8 | Closely matches airside plans: duct and bend boxes over architecture. Numeric labels and vd need definitions; overlaps the next dataset. |
| [hvac project](https://universe.roboflow.com/hvac-project/hvac-vcvog) | 403 / 5 | Alternative annotations for overlapping HVAC drawings. The checked example has clearer named classes, but project labels still include unexplained values. More boxes do not establish greater accuracy. |
| [Tiling_method](https://universe.roboflow.com/opop-ri0av/tiling_method-w1e2l) | 95 / 6 | Lower priority: box and 2M-8M codes; the sample targets a small note/device region on a wallbox drawing. Code meanings and HVAC relevance are unresolved. |
| [Datset2](https://universe.roboflow.com/symbol-detection-boj78/datset2-bbc8k) | 4,542 / 57 | P&ID valves, equipment and reference markers. Named classes are mixed with 1-32, requiring an upstream mapping. |
| [Split_Images](https://universe.roboflow.com/pnid-2u2eu/split_images-uib2u) | 438 / 192 | Broad named P&ID component catalog; promising for targeted valve/instrument classes after checking support per class. |
| [symbol_detection_v2](https://universe.roboflow.com/institute-of-management-sciences-eacvm/symbol_detection_v2-dtl7r) | 8,901 / 1 | Generic symbol localization. The only class is 0; the inspected crop assigns it to 74 boxes across different symbol shapes. |

Counts describe project galleries, not independent source drawings or a single
frozen export. No model scores have been independently verified.

## Verified overlap in the HVAC pair

The [BluePrint sample](https://universe.roboflow.com/blueprint-hu8pr/hvac-hkgny/images/DRtgxRliKWpQDfgn04b7)
and [hvac project sample](https://universe.roboflow.com/hvac-project/hvac-vcvog/images/P9DxOIJGxQBjYoaZlXVz)
show the same drawing content and filename at 3869 by 2764 pixels. Their public
Source Data panels report the same image hash,
a723c5df9f5dda3151167aaabd3ee802. This is a reported image identifier, not a
SHA-256 we computed from a downloaded archive.

BluePrint has 300 boxes: 101 duct, 31 bend, 16 txt and 152 class 3.
The other copy has 364: 178 duct, 34 bend and 152 txt.
The coordinate sets for BluePrint's 152 class-3 boxes and the other copy's
152 txt boxes are not exactly equal. A class-3-to-txt conversion is therefore
not established by this check.

Treat these as overlapping source material with candidate annotation differences.
Recover original drawings, reconcile the labels, and keep all versions/crops of a
source project together when constructing train, validation and test splits.
One shared example does not establish the total overlap rate.

## Inspected exports

| Version | Export images | Train / validation / test | Processing |
| --- | ---: | --- | --- |
| [BluePrint v1](https://universe.roboflow.com/blueprint-hu8pr/hvac-hkgny/dataset/1) | 350 | 350 / 0 / 0 | No preprocessing or augmentation listed. |
| [hvac project v3](https://universe.roboflow.com/hvac-project/hvac-vcvog/dataset/3) | 403 | 403 / 0 / 0 | No preprocessing or augmentation listed. |
| [Tiling_method v1](https://universe.roboflow.com/opop-ri0av/tiling_method-w1e2l/dataset/1) | 237 | 213 / 24 / 0 | Three training outputs per example; brightness variation. |
| [Datset2 v1](https://universe.roboflow.com/symbol-detection-boj78/datset2-bbc8k/dataset/1) | 5,914 | 3,207 / 1,898 / 809 | Fit/pad to 2048, grayscale, three training outputs, blur/noise. |
| [Split_Images v10](https://universe.roboflow.com/pnid-2u2eu/split_images-uib2u/dataset/10) | 438 | 438 / 0 / 0 | No preprocessing or augmentation listed. |
| [symbol_detection_v2 v7](https://universe.roboflow.com/institute-of-management-sciences-eacvm/symbol_detection_v2-dtl7r/dataset/7) | 8,901 | 8,757 / 144 / 0 | Auto-orient; stretch to 1024. No augmentation listed. |

The Datset2 header linked to /dataset/7, but that navigation rendered selected
version v1 and the same v1 statistics. Record the displayed version and verify an
exact export before use; do not infer version identity from a header badge.

## First experiment and admission work

Prioritize the overlapping HVAC pair for an annotation audit because its drawing
style and duct/bend targets most closely match building airside work. Keep the
P&ID collections available for their named valve, instrument and reference tasks.
The single-class set may support a generic localization stage after its class
definition is established.

All six are object-detection projects, and the inspected annotations are boxes.
They may seed region recognition and annotation proposals. Tracing still requires
evidence for paths, endpoints, crossings and connections, with scale and
deterministic quantity rules supplied by the intended takeoff task. A crop
boundary must not silently become a physical endpoint. Missing labels are unknown
unless target absence has been verified.

Next action: recover the original HVAC sources and codebook, adjudicate the
overlapping annotations, and select a project-separated evaluation set before
admitting an exact export. Establish licensing/provenance and per-class coverage
for each additional source as its task becomes concrete. Five projects declare
CC BY 4.0; Split_Images declares Public Domain via a CC0 link. These publisher
declarations have not established underlying drawing rights.

Six machine-readable records are in dataset-candidates, named
roboflow-hvac-hkgny.json, roboflow-hvac-vcvog.json,
roboflow-tiling_method-w1e2l.json, roboflow-datset2-bbc8k.json,
roboflow-split_images-uib2u.json and roboflow-symbol_detection_v2-dtl7r.json.
No archive/model download, training, inference or product change occurred.
