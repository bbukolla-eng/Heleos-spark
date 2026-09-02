"""Finite operator CLI grammar, routing, and public result contracts."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.build_fabric_support import valid_task_manifest
from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.dispatch import DispatchResult
from tools.helios_build.errors import ContractError, TransitionError
from tools.helios_build.types import AttemptOutcome


class BuildCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(__file__).resolve().parents[1]
        self.temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temporary.name) / "state"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        from tools.helios_build.cli import main

        stdout = io.StringIO()
        stderr = io.StringIO()
        complete = [
            "--repo-root",
            str(self.repo),
            "--state-root",
            str(self.state_root),
            *argv,
        ]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(complete)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_all_frozen_commands_parse_and_reach_their_registered_handlers(self) -> None:
        """A removed/wrong registration breaks the command's unique output marker."""
        task = "a" * 64
        adapter = Path(self.temporary.name) / "adapters.json"
        handoff = Path(self.temporary.name) / "handoff.json"
        patch_path = Path(self.temporary.name) / "change.patch"
        raw = Path(self.temporary.name) / "raw.bin"
        review = Path(self.temporary.name) / "review.json"
        routes = (
            (["doctor"], "doctor"),
            (["graph", "status"], "graph-status"),
            (["task", "validate", str(self.repo / "build_control" / "tasks" / "draft.json")], "task-validate"),
            (["dispatch", task, "--adapter-config", str(adapter)], "dispatch"),
            (["collect", task], "collect"),
            (["review", task, "--adapter-config", str(adapter)], "review"),
            (["integrate", task, "--commit-sha", "a" * 40], "integrate"),
            ([
                "external-session", "begin", task,
                "--worker-profile", "codex-builder-v1",
                "--provider", "CODEX",
                "--session-id", "session-a",
                "--role", "BUILDER",
                "--routing-reason", "EXTERNAL_SESSION_SELECTED",
            ], "external-begin"),
            ([
                "external-session", "import-handoff", task,
                "--assignment", "b" * 64,
                "--handoff", str(handoff),
                "--patch", str(patch_path),
                "--raw-evidence", str(raw),
            ], "external-handoff"),
            ([
                "external-session", "import-review", task,
                "--assignment", "c" * 64,
                "--review", str(review),
                "--raw-evidence", str(raw),
            ], "external-review"),
            (["report"], "report"),
        )
        replacements = {
            "build_doctor_report": lambda *_: {"marker": "doctor"},
            "graph_status": lambda *_: {"marker": "graph-status"},
            "_validate_task_reference": lambda *_: {"marker": "task-validate"},
            "dispatch_task": lambda *_: {"marker": "dispatch"},
            "collect_task": lambda *_: {"marker": "collect"},
            "review_task": lambda *_: {"marker": "review"},
            "record_integration": lambda *_: {"marker": "integrate"},
            "begin_external_session": lambda *_: {"marker": "external-begin"},
            "import_external_handoff": lambda *_: {"marker": "external-handoff"},
            "import_external_review": lambda *_: {"marker": "external-review"},
            "build_report": lambda *_: {"marker": "report"},
        }
        with contextlib.ExitStack() as stack:
            for name, replacement in replacements.items():
                stack.enter_context(
                    patch(f"tools.helios_build.cli.{name}", replacement)
                )
            for argv, marker in routes:
                with self.subTest(argv=argv):
                    code, stdout, stderr = self._run(argv)
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(stdout), {"marker": marker})
                    self.assertEqual(stderr, "")

    def test_duplicate_core_registration_fails_before_dispatch(self) -> None:
        """A later extension cannot silently replace an existing command."""
        from tools.helios_build.cli import register_command

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        handlers: dict[str, object] = {}

        register_command(subparsers, handlers, "same", lambda _: None, lambda *_: {})
        with self.assertRaisesRegex(ContractError, "duplicate command"):
            register_command(
                subparsers, handlers, "same", lambda _: None, lambda *_: {}
            )

    def test_task_validation_publishes_the_exact_content_addressed_manifest(self) -> None:
        """Changing canonicalization or publishing outside tasks breaks the digest proof."""
        task = valid_task_manifest()
        digest = sha256_hex(canonical_json_bytes(task))
        source_directory = Path(
            tempfile.mkdtemp(prefix="cli-task-", dir=self.repo / "build_control" / "tasks")
        )
        source = source_directory / "draft.json"
        source.write_bytes(canonical_json_bytes(task))
        published = (
            self.repo
            / "build_control"
            / "tasks"
            / "sha256"
            / digest[:2]
            / f"{digest}.json"
        )
        try:
            code, stdout, stderr = self._run(["task", "validate", str(source)])
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            payload = json.loads(stdout)
            self.assertEqual(payload["task_manifest_sha256"], digest)
            self.assertEqual(published.read_bytes(), canonical_json_bytes(task))
        finally:
            source.unlink(missing_ok=True)
            source_directory.rmdir()
            published.unlink(missing_ok=True)
            for directory in (published.parent, published.parent.parent):
                try:
                    directory.rmdir()
                except OSError:
                    pass

    def test_output_and_exit_codes_are_stable_and_structured(self) -> None:
        """Contract, blocked, failed, and unknown outcomes remain distinguishable."""
        with patch(
            "tools.helios_build.cli.build_doctor_report",
            side_effect=ContractError("bad contract"),
        ):
            code, stdout, stderr = self._run(["doctor"])
        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(
            stderr,
            '{"error":{"code":"CONTRACT_ERROR","message":"bad contract"}}\n',
        )

        with patch(
            "tools.helios_build.cli.graph_status",
            side_effect=TransitionError("not ready"),
        ):
            code, stdout, stderr = self._run(["graph", "status"])
        self.assertEqual((code, stdout), (3, ""))
        self.assertEqual(json.loads(stderr)["error"]["code"], "BLOCKED")

        with patch(
            "tools.helios_build.cli.build_report",
            side_effect=RuntimeError("unexpected"),
        ):
            code, stdout, stderr = self._run(["report"])
        self.assertEqual((code, stdout), (4, ""))
        self.assertEqual(json.loads(stderr)["error"]["code"], "FAILED")

        unknown = DispatchResult(
            run_identity_sha256="d" * 64,
            replayed=False,
            outcome=AttemptOutcome.OUTCOME_UNKNOWN,
            state="FAILED",
            reason_code="PROCESS_OUTCOME_UNKNOWN",
            attempt=None,
        )
        with patch("tools.helios_build.cli.dispatch_task", return_value=unknown):
            code, stdout, stderr = self._run(
                ["dispatch", "a" * 64, "--adapter-config", str(Path(self.temporary.name) / "a.json")]
            )
        self.assertEqual(code, 5)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["outcome"], "OUTCOME_UNKNOWN")

        blocked = SimpleNamespace(
            state="BLOCKED",
            outcome=AttemptOutcome.FAILED,
            reason_code="ADAPTER_NOT_CONFIGURED",
        )
        with patch("tools.helios_build.cli.dispatch_task", return_value=blocked):
            code, _, _ = self._run(
                ["dispatch", "a" * 64, "--adapter-config", str(Path(self.temporary.name) / "a.json")]
            )
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
