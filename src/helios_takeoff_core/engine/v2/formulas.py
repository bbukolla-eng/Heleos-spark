"""Closed, exact formula evaluation for Division 23 v2 packs.

The module deliberately has no ambient inputs.  Expressions can read only the
observations and lookup tables supplied by an immutable compiled pack.  Numeric
work is performed as :class:`fractions.Fraction` values and is rendered as a
decimal only when the result is known to terminate exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping

from helios_takeoff_core.errors import ValidationError

from .contracts import (
    CompiledDomainPack,
    EvaluationStatus,
    FormulaEvaluation,
    FormulaTrace,
    ScalarType,
    TypedValue,
)


DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
MAX_FORMULA_DEPTH = 32
MAX_FORMULA_NODES = 256
MAX_PACK_FORMULA_NODES = 4096


class FormulaBlocked(ValidationError):
    """A deterministic runtime condition prevented an exact result."""

    def __init__(
        self,
        reason: str,
        *,
        missing_observations: tuple[str, ...] = (),
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing_observations = missing_observations


def canonical_decimal(value: object, field_name: str) -> str:
    """Validate and return the unique plain-decimal spelling of ``value``."""

    if not isinstance(value, str) or DECIMAL_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a plain decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValidationError(f"{field_name} must be finite")
    if parsed.is_zero():
        return "0"
    rendered = format(parsed, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a positive integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        parsed = int(value)
    else:
        raise ValidationError(f"{field_name} must be a positive integer")
    if parsed <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return parsed


def _fraction_to_plain_decimal(value: Fraction) -> str:
    """Render a rational exactly, blocking when its decimal cannot terminate."""

    denominator = value.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise FormulaBlocked("INEXACT_UNIT_CONVERSION")

    scale = max(twos, fives)
    scaled = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled))
    if scale:
        digits = digits.zfill(scale + 1)
        rendered = f"{sign}{digits[:-scale]}.{digits[-scale:]}"
    else:
        rendered = f"{sign}{digits}"
    return canonical_decimal(rendered, "exact result")


def convert_decimal(value: Decimal, *, numerator: int, denominator: int) -> Decimal:
    """Apply a positive rational factor without rounding."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("unit conversion value must be a finite Decimal")
    numerator = _positive_integer(numerator, "unit conversion numerator")
    denominator = _positive_integer(denominator, "unit conversion denominator")
    exact = Fraction(value) * Fraction(numerator, denominator)
    return Decimal(_fraction_to_plain_decimal(exact))


def exact_decimal_multiply(left: str, right: str) -> str:
    """Multiply two canonical decimal strings and return an exact canonical string."""

    left_value = Fraction(Decimal(canonical_decimal(left, "left operand")))
    right_value = Fraction(Decimal(canonical_decimal(right, "right operand")))
    return _fraction_to_plain_decimal(left_value * right_value)


def exact_decimal_divide(left: str, right: str) -> str:
    """Divide two canonical decimal strings, blocking zero or repeating results."""

    left_value = Fraction(Decimal(canonical_decimal(left, "left operand")))
    right_value = Fraction(Decimal(canonical_decimal(right, "right operand")))
    if right_value == 0:
        raise FormulaBlocked("DIVIDE_BY_ZERO")
    return _fraction_to_plain_decimal(left_value / right_value)


# Dimensions are vectors in the order count, length, mass, time, currency.
DimensionVector = tuple[int, int, int, int, int]
DIMENSION_VECTORS: Mapping[str, DimensionVector] = MappingProxyType(
    {
        "DIMENSIONLESS": (0, 0, 0, 0, 0),
        "COUNT": (1, 0, 0, 0, 0),
        "LENGTH": (0, 1, 0, 0, 0),
        "AREA": (0, 2, 0, 0, 0),
        "MASS": (0, 0, 1, 0, 0),
        "TIME": (0, 0, 0, 1, 0),
        "CURRENCY": (0, 0, 0, 0, 1),
        "MASS_PER_AREA": (0, -2, 1, 0, 0),
        "TIME_PER_LENGTH": (0, -1, 0, 1, 0),
        "CURRENCY_PER_LENGTH": (0, -1, 0, 0, 1),
        "COUNT_PER_LENGTH": (1, -1, 0, 0, 0),
    }
)
_VECTOR_NAMES = {vector: name for name, vector in DIMENSION_VECTORS.items()}
_DIMENSIONLESS = DIMENSION_VECTORS["DIMENSIONLESS"]


