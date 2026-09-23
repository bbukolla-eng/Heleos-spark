"""Black-box contract checks: removing a boundary must admit a hostile fixture."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKER = {
    "schema": "heleos.research-worker-contract/v1",
    "task_id": "research-case-001",
    "provider": "codex",
    "executable_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "snapshot_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "data_class": "PUBLIC",
    "allowed_inputs": [{"id": "public-case-001", "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}],
    "allowed_tools": ["read_public_source", "write_quarantine"],
    "allowed_endpoints": ["https://example.org"],
    "forbidden_paths": ["host", "private", "production", "credentials"],
    "forbidden_actions": ["self_approve", "merge", "push", "publish", "read_credentials", "write_production"],
    "acceptance_commands": [{"tool": "public-contract-check", "argv": ["tests/research/test_research_contracts.py"], "expected_exit": 0}],
    "budgets": {"wall_seconds": 60, "actions": 10, "output_bytes": 4096, "cost_usd": 1},
    "result_directory": "550e8400-e29b-41d4-a716-446655440000",
    "return_format": "heleos.research-packet/v1",
    "authority": "proposal_only",
}
PACKET = {
    "schema": "heleos.research-packet/v1",
    "provider": "notebook_lm",
    "status": "completed",
    "purpose": "Compare public source claims.",
    "data_class": "PUBLIC",
    "source_sha256": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
    "input_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "query_or_case_id": "public-case-001",
    "retrieved_at": "2026-09-09T12:30:00Z",
    "output_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "citations": [{"url": "https://example.org/spec", "locator": "Section 3", "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],
    "claims": ["The public specification describes a boundary."],
    "contradictions": [],
    "gaps": [],
    "evaluation": {"boundary_passes": 3, "capability_passes": 0, "reason": "Original citation still requires independent verification."},
    "reviewer": "codex",
    "disposition": "research_only",
    "budgets": {"wall_seconds": 60, "actions": 10, "output_bytes": 4096, "cost_usd": 1},
}


class ResearchContractsTest(unittest.TestCase):
    def validate(self, record):
        return self.validate_json(json.dumps(record))

    def validate_json(self, encoded):
        with tempfile.TemporaryDirectory(prefix="heleos-contract-test-") as tmp:
            path = Path(tmp) / "record.json"
            path.write_text(encoded, encoding="utf-8")
            return subprocess.run(
                ["/usr/bin/jq", "-e", "-f", "governance/agents/validate-contracts.jq", str(path)],
                cwd=ROOT, capture_output=True, text=True, timeout=5, check=False,
            )

    def test_public_proposal_worker_is_accepted(self):
        result = self.validate(WORKER)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "true\n")
        self.assertEqual(result.stderr, "")

    def assertRejected(self, record):
        result = self.validate(record)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "false\n")
        self.assertEqual(result.stderr, "")

    def test_valid_packet_and_named_providers(self):
        for provider in ("codex", "claude_code", "kimi", "grok", "cursor", "notebook_lm", "grok_bots"):
            with self.subTest(provider=provider):
                worker = copy.deepcopy(WORKER)
                worker["provider"] = provider
                self.assertEqual(self.validate(worker).returncode, 0)
                packet = copy.deepcopy(PACKET)
                packet.update(provider=provider, reviewer="human")
                self.assertEqual(self.validate(packet).returncode, 0)

    def test_truthful_unavailable_packets(self):
        for status in ("unavailable", "unauthenticated", "sandbox_unavailable", "failed"):
            with self.subTest(status=status):
                packet = copy.deepcopy(PACKET)
                packet.update(status=status, claims=[], citations=[], disposition="disabled")
                packet["evaluation"]["boundary_passes"] = 0
                self.assertEqual(self.validate(packet).returncode, 0)

    def test_every_top_level_field_is_required_and_unknown_fields_rejected(self):
        for fixture in (WORKER, PACKET):
            for key in fixture:
                with self.subTest(schema=fixture["schema"], missing=key):
                    record = copy.deepcopy(fixture)
                    del record[key]
                    self.assertRejected(record)
            for key in ("token", "credential", "allow_self_approval", "production_authority", "unexpected"):
                with self.subTest(extra=key):
                    self.assertRejected(dict(fixture, **{key: True}))

    def test_hashes_must_be_exact_lowercase_sha256(self):
        for bad in ("", "a" * 63, "a" * 65, "g" * 64, "A" * 64, 17, None):
            for key in ("executable_sha256", "snapshot_sha256"):
                self.assertRejected(dict(WORKER, **{key: bad}))
            for key in ("input_sha256", "output_sha256"):
                self.assertRejected(dict(PACKET, **{key: bad}))
            self.assertRejected(dict(PACKET, source_sha256=[bad]))
            worker = copy.deepcopy(WORKER)
            worker["allowed_inputs"][0]["sha256"] = bad
            self.assertRejected(worker)

    def test_missing_input_hash_and_nested_extra_fields(self):
        worker = copy.deepcopy(WORKER)
        del worker["allowed_inputs"][0]["sha256"]
        self.assertRejected(worker)
        for fixture, key in ((WORKER, "allowed_inputs"), (PACKET, "citations")):
            record = copy.deepcopy(fixture)
            record[key][0]["token"] = "SENSITIVE_SENTINEL"
            self.assertRejected(record)

    def test_data_classes_and_unknown_states_fail_closed(self):
        for fixture in (WORKER, PACKET):
            for data_class in ("INTERNAL", "PROJECT_CONFIDENTIAL", "SECRET", "public", None):
                self.assertRejected(dict(fixture, data_class=data_class))
            self.assertRejected(dict(fixture, provider="unknown"))
            self.assertRejected(dict(fixture, schema="heleos.research-packet/v2"))
        for status in ("ready", "approved", "", None):
            self.assertRejected(dict(PACKET, status=status))
        for disposition in ("approved", "production", "merged", "published", "", None):
            self.assertRejected(dict(PACKET, disposition=disposition))

    def test_budgets_are_positive_finite_bounded_and_exact(self):
        for fixture in (WORKER, PACKET):
            for key in fixture["budgets"]:
                for bad in (0, -1, None, True, "1", 1000000000000):
                    with self.subTest(schema=fixture["schema"], key=key, bad=bad):
                        record = copy.deepcopy(fixture)
                        record["budgets"][key] = bad
                        self.assertRejected(record)
                record = copy.deepcopy(fixture)
                del record["budgets"][key]
                self.assertRejected(record)
            record = copy.deepcopy(fixture)
            record["budgets"]["unlimited"] = True
            self.assertRejected(record)
        for key in ("wall_seconds", "actions", "output_bytes"):
            record = copy.deepcopy(WORKER)
            record["budgets"][key] = 0.5
            self.assertRejected(record)

    def test_no_worker_can_gain_authority_or_approve_itself(self):
        for authority in ("self_approve", "merge", "push", "publish", "credential", "production", "write"):
            self.assertRejected(dict(WORKER, authority=authority))
            self.assertRejected(dict(WORKER, allowed_tools=[authority]))
        self.assertRejected(dict(PACKET, reviewer=PACKET["provider"]))
        self.assertRejected(dict(PACKET, reviewer="self"))
        for key in ("forbidden_paths", "forbidden_actions"):
            record = copy.deepcopy(WORKER)
            record[key].pop()
            self.assertRejected(record)

    def test_private_host_paths_are_rejected_without_echo(self):
        for path in ("/Users/alice/private", "/home/alice/private", "/etc/passwd", "~/private", "C:\\Users\\alice", "\\\\server\\share", "../private", "file:///private", "$HOME/private", "%LOCALAPPDATA%", "https://example.org/../../private"):
            with self.subTest(path=path):
                self.assertRejected(dict(PACKET, purpose="SENSITIVE_SENTINEL " + path))
                self.assertRejected(dict(WORKER, result_directory=path))
                self.assertRejected(dict(WORKER, allowed_endpoints=[path]))
                worker = copy.deepcopy(WORKER)
                worker["allowed_inputs"][0]["id"] = path
                self.assertRejected(worker)
        for endpoint in ("http://example.org", "https://localhost", "https://127.0.0.1", "https://192.168.1.1", "https://user:secret@example.org", "https://example.org?token=secret"):
            self.assertRejected(dict(WORKER, allowed_endpoints=[endpoint]))

    def test_bounded_types_and_collections(self):
        for malformed in (None, True, 1, "text", [], [WORKER]):
            self.assertRejected(malformed)
        for fixture in (WORKER, PACKET):
            for key, value in fixture.items():
                if isinstance(value, list):
                    self.assertRejected(dict(fixture, **{key: ["x"] * 65}))
                    self.assertRejected(dict(fixture, **{key: "not-an-array"}))
                elif isinstance(value, str):
                    self.assertRejected(dict(fixture, **{key: "x" * 4097}))
                    self.assertRejected(dict(fixture, **{key: {"nested": "SENSITIVE_SENTINEL"}}))
        for key in ("allowed_inputs", "allowed_tools", "allowed_endpoints", "forbidden_paths", "forbidden_actions", "acceptance_commands"):
            self.assertRejected(dict(WORKER, **{key: []}))
        self.assertRejected(dict(PACKET, source_sha256=[]))
        self.assertRejected(dict(WORKER, return_format="text"))
        for bad in ("2026-02-30T00:00:00Z", "2026-09-09", "2026-09-09T12:30:00+00:00", "2026-09-09T25:00:00Z"):
            self.assertRejected(dict(PACKET, retrieved_at=bad))

    def test_citations_and_evaluations_cannot_claim_unearned_success(self):
        self.assertRejected(dict(PACKET, citations=[]))
        self.assertRejected(dict(PACKET, status="unavailable"))
        record = copy.deepcopy(PACKET)
        record["citations"][0]["source_sha256"] = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        self.assertRejected(record)
        for key, bad in (("boundary_passes", 4), ("capability_passes", 6), ("boundary_passes", -1), ("reason", "")):
            record = copy.deepcopy(PACKET)
            record["evaluation"][key] = bad
            self.assertRejected(record)

    def test_multiple_json_records_cannot_mask_rejection(self):
        for records in ([WORKER, WORKER], [{"token": "SENSITIVE_SENTINEL"}, WORKER], [WORKER, WORKER, WORKER]):
            result = self.validate_json("\n".join(json.dumps(record) for record in records))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "false\n")
            self.assertEqual(result.stderr, "")

    def test_trailing_control_characters_do_not_bypass_anchored_patterns(self):
        for key in ("task_id", "executable_sha256", "snapshot_sha256", "result_directory"):
            self.assertRejected(dict(WORKER, **{key: WORKER[key] + "\n"}))
        self.assertRejected(dict(WORKER, allowed_endpoints=["https://example.org\n"]))
        record = copy.deepcopy(PACKET)
        record["citations"][0]["url"] += "\n"
        self.assertRejected(record)

    def test_capability_score_requires_all_boundary_cases_to_pass(self):
        record = copy.deepcopy(PACKET)
        record["evaluation"].update(boundary_passes=2, capability_passes=5)
        self.assertRejected(record)
        record["evaluation"]["boundary_passes"] = 3
        self.assertEqual(self.validate(record).returncode, 0)

    def test_command_records_accept_future_tool_ids_and_safe_arguments(self):
        for command in (
            {"tool": "future-boundary-probe-42", "argv": ["--case", "fixtures/public/case-42.json", "--mode=strict"], "expected_exit": 17},
            {"tool": "empty-input-check", "argv": [], "expected_exit": 255},
        ):
            with self.subTest(command=command):
                result = self.validate(dict(WORKER, acceptance_commands=[command]))
                self.assertEqual(result.returncode, 0, result.stderr)
        for legacy in ("python3 tests/research/test_research_contracts.py", "python3 governance/agents/validate-sources.py --self-test", "/usr/bin/jq -e -f governance/agents/validate-contracts.jq"):
            self.assertRejected(dict(WORKER, acceptance_commands=[legacy]))

    def test_command_records_reject_shell_paths_credentials_and_bad_bounds(self):
        base = {"tool": "future-check", "argv": ["fixtures/public/case.json"], "expected_exit": 0}
        for key in base:
            bad = copy.deepcopy(base)
            del bad[key]
            self.assertRejected(dict(WORKER, acceptance_commands=[bad]))
        self.assertRejected(dict(WORKER, acceptance_commands=[dict(base, shell=True)]))
        for tool in ("/usr/bin/python3", "../check", "check tool", "check;id", "check\n", "x" * 129):
            self.assertRejected(dict(WORKER, acceptance_commands=[dict(base, tool=tool)]))
        for token in ("/tmp/case", "--input=/home/alice/private", "private/repo.json", ".ssh/config", "fixtures/../secret", "..", "C:/Users/alice", "\\\\host\\share", "~/file", "file:///tmp/foo", "$HOME", "${INPUT}", "$(id)", "`id`", "{{input}}", "%HOME%", "a;b", "a|b", "a>b", "a<b", "a&b", "a*b", "a?b", "[abc]", "a b", "a\nb", "'quoted'", '"quoted"', "--token=secret", "--password=secret", "https://example.org", "a" * 257):
            with self.subTest(token=token):
                self.assertRejected(dict(WORKER, acceptance_commands=[dict(base, argv=[token])]))
        for argv in ("--check", ["ok"] * 65, [1], [None], [{}], [""]):
            self.assertRejected(dict(WORKER, acceptance_commands=[dict(base, argv=argv)]))
        for exit_code in (-1, 256, 1.5, True, "0", None):
            self.assertRejected(dict(WORKER, acceptance_commands=[dict(base, expected_exit=exit_code)]))

    def test_printable_prose_preserves_legitimate_engineering_punctuation(self):
        for text in ("Section 5/6 requires 15% outside air.", "Estimated public list price: $15.50 per unit.", "Air/water ratio is 2/3; tolerance is +/- 5%.", "Pressure rises (5%) while $ cost stays constant."):
            record = copy.deepcopy(PACKET)
            record.update(purpose=text, query_or_case_id=text, claims=[text], contradictions=[text], gaps=[text])
            record["citations"][0]["locator"] = text
            record["evaluation"]["reason"] = text
            result = self.validate(record)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_printable_prose_still_rejects_private_paths_and_secret_shapes(self):
        # Assemble synthetic markers at runtime so source scanners do not mistake
        # the hostile fixtures for material credentials. Runtime bytes are fixed.
        for text in (
            "See (/opt/private/repo).", "path=/Users/alice/private", "Use C:/Users/alice/file", "Use file:///tmp/a", "Use \\\\host\\share", "Input is ../private/repo", "Input is ~/repo", "See /tmp/data", "Use %LOCALAPPDATA%", "Use %2fUsers%2falice", "Use ${INPUT}", "Use $(id)", "Use $HOME", "Use `id`", "Use {{secret}}", "token=abcdef", "API_KEY: abcdef", "Authorization: Bearer abcdef", "password = abcdef", "cookie: session=abcdef",
            "".join(("sk-", "test-", "abcdefghijklmnop")),
            "".join(("ghp_", "abcdefghijklmnopqrstuvwxyz", "123456")),
            "".join(("-----BEGIN ", "PRIVATE KEY", "-----")),
            "text\nsecret", "text\tsecret", "text\x7fsecret",
        ):
            with self.subTest(text=text):
                self.assertRejected(dict(PACKET, purpose=text))

    def test_quoted_credential_assignments_are_not_sanitized_prose(self):
        for text in ('{"token":"abcdef"}', "{'api_key': 'abcdef'}", '"Password" = "abcdef"'):
            with self.subTest(text=text):
                self.assertRejected(dict(PACKET, purpose=text))


if __name__ == "__main__":
    unittest.main()
