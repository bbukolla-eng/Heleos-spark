# Air-device takeoff in the local drawing workspace

Open Mechanical takeoff and select a plan or installation detail. Set the page's
role in Document review, include air devices in Project setup, and enter the
reviewer's name. The local workspace must be launched with a pinned installed
vision model to enable **Read air devices**. Reading and calculation need no
sheet scale; lengths and slot counts remain device attributes.

Completed readings automatically become source-linked draft counts. The screen
shows known subtotals separately from the final quantity. A blank final quantity
means review is incomplete; it does not mean zero. Plan occurrences with the
same tag stay separate. Legends and schedules provide context, not additional
installed devices.

Review the exceptions and open their source regions. Correct attributes or
classifications, exclude false positives, and resolve source warnings explicitly.
Proposed duplicate views, scoped multiplicities, relocation operations and
schedule applicability need source-backed review. Relationship forms also let a
reviewer establish an explicit connection the reader did not propose. Corrections
retain the original reading and append a calculation generation; stale edit pins
are rejected.

A later reading is staged for review. Add a distinct page, replace the same page,
or discard the staged reading. Replaying the same saved reading cannot add the
same devices again. If a source changes, affected results become incomplete while
previous results remain in history. Coverage review binds the current selected
sources and requirements; an empty detector result alone never establishes zero.

The draft export ZIP contains air-device CSV/JSON, calculation history, source
page PNGs, original response bytes and geometry receipts. Exact source revision,
page, observation and evidence references travel with the result. Saved readings
prevent silently removing their scope from the project. These draft outputs do
not constitute estimator-ready Excel or full Division 23 acceptance.

The software contract is checked with original synthetic inputs and independent
review. Recognition accuracy, representative project completeness, native Windows
execution and final production acceptance remain separate recorded obligations.
Malformed, incomplete or truncated local-model output stays a failed reading and
cannot supply quantities. See CURRENT_STATUS.md for the current delivery state;
see `.heleos/air-device-count-2026-09-16/` for exact checks and live-model evidence.

The local reader uses a compact observation protocol with inline text regions.
Code assigns evidence IDs and converts its declared 0–1000 image grid into the
normalized source coordinate system. Omitted attributes remain unknown. Relevant
notes require review; the reader does not infer physical relationships or coverage.
Older raw response formats still parse, while saved readings retain their original
model/prompt/parser identities and can become stale after an implementation change.
