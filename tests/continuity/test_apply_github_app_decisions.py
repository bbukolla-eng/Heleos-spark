"""Black-box owner-decision contracts, using only synthetic disposable Git repos.

No fixture is an owner decision, real installation evidence, or authorization
for the live project. Expectations do not import implementation helpers.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/apply-github-app-decisions.py"
APPS = ["Azure Pipelines", "AWS Connector for GitHub", "Amazon Q Developer", "ECC Tools"]
REGISTRY = "governance/github-apps.toml"
EDITED_FIELDS = ["version_or_digest", "permissions", "egress", "evaluation", "disposition"]
DECISION_FIELDS = ["decision_owner", "decision_date", "decision_evidence", "installation_evidence"]
SECRET = "SYNTHETIC_SECRET_SENTINEL_NEVER_ECHO_91d32"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def parse_registry(data):
    """Independent parser of the test's constrained string-only TOML records."""
    records = []
    for line in data.decode("ascii").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line == "[[github_app]]":
            records.append({})
            continue
        match = re.fullmatch(r'([a-z_]+) = (".*")', line)
        if not match or not records or match.group(1) in records[-1]:
            raise AssertionError("malformed or duplicate registry field")
        records[-1][match.group(1)] = json.loads(match.group(2))
    return records


class ApplyGitHubAppDecisionsTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="heleos-app-decisions-test-")
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.repo = self.base / "visible main"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Synthetic Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_AUTHOR_DATE": "2026-09-09T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-09-09T00:00:00Z", "GIT_OPTIONAL_LOCKS": "0",
        })
        self.git("init", "--initial-branch=main")
        self.git("config", "core.autocrlf", "false")
        # Git 2.55 detached auto-maintenance can race the fixture's full .git snapshot.
        self.git("config", "maintenance.autoDetach", "false")
        self.write(".gitignore", ".worktrees/\n")
        self.write("README.md", "Synthetic disposable fixture.\n")
        self.write(".github/workflows/existing.yml", "name: fixture\non: workflow_dispatch\n")
        sections = ["# Preserve the synthetic inventory header.\n"]
        for number, name in enumerate(APPS):
            fields = {
                "name": name, "origin": "synthetic account inventory",
                "version_or_digest": "not inventoried", "license_or_rights": "reviewed terms",
                "data_class": "PROJECT_CONFIDENTIAL", "owner": "repository owner",
                "permissions": "not inventoried", "egress": "prohibited pending owner decision",
                "evaluation": "not evaluated", "rollback": "owner removes installation",
                "disposition": "owner_decision_required",
            }
            sections.append("[[github_app]]\n# Preserve record %d.\n" % number
                            + "".join(key + " = " + json.dumps(value) + "\n"
                                      for key, value in fields.items())
                            + "# Preserve trailing record comment.\n")
        self.original = "\n".join(sections).encode("ascii")
        self.registry = self.write(REGISTRY, self.original)
        self.registry.chmod(0o640)
        self.commit("synthetic base")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.packet = self.base / "owner packet.json"
        self.data = {
            "schema": "heleos.github-app-decisions/v1",
            "repository": "bbukolla-eng/Heleos-spark", "expected_head": self.head,
            "expected_registry_sha256": sha256(self.original),
            "decision_owner": "Bekim Bukolla", "decision_date": "2026-09-09",
            "apps": [{
                "name": name, "disposition": disposition,
                "version_or_digest": "service-release-2026.09.1",
                "permissions": "installation grants metadata=read; repository selection verified",
                "egress": "approved: public repository metadata only" if number < 2 else "prohibited",
                "evaluation": "owner reviewed scope and installation evidence",
                "decision_evidence": "evidence/synthetic-owner-decision.md",
                "installation_evidence": "evidence/synthetic-installation-%d.json" % number,
            } for number, (name, disposition) in enumerate(zip(APPS, ["retain", "restrict", "suspend", "remove"]))],
        }
        self.save_packet()

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args], env=self.env,
                              capture_output=True, text=True, check=True).stdout

    def write(self, relative, value):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode("utf-8"))
        return path

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def save_packet(self, data=None, raw=None):
        self.packet.write_bytes(raw if raw is not None else json.dumps(
            self.data if data is None else data, ensure_ascii=True).encode("ascii"))

    def snapshot(self):
        result = {}
        for path in self.base.rglob("*"):
            info = path.lstat()
            relative = str(path.relative_to(self.base))
            if stat.S_ISLNK(info.st_mode):
                result[relative] = ("symlink", os.readlink(path), info.st_mode, info.st_mtime_ns)
            elif stat.S_ISREG(info.st_mode):
                result[relative] = ("file", sha256(path.read_bytes()), info.st_mode,
                                    info.st_mtime_ns, info.st_ino)
            else:
                result[relative] = ("directory" if path.is_dir() else "special", info.st_mode)
        return result

    def invoke(self, apply=False, repo=None, packet=None, extra=(), env=None, default_repo=False):
        argv = [sys.executable, "-B", str(SCRIPT)]
        if not default_repo:
            argv.extend(["--repo", str(repo or self.repo)])
        argv.extend(["--packet", str(packet or self.packet)])
        if apply:
            argv.append("--apply")
        argv.extend(extra)
        return subprocess.run(argv, cwd=str(repo or self.repo), env=self.env if env is None else env,
                              capture_output=True, text=True, timeout=15)

    def output(self, run, status):
        self.assertEqual(run.returncode, 1 if status == "FAIL" else 0, run.stderr + run.stdout)
        self.assertEqual(run.stderr, "")
        self.assertNotIn(SECRET, run.stdout)
        result = json.loads(run.stdout)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["status"], status)
        self.assertIs(result["account_changes_performed"], False)
        self.assertIs(result["write_performed"], status == "APPLIED")
        self.assertEqual(set(result), {"schema_version", "status", "main", "packet", "registry",
                                       "apps", "write_performed", "account_changes_performed", "errors"})
        self.assertEqual(set(result["main"]), {"path", "head"})
        self.assertEqual(set(result["packet"]), {"path", "sha256"})
        self.assertEqual(set(result["registry"]), {"path", "before_sha256", "after_sha256"})
        self.assertEqual(run.stdout.count("\n"), 1)
        self.assertEqual(run.stdout.strip(), json.dumps(result, separators=(",", ":"), ensure_ascii=True))
        self.assertIsInstance(result["errors"], list)
        if status == "FAIL":
            self.assertTrue(result["errors"])
        else:
            self.assertEqual(result["errors"], [])
        return result

    def rejected(self, **kwargs):
        before = self.snapshot()
        result = self.output(self.invoke(apply=True, **kwargs), "FAIL")
        self.assertEqual(self.snapshot(), before, "failure changed disposable filesystem or Git")
        return result

    def proposed_bytes(self):
        # The expected edit is specified independently using literal fixture lines.
        expected = self.original.decode("ascii")
        for app in self.data["apps"]:
            section_start = expected.index('name = ' + json.dumps(app["name"]))
            section_end = expected.find("[[github_app]]", section_start)
            if section_end < 0:
                section_end = len(expected)
            section = expected[section_start:section_end]
            for field in EDITED_FIELDS:
                section = re.sub(r"^" + field + r" = .*?$",
                                 lambda match: field + " = " + json.dumps(app[field]),
                                 section, flags=re.MULTILINE)
            insertion = "".join(field + " = " + json.dumps(self.data[field] if field in
                                ("decision_owner", "decision_date") else app[field]) + "\n"
                                for field in DECISION_FIELDS)
            disposition = 'disposition = ' + json.dumps(app["disposition"]) + "\n"
            section = section.replace(disposition, disposition + insertion)
            expected = expected[:section_start] + section + expected[section_end:]
        return expected.encode("ascii")

    def test_dry_run_reports_exact_proposed_hash_without_any_write(self):
        before = self.snapshot()
        result = self.output(self.invoke(), "DRY_RUN")
        self.assertEqual(result["main"], {"path": str(self.repo), "head": self.head})
        self.assertEqual(result["packet"], {"path": str(self.packet), "sha256": sha256(self.packet.read_bytes())})
        self.assertEqual(result["registry"], {"path": str(self.registry),
                         "before_sha256": sha256(self.original), "after_sha256": sha256(self.proposed_bytes())})
        self.assertEqual(result["apps"], [{"name": app["name"], "disposition": app["disposition"]}
                                           for app in self.data["apps"]])
        self.assertEqual(self.snapshot(), before)

    def test_apply_preserves_comments_order_fields_mode_and_changes_only_registry(self):
        before = self.snapshot()
        result = self.output(self.invoke(apply=True), "APPLIED")
        self.assertEqual(self.registry.read_bytes(), self.proposed_bytes())
        self.assertEqual(result["registry"]["after_sha256"], sha256(self.registry.read_bytes()))
        self.assertEqual(stat.S_IMODE(self.registry.stat().st_mode), 0o640)
        records = parse_registry(self.registry.read_bytes())
        self.assertEqual([record["name"] for record in records], APPS)
        for record, original, app in zip(records, parse_registry(self.original), self.data["apps"]):
            for field in EDITED_FIELDS:
                self.assertEqual(record[field], app[field])
            for field in DECISION_FIELDS:
                self.assertEqual(record[field], self.data[field] if field in self.data else app[field])
            for field in set(original) - set(EDITED_FIELDS):
                self.assertEqual(record[field], original[field])
            keys = list(record)
            self.assertEqual(keys[keys.index("disposition") + 1:][:4], DECISION_FIELDS)
        after = self.snapshot()
        key = str(self.registry.relative_to(self.base))
        self.assertNotEqual(after[key][-1], before[key][-1], "registry was overwritten in place")
        self.assertEqual(set(after), set(before), "apply created or removed another path")
        self.assertEqual({k: v for k, v in after.items() if k != key},
                         {k: v for k, v in before.items() if k != key})
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(self.git("diff", "--name-only").strip(), REGISTRY)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_same_packet_after_apply_is_idempotent_before_commit(self):
        self.output(self.invoke(apply=True), "APPLIED")
        before = self.snapshot()
        first = self.invoke(apply=True)
        self.output(first, "ALREADY_APPLIED")
        self.assertEqual(self.snapshot(), before)
        second = self.invoke(apply=True)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_reordered_packet_produces_canonical_registry_order(self):
        self.data["apps"].reverse()
        self.save_packet()
        result = self.output(self.invoke(apply=True), "APPLIED")
        self.assertEqual([app["name"] for app in result["apps"]], APPS)
        self.assertEqual([app["name"] for app in parse_registry(self.registry.read_bytes())], APPS)

    def test_nested_worktree_applies_only_to_unique_registered_main(self):
        worktree = self.repo / ".worktrees" / "candidate"
        self.git("worktree", "add", "-b", "candidate", str(worktree))
        nested = worktree / "governance"
        before = self.snapshot()
        result = self.output(self.invoke(apply=True, repo=nested), "APPLIED")
        self.assertEqual(result["main"], {"path": str(self.repo), "head": self.head})
        self.assertEqual(self.registry.read_bytes(), self.proposed_bytes())
        self.assertEqual((worktree / REGISTRY).read_bytes(), self.original)
        key = str(self.registry.relative_to(self.base))
        self.assertEqual({k: v for k, v in self.snapshot().items() if k != key},
                         {k: v for k, v in before.items() if k != key})

    def test_default_repo_resolves_current_directory(self):
        result = self.output(self.invoke(default_repo=True), "DRY_RUN")
        self.assertEqual(result["main"]["path"], str(self.repo))

    def test_required_packet_and_unknown_cli_argument_fail_without_writes(self):
        before = self.snapshot()
        run = subprocess.run([sys.executable, "-B", str(SCRIPT), "--repo", str(self.repo)],
                             cwd=str(self.repo), env=self.env, capture_output=True,
                             text=True, timeout=15)
        self.output(run, "FAIL")
        self.assertEqual(self.snapshot(), before)
        self.rejected(extra=("--unexpected-option",))

    def test_stale_expected_head_and_registry_hash_fail_without_writes(self):
        for field, value in [("expected_head", "0" * 40), ("expected_registry_sha256", "0" * 64)]:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.data)
                changed[field] = value
                self.save_packet(changed)
                self.rejected()

    def test_advanced_head_rejects_previously_prepared_packet(self):
        self.write("README.md", "A subsequent synthetic change.\n")
        self.commit("later synthetic change")
        self.rejected()

    def test_dirty_other_tracked_file_unstaged_or_staged_rejected(self):
        self.write("README.md", "Uncommitted unrelated work.\n")
        self.rejected()
        self.git("add", "README.md")
        self.rejected()

    def test_uncommitted_registry_drift_cannot_be_adopted_by_packet_hash(self):
        self.registry.write_bytes(self.original + b"# Uncommitted inventory drift.\n")
        self.rejected()
        self.data["expected_registry_sha256"] = sha256(self.registry.read_bytes())
        self.save_packet()
        self.rejected()
        self.git("add", REGISTRY)
        self.rejected()

    def test_changed_applied_registry_is_not_idempotent(self):
        self.output(self.invoke(apply=True), "APPLIED")
        self.registry.write_bytes(self.registry.read_bytes() + b"# Uncommitted post-apply drift.\n")
        self.rejected()

    def test_top_level_exact_keys_duplicate_missing_and_extra(self):
        for key in self.data:
            with self.subTest(key=key, mutation="missing"):
                changed = copy.deepcopy(self.data)
                del changed[key]
                self.save_packet(changed)
                self.rejected()
            with self.subTest(key=key, mutation="duplicate"):
                raw = json.dumps(self.data)[:-1] + "," + json.dumps(key) + ":" + json.dumps(self.data[key]) + "}"
                self.save_packet(raw=raw.encode("ascii"))
                self.rejected()
        changed = dict(self.data, unexpected=SECRET)
        self.save_packet(changed)
        self.rejected()

    def test_app_exact_keys_duplicate_missing_and_extra_at_every_record(self):
        for index in range(4):
            for key in self.data["apps"][index]:
                with self.subTest(index=index, key=key, mutation="missing"):
                    changed = copy.deepcopy(self.data)
                    del changed["apps"][index][key]
                    self.save_packet(changed)
                    self.rejected()
                with self.subTest(index=index, key=key, mutation="duplicate"):
                    original = json.dumps(self.data["apps"][index])
                    duplicate = original[:-1] + "," + json.dumps(key) + ":" + json.dumps(self.data["apps"][index][key]) + "}"
                    self.save_packet(raw=json.dumps(self.data).replace(original, duplicate).encode("ascii"))
                    self.rejected()
            changed = copy.deepcopy(self.data)
            changed["apps"][index]["unexpected"] = SECRET
            self.save_packet(changed)
            self.rejected()

    def test_wrong_schema_repository_owner_and_hash_grammar(self):
        invalid = {
            "schema": ["heleos.github-app-decisions/v2", "", 1],
            "repository": ["someone/Heleos-spark", "bbukolla-eng/heleos-spark", "", None],
            "decision_owner": ["Repository Owner", "bekim bukolla", " Bekim Bukolla", "Bekim Bukolla ", None],
            "expected_head": ["A" * 40, "0" * 39, "g" * 40, self.head + " ", 123],
            "expected_registry_sha256": ["A" * 64, "0" * 63, "g" * 64, None],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    changed = copy.deepcopy(self.data)
                    changed[field] = value
                    self.save_packet(changed)
                    self.rejected()

    def test_packet_app_set_duplicate_missing_extra_and_case_changed(self):
        mutations = [self.data["apps"][:-1], self.data["apps"] + [self.data["apps"][0]],
                     self.data["apps"][:3] + [self.data["apps"][0]]]
        for names in mutations:
            with self.subTest(apps=names):
                self.save_packet(dict(self.data, apps=names))
                self.rejected()
        for index in range(4):
            for name in [APPS[index].lower(), "Unregistered App", "", APPS[index] + " "]:
                with self.subTest(index=index, name=name):
                    changed = copy.deepcopy(self.data)
                    changed["apps"][index]["name"] = name
                    self.save_packet(changed)
                    self.rejected()

    def test_invalid_json_container_and_field_types(self):
        for value in [None, [], "packet", 3, True]:
            with self.subTest(top=value):
                self.save_packet(value, raw=json.dumps(value).encode("ascii"))
                self.rejected()
        for value in [None, {}, "apps", 4, True, [None] * 4, ["app"] * 4]:
            with self.subTest(apps=value):
                self.save_packet(dict(self.data, apps=value))
                self.rejected()
        for key in self.data["apps"][0]:
            for value in [None, 1, False, [], {}]:
                with self.subTest(field=key, value=value):
                    changed = copy.deepcopy(self.data)
                    changed["apps"][0][key] = value
                    self.save_packet(changed)
                    self.rejected()

    def test_invalid_and_valid_calendar_dates(self):
        invalid = ["", "pending", "2026-9-09", "2026-09-9", "2026/09/09",
                   "2026-09-09T00:00:00Z", " 2026-09-09", "2026-09-09 ",
                   "2026-00-09", "2026-13-09", "2026-09-00", "2026-09-31",
                   "2026-02-29", "1900-02-29", "0000-01-01", "２０２６-09-09", None, 20260909]
        for date in invalid:
            with self.subTest(date=date):
                self.save_packet(dict(self.data, decision_date=date))
                self.rejected()
        for date in ["2024-02-29", "2000-02-29", "2026-12-31", "0001-01-01"]:
            with self.subTest(date=date):
                self.save_packet(dict(self.data, decision_date=date))
                self.output(self.invoke(), "DRY_RUN")

    def test_every_rust_placeholder_spelling_rejected_in_resolved_fields(self):
        placeholders = ["", "  ", "pending", "Pending owner decision", "unknown", " UNKNOWN ",
                        "not inventoried", "not-inventoried", "not inventoried; no repository use authorized",
                        "not evaluated", "tbd", "unversioned", "latest", "owner_decision_required",
                        "unavailable", "PENDING: installation", "not_evaluated", "owner-decision-required"]
        for key in ["version_or_digest", "permissions", "evaluation", "decision_evidence", "installation_evidence"]:
            for value in placeholders:
                with self.subTest(field=key, value=value):
                    changed = copy.deepcopy(self.data)
                    changed["apps"][0][key] = value
                    self.save_packet(changed)
                    self.rejected()

    def test_unresolved_inventory_words_rejected_anywhere(self):
        values = ["provider runtime version/digest unavailable from public GitHub metadata; public app id=9426",
                  "public application-declared: metadata=read; actual installation grants, repository selection, and suspension unknown",
                  "public metadata observed; owner disposition unresolved",
                  "application declares metadata=read; installation grants not-inventoried",
                  "public app reviewed; installation pending evaluation",
                  "reviewed yet not_evaluated", "inventory UNRESOLVED", "reviewed NOT EVALUATED"]
        for key in ["version_or_digest", "permissions", "evaluation"]:
            for value in values:
                with self.subTest(field=key, value=value):
                    changed = copy.deepcopy(self.data)
                    changed["apps"][0][key] = value
                    self.save_packet(changed)
                    self.rejected()

    def test_substantive_token_boundaries_and_inactive_reasons_remain_valid(self):
        changed = copy.deepcopy(self.data)
        changed["apps"][0]["version_or_digest"] = "service-pendingfix-2026.09.1"
        changed["apps"][0]["permissions"] = "metadata=read; unknownness detector disabled"
        changed["apps"][0]["evaluation"] = "pendingfix release reviewed against policy"
        changed["apps"][2]["version_or_digest"] = "unavailable: installation removed; prior version unknown"
        changed["apps"][3]["permissions"] = "unavailable: installation removed; prior grants unknown"
        self.save_packet(changed)
        self.output(self.invoke(), "DRY_RUN")

    def test_app_and_installation_ids_are_not_runtime_versions(self):
        for value in ["123456", "app-id: 123456", "GitHub App ID 123456", "installation-id: 123456", "installation-123456"]:
            with self.subTest(value=value):
                changed = copy.deepcopy(self.data)
                changed["apps"][0]["version_or_digest"] = value
                self.save_packet(changed)
                self.rejected()

    def test_dispositions_and_egress_must_be_compatible(self):
        for disposition in ["owner_decision_required", "RETAIN", "keep", "", "remove ", None]:
            with self.subTest(disposition=disposition):
                changed = copy.deepcopy(self.data)
                changed["apps"][0]["disposition"] = disposition
                self.save_packet(changed)
                self.rejected()
        for index in range(4):
            for egress in ["approved:", "approved: pending", "approved: unknown", "allowed", "", "Approved: public metadata"]:
                with self.subTest(index=index, egress=egress):
                    changed = copy.deepcopy(self.data)
                    changed["apps"][index]["egress"] = egress
                    self.save_packet(changed)
                    self.rejected()
        for index in [2, 3]:
            changed = copy.deepcopy(self.data)
            changed["apps"][index]["egress"] = "approved: public repository metadata"
            self.save_packet(changed)
            self.rejected()
        changed = copy.deepcopy(self.data)
        for app in changed["apps"]:
            app["egress"] = "prohibited by synthetic owner decision"
        self.save_packet(changed)
        self.output(self.invoke(), "DRY_RUN")

    def test_unavailable_exception_only_for_inactive_version_and_permissions(self):
        for index in range(4):
            for key in ["version_or_digest", "permissions"]:
                changed = copy.deepcopy(self.data)
                changed["apps"][index][key] = "unavailable: installation disabled or removed; see installation evidence"
                self.save_packet(changed)
                with self.subTest(index=index, field=key, value="reason"):
                    if index < 2:
                        self.rejected()
                    else:
                        self.output(self.invoke(), "DRY_RUN")
                for value in ["unavailable", "unavailable:", "unavailable: pending", "unavailable: unknown", "Unavailable: installation removed", "unavailable : installation removed"]:
                    with self.subTest(index=index, field=key, value=value):
                        changed["apps"][index][key] = value
                        self.save_packet(changed)
                        self.rejected()
            for key in ["evaluation", "decision_evidence", "installation_evidence"]:
                with self.subTest(index=index, field=key):
                    changed = copy.deepcopy(self.data)
                    changed["apps"][index][key] = "unavailable: installation removed"
                    self.save_packet(changed)
                    self.rejected()

    def test_controls_non_ascii_untrimmed_and_overlength_fields(self):
        limits = {"version_or_digest": 4096, "permissions": 8192, "egress": 4096,
                  "evaluation": 8192, "decision_evidence": 2048, "installation_evidence": 2048}
        for key, limit in limits.items():
            for value in [" leading", "trailing ", "embedded\nnewline", "embedded\ttab", "embedded\x00nul",
                          "embedded\x1bescape", "embedded\x7fdel", "caf\u00e9", "x" * (limit + 1)]:
                with self.subTest(field=key, value=repr(value[:30])):
                    changed = copy.deepcopy(self.data)
                    changed["apps"][0][key] = value
                    self.save_packet(changed)
                    self.rejected()
            changed = copy.deepcopy(self.data)
            prefix = "approved:" if key == "egress" else "evidence-"
            changed["apps"][0][key] = prefix + "x" * (limit - len(prefix))
            self.save_packet(changed)
            with self.subTest(field=key, boundary="exact limit"):
                self.output(self.invoke(), "DRY_RUN")

    def test_printable_quotes_and_backslashes_round_trip_as_data(self):
        value = 'evidence/"literal"/directory\\file.txt'
        self.data["apps"][0]["decision_evidence"] = value
        self.save_packet()
        self.output(self.invoke(apply=True), "APPLIED")
        self.assertEqual(parse_registry(self.registry.read_bytes())[0]["decision_evidence"], value)

    def test_packet_size_limit_exact_boundary_and_one_byte_over(self):
        raw = json.dumps(self.data).encode("ascii")
        self.save_packet(raw=raw + b" " * (65536 - len(raw)))
        self.output(self.invoke(), "DRY_RUN")
        self.save_packet(raw=raw + b" " * (65537 - len(raw)))
        self.rejected()

    def test_malformed_json_and_packet_secret_are_never_echoed(self):
        for raw in [b"", b"{", ('{"unexpected":"' + SECRET + '"} trailing').encode("ascii"),
                    b"\xff\xfe", b"{\"schema\":NaN}", b"{\"schema\":Infinity}"]:
            with self.subTest(raw=raw[:10]):
                self.save_packet(raw=raw)
                self.rejected()
        changed = copy.deepcopy(self.data)
        changed["apps"][0]["name"] = SECRET
        self.save_packet(changed)
        self.rejected()

    def test_missing_symlink_directory_and_fifo_packet_rejected(self):
        self.rejected(packet=self.base / "missing.json")
        link = self.base / "symlink.json"
        link.symlink_to(self.packet)
        self.rejected(packet=link)
        directory = self.base / "packet directory"
        directory.mkdir()
        self.rejected(packet=directory)
        if hasattr(os, "mkfifo"):
            fifo = self.base / "packet.fifo"
            os.mkfifo(fifo)
            self.rejected(packet=fifo)

    def test_current_registry_malformed_duplicate_extra_missing_and_renamed(self):
        text = self.original.decode("ascii")
        variants = [
            text + "not TOML\n", text.replace('owner = "repository owner"', 'owner = "repository owner"\nowner = "duplicate"', 1),
            text.replace('owner = "repository owner"', 'owner = "repository owner"\nunexpected = "extra"', 1),
            text.replace('owner = "repository owner"\n', "", 1),
            text.replace('name = "Azure Pipelines"', 'name = "Renamed App"', 1),
            text.replace('name = "ECC Tools"', 'name = "Azure Pipelines"', 1),
            text[:text.rfind("[[github_app]]")], text + text[text.rfind("[[github_app]]"):],
            text.replace('[[github_app]]', '[[github_apps]]', 1),
            text.replace('name = "Azure Pipelines"', 'name = "azure pipelines"', 1),
        ]
        for index, value in enumerate(variants):
            with self.subTest(variant=index):
                self.registry.write_text(value, encoding="ascii")
                self.commit("synthetic malformed registry %d" % index)
                changed = copy.deepcopy(self.data)
                changed["expected_head"] = self.git("rev-parse", "HEAD").strip()
                changed["expected_registry_sha256"] = sha256(self.registry.read_bytes())
                self.save_packet(changed)
                self.rejected()

    def test_symlink_and_missing_registry_rejected(self):
        target = self.base / "outside-registry.toml"
        target.write_bytes(self.original)
        self.registry.unlink()
        self.registry.symlink_to(target)
        self.commit("synthetic symlink registry")
        self.data["expected_head"] = self.git("rev-parse", "HEAD").strip()
        self.save_packet()
        self.rejected()
        self.registry.unlink()
        self.commit("synthetic absent registry")
        self.data["expected_head"] = self.git("rev-parse", "HEAD").strip()
        self.save_packet()
        self.rejected()

    def test_missing_registered_main_fails_closed(self):
        self.git("branch", "-m", "candidate")
        self.rejected()

    def test_multiple_registered_main_checkouts_fail_closed(self):
        worktree = self.repo / ".worktrees" / "second-main"
        self.git("worktree", "add", "--force", str(worktree), "main")
        self.rejected(repo=worktree)

    def test_git_environment_overrides_cannot_redirect_repository_or_write(self):
        poisoned = self.base / "poisoned target"
        poisoned.mkdir()
        self.git("init", "--initial-branch=main", cwd=poisoned)
        (poisoned / "sentinel.txt").write_text("Untouched alternate target.\n")
        for overrides in [
            {"GIT_DIR": str(poisoned / ".git"), "GIT_WORK_TREE": str(poisoned)},
            {"GIT_DIR": str(poisoned / ".git")},
            {"GIT_WORK_TREE": str(poisoned)},
            {"GIT_INDEX_FILE": str(self.base / "poisoned-index")},
            {"GIT_COMMON_DIR": str(poisoned / ".git")},
            {"GIT_NAMESPACE": "poisoned"},
            {"GIT_OBJECT_DIRECTORY": str(poisoned / ".git" / "objects")},
            {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(poisoned / ".git" / "objects")},
            {"GIT_CONFIG_PARAMETERS": "'core.worktree=%s'" % poisoned},
            {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree", "GIT_CONFIG_VALUE_0": str(poisoned)},
        ]:
            with self.subTest(overrides=tuple(overrides)):
                self.rejected(env=dict(self.env, **overrides))

    def test_compact_json_and_human_output_are_deterministic_and_dry(self):
        before = self.snapshot()
        first = self.invoke()
        self.output(first, "DRY_RUN")
        self.assertEqual(first.stdout, self.invoke().stdout)
        first_human = self.invoke(extra=("--human",))
        second_human = self.invoke(extra=("--human",))
        self.assertEqual(first_human.returncode, 0, first_human.stderr)
        self.assertEqual(first_human.stderr, "")
        self.assertEqual(first_human.stdout, second_human.stdout)
        self.assertIn("DRY_RUN", first_human.stdout)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