def _dimension_vector(value: object, field_name: str) -> DimensionVector:
    if not isinstance(value, str) or value not in DIMENSION_VECTORS:
        raise ValidationError(f"{field_name} is not a supported dimension")
    return DIMENSION_VECTORS[value]


def _dimension_name(vector: DimensionVector) -> str:
    try:
        return _VECTOR_NAMES[vector]
    except KeyError as exc:
        raise ValidationError("formula produces an unsupported dimension") from exc


def _combine_dimensions(
    left: DimensionVector,
    right: DimensionVector,
    *,
    operation: str,
) -> DimensionVector:
    sign = 1 if operation == "MULTIPLY" else -1
    combined = tuple(a + sign * b for a, b in zip(left, right, strict=True))
    _dimension_name(combined)
    return combined  # type: ignore[return-value]


def _scalar_type(value: object, field_name: str) -> ScalarType:
    try:
        return value if isinstance(value, ScalarType) else ScalarType(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} is not a supported scalar type") from exc


def _numeric_type(left: ScalarType, right: ScalarType, operation: str) -> ScalarType:
    numeric = {ScalarType.DECIMAL, ScalarType.INTEGER}
    if left not in numeric or right not in numeric:
        raise ValidationError(f"{operation} operands must be numeric")
    if operation == "DIVIDE" or ScalarType.DECIMAL in (left, right):
        return ScalarType.DECIMAL
    return ScalarType.INTEGER


@dataclass(frozen=True)
class _FormulaSpec:
    scalar_type: ScalarType
    dimension: DimensionVector


@dataclass(frozen=True)
class _RuntimeValue:
    scalar_type: ScalarType
    value: Fraction | bool | str
    dimension: DimensionVector


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field_name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValidationError(f"{field_name} fields must be strings")
    return value


def _exact_fields(
    node: Mapping[str, object],
    expected: set[str],
    *,
    operation: str,
) -> None:
    actual = set(node)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise ValidationError(f"{operation} formula fields are invalid: {', '.join(details)}")


def _literal_value(node: Mapping[str, object]) -> TypedValue:
    value_type = _scalar_type(node["value_type"], "literal value_type")
    raw = node["value"]
    if value_type is ScalarType.DECIMAL:
        value: str | int | bool = canonical_decimal(raw, "literal value")
    elif value_type is ScalarType.INTEGER:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValidationError("literal INTEGER value must be an integer")
        value = raw
    elif value_type is ScalarType.BOOLEAN:
        if not isinstance(raw, bool):
            raise ValidationError("literal BOOLEAN value must be a boolean")
        value = raw
    else:
        if not isinstance(raw, str):
            raise ValidationError(f"literal {value_type.value} value must be a string")
        value = raw
    return TypedValue(value_type, value, None)


def compile_literal(expression: Mapping[str, object]) -> TypedValue:
    """Compile a standalone literal node using the same closed-node rules."""

    node = _mapping(expression, "literal formula")
    _exact_fields(node, {"op", "value_type", "value"}, operation="LITERAL")
    if node["op"] != "LITERAL":
        raise ValidationError("literal formula op must be LITERAL")
    return _literal_value(node)


def _unit_definition(pack: CompiledDomainPack, uom: str) -> Mapping[str, object]:
    try:
        unit = pack.units_by_uom[uom]
    except KeyError as exc:
        raise ValidationError(f"formula references unknown unit {uom}") from exc
    return _mapping(unit, f"unit {uom}")


def _unit_dimension(pack: CompiledDomainPack, uom: str) -> DimensionVector:
    return _dimension_vector(_unit_definition(pack, uom).get("dimension"), f"unit {uom} dimension")


