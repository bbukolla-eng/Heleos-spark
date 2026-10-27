"""Synthetic black-box checks for the research-only source metadata boundary."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "governance/agents/validate-sources.py"


def source():
    return {
        "id": "synthetic-standard-v1",
        "canonical_url": "https://standards.example.org/2026/manual",
        "publisher": "Synthetic publisher",
        "title": "Synthetic source, no real standard content",
        "edition_or_version": "2026",
        "effective_date": "2026-01-01",
        "retrieved_at": "2026-09-09T12:00:00Z",
        "cache_status": "not_cached",
        "license_or_rights": "Repository-authored synthetic metadata",
        "data_class": "PUBLIC",
        "locators": ["Section 1, paragraph 2"],
        "claims_supported": ["Synthetic citation for validator testing only"],
        "applicability": "Synthetic fixture jurisdiction; advisory only",
        "supersession_state": "unknown",
        "notebooklm_permission": "denied",
        "notebooklm_rights_basis": "No upload authorization",
        "contradictions": [],
        "gaps": ["No real source content or independently checked rights"],
    }


def manifest(entries):
    text = 'schema_version = 1\nstatus = "research_only"\n'
    for entry in entries:
        text += "\n[[source]]\n"
        for key, value in entry.items():
            text += key + " = " + json.dumps(value, ensure_ascii=True) + "\n"
    return text.encode()


def aggregate_manifest(hashes, refs):
    text = 'schema_version = 1\nstatus = "research_only"\n'
    text += 'lane_manifest_sha256 = ' + json.dumps(hashes) + '\n'
    if not refs:
        text += 'source_ref = []\n'
    for ref in refs:
        text += '\n[[source_ref]]\n'
        for key, value in ref.items():
            text += key + ' = ' + json.dumps(value) + '\n'
    return text.encode()


class SourceCandidateTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="heleos-source-candidate-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.path = self.root / "candidates.toml"

    def probe(self, raw=None, paths=None):
        self.assertTrue(SCRIPT.is_file(), "research source validator is missing")
        if raw is not None:
            self.path.write_bytes(raw)
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, paths or [self.path])],
            cwd=self.root, capture_output=True, timeout=5,
        )
        self.assertEqual(result.stderr, b"")
        return result.returncode, json.loads(result.stdout)

    def reject(self, entry, code):
        status, result = self.probe(manifest([entry]))
        self.assertEqual(status, 1, result)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["error"], code)
        self.assertFalse(result["production_authority"])

    def test_valid_metadata_is_deterministic_read_only_and_never_admission(self):
        raw = manifest([source()])
        self.path.write_bytes(raw)
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        first = self.probe()
        self.assertEqual(first[0], 0, first)
        self.assertEqual(first[1], {
            "schema_version": 1, "status": "CANDIDATE_METADATA_VALID",
            "source_count": 1, "manifest_sha256": [hashlib.sha256(raw).hexdigest()],
            "lane_manifest_count": 1, "aggregate_manifest_count": 0,
            "production_authority": False, "source_bytes_verified": False,
            "citations_verified": False, "rights_verified": False,
        })
        self.assertEqual(first, self.probe())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_missing_required_fields_fail_closed(self):
        for field in source():
            with self.subTest(field=field):
                entry = source()
                del entry[field]
                self.reject(entry, "invalid_fields")

    def test_unknown_fields_and_promotion_attempts_are_rejected(self):
        for field in ("approved", "quantity", "rule", "production_authority", "taxonomy"):
            with self.subTest(field=field):
                self.reject(dict(source(), **{field: "candidate"}), "invalid_fields")
        raw = manifest([source()]).replace(b'research_only', b'approved')
        self.assertEqual(self.probe(raw)[1]["error"], "invalid_manifest")

    def test_missing_or_empty_citations_rights_and_applicability_are_rejected(self):
        for field, value in (("locators", []), ("claims_supported", []),
                             ("license_or_rights", " "), ("applicability", "")):
            with self.subTest(field=field):
                self.reject(dict(source(), **{field: value}), "invalid_value")

    def test_declared_class_must_be_public(self):
        for value in ("SECRET", "INTERNAL", "PROJECT_CONFIDENTIAL", "public", 1):
            with self.subTest(value=value):
                self.reject(dict(source(), data_class=value), "invalid_value")

    def test_cache_state_controls_exact_digest_requirement(self):
        self.reject(dict(source(), cache_status="cached_verified"), "invalid_cache")
        self.reject(dict(source(), cache_sha256="a" * 64), "invalid_cache")
        for digest in ("", "a" * 63, "A" * 64, "z" * 64, 123):
            with self.subTest(digest=digest):
                self.reject(dict(source(), cache_status="cached_verified", cache_sha256=digest),
                            "invalid_cache")
        entry = dict(source(), cache_status="cached_verified", cache_sha256="a" * 64)
        code, report = self.probe(manifest([entry]))
        self.assertEqual(code, 0, report)
        self.assertFalse(report["source_bytes_verified"])

    def test_unknown_states_and_unaffirmed_upload_rights_are_rejected(self):
        for field in ("cache_status", "supersession_state", "notebooklm_permission"):
            self.reject(dict(source(), **{field: "approved"}), "invalid_value")
        self.reject(dict(source(), notebooklm_permission="permitted", notebooklm_rights_basis=""),
                    "invalid_value")
        entry = dict(source(), notebooklm_permission="permitted",
                     notebooklm_rights_basis="AFFIRMATIVE: Synthetic declared permission; requires independent check")
        code, report = self.probe(manifest([entry]))
        self.assertEqual(code, 0, report)
        self.assertFalse(report["rights_verified"])

    def test_permitted_upload_requires_explicit_affirmative_rights_declaration(self):
        for basis in ("No upload authorization", "Rights unverified", "Permission unknown",
                      "Synthetic declared permission", "AFFIRMATIVE:", "AFFIRMATIVE: ",
                      "affirmative: Declared permission", "AFFIRMATIVE:  Declared permission"):
            with self.subTest(basis=basis):
                self.reject(dict(source(), notebooklm_permission="permitted",
                                 notebooklm_rights_basis=basis), "invalid_value")
        for permission in ("reference_only", "denied"):
            for basis in ("No upload authorization", "Rights unverified", "Permission unknown"):
                with self.subTest(permission=permission, basis=basis):
                    code, report = self.probe(manifest([dict(
                        source(), notebooklm_permission=permission, notebooklm_rights_basis=basis)]))
                    self.assertEqual(code, 0, report)
                    self.assertIs(report["rights_verified"], False)

    def test_research_can_record_unresolved_and_retired_sources_without_promoting_them(self):
        for state in ("current", "unknown", "superseded", "retired"):
            with self.subTest(state=state):
                code, report = self.probe(manifest([dict(source(), supersession_state=state)]))
                self.assertEqual(code, 0, report)
                self.assertFalse(report["production_authority"])

    def test_urls_reject_private_credentials_search_queries_and_noncanonical_forms(self):
        for url in (
            "file:///private/source.pdf", "https://localhost/source", "http://127.0.0.1/source",
            "https://192.168.1.2/source", "http://[::1]/source", "https://user:pass@example.org/a",
            "https://www.google.com/search?q=standard", "https://www.bing.com/search",
            "https://duckduckgo.com/?q=standard", "https://example.org/manual?token=hidden",
            "HTTPS://EXAMPLE.ORG/manual", "https://example.org/a/../manual", "https://example.org/a#section",
            "https://example.org/white space", "https://example.org/a%2f..%2fsecret",
        ):
            with self.subTest(url=url):
                self.reject(dict(source(), canonical_url=url), "invalid_url")

    def test_host_paths_are_rejected_in_all_nested_text_without_echoing_input(self):
        for value in ("Read /Users/example/private.pdf", "file:///tmp/source", "C:\\Users\\example\\source",
                      "Read /home/example/document", "~/Library/private", "\\\\server\\share\\file"):
            with self.subTest(value=value):
                entry = dict(source(), claims_supported=[value])
                self.reject(entry, "private_path")
                self.assertNotIn(value, json.dumps(self.probe()[1]))

    def test_dates_and_ids_reject_invalid_or_ambiguous_values(self):
        for field, value in (("effective_date", "2026-02-30"), ("effective_date", "20260101"),
                             ("retrieved_at", "2026-09-09T12:00:00"), ("retrieved_at", "tomorrow"),
                             ("id", "duplicate space"), ("id", "")):
            with self.subTest(field=field, value=value):
                self.reject(dict(source(), **{field: value}), "invalid_value")

    def test_duplicates_across_manifests_and_input_order(self):
        self.path.write_bytes(manifest([source()]))
        second = self.root / "second.toml"
        second.write_bytes(manifest([source()]))
        self.assertEqual(self.probe(paths=[self.path, second])[1]["error"], "duplicate_source_id")
        second.write_bytes(manifest([dict(source(), id="another-source")]))
        first = self.probe(paths=[self.path, second])
        self.assertEqual(first[0], 0, first)
        self.assertEqual(first[1]["source_count"], 2)
        self.assertEqual(first, self.probe(paths=[second, self.path]))

    def test_duplicate_toml_keys_and_bad_encoding_are_rejected_without_tracebacks(self):
        for raw in (b'key = 1\nkey = 2\n', b'\xff', b'[[[[bad', b''):
            with self.subTest(raw=raw):
                code, report = self.probe(raw)
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "REJECTED")

    def test_wrong_types_and_unknown_top_level_keys_are_rejected(self):
        for raw in (b'schema_version = true\nstatus = "research_only"\nsource = []\n',
                    b'schema_version = 1\nstatus = "research_only"\nsource = {}\n',
                    manifest([source()]).replace(b'[[source]]', b'approved = true\n[[source]]')):
            self.assertEqual(self.probe(raw)[1]["error"], "invalid_manifest")
        self.reject(dict(source(), contradictions="none"), "invalid_value")
        self.reject(dict(source(), locators=[1]), "invalid_value")

    def test_oversized_and_nonregular_input_are_rejected(self):
        self.assertEqual(self.probe(b" " * (1024 * 1024 + 1))[1]["error"], "input_too_large")
        self.assertEqual(self.probe(paths=[self.root])[1]["error"], "input_unavailable")
        self.assertEqual(self.probe(paths=[self.root / "missing"])[1]["error"], "input_unavailable")

    def aggregate_fixture(self, hashes=None, refs=None):
        lane_raw = manifest([source()])
        self.path.write_bytes(lane_raw)
        digest = hashlib.sha256(lane_raw).hexdigest()
        aggregate = self.root / "aggregate.toml"
        aggregate.write_bytes(aggregate_manifest(
            [digest] if hashes is None else hashes,
            [{"id": source()["id"], "lane_manifest_sha256": digest}] if refs is None else refs))
        return aggregate

    def test_aggregate_requires_exact_lane_hashes_and_reports_sorted_counts(self):
        aggregate = self.aggregate_fixture()
        second = self.root / "second.toml"
        second.write_bytes(manifest([dict(source(), id="second-lane")]))
        lane_hashes = sorted(hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (self.path, second))
        aggregate.write_bytes(aggregate_manifest(lane_hashes, [
            {"id": source()["id"], "lane_manifest_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest()},
            {"id": "second-lane", "lane_manifest_sha256": hashlib.sha256(second.read_bytes()).hexdigest()},
        ]))
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        first = self.probe(paths=[aggregate, second, self.path])
        self.assertEqual(first[0], 0, first)
        self.assertEqual(first[1]["lane_manifest_count"], 2)
        self.assertEqual(first[1]["aggregate_manifest_count"], 1)
        self.assertEqual(first[1]["source_count"], 2)
        self.assertEqual(first[1]["manifest_sha256"],
                         sorted(hashlib.sha256(raw).hexdigest() for raw in before.values()))
        for field in ("production_authority", "source_bytes_verified", "citations_verified",
                      "rights_verified"):
            self.assertIs(first[1][field], False)
        self.assertEqual(first, self.probe(paths=[self.path, aggregate, second]))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_aggregate_alone_or_wrong_lane_binding_is_rejected(self):
        aggregate = self.aggregate_fixture()
        for paths in ([aggregate], [self.path, aggregate]):
            if len(paths) == 2:
                self.path.write_bytes(manifest([dict(source(), id="changed-lane")]))
            with self.subTest(paths=len(paths)):
                code, report = self.probe(paths=paths)
                self.assertEqual(code, 1)
                self.assertEqual(report["error"], "aggregate_lane_mismatch")

    def test_aggregate_cannot_omit_a_named_lane_or_include_an_aggregate_hash(self):
        aggregate = self.aggregate_fixture()
        second = self.root / "second.toml"
        second.write_bytes(manifest([dict(source(), id="second-lane")]))
        self.assertEqual(self.probe(paths=[self.path, second, aggregate])[1]["error"],
                         "aggregate_lane_mismatch")
        hashes = sorted(hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in (self.path, aggregate))
        aggregate.write_bytes(aggregate_manifest(hashes, [
            {"id": source()["id"], "lane_manifest_sha256": hashes[0]}]))
        self.assertEqual(self.probe(paths=[self.path, aggregate])[1]["error"],
                         "aggregate_lane_mismatch")

    def test_aggregate_hash_list_is_nonempty_sorted_unique_lowercase_hex_and_bounded(self):
        for hashes in ([], "a" * 64, [1], ["A" * 64], ["g" * 64], ["a" * 63],
                       ["b" * 64, "a" * 64], ["a" * 64, "a" * 64], ["a" * 64] * 1001):
            with self.subTest(hashes=str(hashes)[:80]):
                aggregate = self.aggregate_fixture(hashes=hashes)
                self.assertEqual(self.probe(paths=[self.path, aggregate])[1]["error"],
                                 "invalid_manifest")

    def test_aggregate_references_reject_duplicates_missing_unknown_and_extra_fields(self):
        digest = hashlib.sha256(manifest([source()])).hexdigest()
        ref = {"id": source()["id"], "lane_manifest_sha256": digest}
        for refs, error in (([ref, ref], "duplicate_source_id"),
                            ([], "aggregate_source_mismatch"),
                            ([dict(ref, id="unknown")], "aggregate_source_mismatch"),
                            ([dict(ref, lane_manifest_sha256="a" * 64)], "aggregate_source_mismatch"),
                            ([dict(ref, title="untrusted-marker")], "invalid_fields"),
                            ([{"id": source()["id"]}], "invalid_fields"),
                            ([dict(ref, id=1)], "invalid_value"),
                            ([dict(ref, lane_manifest_sha256="A" * 64)], "invalid_value")):
            with self.subTest(refs=refs):
                aggregate = self.aggregate_fixture(refs=refs)
                self.assertEqual(self.probe(paths=[self.path, aggregate])[1]["error"], error)

    def test_aggregate_reference_must_bind_id_to_its_own_lane(self):
        aggregate = self.aggregate_fixture()
        second = self.root / "second.toml"
        second.write_bytes(manifest([dict(source(), id="second-lane")]))
        first_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        second_hash = hashlib.sha256(second.read_bytes()).hexdigest()
        aggregate.write_bytes(aggregate_manifest(sorted([first_hash, second_hash]), [
            {"id": source()["id"], "lane_manifest_sha256": second_hash},
            {"id": "second-lane", "lane_manifest_sha256": first_hash},
        ]))
        self.assertEqual(self.probe(paths=[self.path, second, aggregate])[1]["error"],
                         "aggregate_source_mismatch")

    def test_multiple_aggregates_are_rejected_and_lane_ids_remain_unique(self):
        aggregate = self.aggregate_fixture()
        second = self.root / "aggregate-two.toml"
        second.write_bytes(aggregate.read_bytes())
        code, report = self.probe(paths=[aggregate, self.path, second])
        self.assertEqual(code, 1, report)
        self.assertEqual(report["error"], "multiple_aggregate_manifests")
        self.assertIs(report["production_authority"], False)
        second.write_bytes(manifest([source()]))
        self.assertEqual(self.probe(paths=[aggregate, self.path, second])[1]["error"],
                         "duplicate_source_id")

    def test_aggregate_and_lane_top_levels_remain_exact(self):
        aggregate = self.aggregate_fixture()
        for path in (self.path, aggregate):
            original = path.read_bytes()
            path.write_bytes(b'unknown = "untrusted-marker"\n' + original)
            code, report = self.probe(paths=[self.path, aggregate])
            self.assertEqual(code, 1)
            self.assertEqual(report["error"], "invalid_manifest")
            self.assertNotIn("untrusted-marker", json.dumps(report))
            path.write_bytes(original)
        raw = aggregate.read_bytes().replace(b'[[source_ref]]', b'[[source]]')
        aggregate.write_bytes(raw)
        self.assertEqual(self.probe(paths=[self.path, aggregate])[1]["error"], "invalid_manifest")

    def test_manifest_argument_count_is_bounded(self):
        code, report = self.probe(paths=[self.root / "untrusted-marker"] * 1001)
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "invalid_manifest_count")

    def test_self_test_is_deterministic_private_cleanup_and_never_authority(self):
        expected_cases = {"valid_lane", "valid_aggregate", "missing_locator", "missing_rights",
                          "non_public", "absent_cached_hash", "unrecognized_notebooklm_permission",
                          "duplicate_id_across_manifests", "unknown_fields", "private_path",
                          "valid_worker", "valid_packet", "worker_missing_input_hash",
                          "worker_non_public", "worker_absent_budget", "worker_authority_attempt",
                          "worker_private_path", "worker_credential_text", "packet_missing_input_hash",
                          "packet_non_public", "packet_absent_budget", "packet_self_approval",
                          "packet_private_path", "packet_credential_text",
                          "valid_affirmative_rights", "unaffirmed_upload_rights"}
        results = []
        for _ in range(2):
            result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--self-test"],
                                    cwd=self.root, env=dict(os.environ, TMPDIR=str(self.root)),
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b"")
            self.assertLess(len(result.stdout), 4096)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "SELF_TEST_PASS")
            self.assertIs(report["production_authority"], False)
            self.assertEqual(set(report["cases"]), expected_cases)
            self.assertTrue(all(value == "PASS" for value in report["cases"].values()))
            self.assertNotIn(str(self.root), result.stdout.decode())
            self.assertEqual(list(self.root.iterdir()), [])
            results.append(result.stdout)
        self.assertEqual(*results)

    def isolated_self_test(self, jq_filter):
        # Invoke the real CLI and /usr/bin/jq using a disposable neighboring
        # filter to prove that the self-test observes production verdicts.
        script = self.root / "validate-sources.py"
        script.write_bytes(SCRIPT.read_bytes())
        if jq_filter is not None:
            (self.root / "validate-contracts.jq").write_text(jq_filter, encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = subprocess.run([sys.executable, "-B", str(script), "--self-test"],
                                cwd=self.root, env=dict(os.environ, TMPDIR=str(self.root)),
                                capture_output=True, timeout=5)
        self.assertEqual(result.stderr, b"")
        self.assertLess(len(result.stdout), 4096)
        self.assertNotIn(str(self.root), result.stdout.decode())
        self.assertNotIn("untrusted-marker", result.stdout.decode())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        return result.returncode, json.loads(result.stdout)

    def test_self_test_rejects_missing_real_contract_filter(self):
        code, report = self.isolated_self_test(None)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["error"], "contract_validator_unavailable")
        self.assertIs(report["production_authority"], False)

    def test_self_test_detects_contract_filter_accepting_every_record(self):
        code, report = self.isolated_self_test("true")
        self.assertEqual(code, 1, report)
        self.assertEqual(report["status"], "SELF_TEST_FAIL")
        for name in ("valid_worker", "valid_packet"):
            self.assertEqual(report["cases"][name], "PASS")
        for name in ("worker_missing_input_hash", "worker_non_public", "worker_absent_budget",
                     "worker_authority_attempt", "worker_private_path", "worker_credential_text",
                     "packet_missing_input_hash", "packet_non_public", "packet_absent_budget",
                     "packet_self_approval", "packet_private_path", "packet_credential_text"):
            self.assertEqual(report["cases"][name], "FAIL")

    def test_self_test_detects_contract_filter_rejecting_valid_records(self):
        code, report = self.isolated_self_test("false")
        self.assertEqual(code, 1, report)
        self.assertEqual(report["status"], "SELF_TEST_FAIL")
        self.assertEqual(report["cases"]["valid_worker"], "FAIL")
        self.assertEqual(report["cases"]["valid_packet"], "FAIL")

    def test_self_test_bounds_contract_subprocess_output_and_time_and_sanitizes_errors(self):
        for jq_filter, error in (("range(0; 1000000)", "contract_validator_output_limit"),
                                 ("def spin: spin; spin", "contract_validator_timeout"),
                                 ('error("untrusted-marker")', "contract_validator_failed")):
            with self.subTest(error=error):
                code, report = self.isolated_self_test(jq_filter)
                self.assertEqual(code, 1, report)
                self.assertEqual(report["error"], error)
                self.assertIs(report["production_authority"], False)

    def test_self_test_and_manifest_modes_are_exclusive_with_sanitized_errors(self):
        for args in ([], ["--self-test", str(self.root / "untrusted-marker")],
                     ["--untrusted-marker"]):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-B", str(SCRIPT), *args],
                                        cwd=self.root, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, b"")
                report = json.loads(result.stdout)
                self.assertEqual(report["error"], "invalid_arguments")
                self.assertNotIn("untrusted-marker", result.stdout.decode())


if __name__ == "__main__":
    unittest.main()
