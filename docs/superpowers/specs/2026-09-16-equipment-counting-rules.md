# Physical-equipment counting rules and examples

Task EQUIPMENT-RULES-1. **E01–E12 and EC01–EC20 approved by the owner on 2026-09-16.** This packet is the completed preparation deliverable,
not a quantity engine or an acceptance claim. Approved imperial outputs,
D01–D10 and A01–A12 remain unchanged. Equipment does not depend on resolving
air-device recognition. The class decision is settled and does not gate other categories.
See the [approval record](../../../tests/fixtures/equipment-takeoff/2026-09-16-owner-decision.json).

## Verified context and authority

The [NotebookLM findings](../../research/notebooklm/equipment-counting-findings-2026-09-16.json)
record one bounded query and seven checked passages from three public VA guides:
hydronic pumps, indoor AHUs and packaged chillers. They support explicit assembly,
shipping-split, spare-material and supplied-versus-installed relationships.
They do not define drawing takeoff counting policy. The rules below are owner-approved
project policy, with source-backed examples, not quotations from a universal
measurement standard. Actual project documents establish applicable requirements.

The generated answer incorrectly generalized guide text to every project and
misplaced AHU filter racks in a field-supplied table. Neither assertion is adopted.
Source editions, indexed-text offsets, hashes and limitations remain in the
findings record; no complete CSI hierarchy or new live-model accuracy is claimed.

## Approved rules

| ID | Approved deterministic basis |
| --- | --- |
| E01 | Count distinct physical equipment assemblies at the source-supported assembly level. Families may include AHUs, RTUs, fans, terminal units, pumps, boilers, chillers, heat-rejection units and local/split/heat-pump equipment when the project assigns them to mechanical scope. Prefixes and this example list do not establish contractual inclusion. Air outlets retain A01 routing. The same physical object cannot silently contribute in two categories. |
| E02 | One supported physical assembly contributes one each. Tags, schedule rows and reconciliation edges are evidence, not instances. Distinct assemblies sharing a tag remain distinct after identity review; an untagged but established assembly can be counted. Repeated views/tiles/details require source-supported correspondence before deduplication. Unknown identity leaves the affected final count unresolved. |
| E03 | Preserve observed physical count and schedule-declared quantity separately. A schedule-only unit is an outstanding requirement, not a discovered plan instance. Conflicts remain visible and block only the affected final result; do not silently force either value to match. The rules do not permit a blanket schedule precedence default. |
| E04 | Keep the assembly hierarchy explicit. Shipping sections and integral fans, motors, coils, compressors or controls do not create additional top-level equipment purchases when included in the selected package. Retain component requirements/attributes and separately requested component quantities without summing parent and children into a misleading equipment total. Distinct installed indoor/outdoor units remain separate physical assemblies even when linked into one system or purchase package. |
| E05 | Track physical assembly, procurement package and installation obligation separately. Record supplied-by, installed-by and included-in relationships from project evidence. A manufacturer-supplied loose accessory may need separate field installation without another material purchase. A separately supplied accessory remains visible even when factory mounted. Unknown inclusion/responsibility blocks its affected procurement total, not an otherwise established parent count. No default accessory allowance. |
| E06 | Use only explicit positive integer multiplicity with evidence binding the represented items and applicable room/floor/system scope. A representative symbol plus a note stating three total means three, not four. Already represented members cannot be added again. Bare TYP, repeated-floor similarity and equipment capacity do not establish multipliers. |
| E07 | Keep new installation, existing-to-remain, demolition, relocation and separately required uninstalled spares distinct. Count a source-supported installed standby unit as installed equipment; low operating duty does not exclude it. Uninstalled spares and spare materials are separate procurement obligations. No automatic spare factor or default spare equipment. |
| E08 | A verified relocation retains one physical asset with separately supported removal and reinstallation operations; it does not imply a new purchase. A replacement has distinct removed and new assets. Link phases/alternates explicitly and never add mutually exclusive choices or net demolition against new work. Unresolved identity or phase applicability blocks only affected outputs. |
| E09 | Group by supported family/type, tag or instance, work status and requested attributes/system identity. Preserve capacity, electrical, connection and dimensional attributes with original text/units; present compatible dimensions in imperial units. A missing required grouping attribute leaves that final group unresolved while preserving the known physical subtotal. Each counts are dimensionless and do not depend on scale; source geometry identity still matters. |
| E10 | Show known subtotals, unresolved requirements and coverage separately. A detector finding nothing does not establish zero. Final zero requires an explicitly complete current selected scope with no unresolved target instance or requirement. Partial observation never becomes complete project scope. |
| E11 | Preserve observations, source identities, rule version, correspondence, package links and review decisions with append-only history. Replaying a command does not add quantities. Changed evidence or corrections invalidate/recalculate the affected results and retain prior outcomes; unrelated groups remain current. Model-provided totals cannot replace deterministic records. |
| E12 | Full Division 23 includes the equipment's related piping, fittings, controls, insulation, supports, testing and service obligations. A correct equipment count does not complete those categories. Each proceeds against its own evidence and rules; neither a duct issue nor an air-device recognition failure is an equipment prerequisite. No new pricing, labor-unit or fabrication formula is authorized by this packet. |