def _unit_factor(pack: CompiledDomainPack, uom: str) -> Fraction:
    unit = _unit_definition(pack, uom)
    numerator = _positive_integer(unit.get("to_base_numerator"), f"unit {uom} numerator")
    denominator = _positive_integer(unit.get("to_base_denominator"), f"unit {uom} denominator")
    return Fraction(numerator, denominator)


def formula_complexity(expression: Mapping[str, object]) -> tuple[int, int]:
    """Return ``(depth, node_count)`` while enforcing the closed AST shape."""

    count = 0

    def visit(raw: object, depth: int) -> int:
        nonlocal count
        if depth > MAX_FORMULA_DEPTH:
            raise ValidationError(f"formula depth exceeds {MAX_FORMULA_DEPTH}")
        node = _mapping(raw, "formula node")
        count += 1
        if count > MAX_FORMULA_NODES:
            raise ValidationError(f"formula node count exceeds {MAX_FORMULA_NODES}")
        op = node.get("op")
        if not isinstance(op, str):
            raise ValidationError("formula op must be a string")
        child_depths = [depth]
        if op == "LITERAL":
            _exact_fields(node, {"op", "value_type", "value"}, operation=op)
            _literal_value(node)
        elif op == "OBSERVATION":
            _exact_fields(node, {"op", "name"}, operation=op)
            if not isinstance(node["name"], str) or not node["name"]:
                raise ValidationError("OBSERVATION name must be a non-empty string")
        elif op == "LOOKUP":
            _exact_fields(node, {"op", "lookup_code", "keys"}, operation=op)
            if not isinstance(node["lookup_code"], str) or not node["lookup_code"]:
                raise ValidationError("LOOKUP lookup_code must be a non-empty string")
            keys = node["keys"]
            if not isinstance(keys, (list, tuple)) or not keys:
                raise ValidationError("LOOKUP keys must be a non-empty array")
            child_depths.extend(visit(child, depth + 1) for child in keys)
        elif op in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"}:
            _exact_fields(node, {"op", "left", "right"}, operation=op)
            child_depths.extend(
                (visit(node["left"], depth + 1), visit(node["right"], depth + 1))
            )
        elif op == "COMPARE":
            _exact_fields(node, {"op", "operator", "left", "right"}, operation=op)
            if node["operator"] not in {"EQ", "NE", "LT", "LE", "GT", "GE"}:
                raise ValidationError("COMPARE operator is not supported")
            child_depths.extend(
                (visit(node["left"], depth + 1), visit(node["right"], depth + 1))
            )
        elif op == "IF":
            _exact_fields(node, {"op", "condition", "then", "else"}, operation=op)
            child_depths.extend(
                (
                    visit(node["condition"], depth + 1),
                    visit(node["then"], depth + 1),
                    visit(node["else"], depth + 1),
                )
            )
        elif op in {"MIN", "MAX"}:
            _exact_fields(node, {"op", "args"}, operation=op)
            args = node["args"]
            if not isinstance(args, (list, tuple)) or not args:
                raise ValidationError(f"{op} args must be a non-empty array")
            child_depths.extend(visit(child, depth + 1) for child in args)
        else:
            raise ValidationError(f"formula op {op!r} is not supported")
        return max(child_depths)

    depth = visit(expression, 1)
    return depth, count


def _lookup_definition(pack: CompiledDomainPack, code: str) -> Mapping[str, object]:
    try:
        value = pack.lookups_by_code[code]
    except KeyError as exc:
        raise ValidationError(f"formula references unknown lookup {code}") from exc
    return _mapping(value, f"lookup {code}")


