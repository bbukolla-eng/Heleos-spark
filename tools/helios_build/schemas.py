"""Offline JSON Schema validation for frozen Build Fabric contracts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urldefrag, urljoin

from jsonschema import Draft202012Validator, FormatChecker, RefResolver
from jsonschema.exceptions import SchemaError

from tools.helios_build.canonical import load_strict_json, reject_floats_and_invalid_values
from tools.helios_build.errors import ContractError
from tools.helios_build.types import JsonValue


_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("rfc3339-utc")
def _is_rfc3339_utc(value: object) -> bool:
    """Validate a real RFC 3339 UTC timestamp, not only its shape."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return False
    return True


class SchemaRegistry:
    """Validate only schemas committed under ``build_control/schemas``."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.schemas_root = self.repo_root / "build_control" / "schemas"
        if not self.schemas_root.is_dir():
            raise ContractError(f"schema directory does not exist: {self.schemas_root}")
        self._schemas: dict[str, dict[str, object]] = {}
        self._schemas_by_id: dict[str, dict[str, object]] = {}
        for schema_path in sorted(self.schemas_root.glob("*.schema.json")):
            schema = load_strict_json(schema_path)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as error:
                raise ContractError(f"invalid schema {schema_path.name}: {error.message}") from error
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str) or not schema_id.startswith(
                "https://helios.local/build-control/schemas/"
            ):
                raise ContractError(f"schema {schema_path.name} has an invalid local $id")
            if schema_id in self._schemas_by_id:
                raise ContractError(f"duplicate schema $id: {schema_id}")
            self._schemas[schema_path.name] = schema
            self._schemas_by_id[schema_id] = schema
        if not self._schemas:
            raise ContractError("no Build Fabric schemas were found")
        for schema_id, schema in self._schemas_by_id.items():
            self._validate_local_references(schema, schema_id)

    def validate(self, payload: JsonValue, schema_name: str) -> None:
        """Validate a strict in-memory JSON value against one local schema."""
        if Path(schema_name).name != schema_name or schema_name not in self._schemas:
            raise ContractError(f"unknown local schema: {schema_name}")
        reject_floats_and_invalid_values(payload)
        schema = self._schemas[schema_name]

        def reject_remote(uri: str) -> object:
            raise ContractError(f"network schema retrieval is forbidden: {uri}")

        resolver = RefResolver.from_schema(
            schema,
            store=self._schemas_by_id,
            handlers={"http": reject_remote, "https": reject_remote},
        )
        validator = Draft202012Validator(
            schema,
            resolver=resolver,
            format_checker=_FORMAT_CHECKER,
        )
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.absolute_path) or "$"
            raise ContractError(f"{schema_name} invalid at {location}: {error.message}")
        self._validate_semantics(payload, schema_name)

    @staticmethod
    def _validate_semantics(payload: JsonValue, schema_name: str) -> None:
        if schema_name != "task-manifest-v1.schema.json":
            return
        assert isinstance(payload, dict)
        roles = payload["roles"]
        assert isinstance(roles, dict)
        if roles["builder_profile_id"] == roles["reviewer_profile_id"]:
            raise ContractError("builder_profile_id must differ from reviewer_profile_id")

    def _validate_local_references(self, value: object, base_uri: str) -> None:
        if isinstance(value, list):
            for item in value:
                self._validate_local_references(item, base_uri)
            return
        if not isinstance(value, dict):
            return
        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str):
                raise ContractError("schema $ref must be a string")
            target_uri, _ = urldefrag(urljoin(base_uri, reference))
            if target_uri not in self._schemas_by_id:
                raise ContractError(
                    f"schema reference is not a local Build Fabric schema: {reference}"
                )
        for item in value.values():
            self._validate_local_references(item, base_uri)