## Approved literal examples

These are synthetic semantic examples checked against the approved rules, not
original-drawing fixtures or recognition tests. Their machine-readable counterparts
are [EC01–EC20](../../../tests/fixtures/equipment-takeoff/2026-09-16-rule-examples.json).
`Unknown` means an incomplete affected output, not zero. Unless stated otherwise,
each case has current, complete selected source coverage and established scope.

| Case | Evidence condition | Approved expected result |
| --- | --- | --- |
| EC01 | Two distinct, tagged new pumps, with matching schedule declarations. | New installed pumps 2 each. |
| EC02 | One AHU depicted on plan, roof detail and schedule; physical correspondence established. | AHU 1 each; all references retained. |
| EC03 | Two pump depictions share a tag but their physical correspondence is unresolved. | Affected final pump count unknown; do not choose 1 or 2. |
| EC04 | Two independently established physical pumps share one tag. | Physical pumps 2; tag conflict visible; affected final remains unknown until resolved. |
| EC05 | One AHU supplied in three shipping sections, with four integral fan modules. | AHU 1 each; shipping sections 3 and fan modules 4 are linked details, not 7 extra AHUs. |
| EC06 | One chiller package includes two compressors and a unit-mounted controller. | Chiller 1 each; included components retained; no additional procurement for those same included items. |
| EC07 | Two pump packages explicitly include their motors. | Pumps 2 each; included motors 2; additional motor procurement 0. |
| EC08 | One chiller's package expressly includes a remote starter furnished loose for field installation. | Chiller 1; linked starter 1; additional starter procurement 0; field-installation obligation 1. |
| EC09 | Three observed pumps; applicable schedule declares four. | Observed 3, declared 4; affected final count unknown. |
| EC10 | Schedule declares one boiler; no supported plan occurrence or resolved omission. | Observed subtotal 0, declared 1; final unknown, outstanding requirement 1. |
| EC11 | One duty pump and one installed standby pump, both explicit. | Installed pumps 2; operating role does not remove the standby unit. |
| EC12 | Two installed pumps; project explicitly requires one spare seal and casing gasket per pump. | Pumps 2; spare seals 2 and spare gaskets 2 as separate obligations, not additional pumps. |
| EC13 | Two installed pumps plus one explicitly required uninstalled spare pump. | Installed 2; uninstalled spare 1; purchase requirement 3 if all are expressly new and separately procured. |
| EC14 | One AHU includes its filter racks; project expressly procures six operating filter elements separately. | AHU 1; separate filter elements 6; additional rack procurement 0. No unstated temporary/testing filter quantity. |
| EC15 | One representative terminal-unit symbol; current scoped note states three total including the representative. | Terminal units 3 each. Bare TYP alone would leave the affected final unknown. |
| EC16 | One verified reused fan is removed at its old location and reinstalled at a new location. | Physical fan 1; removal 1 and reinstallation 1; new fan purchase 0. |
| EC17 | One old boiler is removed and a distinct new boiler replaces it. | Demolition 1 and new installation 1; neither net 0 nor two new boilers. |
| EC18 | One outdoor unit and two indoor units are distinct installed assemblies explicitly linked to one system. | Outdoor 1 and indoor 2; system 1 separately. Procurement package count remains unknown unless supported. |
| EC19 | Detector returns no equipment with unknown coverage; separate case verifies a complete empty selected scope. | First final unknown; verified empty scope final 0. |
| EC20 | A source-backed correction removes a false positive from 3 pump proposals; unrelated AHU group is 1. | Current pumps 2, prior 3 retained; AHU remains 1; replay remains 2. |

Original plan/schedule/specification pages, model omission/false-positive checks,
relocation/revision pairs and representative acceptance thresholds remain to be
created and verified. These examples do not grant production or native acceptance.

## Next implementation boundary

The class decision is recorded. Bind the approved rule bytes and literal answers to a
source-bound equipment count projection, then connect it to existing equipment
observations, schedule reconciliation, corrections, saved history and exports.
Reuse accepted evidence/state infrastructure; do not reuse the old one-per-tag
draft as physical truth. Count implementation and meaningful checks must cover
these rules plus malformed/source-change and category-overlap cases.

Equipment quantity implementation is ready under this decision. Piping
preparation, insulation and controls requirement extraction, section coverage
and supported exports can advance independently. Do not ask for this approval
again or return to duct/air-device refinement as a prerequisite.
