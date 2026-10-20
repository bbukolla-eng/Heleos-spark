"""Offline black-box checks for readiness records; never execute recorded providers.

Python 3.9 uses the fixed local Python 3.14 TOML parser, not a manifest command.
The --check mode validates an explicitly supplied record root for mutation tests.
"""

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROVIDERS = {"codex", "claude_code", "kimi", "grok", "cursor", "notebook_lm", "grok_bots"}
AUTHORITIES = {"production", "push", "merge", "approval", "credential", "repository_write", "launch"}
REQUIREMENTS = {"provider", "purpose", "source_sha256", "endpoint_allowlist", "approved_contract_sha256", "quarantine_destination", "policy_decision"}
PROVIDER_FILE = "governance/agents/providers.toml"
POLICY_FILE = "governance/agents/egress-policy.toml"
DOCS = ("docs/research/engineering/readiness-2026-09-09.md", "docs/research/engineering/ecc-inventory.md")


def require(value):
    if not value:
        raise ValueError("invalid_readiness")


def parse(raw):
    try:
        import tomllib
    except ImportError:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3", "-I", "-B", "-c",
             "import json,sys,tomllib; print(json.dumps(tomllib.loads(sys.stdin.read())))"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        require(result.returncode == 0)
        return json.loads(result.stdout)
    return tomllib.loads(raw.decode("utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def exact(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def digest(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value))


def denied(value):
    exact(value, AUTHORITIES)
    require(all(flag is False for flag in value.values()))


def check(root):
    raw = (root / PROVIDER_FILE).read_bytes()
    policy_raw = (root / POLICY_FILE).read_bytes()
    require(len(raw) <= 65536 and len(policy_raw) <= 65536)
    records, policy = parse(raw), parse(policy_raw)
    exact(records, {"schema_version", "status", "observed_date", "base_commit", "authority", "provider", "validator"})
    require(type(records["schema_version"]) is int and records["schema_version"] == 1)
    require(records["status"] == "observation_only" and records["observed_date"] == "2026-09-09")
    require(records["base_commit"] == "485b54e9b0915b81e564425e9b7981c5ea1e0590")
    denied(records["authority"])
    entries = records["provider"]
    require(type(entries) is list and len(entries) == 7)
    require({p["id"] for p in entries} == PROVIDERS)
    common = {"id", "kind", "installed", "executable_path", "resolved_path", "executable_sha256", "version", "version_evidence", "authentication", "authentication_probe", "origin", "license_or_rights", "update_channel", "environment_exposure", "containment", "egress", "status"}
    for provider in entries:
        exact(provider, common)
        require(all(type(v) is str and bool(v.strip()) for k, v in provider.items() if k != "installed"))
        require(type(provider["installed"]) is bool)
        require(provider["status"] == "not_admitted")
        require(provider["authentication"] == "unverified")
        require(provider["authentication_probe"] == "not_run_no_safe_status_probe_established")
        require(provider["egress"] == "not_admitted")
        require(provider["containment"] in {"not_admitted", "combined_auth_runtime_root"})
        require(provider["environment_exposure"] == "unverified_not_inspected")
        if provider["id"] in {"notebook_lm", "grok_bots"}:
            require(provider["kind"] == "browser_research_only" and provider["installed"] is False)
            require(all(provider[k] == "unverified" for k in ("executable_path", "resolved_path", "executable_sha256", "version")))
        else:
            require(provider["kind"] == "cli")
            if provider["installed"]:
                for key in ("executable_path", "resolved_path"):
                    require(Path(provider[key]).is_absolute() and ".." not in Path(provider[key]).parts)
                digest(provider["executable_sha256"])
            else:
                require(all(provider[k] == "unavailable" for k in ("executable_path", "resolved_path", "executable_sha256", "version")))
    validators = records["validator"]
    require(type(validators) is list and len(validators) == 3)
    require({v["id"] for v in validators} == {"python3", "system_python3", "jq"})
    for validator in validators:
        exact(validator, {"id", "executable_path", "resolved_path", "executable_sha256", "version", "origin", "license_or_rights", "tomllib"})
        require(all(type(v) is str and v.strip() for v in validator.values()))
        expected = {"python3": "/opt/homebrew/bin/python3", "system_python3": "/usr/bin/python3", "jq": "/usr/bin/jq"}
        require(validator["executable_path"] == expected[validator["id"]])
        require(Path(validator["resolved_path"]).is_absolute())
        digest(validator["executable_sha256"])
        require(validator["tomllib"] == {"python3": "available", "system_python3": "unavailable", "jq": "not_applicable"}[validator["id"]])
    exact(policy, {"schema_version", "status", "decision", "provider_registry_sha256", "provider_ids", "public_requirements", "approved_contracts", "authority", "rule"})
    require(type(policy["schema_version"]) is int and policy["schema_version"] == 1)
    require(policy["status"] == "declarative_policy_only" and policy["decision"] == "deny_until_exact_approval")
    require(policy["provider_registry_sha256"] == sha(raw))
    require(type(policy["provider_ids"]) is list and len(policy["provider_ids"]) == 7 and set(policy["provider_ids"]) == PROVIDERS)
    require(type(policy["public_requirements"]) is list and len(policy["public_requirements"]) == 7 and set(policy["public_requirements"]) == REQUIREMENTS)
    require(policy["approved_contracts"] == [])
    denied(policy["authority"])
    rules = policy["rule"]
    require(type(rules) is list and len(rules) == 4)
    require({r["data_class"] for r in rules} == {"PUBLIC", "SECRET", "INTERNAL", "PROJECT_CONFIDENTIAL"})
    for rule in rules:
        exact(rule, {"data_class", "decision"})
        require(rule["decision"] == ("require_exact_approval" if rule["data_class"] == "PUBLIC" else "deny"))
    for doc in DOCS:
        body = (root / doc).read_text(encoding="utf-8")
        blocks = re.findall(r"<!-- readiness-identities:v1\n(.*?)\n-->", body, re.S)
        require(len(blocks) == 1)
        identities = json.loads(blocks[0])
        exact(identities, {"providers_sha256", "egress_policy_sha256", "github_apps_sha256"})
        require(identities == {"providers_sha256": sha(raw), "egress_policy_sha256": sha(policy_raw), "github_apps_sha256": sha((root / "governance/github-apps.toml").read_bytes())})


def encode_toml(record):
    lines = []
    for key, value in record.items():
        if not isinstance(value, dict) and not (isinstance(value, list) and value and isinstance(value[0], dict)):
            lines.append(key + " = " + json.dumps(value))
    for key, value in record.items():
        if isinstance(value, dict):
            lines.append("[" + key + "]")
            lines.extend(k + " = " + json.dumps(v) for k, v in value.items())
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for entry in value:
                lines.append("[[" + key + "]]")
                lines.extend(k + " = " + json.dumps(v) for k, v in entry.items())
    return ("\n".join(lines) + "\n").encode()


class ProviderReadinessTests(unittest.TestCase):
    def setUp(self):
        for relative in (PROVIDER_FILE, POLICY_FILE, *DOCS):
            self.assertTrue((ROOT / relative).is_file(), "missing readiness record: " + relative)

    def probe(self, root):
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--check", str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(result.stderr, b"")
        return result.returncode, result.stdout

    def mutated(self, transform):
        with tempfile.TemporaryDirectory(prefix="heleos-readiness-test-") as scratch:
            root = Path(scratch)
            for relative in (PROVIDER_FILE, POLICY_FILE, *DOCS, "governance/github-apps.toml"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / relative).read_bytes())
            providers = copy.deepcopy(parse((root / PROVIDER_FILE).read_bytes()))
            policy = copy.deepcopy(parse((root / POLICY_FILE).read_bytes()))
            transform(providers, policy)
            provider_raw = encode_toml(providers)
            policy["provider_registry_sha256"] = sha(provider_raw)
            policy_raw = encode_toml(policy)
            (root / PROVIDER_FILE).write_bytes(provider_raw)
            (root / POLICY_FILE).write_bytes(policy_raw)
            identities = {"providers_sha256": sha(provider_raw), "egress_policy_sha256": sha(policy_raw), "github_apps_sha256": sha((root / "governance/github-apps.toml").read_bytes())}
            for doc in DOCS:
                path = root / doc
                path.write_text(re.sub(r"<!-- readiness-identities:v1\n.*?\n-->", "<!-- readiness-identities:v1\n" + json.dumps(identities) + "\n-->", path.read_text(), flags=re.S))
            return self.probe(root)

    def test_records_validate_deterministically(self):
        self.assertEqual(self.probe(ROOT), (0, b"READINESS_VALID_OBSERVATION_ONLY\n"))
        self.assertEqual(self.probe(ROOT), self.probe(ROOT))

    def test_missing_provider_is_rejected(self):
        self.assertEqual(self.mutated(lambda p, e: p["provider"].pop())[0], 1)

    def test_unknown_keys_are_rejected_at_every_table(self):
        for target in (lambda p,e:p, lambda p,e:p["provider"][0], lambda p,e:p["validator"][0], lambda p,e:p["authority"], lambda p,e:e, lambda p,e:e["rule"][0], lambda p,e:e["authority"]):
            self.assertEqual(self.mutated(lambda p,e: target(p,e).update(unknown=True))[0], 1)

    def test_private_classes_cannot_gain_permission(self):
        for data_class in ("SECRET", "INTERNAL", "PROJECT_CONFIDENTIAL"):
            for decision in ("allow", "require_exact_approval"):
                def mutate(p,e):
                    next(r for r in e["rule"] if r["data_class"] == data_class)["decision"] = decision
                self.assertEqual(self.mutated(mutate)[0], 1)

    def test_installed_cli_requires_digest(self):
        for invalid in ("", "unverified", "a" * 63, "z" * 64):
            self.assertEqual(self.mutated(lambda p,e:p["provider"][0].update(executable_sha256=invalid))[0], 1)

    def test_no_authority_can_be_granted(self):
        for authority in AUTHORITIES:
            for side in ("providers", "egress"):
                self.assertEqual(self.mutated(lambda p,e:(p if side == "providers" else e)["authority"].update({authority: True}))[0], 1)

    def test_public_requires_every_exact_match_and_no_standing_approval(self):
        for field in REQUIREMENTS:
            self.assertEqual(self.mutated(lambda p,e:e["public_requirements"].remove(field))[0], 1)
        self.assertEqual(self.mutated(lambda p,e:e.update(approved_contracts=["blanket"]))[0], 1)

    def test_unadmitted_provider_cannot_claim_ready_or_browser_cli(self):
        self.assertEqual(self.mutated(lambda p,e:p["provider"][0].update(status="ready"))[0], 1)
        self.assertEqual(self.mutated(lambda p,e:p["provider"][-1].update(kind="cli"))[0], 1)

    def test_cross_file_identity_drift_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="heleos-readiness-drift-") as scratch:
            root = Path(scratch)
            for relative in (PROVIDER_FILE, POLICY_FILE, *DOCS, "governance/github-apps.toml"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / relative).read_bytes())
            with (root / PROVIDER_FILE).open("ab") as stream:
                stream.write(b"\n# changed observation\n")
            self.assertEqual(self.probe(root), (1, b"READINESS_REJECTED\n"))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        try:
            check(Path(sys.argv[2]))
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
            print("READINESS_REJECTED")
            sys.exit(1)
        print("READINESS_VALID_OBSERVATION_ONLY")
    else:
        unittest.main()
