# Full Division 23 scope audit and proposed work lineup

Owner clarification, 2026-09-16: mechanical takeoff covers all CSI Division 23
sections and subsections. Duct and air-device implementation are partial delivery
slices. This restates the existing full Mac/Windows goal; it is not a scope change.

This document records the coverage audit and a proposed delivery sequence for
discussion. It is not a complete MasterFormat hierarchy, approved new mechanical
rule packet or permission to treat every guide-spec option as a project requirement.
No application code changes accompany this audit.

## What is missing from the current plan

The current taxonomy contains nine broad application categories. The roadmap
names complete Division 23, but has no verified section-by-section completion
register. Broad labels such as equipment, accessories and controls can conceal
unimplemented systems, specialist requirements and non-graphical service scope.
Air-device recognition is also unqualified despite its tested calculation/UI.

A source catalogue, recognized term, count helper or generic export is not
completion of a mechanical section. Existing duct and air-device work remains
useful and is preserved.

## Coverage that the full register must represent

These are functional coverage areas, not an authoritative numbering list. The
complete applicable edition's section/subsection register must be verified and
mapped, including project-specific sections and cross-references.

| Coverage area | Required takeoff treatment |
| --- | --- |
| Common HVAC work | Sleeves, seals, identification, supports, vibration/seismic requirements, connections and responsibility boundaries |
| Operation, maintenance and existing-system work | Cleaning, servicing, retained/reused equipment, removals, relocation and project-required maintenance obligations |
| HVAC schedules | Equipment/device declarations, attributes, system relationships and plan/spec reconciliation |
| Insulation | Pipe, duct and equipment insulation, liner, vapor barriers, jackets, fittings and termination/accessory requirements |
| Commissioning | Applicable system scope, tests, startup, training, documentation and coordination obligations |
| Instrumentation and controls | Sensors, actuators, control valves/dampers, controllers, panels, points and applicable wiring/tubing |
| Facility fuel systems | Applicable fuel storage, pumps, distribution, fittings, valves, accessories and equipment interfaces |
| HVAC piping and pumps | Hydronic, steam/condensate, refrigerant and other applicable HVAC services, including treatment and specialties |
| Air distribution | Ducts, fittings, plenums, accessories, fans, terminals, outlets/inlets, special exhaust and ventilation hoods |
| Air cleaning | Filters, housings, air-cleaning assemblies, replacement/spare requirements and applicable accessories |
| Central heating | Boilers, heating plant equipment, heat exchangers, combustion/venting interfaces and specified ancillary components |
| Central cooling | Chillers, cooling towers/heat rejection equipment and associated assemblies/accessories |
| Central HVAC equipment | Air handling, energy recovery, packaged central/outdoor-air equipment and factory/field component boundaries |
| Decentralized HVAC equipment | Unitary/split/VRF/heat-pump and terminal systems, local heating/cooling and humidity-control equipment as applicable |

Demolition, existing-to-remain, new work, relocation, alternates and revisions are
cross-cutting states, not an excuse to omit the underlying section. Interfaces
with other divisions require explicit project responsibility, not assumptions.

Maintain two distinct records:

1. Product coverage: every verified section/subsection, its capability, rules,
   tests and representative/native acceptance. An unfinished section stays open
   even if absent from the current pilot project.
2. Project coverage: every supplied specification section and drawing/schedule
   requirement, with applicability and disposition. Absence of a detected symbol
   does not prove absence of scope. Not-applicable needs project evidence/review.

## Delivery approaches

- Recommended: shared calculation capabilities plus explicit section coverage.
  Reuse each-count, route-length, area, assembly and requirement handling while
  keeping system-specific rules and acceptance cases separate.
- Strictly section-by-section implementation is easy to label, but repeats shared
  work and delays connected systems spanning multiple sections.
- A single all-Division-23 implementation batch hides defects and postpones usable
  delivery. It does not provide a reliable progress signal.

## Proposed implementation queue

The settled duct → air-device → equipment quantity-class order remains intact.
The equipment packet is the next class preparation. Full Division 23 remains the
finish line; the rows below are delivery packages, not scope exclusions.

