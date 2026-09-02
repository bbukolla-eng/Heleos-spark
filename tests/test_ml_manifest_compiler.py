"""Contract tests for immutable, canonical ML registry documents."""

from __future__ import annotations

import unittest

from helios_takeoff_core.errors import ValidationError
from helios_takeoff_core.ml.canonical import (
    canonicalize_receipt,
    compile_benchmark_spec,
    compile_dataset_manifest,
    compile_model_manifest,
)


def _sha(character: str) -> str:
    return character * 64


def valid_model_manifest() -> dict[str, object]:
    revision = "a" * 40
    return {
        "protocol": "helios.ml.model-manifest/v1",
        "model_code": "TITLE_BLOCK_OCR",
        "manifest_version": "1.0.0",
        "provider": "HUGGING_FACE",
        "repository": "example.invalid/acme/title-block-ocr",
        "revision": revision,
        "source_url": f"https://models.example.invalid/acme/title-block-ocr/commit/{revision}",
        "task": "TITLE_BLOCK_TEXT_REGION_OCR",
        "intended_use": "Read title-block text from admitted benchmark images.",
        "model_card_sha256": _sha("b"),
        "artifacts": [
            {
                "logical_name": "config.json",
                "source_url": "https://models.example.invalid/acme/title-block-ocr/config.json",
                "sha256": _sha("c"),
                "byte_size": 12,
                "media_type": "application/json",
                "serialization": "JSON",
            },
            {
                "logical_name": "weights.safetensors",
                "source_url": "https://models.example.invalid/acme/title-block-ocr/weights.safetensors",
                "sha256": _sha("d"),
                "byte_size": 24,
                "media_type": "application/octet-stream",
                "serialization": "SAFETENSORS",
            },
        ],
        "rights": [
            {
                "right": right,
                "verdict": "PERMITTED",
                "source_url": "https://rights.example.invalid/model-license",
                "reviewed_by": "reviewer@example.invalid",
                "reviewed_at": "2026-09-01T00:00:00Z",
                "basis": "Fictional test-only rights finding.",
            }
            for right in (
                "TRAINING",
                "COPYING",
                "ACCESS",
                "COMMERCIAL_USE",
                "REDISTRIBUTION",
            )
        ],
        "runtime": {
            "dependency_lock_sha256": _sha("e"),
            "adapter_name": "helios-local-ocr",
            "adapter_revision": "adapter-v1",
            "python_version": "3.12.0",
            "framework_versions": {"transformers": "0.0-test", "torch": "0.0-test"},
            "device_class": "APPLE_SILICON_MPS",
            "precision": "float16",
            "trust_remote_code": False,
        },
        "memory_requirements": {"minimum_bytes": 1, "recommended_bytes": 2},
        "hardware_requirements": {"accelerator": "MPS", "minimum_unified_memory_bytes": 2},
        "input_schema": {
            "type": "object",
            "properties": {"image_sha256": {"type": "string"}},
            "required": ["image_sha256"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "permitted_data_classes": ["PUBLIC_MECHANICAL_DRAWING", "SYNTHETIC"],
        "benchmark_bindings": [_sha("f")],
        "promotion": {
            "state": "CANDIDATE",
            "supersedes_manifest_sha256": None,
            "benchmark_receipt_sha256s": [],
            "rollback_manifest_sha256": None,
            "failure_set_review_sha256": None,
        },
    }


def valid_dataset_manifest() -> dict[str, object]:
    return {
        "protocol": "helios.ml.dataset-manifest/v1",
        "dataset_code": "TITLE_BLOCK_EVAL",
        "manifest_version": "1.0.0",
        "provider": "HUGGING_FACE",
        "dataset": "example.invalid/acme/title-block-eval",
        "revision": "1" * 40,
        "source_url": "https://datasets.example.invalid/acme/title-block-eval",
        "purpose": "EVALUATION",
        "intended_use": "Evaluate title-block OCR only.",
        "artifacts": [
            {
                "logical_name": "index.jsonl",
                "source_url": "https://datasets.example.invalid/acme/title-block-eval/index.jsonl",
                "sha256": _sha("1"),
                "byte_size": 10,
                "media_type": "application/x-ndjson",
                "serialization": "JSONL",
            }
        ],
        "rights": [
            {
                "right": right,
                "verdict": "PERMITTED",
                "source_url": "https://rights.example.invalid/dataset-license",
                "reviewed_by": "reviewer@example.invalid",
                "reviewed_at": "2026-09-01T00:00:00Z",
                "basis": "Fictional test-only rights finding.",
            }
            for right in (
                "ACCESS", "COPYING", "REDISTRIBUTION", "COMMERCIAL_USE", "TRAINING"
            )
        ],
        "provenance": {
            "original_collection_method": "fictional synthetic fixture",
            "coverage": "mechanical title blocks",
            "private_or_proprietary_risk": "NONE",
        },
        "permitted_data_classes": ["SYNTHETIC", "PUBLIC_MECHANICAL_DRAWING"],
    }


def valid_benchmark_spec() -> dict[str, object]:
    return {
        "protocol": "helios.ml.benchmark-spec/v1",
        "benchmark_code": "HELIOS_TITLE_BLOCK_OCR_V1",
        "version": "1.0.0",
        "task": "TITLE_BLOCK_TEXT_REGION_OCR",
        "dataset_manifest_sha256": _sha("2"),
        "model_manifest_sha256": _sha("3"),
        "input_artifact_sha256": _sha("4"),
        "metrics": [
            {"name": "WER", "direction": "MAXIMUM"},
            {"name": "CER", "direction": "MAXIMUM"},
        ],
        "thresholds": {"CER": "0.08", "WER": "0.20"},
        "confidence_threshold": "0.90",
        "warmup_count": 0,
        "measurement_repetitions": 1,
        "failure_set_review_required": True,
    }


class MlManifestCompilerTests(unittest.TestCase):
    def test_model_manifest_is_canonical_across_object_order(self) -> None:
        first = compile_model_manifest(valid_model_manifest())
        reordered = dict(reversed(list(valid_model_manifest().items())))
        second = compile_model_manifest(reordered)
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first.sha256, second.sha256)

    def test_model_manifest_rejects_mutable_revision_and_remote_code(self) -> None:
        mutable = valid_model_manifest()
        mutable["revision"] = "main"
        with self.assertRaisesRegex(ValidationError, "immutable lowercase hexadecimal commit"):
            compile_model_manifest(mutable)

        remote_code = valid_model_manifest()
        remote_code["runtime"]["trust_remote_code"] = True  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "trust_remote_code must be false"):
            compile_model_manifest(remote_code)

    def test_promoted_manifest_requires_passed_benchmark_and_rollback(self) -> None:
        document = valid_model_manifest()
        document["promotion"] = {
            "state": "PROMOTED",
            "supersedes_manifest_sha256": _sha("a"),
            "benchmark_receipt_sha256s": [],
            "rollback_manifest_sha256": None,
            "failure_set_review_sha256": None,
        }
        with self.assertRaisesRegex(ValidationError, "PROMOTED model"):
            compile_model_manifest(document)

    def test_compilers_reject_unknown_fields_and_non_bool_integer_sizes(self) -> None:
        unknown = valid_model_manifest()
        unknown["download_command"] = "never"
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            compile_model_manifest(unknown)

        bad_size = valid_dataset_manifest()
        bad_size["artifacts"][0]["byte_size"] = True  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "byte_size"):
            compile_dataset_manifest(bad_size)

    def test_semantic_sets_sort_and_reject_duplicates(self) -> None:
        first = compile_model_manifest(valid_model_manifest())
        reordered = valid_model_manifest()
        reordered["rights"] = list(reversed(reordered["rights"]))  # type: ignore[arg-type]
        reordered["artifacts"] = list(reversed(reordered["artifacts"]))  # type: ignore[arg-type]
        reordered["permitted_data_classes"] = list(reversed(reordered["permitted_data_classes"]))  # type: ignore[arg-type]
        self.assertEqual(first.canonical_bytes, compile_model_manifest(reordered).canonical_bytes)

        duplicate = valid_model_manifest()
        duplicate["permitted_data_classes"] = ["SYNTHETIC", "SYNTHETIC"]
        with self.assertRaisesRegex(ValidationError, "duplicates"):
            compile_model_manifest(duplicate)

    def test_schema_rejects_executable_path_callback_import_environment_provider_and_credentials(self) -> None:
        for forbidden in (
            "executable", "model_path", "callback", "import_module", "environment",
            "provider_command", "credential",
        ):
            with self.subTest(forbidden=forbidden):
                document = valid_model_manifest()
                document["input_schema"][forbidden] = "unsafe"  # type: ignore[index]
                with self.assertRaisesRegex(ValidationError, "forbidden"):
                    compile_model_manifest(document)

    def test_schema_rejects_forbidden_field_name_bypasses(self) -> None:
        for forbidden in ("providerCommand", "provider command", "provider-command", "modelPath"):
            with self.subTest(forbidden=forbidden):
                document = valid_model_manifest()
                document["output_schema"][forbidden] = "unsafe"  # type: ignore[index]
                with self.assertRaisesRegex(ValidationError, "forbidden"):
                    compile_model_manifest(document)

    def test_runtime_rejects_duplicate_normalized_framework_names(self) -> None:
        document = valid_model_manifest()
        document["runtime"]["framework_versions"] = {  # type: ignore[index]
            "torch": "0.0-test",
            " torch ": "0.0-other",
        }
        with self.assertRaisesRegex(ValidationError, "duplicate framework"):
            compile_model_manifest(document)

    def test_dataset_compiler_is_canonical_and_requires_all_five_rights(self) -> None:
        first = compile_dataset_manifest(valid_dataset_manifest())
        reordered = dict(reversed(list(valid_dataset_manifest().items())))
        self.assertEqual(first.canonical_bytes, compile_dataset_manifest(reordered).canonical_bytes)

        missing_right = valid_dataset_manifest()
        missing_right["rights"] = missing_right["rights"][:-1]  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "five rights"):
            compile_dataset_manifest(missing_right)

    def test_benchmark_uses_decimal_strings_and_sorts_metric_declarations(self) -> None:
        first = compile_benchmark_spec(valid_benchmark_spec())
        reordered = valid_benchmark_spec()
        reordered["metrics"] = list(reversed(reordered["metrics"]))  # type: ignore[arg-type]
        self.assertEqual(first.canonical_bytes, compile_benchmark_spec(reordered).canonical_bytes)

        floating = valid_benchmark_spec()
        floating["thresholds"]["CER"] = 0.08  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "decimal string"):
            compile_benchmark_spec(floating)

        exponent = valid_benchmark_spec()
        exponent["confidence_threshold"] = "9e-1"
        with self.assertRaisesRegex(ValidationError, "decimal string"):
            compile_benchmark_spec(exponent)

    def test_receipt_canonicalization_rejects_floats_and_sorts_evidence_references(self) -> None:
        first = canonicalize_receipt({
            "protocol": "helios.ml.artifact-receipt/v1",
            "status": "PASSED",
            "evidence_refs": [_sha("6"), _sha("5")],
        })
        second = canonicalize_receipt({
            "status": "PASSED",
            "evidence_refs": [_sha("5"), _sha("6")],
            "protocol": "helios.ml.artifact-receipt/v1",
        })
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        with self.assertRaisesRegex(ValidationError, "binary floats"):
            canonicalize_receipt({"protocol": "helios.ml.artifact-receipt/v1", "value": 1.0})

    def test_receipt_canonicalization_requires_the_common_closed_envelope(self) -> None:
        for field in ("credential", "target_host_eligible"):
            with self.subTest(field=field):
                receipt = {
                    "protocol": "helios.ml.artifact-receipt/v1",
                    "status": "PASSED",
                    "evidence_refs": [],
                    field: "unsafe",
                }
                with self.assertRaisesRegex(ValidationError, "unknown fields"):
                    canonicalize_receipt(receipt)

        with self.assertRaisesRegex(ValidationError, "receipt status is unsupported"):
            canonicalize_receipt({
                "protocol": "helios.ml.artifact-receipt/v1",
                "status": "UNKNOWN",
                "evidence_refs": [],
            })


if __name__ == "__main__":
    unittest.main()
