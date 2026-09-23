"""Original application vocabulary for mechanical document classification.

The catalogue is deliberately descriptive.  It does not encode engineering
requirements, quantities, inclusions, or contractual precedence.
"""

import re


VERSION = "division23-1.0.0"

_CATEGORIES = (
    "equipment",
    "ductwork",
    "air_devices",
    "piping",
    "fittings",
    "insulation",
    "controls",
    "accessories",
    "demolition",
)

# Tuples keep the module's source vocabulary immutable.  catalogue() converts
# every row and alias collection to new JSON-compatible objects for callers.
_TERM_ROWS = (
    ("equipment.fan", "Fan", "equipment",
     ("fan", "fans", "supply fan", "return fan", "exhaust fan")),
    ("equipment.air_handling_unit", "Air-handling unit", "equipment",
     ("air handling unit", "air handling units", "air-handling unit",
      "air-handling units", "AHU", "AHUs")),
    ("equipment.terminal_unit", "Terminal unit", "equipment",
     ("terminal unit", "terminal units", "air terminal unit", "air terminal units",
      "variable air volume terminal", "variable air volume terminals")),
    ("equipment.boiler", "Boiler", "equipment", ("boiler", "boilers")),
    ("equipment.chiller", "Chiller", "equipment", ("chiller", "chillers")),
    ("equipment.pump", "Pump", "equipment", ("pump", "pumps")),
    ("equipment.heat_pump", "Heat pump", "equipment", ("heat pump", "heat pumps")),
    ("equipment.cooling_tower", "Cooling tower", "equipment",
     ("cooling tower", "cooling towers")),
    ("equipment.unit_heater", "Unit heater", "equipment",
     ("unit heater", "unit heaters")),
    ("equipment.rooftop_unit", "Rooftop unit", "equipment",
     ("rooftop unit", "rooftop units", "roof top unit", "roof top units")),
    ("equipment.makeup_air_unit", "Make-up air unit", "equipment",
     ("makeup air unit", "makeup air units", "make-up air unit", "make-up air units")),
    ("equipment.energy_recovery_ventilator", "Energy-recovery ventilator", "equipment",
     ("energy recovery ventilator", "energy recovery ventilators",
      "energy-recovery ventilator", "energy-recovery ventilators")),

    ("ductwork.duct", "Duct", "ductwork", ("duct", "ducts", "ductwork")),
    ("ductwork.round_duct", "Round duct", "ductwork", ("round duct", "round ducts")),
    ("ductwork.rectangular_duct", "Rectangular duct", "ductwork",
     ("rectangular duct", "rectangular ducts")),
    ("ductwork.flexible_duct", "Flexible duct", "ductwork",
     ("flexible duct", "flexible ducts", "flex duct", "flex ducts")),
    ("ductwork.spiral_duct", "Spiral duct", "ductwork", ("spiral duct", "spiral ducts")),
    ("ductwork.supply_duct", "Supply duct", "ductwork",
     ("supply duct", "supply ducts", "supply ductwork",
      "supply air duct", "supply air ducts", "supply air ductwork")),
    ("ductwork.return_duct", "Return duct", "ductwork",
     ("return duct", "return ducts", "return ductwork",
      "return air duct", "return air ducts", "return air ductwork")),
    ("ductwork.exhaust_duct", "Exhaust duct", "ductwork",
     ("exhaust duct", "exhaust ducts", "exhaust ductwork",
      "exhaust air duct", "exhaust air ducts", "exhaust air ductwork")),

    ("air_devices.diffuser", "Diffuser", "air_devices", ("diffuser", "diffusers")),
    ("air_devices.grille", "Grille", "air_devices", ("grille", "grilles")),
    ("air_devices.register", "Register", "air_devices", ("register", "registers")),
    ("air_devices.louver", "Louver", "air_devices", ("louver", "louvers")),

    ("piping.pipe", "Pipe", "piping", ("pipe", "pipes", "piping")),
    ("piping.heating_water_supply", "Heating-water supply", "piping",
     ("heating water supply", "heating-water supply", "heating water supply piping",
      "heating-water supply piping")),
    ("piping.heating_water_return", "Heating-water return", "piping",
     ("heating water return", "heating-water return", "heating water return piping",
      "heating-water return piping")),
    ("piping.chilled_water_supply", "Chilled-water supply", "piping",
     ("chilled water supply", "chilled-water supply", "chilled water supply piping",
      "chilled-water supply piping")),
    ("piping.chilled_water_return", "Chilled-water return", "piping",
     ("chilled water return", "chilled-water return", "chilled water return piping",
      "chilled-water return piping")),
    ("piping.condensate", "Condensate piping", "piping",
     ("condensate", "condensate pipe", "condensate pipes", "condensate piping",
      "condensate drain", "condensate drains")),
    ("piping.steam", "Steam piping", "piping",
     ("steam pipe", "steam pipes", "steam piping", "steam supply", "steam return")),
    ("piping.refrigerant", "Refrigerant piping", "piping",
     ("refrigerant pipe", "refrigerant pipes", "refrigerant piping",
      "refrigerant line", "refrigerant lines")),

    ("fittings.fitting", "Fitting", "fittings",
     ("fitting", "fittings", "pipe fitting", "pipe fittings", "duct fitting", "duct fittings")),
    ("fittings.elbow", "Elbow", "fittings", ("elbow", "elbows")),
    ("fittings.tee", "Tee", "fittings", ("tee", "tees")),
    ("fittings.reducer", "Reducer", "fittings", ("reducer", "reducers")),
    ("fittings.transition", "Transition", "fittings", ("transition", "transitions")),
    ("fittings.coupling", "Coupling", "fittings", ("coupling", "couplings")),
    ("fittings.union", "Union", "fittings", ("union", "unions")),

    ("insulation.insulation", "Insulation", "insulation",
     ("insulation", "insulate", "insulated", "insulating")),
    ("insulation.pipe_insulation", "Pipe insulation", "insulation",
     ("pipe insulation", "piping insulation", "insulated pipe", "insulated pipes",
      "insulated piping")),
    ("insulation.duct_insulation", "Duct insulation", "insulation",
     ("duct insulation", "ductwork insulation", "insulated duct", "insulated ducts",
      "insulated ductwork", "insulated supply duct", "insulated return duct",
      "insulated exhaust duct")),
    ("insulation.duct_liner", "Duct liner", "insulation",
     ("duct liner", "duct liners", "duct lining", "duct linings",
      "lined duct", "lined ducts")),

    ("controls.thermostat", "Thermostat", "controls", ("thermostat", "thermostats")),
    ("controls.temperature_sensor", "Temperature sensor", "controls",
     ("temperature sensor", "temperature sensors")),
    ("controls.pressure_sensor", "Pressure sensor", "controls",
     ("pressure sensor", "pressure sensors")),
    ("controls.control_valve", "Control valve", "controls",
     ("control valve", "control valves")),
    ("controls.damper_actuator", "Damper actuator", "controls",
     ("damper actuator", "damper actuators")),
    ("controls.variable_frequency_drive", "Variable-frequency drive", "controls",
     ("variable frequency drive", "variable frequency drives",
      "variable-frequency drive", "variable-frequency drives")),

    ("accessories.damper", "Damper", "accessories", ("damper", "dampers")),
    ("accessories.fire_damper", "Fire damper", "accessories",
     ("fire damper", "fire dampers")),
    ("accessories.smoke_damper", "Smoke damper", "accessories",
     ("smoke damper", "smoke dampers")),
    ("accessories.combination_fire_smoke_damper", "Combination fire/smoke damper",
     "accessories", ("combination fire smoke damper", "combination fire smoke dampers",
                     "combination fire/smoke damper", "combination fire/smoke dampers",
                     "fire smoke damper", "fire smoke dampers",
                     "fire/smoke damper", "fire/smoke dampers")),
    ("accessories.vibration_isolator", "Vibration isolator", "accessories",
     ("vibration isolator", "vibration isolators", "vibration isolation")),
    ("accessories.equipment_curb", "Equipment curb", "accessories",
     ("equipment curb", "equipment curbs", "roof curb", "roof curbs")),
    ("accessories.strainer", "Strainer", "accessories", ("strainer", "strainers")),
    ("accessories.air_separator", "Air separator", "accessories",
     ("air separator", "air separators")),
    ("accessories.expansion_tank", "Expansion tank", "accessories",
     ("expansion tank", "expansion tanks")),

    ("demolition.demolition", "Demolition", "demolition", ("demolition", "demolish")),
    ("demolition.remove_existing", "Remove existing", "demolition",
     ("remove existing", "remove the existing", "existing to be removed")),
    ("demolition.existing_to_remain", "Existing to remain", "demolition",
     ("existing to remain", "existing equipment to remain")),
    ("demolition.abandon_in_place", "Abandon in place", "demolition",
     ("abandon in place", "abandoned in place")),
)