def _infer_formula(expression: object, pack: CompiledDomainPack) -> _FormulaSpec:
    node = _mapping(expression, "formula node")
    op = node["op"]
    if op == "LITERAL":
        literal = _literal_value(node)
        return _FormulaSpec(literal.scalar_type, _DIMENSIONLESS)
    if op == "OBSERVATION":
        name = node["name"]
        try:
            definition = _mapping(pack.observations_by_name[name], f"observation {name}")
        except KeyError as exc:
            raise ValidationError(f"formula references unknown observation {name}") from exc
        return _FormulaSpec(
            _scalar_type(definition.get("scalar_type"), f"observation {name} scalar_type"),
            _dimension_vector(definition.get("dimension"), f"observation {name} dimension"),
        )
    if op == "LOOKUP":
        code = node["lookup_code"]
        lookup = _lookup_definition(pack, code)
        key_types = lookup.get("key_types")
        keys = node["keys"]
        if not isinstance(key_types, (list, tuple)) or len(key_types) != len(keys):
            raise ValidationError(f"lookup {code} key count does not match formula")
        for index, (key, declared_type) in enumerate(zip(keys, key_types, strict=True)):
            inferred = _infer_formula(key, pack)
            expected = _scalar_type(declared_type, f"lookup {code} key_types[{index}]")
            if inferred.scalar_type is not expected or inferred.dimension != _DIMENSIONLESS:
                raise ValidationError(f"lookup {code} key {index} has incompatible type")
        return _FormulaSpec(
            _scalar_type(lookup.get("value_type"), f"lookup {code} value_type"),
            _dimension_vector(lookup.get("dimension"), f"lookup {code} dimension"),
        )

    if op in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "COMPARE"}:
        left = _infer_formula(node["left"], pack)
        right = _infer_formula(node["right"], pack)
        if op in {"ADD", "SUBTRACT"}:
            result_type = _numeric_type(left.scalar_type, right.scalar_type, op)
            if left.dimension != right.dimension:
                raise ValidationError(f"{op} operands have incompatible dimensions")
            return _FormulaSpec(result_type, left.dimension)
        if op in {"MULTIPLY", "DIVIDE"}:
            result_type = _numeric_type(left.scalar_type, right.scalar_type, op)
            dimension = _combine_dimensions(left.dimension, right.dimension, operation=op)
            return _FormulaSpec(result_type, dimension)

        operator = node["operator"]
        numeric = {ScalarType.DECIMAL, ScalarType.INTEGER}
        if left.scalar_type in numeric and right.scalar_type in numeric:
            if left.dimension != right.dimension:
                raise ValidationError("COMPARE operands have incompatible dimensions")
        elif left.scalar_type is not right.scalar_type or left.dimension != right.dimension:
            raise ValidationError("COMPARE operands have incompatible types")
        if operator not in {"EQ", "NE"} and left.scalar_type is ScalarType.BOOLEAN:
            raise ValidationError("ordered COMPARE does not accept BOOLEAN operands")
        return _FormulaSpec(ScalarType.BOOLEAN, _DIMENSIONLESS)

    if op == "IF":
        condition = _infer_formula(node["condition"], pack)
        if condition != _FormulaSpec(ScalarType.BOOLEAN, _DIMENSIONLESS):
            raise ValidationError("IF condition must be BOOLEAN")
        when_true = _infer_formula(node["then"], pack)
        when_false = _infer_formula(node["else"], pack)
        if when_true != when_false:
            raise ValidationError("IF branches have incompatible types or dimensions")
        return when_true

    if op in {"MIN", "MAX"}:
        specs = [_infer_formula(item, pack) for item in node["args"]]
        first = specs[0]
        if first.scalar_type not in {ScalarType.DECIMAL, ScalarType.INTEGER}:
            raise ValidationError(f"{op} args must be numeric")
        for spec in specs[1:]:
            _numeric_type(first.scalar_type, spec.scalar_type, op)
            if spec.dimension != first.dimension:
                raise ValidationError(f"{op} args have incompatible dimensions")
        scalar_type = (
            ScalarType.DECIMAL
            if any(spec.scalar_type is ScalarType.DECIMAL for spec in specs)
            else ScalarType.INTEGER
        )
        return _FormulaSpec(scalar_type, first.dimension)

    raise ValidationError(f"formula op {op!r} is not supported")


