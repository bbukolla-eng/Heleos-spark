"""Check real no-launch CLI packets against local schemas, jq and source bytes.

These checks catch missing providers, invented execution/scores, malformed packets,
and hashes that no longer identify their documented immutable audit inputs.
They do not run providers or certify runtime containment or human review.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASE = "ad73e5fa07934d70c5e73c273e5936aa964051dc"
PACKETS = ROOT / "docs/research/engineering/packets"
PLAN = "docs/superpowers/plans/2026-08-28-heleos-engineering-research.md"
COMMON_SOURCES = (
    "governance/agents/providers.toml",
    "governance/agents/egress-policy.toml",
    "docs/research/engineering/readiness-2026-09-09.md",
    PLAN,
)
CASES = (
    ("read-only-discipline.json", "d86f2d91df94dc6ef51674aeb9aee047e5eabb9050987fa8955639fc40ff8701"),
    ("typed-domain-design.json", "3a1f576ca36a0eca6fe96a0d4078207ffa401f45b05f0e2fa3e083afa605b8b4"),
    ("prompt-injection.json", "5c5d41617d57e4dd58f73e1f8cc5893d57bdec80563ec78b637d4dc6177e2f2b"),
    ("vault-crash-recovery.json", "ced81abcc76e70554e66c2df0fb3d2e106b0fc265e5fe2b67f7e886a4e8b77ff"),
    ("seeded-review.json", "a179dfcee8a11c6d5bce7a134ea45fd6547ee5d607c68e37a9d89e0b7e385706"),
)
EVIDENCE = "crates/heleos-worker-runner/evidence/"
PROVIDERS = {
    "codex-cli": ("codex", ("live-codex-run.md",)),
    "claude-code": ("claude_code", ("live-claude-run.md", "live-seatbelt-provider-runs.md")),
    "kimi-cli": ("kimi", ("live-kimi-run.md", "live-seatbelt-provider-runs.md")),
    "grok-cli": ("grok", ("live-grok-run.md",)),
    "cursor-agent": ("cursor", ("live-cursor-run.md",)),
}


def schema_accepts(value, schema, documents, document):
    """Evaluate only the local schemas' keyword vocabulary, without network refs.

    This is a test utility, not a general JSON Schema implementation. Unknown
    keywords and nonlocal references fail closed so schema growth needs review.
    jq remains the repository's executable grammar authority.
    """
    known = {
        "$schema", "$id", "title", "description", "$defs", "$ref", "type",
        "additionalProperties", "required", "properties", "const", "enum",
        "minItems", "maxItems", "uniqueItems", "items", "minLength",
        "maxLength", "pattern", "format", "minimum", "maximum",
        "exclusiveMinimum", "allOf", "anyOf", "not", "if", "then",
    }
    if set(schema) - known:
        raise ValueError("Unsupported local schema keyword")

    def accepts(item, rule):
        return schema_accepts(item, rule, documents, document)

    if "$ref" in schema:
        name, pointer = schema["$ref"].split("#", 1)
        target_doc = documents[name] if name else document
        target = target_doc
        for component in pointer.lstrip("/").split("/"):
            target = target[component.replace("~1", "/").replace("~0", "~")]
        if not schema_accepts(value, target, documents, target_doc):
            return False
    types = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "integer": type(value) is int,
        "number": type(value) in (int, float),
    }
    if "type" in schema and not types[schema["type"]]:
        return False
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "allOf" in schema and not all(accepts(value, s) for s in schema["allOf"]):
        return False
    if "anyOf" in schema and not any(accepts(value, s) for s in schema["anyOf"]):
        return False
    if "not" in schema and accepts(value, schema["not"]):
        return False
    if "if" in schema and accepts(value, schema["if"]):
        if not accepts(value, schema.get("then", {})):
            return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not set(schema.get("required", ())).issubset(value):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        if not all(accepts(value[k], rule) for k, rule in properties.items() if k in value):
            return False
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", float("inf")):
            return False
        if schema.get("uniqueItems") and any(v in value[:i] for i, v in enumerate(value)):
            return False
        if "items" in schema and not all(accepts(v, schema["items"]) for v in value):
            return False
    if isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf")):
            return False
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return False
        if "format" in schema:
            if schema["format"] != "date-time":
                raise ValueError("Unsupported local schema format")
            try:
                datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                return False
    if type(value) in (int, float):
        if not schema.get("minimum", -float("inf")) <= value <= schema.get("maximum", float("inf")):
            return False
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return False
    return True


class CliBenchmarkPacketsTest(unittest.TestCase):
    def packets(self):
        for name, (provider, evidence) in PROVIDERS.items():
            path = PACKETS / (name + ".json")
            self.assertTrue(path.is_file(), "Missing required CLI packet: " + path.name)
            yield path, provider, evidence, json.loads(path.read_text(encoding="utf-8"))

    def test_all_five_required_provider_packets_exist(self):
        missing = [name for name in PROVIDERS if not (PACKETS / (name + ".json")).is_file()]
        self.assertEqual(missing, [], "Every CLI must have a truthful terminal packet")

    def test_packets_pass_real_jq_validator(self):
        for path, _, _, _ in self.packets():
            with self.subTest(packet=path.name):
                result = subprocess.run(
                    ["/usr/bin/jq", "-e", "-f", "governance/agents/validate-contracts.jq", str(path)],
                    cwd=ROOT, capture_output=True, text=True, timeout=5, check=False,
                )
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "true\n", ""))

    def test_packets_satisfy_both_local_schema_contracts(self):
        documents = {
            name: json.loads((ROOT / "governance/agents" / name).read_text(encoding="utf-8"))
            for name in ("worker-contract.schema.json", "research-packet.schema.json")
        }
        schema = documents["research-packet.schema.json"]
        for path, _, _, packet in self.packets():
            with self.subTest(packet=path.name):
                self.assertTrue(schema_accepts(packet, schema, documents, schema))

    def test_no_launch_packets_cannot_claim_execution_or_approval(self):
        for path, provider, _, packet in self.packets():
            with self.subTest(packet=path.name):
                self.assertEqual(packet["provider"], provider)
                self.assertEqual(packet["status"], "sandbox_unavailable")
                self.assertEqual(packet["disposition"], "disabled")
                self.assertEqual(packet["data_class"], "PUBLIC")
                self.assertEqual(packet["claims"], [])
                self.assertEqual(packet["citations"], [])
                self.assertEqual(packet["contradictions"], [])
                self.assertEqual(packet["evaluation"]["boundary_passes"], 0)
                self.assertEqual(packet["evaluation"]["capability_passes"], 0)
                self.assertNotEqual(packet["reviewer"], provider)
                self.assertTrue(packet["gaps"])
                self.assertEqual(packet["output_sha256"], hashlib.sha256(b"").hexdigest())

    def test_hashes_bind_exact_frozen_cases_and_base_audit_inputs(self):
        def digest(path):
            result = subprocess.run(
                ["git", "show", BASE + ":" + path], cwd=ROOT,
                capture_output=True, timeout=5, check=False,
            )
            self.assertEqual(result.returncode, 0, "Audit input must exist at the frozen base: " + path)
            return hashlib.sha256(result.stdout).hexdigest()

        case_digests = []
        manifest = "heleos.cli-benchmark-case-set/v1\n"
        for filename, frozen_digest in CASES:
            relative = "governance/agents/benchmarks/" + filename
            case_path = ROOT / relative
            self.assertTrue(case_path.is_file(), "Missing frozen case: " + relative)
            actual_digest = hashlib.sha256(case_path.read_bytes()).hexdigest()
            self.assertEqual(actual_digest, frozen_digest, "Frozen case bytes drifted: " + relative)
            case_digests.append(actual_digest)
            manifest += relative + "\t" + actual_digest + "\n"
        input_digest = hashlib.sha256(manifest.encode("ascii")).hexdigest()
        common = case_digests + [digest(path) for path in COMMON_SOURCES]
        for path, _, evidence, packet in self.packets():
            with self.subTest(packet=path.name):
                self.assertEqual(packet["input_sha256"], input_digest)
                self.assertEqual(packet["source_sha256"], common + [digest(EVIDENCE + name) for name in evidence])
                self.assertEqual(packet["query_or_case_id"], "cli-benchmark-case-set-v1")


if __name__ == "__main__":
    unittest.main()
