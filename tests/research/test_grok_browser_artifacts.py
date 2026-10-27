"""Offline artifact checks; these never execute or score a browser provider."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
CASE = "governance/agents/benchmarks/browser-research.json"
PACKET = "docs/research/engineering/packets/grok-browser.json"
MANIFEST = "docs/research/engineering/sources/grok-platform.toml"
DOCS = ["docs/research/engineering/" + name for name in (
    "grok-platform-discovery.md", "grok-bot-contract.md", "grok-bot-evaluation.md")]


class GrokBrowserArtifactsTest(unittest.TestCase):
    def raw(self, path):
        self.assertTrue((ROOT / path).is_file(), "Missing Task 9 artifact: " + path)
        return (ROOT / path).read_bytes()

    def record(self, path):
        return json.loads(self.raw(path))

    def verdict(self, packet):
        return subprocess.run(
            ["/usr/bin/jq", "-e", "-f", "governance/agents/validate-contracts.jq"],
            input=json.dumps(packet), text=True, capture_output=True,
            cwd=ROOT, timeout=10, check=False)

    def test_complete_task_9_inventory(self):
        for path in [CASE, PACKET, MANIFEST] + DOCS:
            with self.subTest(path=path):
                self.assertGreater(len(self.raw(path)), 0)

    def test_unavailable_packet_has_no_invented_provider_evidence(self):
        packet = self.record(PACKET)
        self.assertEqual(packet["provider"], "grok_bots")
        self.assertEqual(packet["status"], "unavailable")
        self.assertEqual(packet["disposition"], "disabled")
        self.assertEqual(packet["claims"], [])
        self.assertEqual(packet["citations"], [])
        self.assertEqual(packet["evaluation"]["boundary_passes"], 0)
        self.assertEqual(packet["evaluation"]["capability_passes"], 0)
        self.assertEqual(packet["output_sha256"], hashlib.sha256(b"").hexdigest())
        result = self.verdict(packet)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "true\n", ""))

    def test_packet_binds_exact_public_case_bytes_and_budgets(self):
        packet, case = self.record(PACKET), self.record(CASE)
        digest = hashlib.sha256(self.raw(CASE)).hexdigest()
        self.assertEqual(packet["input_sha256"], digest)
        self.assertEqual(packet["source_sha256"], [digest])
        self.assertEqual(packet["query_or_case_id"], case["case_id"])
        self.assertEqual(packet["budgets"], case["budgets"])
        self.assertEqual(packet["data_class"], case["data_class"])

    def test_case_limits_public_input_and_denies_injected_actions(self):
        case = self.record(CASE)
        self.assertEqual(case["data_class"], "PUBLIC")
        self.assertEqual(case["origin"], "repository_authored_public_synthetic")
        self.assertEqual(case["execution_status"], "not_run")
        self.assertEqual(case["allowed_actions"], ["read_supplied_sources", "synthesize_cited_response"])
        self.assertEqual(case["allowed_network_endpoints"], [])
        denied = set(case["denied_actions"])
        self.assertTrue({"request_secrets", "read_private_files", "connect_accounts", "publish",
                         "send_messages", "run_code", "change_settings", "download_files"} <= denied)
        self.assertTrue(set(case["injection"]["requested_actions"]) <= denied)
        self.assertEqual(case["injection"]["trust"], "untrusted_source_data")
        self.assertGreater(len(case["injection"]["text"]), 40)
        self.assertEqual({row["id"] for row in case["boundary_assertions"]},
                         {"refuse_injection", "source_boundary", "no_side_effects"})
        self.assertEqual({row["id"] for row in case["citation_assertions"]},
                         {"resolvable_locators", "fact_vs_inference"})
        sources = {row["id"]: row for row in case["sources"]}
        self.assertEqual(set(sources), {"synthetic-alpha", "synthetic-beta"})
        for row in case["expected_facts"]:
            self.assertIn(row["locator"], sources[row["source_id"]]["sections"])
            self.assertEqual(row["value"], sources[row["source_id"]]["sections"][row["locator"]])
        for key, maximum in [("wall_seconds", 300), ("actions", 10),
                             ("output_bytes", 4096), ("cost_usd", 1)]:
            self.assertGreater(case["budgets"][key], 0)
            self.assertLessEqual(case["budgets"][key], maximum)

    def test_real_validator_rejects_scoring_or_claims_for_unrun_case(self):
        packet = self.record(PACKET)
        for field, value in [("claims", ["Unsupported result"]),
                             ("disposition", "research_only"), ("input_sha256", "missing")]:
            altered = copy.deepcopy(packet)
            altered[field] = value
            with self.subTest(field=field):
                self.assertNotEqual(self.verdict(altered).returncode, 0)
        for field in ("boundary_passes", "capability_passes"):
            altered = copy.deepcopy(packet)
            altered["evaluation"][field] = 1
            with self.subTest(field=field):
                self.assertNotEqual(self.verdict(altered).returncode, 0)

    def test_official_source_manifest_passes_without_admitting_authority(self):
        self.raw(MANIFEST)
        result = subprocess.run(
            ["python3", "-B", "governance/agents/validate-sources.py", MANIFEST],
            cwd=ROOT, text=True, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertGreaterEqual(report["source_count"], 4)
        self.assertEqual(report["manifest_sha256"], [hashlib.sha256(self.raw(MANIFEST)).hexdigest()])
        for key in ("production_authority", "source_bytes_verified", "citations_verified", "rights_verified"):
            self.assertIs(report[key], False)


if __name__ == "__main__":
    unittest.main()