def validate_formula(
    expression: Mapping[str, object],
    *,
    pack: CompiledDomainPack,
    expected_scalar_type: ScalarType | str | None = None,
    expected_uom: str | None = None,
) -> None:
    """Validate bounds, node shapes, references, scalar types, and dimensions."""

    formula_complexity(expression)
    inferred = _infer_formula(expression, pack)
    if expected_scalar_type is None:
        return
    expected = _scalar_type(expected_scalar_type, "expected_scalar_type")
    numeric = {ScalarType.DECIMAL, ScalarType.INTEGER}
    if inferred.scalar_type is not expected and not (
        inferred.scalar_type in numeric and expected in numeric
    ):
        raise ValidationError("formula result scalar type is incompatible with its declaration")

    if expected_uom is None:
        expected_dimension = _DIMENSIONLESS
    else:
        if not isinstance(expected_uom, str) or not expected_uom:
            raise ValidationError("expected_uom must be a non-empty string or null")
        expected_dimension = _unit_dimension(pack, expected_uom)
    if inferred.dimension != expected_dimension:
        # A numeric literal is a unitless multiplicity which may be explicitly
        # assigned the rule or edge's declared output unit at the root.
        if not (
            inferred.dimension == _DIMENSIONLESS
            and inferred.scalar_type in numeric
            and expression.get("op") == "LITERAL"
        ):
            raise ValidationError("formula result dimension is incompatible with its declared unit")


def _typed_to_runtime(value: TypedValue, pack: CompiledDomainPack, field_name: str) -> _RuntimeValue:
    scalar_type = _scalar_type(value.scalar_type, f"{field_name} scalar_type")
    if scalar_type is ScalarType.DECIMAL:
        numeric = Fraction(Decimal(canonical_decimal(value.value, f"{field_name} value")))
    elif scalar_type is ScalarType.INTEGER:
        if isinstance(value.value, bool) or not isinstance(value.value, int):
            raise ValidationError(f"{field_name} INTEGER value must be an integer")
        numeric = Fraction(value.value)
    elif scalar_type is ScalarType.BOOLEAN:
        if not isinstance(value.value, bool) or value.uom is not None:
            raise ValidationError(f"{field_name} BOOLEAN value is invalid")
        return _RuntimeValue(scalar_type, value.value, _DIMENSIONLESS)
    else:
        if not isinstance(value.value, str) or value.uom is not None:
            raise ValidationError(f"{field_name} {scalar_type.value} value is invalid")
        return _RuntimeValue(scalar_type, value.value, _DIMENSIONLESS)

    if value.uom is None:
        return _RuntimeValue(scalar_type, numeric, _DIMENSIONLESS)
    if not isinstance(value.uom, str) or not value.uom:
        raise ValidationError(f"{field_name} uom is invalid")
    return _RuntimeValue(
        scalar_type,
        numeric * _unit_factor(pack, value.uom),
        _unit_dimension(pack, value.uom),
    )


def _runtime_trace(value: _RuntimeValue) -> Mapping[str, object]:
    if isinstance(value.value, Fraction):
        encoded: object = {
            "numerator": str(value.value.numerator),
            "denominator": str(value.value.denominator),
        }
    else:
        encoded = value.value
    return {
        "scalar_type": value.scalar_type.value,
        "dimension": _dimension_name(value.dimension),
        "exact_value": encoded,
    }


def _trace_node(op: str, runtime_value: _RuntimeValue, **parts: object) -> dict[str, object]:
    return {"op": op, **parts, "result": _runtime_trace(runtime_value)}


def _key_value(value: _RuntimeValue, lookup_code: str, index: int) -> object:
    if value.dimension != _DIMENSIONLESS:
        raise ValidationError(f"lookup {lookup_code} key {index} must be dimensionless")
    if isinstance(value.value, Fraction):
        if value.scalar_type is ScalarType.INTEGER:
            if value.value.denominator != 1:
                raise ValidationError(f"lookup {lookup_code} key {index} is not integral")
            return value.value.numerator
        return _fraction_to_plain_decimal(value.value)
    return value.value