| Order | Work package | Concrete finish line |
| --- | --- | --- |
| 0, alongside delivery | Section coverage and specification obligations | Versioned section register with explicit product/project status, source locators and linked implementation tasks; complete hierarchy verification recorded separately |
| 1 | Physical equipment counts and assembly rules | Counts from original plans/schedules/specs, exact instance reconciliation, factory-vs-field accessories, new/existing/demolition/relocation, corrections and source-linked exports |
| 2 | Remaining air-distribution items | Supported fitting/accessory/fan/terminal/hood/air-cleaning quantities and relationships, avoiding overlap with equipment assemblies and existing duct lengths |
| 3 | HVAC piping and system variants | Lengths by service/material/size, supported verticals, fittings, valves, pumps and specialties; separately verified hydronic, steam/condensate, refrigerant and fuel rule packets |
| 4 | Insulation, liner and jackets | Supported pipe/duct/equipment quantities by material, thickness and finish; fitting/valve treatments and exclusions tied to specs |
| 5 | Common-work accessories and supports | Hangers, supports, isolation, bracing, sleeves, seals and identification from explicit evidence or approved derivation; assumptions stay visible |
| 6 | Controls and instrumentation | Physical devices, assembly/point relationships, responsibility boundaries and supported wiring/tubing quantities; no automatic route guesses |
| 7 | Testing, balancing, commissioning and O&M | Source-linked service obligations, system/equipment coverage, deliverables and unresolved scope available for estimator review and procurement |
| Every package | Corrections, revisions, outputs and platform checks | New category works through the connected app, reopen/recalculate, Excel and evidence PDF; native checks scheduled against exact bytes |

Central heating/cooling, central/decentralized HVAC, fuel and specialist systems
must have explicit child tasks under their relevant packages. A generic equipment
counter does not finish those systems' piping, insulation, controls or services.

## Keep work moving

- Primary product work: prepare the next equipment rules/examples and then deliver
  that connected capability. Ask for concrete unresolved rule decisions together
  with examples, not a fresh broad architecture approval every session.
- Independent current output work: finish the consolidated evidence PDF using
  accepted quantities and source identities. Do not use it to indefinitely defer
  later calculation classes.
- Bounded recognition work: evaluate a deliberate alternative to the failed
  reader against frozen original-source examples. Record accuracy and omissions;
  do not repeat unbounded prompt tuning or block all independent implementation.
- Research ahead by one package: NotebookLM query, source-passage verification,
  rules/examples packet, then bounded Claude assignment once authentication works.
  Provider unavailability does not block independently authorized Codex work.
- Keep one primary implementation and at most two useful independent tracks.
  Publish completed capability, acceptance limits and the next three ready tasks.
- Do not reopen accepted duct geometry without a concrete blocking defect.
  Do not interpret full Division 23 as approval for new pricing/labor rules.

## Done means connected and verified

For each section: applicable documents/requirements identified; physical and
non-physical scope represented; approved deterministic rule basis; original-source
recognition demonstrated for automated quantities; no duplicate assembly purchase;
uncertainty visible; corrections/revisions/reopen work; source-linked export agrees
with core; representative and native-platform acceptance status explicit.

Product completion requires the full verified section register and representative
project suite on Mac and Windows. A smaller project's complete takeoff does not
turn unsupported product sections into completed work.

## Discussion still needed

First question sent to the owner: which project type should set the initial
representative cases and prioritization: commercial/institutional, residential/
multifamily, or industrial/process-heavy? The scope remains full Division 23.

Subsequent focused questions: applicable MasterFormat edition/project spec source;
first pilot project sizes and drawing quality; responsibility for subcontracted
controls/insulation/TAB; equipment assembly conventions; service obligation output;
acceptable omission/quantity tolerances; recognition review effort; and any later
pricing/labor objective. Settled imperial/D01–D10/A01–A12 decisions stay closed.

## Verified research and limitations

The [research record](notebooklm/div23-scope-findings-2026-09-16.json) reuses the
NotebookLM inventory and verifies public VA/UFGS source passages for equipment
assembly boundaries, insulation and TAB obligations. Guide-specific options are
not universal project rules. A generated suggestion to itemize all accessories
separately is rejected without assembly/scope evidence.

[CSI's current overview](https://www.csiresources.org/standards/overview) identifies
MasterFormat 2026 and its authorized edition-aware source.
[The publisher's Division 23 overview](https://theconstructionstandard.com/masterformat-division-23-heating-ventilating-and-air-conditioning-hvac)
confirms broad HVAC scope and coordination boundaries, but is explicitly not the
complete subsection hierarchy. Exact edition/section coverage must be verified;
this audit does not claim it has already been imported or implemented.