_SYSTEM_ROWS = (
    ("system.supply_air", "Supply air", ("supply air",)),
    ("system.return_air", "Return air", ("return air",)),
    ("system.exhaust_air", "Exhaust air", ("exhaust air",)),
    ("system.heating_water_supply", "Heating-water supply",
     ("heating water supply", "heating-water supply")),
    ("system.heating_water_return", "Heating-water return",
     ("heating water return", "heating-water return")),
    ("system.chilled_water_supply", "Chilled-water supply",
     ("chilled water supply", "chilled-water supply")),
    ("system.chilled_water_return", "Chilled-water return",
     ("chilled water return", "chilled-water return")),
    ("system.condensate", "Condensate", ("condensate",)),
    ("system.steam", "Steam", ("steam",)),
    ("system.refrigerant", "Refrigerant", ("refrigerant",)),
)

_SYSTEM_BY_TERM = {
    "ductwork.supply_duct": "system.supply_air",
    "ductwork.return_duct": "system.return_air",
    "ductwork.exhaust_duct": "system.exhaust_air",
    "piping.heating_water_supply": "system.heating_water_supply",
    "piping.heating_water_return": "system.heating_water_return",
    "piping.chilled_water_supply": "system.chilled_water_supply",
    "piping.chilled_water_return": "system.chilled_water_return",
    "piping.condensate": "system.condensate",
    "piping.steam": "system.steam",
    "piping.refrigerant": "system.refrigerant",
}