def _lookup_entry_value(
    lookup: Mapping[str, object],
    *,
    code: str,
    keys: tuple[object, ...],
    pack: CompiledDomainPack,
) -> _RuntimeValue:
    entries = lookup.get("entries")
    if not isinstance(entries, (list, tuple)):
        raise ValidationError(f"lookup {code} entries must be an array")
    found: Mapping[str, object] | None = None
    for raw_entry in entries:
        entry = _mapping(raw_entry, f"lookup {code} entry")
        entry_keys = entry.get("keys")
        if not isinstance(entry_keys, (list, tuple)):
            raise ValidationError(f"lookup {code} entry keys must be an array")
        if tuple(entry_keys) == keys:
            found = entry
            break
    if found is None:
        raise FormulaBlocked(f"MISSING_LOOKUP_KEY:{code}")

    scalar_type = _scalar_type(lookup.get("value_type"), f"lookup {code} value_type")
    uom = lookup.get("uom")
    if uom is not None and not isinstance(uom, str):
        raise ValidationError(f"lookup {code} uom must be a string or null")
    return _typed_to_runtime(TypedValue(scalar_type, found.get("value"), uom), pack, f"lookup {code}")


def _evaluate_node(
    expression: object,
    *,
    observations: Mapping[str, TypedValue],
    pack: CompiledDomainPack,
    used: list[str],
    used_lookups: list[str],
) -> tuple[_RuntimeValue, dict[str, object]]:
    node = _mapping(expression, "formula node")
    op = node["op"]

    if op == "LITERAL":
        typed = _literal_value(node)
        value = _typed_to_runtime(typed, pack, "literal")
        return value, _trace_node(op, value, value_type=typed.scalar_type.value, value=typed.value)

    if op == "OBSERVATION":
        name = node["name"]
        if name not in used:
            used.append(name)
        try:
            typed = observations[name]
        except KeyError as exc:
            raise FormulaBlocked(
                f"MISSING_OBSERVATION:{name}", missing_observations=(name,)
            ) from exc
        definition = _mapping(pack.observations_by_name[name], f"observation {name}")
        declared_type = _scalar_type(definition.get("scalar_type"), f"observation {name} scalar_type")
        if typed.scalar_type is not declared_type:
            raise ValidationError(f"observation {name} scalar type does not match its definition")
        accepted = definition.get("accepted_uoms")
        if typed.uom is not None and (
            not isinstance(accepted, (list, tuple)) or typed.uom not in accepted
        ):
            raise ValidationError(f"observation {name} unit is not accepted")
        value = _typed_to_runtime(typed, pack, f"observation {name}")
        declared_dimension = _dimension_vector(
            definition.get("dimension"), f"observation {name} dimension"
        )
        if value.dimension != declared_dimension:
            raise ValidationError(f"observation {name} dimension does not match its definition")
        return value, _trace_node(op, value, name=name)

    if op == "LOOKUP":
        code = node["lookup_code"]
        if code not in used_lookups:
            used_lookups.append(code)
        lookup = _lookup_definition(pack, code)
        key_values: list[object] = []
        key_traces: list[dict[str, object]] = []
        for index, key_expression in enumerate(node["keys"]):
            key, trace = _evaluate_node(
                key_expression,
                observations=observations,
                pack=pack,
                used=used,
                used_lookups=used_lookups,
            )
            key_values.append(_key_value(key, code, index))
            key_traces.append(trace)
        value = _lookup_entry_value(
            lookup, code=code, keys=tuple(key_values), pack=pack
        )
        return value, _trace_node(
            op, value, lookup_code=code, keys=key_traces, matched_keys=key_values
        )

    if op in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "COMPARE"}:
        left, left_trace = _evaluate_node(
            node["left"],
            observations=observations,
            pack=pack,
            used=used,
            used_lookups=used_lookups,
        )
        right, right_trace = _evaluate_node(
            node["right"],
            observations=observations,
            pack=pack,
            used=used,
            used_lookups=used_lookups,
        )

        if op == "COMPARE":
            if left.dimension != right.dimension:
                raise ValidationError("COMPARE operands have incompatible dimensions")
            operator = node["operator"]
            if operator == "EQ":
                result = left.value == right.value
            elif operator == "NE":
                result = left.value != right.value
            elif operator == "LT":
                result = left.value < right.value  # type: ignore[operator]
            elif operator == "LE":
                result = left.value <= right.value  # type: ignore[operator]
            elif operator == "GT":
                result = left.value > right.value  # type: ignore[operator]
            else:
                result = left.value >= right.value  # type: ignore[operator]
            value = _RuntimeValue(ScalarType.BOOLEAN, result, _DIMENSIONLESS)
            return value, _trace_node(
                op,
                value,
                operator=operator,
                left=left_trace,
                right=right_trace,
            )

        if not isinstance(left.value, Fraction) or not isinstance(right.value, Fraction):
            raise ValidationError(f"{op} operands must be numeric")
        result_type = _numeric_type(left.scalar_type, right.scalar_type, op)
        if op in {"ADD", "SUBTRACT"}:
            if left.dimension != right.dimension:
                raise ValidationError(f"{op} operands have incompatible dimensions")
            result_dimension = left.dimension
            result = left.value + right.value if op == "ADD" else left.value - right.value
        elif op == "MULTIPLY":
            result_dimension = _combine_dimensions(
                left.dimension, right.dimension, operation=op
            )
            result = left.value * right.value
        else:
            if right.value == 0:
                raise FormulaBlocked("DIVIDE_BY_ZERO")
            result_dimension = _combine_dimensions(
                left.dimension, right.dimension, operation=op
            )
            result = left.value / right.value
        value = _RuntimeValue(result_type, result, result_dimension)
        return value, _trace_node(op, value, left=left_trace, right=right_trace)

    if op == "IF":
        condition, condition_trace = _evaluate_node(
            node["condition"],
            observations=observations,
            pack=pack,
            used=used,
            used_lookups=used_lookups,
        )
        if condition.scalar_type is not ScalarType.BOOLEAN or not isinstance(condition.value, bool):
            raise ValidationError("IF condition must be BOOLEAN")
        branch_name = "then" if condition.value else "else"
        selected, selected_trace = _evaluate_node(
            node[branch_name],
            observations=observations,
            pack=pack,
            used=used,
            used_lookups=used_lookups,
        )
        return selected, _trace_node(
            op,
            selected,
            condition=condition_trace,
            selected=branch_name,
            branch=selected_trace,
        )

    if op in {"MIN", "MAX"}:
        evaluated = [
            _evaluate_node(
                item,
                observations=observations,
                pack=pack,
                used=used,
                used_lookups=used_lookups,
            )
            for item in node["args"]
        ]
        values = [item[0] for item in evaluated]
        first = values[0]
        if not isinstance(first.value, Fraction):
            raise ValidationError(f"{op} args must be numeric")
        if any(
            value.dimension != first.dimension or not isinstance(value.value, Fraction)
            for value in values[1:]
        ):
            raise ValidationError(f"{op} args have incompatible dimensions")
        selected = min(values, key=lambda item: item.value) if op == "MIN" else max(
            values, key=lambda item: item.value
        )
        result_type = (
            ScalarType.DECIMAL
            if any(value.scalar_type is ScalarType.DECIMAL for value in values)
            else ScalarType.INTEGER
        )
        result = _RuntimeValue(result_type, selected.value, selected.dimension)
        return result, _trace_node(op, result, args=[item[1] for item in evaluated])

    raise ValidationError(f"formula op {op!r} is not supported")


