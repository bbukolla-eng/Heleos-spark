"""Synthetic black-box checks for the research-only source metadata boundary."""

import hashlib
import json
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
                     notebooklm_rights_basis="Synthetic declared permission; requires independent check")
        code, report = self.probe(manifest([entry]))
        self.assertEqual(code, 0, report)
        self.assertFalse(report["rights_verified"])

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


if __name__ == "__main__":
    unittest.main()