_SEPARATOR = r"[\s\u00a0\u2000-\u200a\u202f\u205f\u3000\-\u2010-\u2015]+"


def _alias_pattern(alias):
    """Build a span-preserving pattern with flexible dash/space separators."""
    parts = re.split(r"[\s\u00a0\u2000-\u200a\u202f\u205f\u3000\-\u2010-\u2015]+", alias)
    body = _SEPARATOR.join(re.escape(part) for part in parts if part)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE | re.UNICODE)


_MATCHERS = tuple(
    (term_id, label, category, _alias_pattern(alias))
    for term_id, label, category, aliases in _TERM_ROWS
    for alias in aliases
)

_SYSTEM_MATCHERS = tuple(
    (system_id, _alias_pattern(alias))
    for system_id, _label, aliases in _SYSTEM_ROWS
    for alias in aliases
)


def catalogue():
    """Return a fresh JSON-compatible snapshot of the shared vocabulary."""
    return {
        "version": VERSION,
        "categories": list(_CATEGORIES),
        "systems": [
            {"id": system_id, "label": label, "aliases": list(aliases)}
            for system_id, label, aliases in _SYSTEM_ROWS
        ],
        "terms": [
            {"id": term_id, "label": label, "category": category,
             "aliases": list(aliases)}
            for term_id, label, category, aliases in _TERM_ROWS
        ],
    }


def classify(text):
    """Classify known mechanical vocabulary in *text*."""
    empty = {
        "taxonomy_version": VERSION,
        "categories": [],
        "term_ids": [],
        "matches": [],
        "system_ids": [],
    }
    if not isinstance(text, str) or not text.strip():
        return empty

    candidates = []
    seen = set()
    for term_id, label, category, pattern in _MATCHERS:
        for found in pattern.finditer(text):
            key = (term_id, found.start(), found.end())
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "term_id": term_id,
                "label": label,
                "category": category,
                "text": text[found.start():found.end()],
                "start": found.start(),
                "end": found.end(),
            })

    # Prefer the most specific wording within one category.  Overlaps across
    # categories remain because they can express separate concepts, such as
    # insulation on an exhaust duct.
    accepted = []
    for candidate in sorted(
            candidates,
            key=lambda item: (-(item["end"] - item["start"]), item["start"],
                              item["end"], item["term_id"])):
        contained = any(
            other["category"] == candidate["category"]
            and other["start"] <= candidate["start"]
            and candidate["end"] <= other["end"]
            for other in accepted
        )
        if not contained:
            accepted.append(candidate)

    matches = sorted(
        accepted,
        key=lambda item: (item["start"], item["end"], item["term_id"]),
    )
    term_ids = sorted({match["term_id"] for match in matches})
    directly_matched_systems = {
        system_id
        for system_id, pattern in _SYSTEM_MATCHERS
        if pattern.search(text)
    }
    return {
        "taxonomy_version": VERSION,
        "categories": sorted({match["category"] for match in matches}),
        "term_ids": term_ids,
        "matches": matches,
        "system_ids": sorted(
            directly_matched_systems
            | {_SYSTEM_BY_TERM[term_id] for term_id in term_ids
               if term_id in _SYSTEM_BY_TERM}
        ),
    }