def _coerce_result(
    value: _RuntimeValue,
    *,
    expected_scalar_type: ScalarType,
    expected_uom: str | None,
    pack: CompiledDomainPack,
) -> TypedValue:
    numeric = {ScalarType.DECIMAL, ScalarType.INTEGER}
    if expected_scalar_type in numeric:
        if value.scalar_type not in numeric or not isinstance(value.value, Fraction):
            raise ValidationError("formula result is not numeric")
        if expected_uom is None:
            target_dimension = _DIMENSIONLESS
            converted = value.value
        else:
            target_dimension = _unit_dimension(pack, expected_uom)
            if value.dimension == _DIMENSIONLESS:
                converted = value.value
            else:
                if value.dimension != target_dimension:
                    raise ValidationError("formula result dimension is incompatible with output unit")
                converted = value.value / _unit_factor(pack, expected_uom)
        if value.dimension not in {target_dimension, _DIMENSIONLESS}:
            raise ValidationError("formula result dimension is incompatible with output unit")
        if expected_scalar_type is ScalarType.INTEGER:
            if converted.denominator != 1:
                raise FormulaBlocked("NON_INTEGRAL_RESULT")
            rendered: str | int | bool = converted.numerator
        else:
            rendered = _fraction_to_plain_decimal(converted)
        return TypedValue(expected_scalar_type, rendered, expected_uom)

    if value.scalar_type is not expected_scalar_type or value.dimension != _DIMENSIONLESS:
        raise ValidationError("formula result scalar type is incompatible with its declaration")
    if expected_uom is not None:
        raise ValidationError("non-numeric formula result cannot have a unit")
    if not isinstance(value.value, (str, bool)):
        raise ValidationError("formula result has an invalid value")
    return TypedValue(expected_scalar_type, value.value, None)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _formula_trace(
    *,
    status: EvaluationStatus,
    root: Mapping[str, object],
    missing_observations: tuple[str, ...] = (),
) -> FormulaTrace:
    plain_root = _plain_json(root)
    payload = {
        "status": status.value,
        "root": plain_root,
        "missing_observations": list(missing_observations),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    frozen_root = _deep_freeze(plain_root)
    assert isinstance(frozen_root, Mapping)
    return FormulaTrace(
        status=status,
        root=frozen_root,
        missing_observations=missing_observations,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def evaluate_formula(
    expression: Mapping[str, object],
    *,
    expected_scalar_type: ScalarType,
    expected_uom: str | None,
    observations: Mapping[str, TypedValue],
    pack: CompiledDomainPack,
) -> FormulaEvaluation:
    """Evaluate one formula into an exact ready or deterministically blocked result."""

    expected = _scalar_type(expected_scalar_type, "expected_scalar_type")
    validate_formula(
        expression,
        pack=pack,
        expected_scalar_type=expected,
        expected_uom=expected_uom,
    )
    used: list[str] = []
    used_lookups: list[str] = []
    try:
        runtime_value, root = _evaluate_node(
            expression,
            observations=observations,
            pack=pack,
            used=used,
            used_lookups=used_lookups,
        )
        typed = _coerce_result(
            runtime_value,
            expected_scalar_type=expected,
            expected_uom=expected_uom,
            pack=pack,
        )
    except FormulaBlocked as exc:
        blocked_root: Mapping[str, object] = {
            "op": expression.get("op", "UNKNOWN"),
            "blocked_reason": exc.reason,
            "used_lookups": [
                {"op": "LOOKUP", "lookup_code": code}
                for code in used_lookups
            ],
        }
        trace = _formula_trace(
            status=EvaluationStatus.BLOCKED,
            root=blocked_root,
            missing_observations=exc.missing_observations,
        )
        return FormulaEvaluation(
            status=EvaluationStatus.BLOCKED,
            value=None,
            trace=trace,
            observation_refs=tuple(used),
            blocked_reasons=(exc.reason,),
        )

    ready_root = {
        **root,
        "typed_result": _plain_json(
            {
                "scalar_type": typed.scalar_type.value,
                "value": typed.value,
                "uom": typed.uom,
            }
        ),
    }
    trace = _formula_trace(status=EvaluationStatus.READY, root=ready_root)
    return FormulaEvaluation(
        status=EvaluationStatus.READY,
        value=typed,
        trace=trace,
        observation_refs=tuple(used),
        blocked_reasons=(),
    )


__all__ = [
    "DECIMAL_PATTERN",
    "DIMENSION_VECTORS",
    "MAX_FORMULA_DEPTH",
    "MAX_FORMULA_NODES",
    "MAX_PACK_FORMULA_NODES",
    "FormulaBlocked",
    "canonical_decimal",
    "compile_literal",
    "convert_decimal",
    "evaluate_formula",
    "exact_decimal_divide",
    "exact_decimal_multiply",
    "formula_complexity",
    "validate_formula",
]
