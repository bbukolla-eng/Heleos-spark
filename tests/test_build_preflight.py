"""Command-free worker policy and exact finite preflight tests."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.helios_build.doctor as doctor_module
import tools.helios_build.process as process_module
import tools.helios_build.profiles as profiles_module
from tools.helios_build.errors import ContractError
from tools.helios_build.paths import BuildPaths
from tools.helios_build.process import run_bounded_process
from tools.helios_build.profiles import (
    adapter_configuration_sha256,
    load_adapter_configuration,
    load_worker_profile,
)
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.doctor import build_doctor_report, preflight_worker


class BuildPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.schemas = SchemaRegistry(self.repo_root)
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.fixture_repo = self.root / "repo"
        shutil.copytree(
            self.repo_root / "build_control" / "schemas",
            self.fixture_repo / "build_control" / "schemas",
        )
        shutil.copytree(
            self.repo_root / "build_control" / "worker_profiles",
            self.fixture_repo / "build_control" / "worker_profiles",
        )
        (self.fixture_repo / "build_control" / "graph" / "receipts").mkdir(parents=True)
        self.paths = BuildPaths(repo_root=self.fixture_repo, state_root=self.root / "state")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _profile(self, profile_id: str = "claude-builder-v1"):
        return load_worker_profile(
            self.repo_root / "build_control" / "worker_profiles" / f"{profile_id}.json",
            self.schemas,
        )

    def _adapter_payload(
        self,
        *,
        worker_id: str = "claude",
        executable: Path | None = None,
        executable_sha256: str | None = None,
        preflight_argv: list[str] | None = None,
        dispatch_argv: list[str] | None = None,
        environment_variable_names: list[str] | None = None,
        max_capture_bytes: int = 4096,
        preflight_timeout_seconds: int = 2,
    ) -> dict[str, object]:
        executable = executable or Path(sys.executable).resolve()
        executable_sha256 = executable_sha256 or hashlib.sha256(executable.read_bytes()).hexdigest()
        return {
            "protocol": "helios.build.adapter-config/v1",
            "host_id": "test-host",
            "adapters": [
                {
                    "worker_id": worker_id,
                    "adapter_name": f"{worker_id}-fixture",
                    "adapter_revision": "fixture-v1",
                    "executable": str(executable),
                    "executable_sha256": executable_sha256,
                    "preflight_argv": preflight_argv or ["-c", "raise SystemExit(0)"],
                    "dispatch_argv": dispatch_argv
                    or [
                        "--task",
                        "{task_manifest}",
                        "--handoff",
                        "{handoff_path}",
                        "--checkpoint",
                        "{checkpoint_dir}",
                        "--worktree",
                        "{worktree}",
                    ],
                    "environment_variable_names": environment_variable_names or [],
                    "preflight_timeout_seconds": preflight_timeout_seconds,
                    "attempt_timeout_seconds": 10,
                    "max_capture_bytes": max_capture_bytes,
                    "result_protocol": "helios.build.worker-handoff/v1",
                }
            ],
        }

    def _load_adapter(self, payload: dict[str, object] | None = None):
        path = self.root / f"adapter-{time.time_ns()}.json"
        path.write_text(json.dumps(payload or self._adapter_payload()), encoding="utf-8")
        return load_adapter_configuration(path, self.schemas)[0]

    def test_profiles_are_command_free_and_preserve_role_authority(self) -> None:
        expected_roles = {
            "codex-control-v1": ("CONTROLLER", "INTEGRATOR"),
            "codex-builder-v1": ("BUILDER",),
            "codex-reviewer-v1": ("REVIEWER",),
            "claude-builder-v1": ("ARCHITECT", "BUILDER", "REVIEWER"),
            "kimi-builder-v1": ("BUILDER", "REVIEWER"),
            "grok-builder-v1": ("BUILDER", "REVIEWER"),
            "cursor-builder-v1": ("BUILDER", "REVIEWER"),
            "copilot-builder-v1": ("BUILDER", "REVIEWER"),
        }
        for profile_id, roles in expected_roles.items():
            with self.subTest(profile_id=profile_id):
                profile_path = (
                    self.repo_root / "build_control" / "worker_profiles" / f"{profile_id}.json"
                )
                profile = load_worker_profile(profile_path, self.schemas)
                self.assertEqual(profile.roles, roles)
                source = json.loads(profile_path.read_text(encoding="utf-8"))
                self.assertNotIn("command", source)
                self.assertNotIn("executable", source)
                self.assertEqual(profile.skills, ())
                self.assertEqual(profile.plugins, ())
        self.assertEqual(self._profile("codex-builder-v1").transports, ("EXTERNAL_SESSION",))
        self.assertEqual(self._profile("codex-reviewer-v1").transports, ("EXTERNAL_SESSION",))
        self.assertNotIn("ATHENA", {item.worker_id for item in map(self._profile, expected_roles)})

    def test_adapter_normalization_sorts_environment_names_and_rejects_tokens(self) -> None:
        first = self._load_adapter(
            self._adapter_payload(environment_variable_names=["Z_SECRET", "A_SECRET"])
        )
        second = self._load_adapter(
            self._adapter_payload(environment_variable_names=["A_SECRET", "Z_SECRET"])
        )
        self.assertEqual(first.environment_variable_names, ("A_SECRET", "Z_SECRET"))
        self.assertEqual(adapter_configuration_sha256(first), adapter_configuration_sha256(second))

        invalid_dispatch = self._adapter_payload(dispatch_argv=["{undeclared}"])
        with self.assertRaisesRegex(ContractError, "dispatch_argv token"):
            self._load_adapter(invalid_dispatch)
        invalid_preflight = self._adapter_payload(preflight_argv=["--check", "{worktree}"])
        with self.assertRaisesRegex(ContractError, "preflight_argv must not contain tokens"):
            self._load_adapter(invalid_preflight)

    def test_unconfigured_profile_is_unavailable(self) -> None:
        result = preflight_worker(self.paths, self._profile(), None)
        self.assertEqual(
            (result.availability, result.reason_code),
            ("UNAVAILABLE", "ADAPTER_NOT_CONFIGURED"),
        )
        self.assertIsNone(result.receipt_path)

    def test_success_receipt_contains_only_hashes_not_secrets_paths_or_raw_output(self) -> None:
        secret_name = "HELIOS_BUILD_TEST_SECRET"
        secret = "DO-NOT-PERSIST-THIS-SECRET"
        executable_text = str(Path(sys.executable))
        adapter = self._load_adapter(
            self._adapter_payload(
                environment_variable_names=[secret_name],
                preflight_argv=[
                    "-c",
                    (
                        "import os,sys;"
                        f"value=os.environ.get('{secret_name}');"
                        "print(value);print(sys.executable,file=sys.stderr);"
                        "raise SystemExit(0 if value else 9)"
                    ),
                ],
            )
        )
        with patch.dict(os.environ, {secret_name: secret}, clear=False):
            available = preflight_worker(self.paths, self._profile(), adapter)

        self.assertEqual(
            (available.availability, available.reason_code),
            ("AVAILABLE", "PREFLIGHT_READY"),
        )
        self.assertIsNotNone(available.receipt_path)
        assert available.receipt_path is not None
        receipt = available.receipt_path.read_bytes()
        self.assertNotIn(secret.encode(), receipt)
        self.assertNotIn(executable_text.encode(), receipt)
        self.assertNotIn(b"-c", receipt)
        receipt_payload = json.loads(receipt)
        self.schemas.validate(receipt_payload, "preflight-receipt-v1.schema.json")
        self.assertEqual(receipt_payload["stdout_sha256"], hashlib.sha256((secret + "\n").encode()).hexdigest())
        self.assertTrue(self.paths.state_root.joinpath("preflight").is_dir())

    def test_exact_unavailable_reason_codes(self) -> None:
        profile = self._profile()
        cases = [
            (
                "missing",
                self._adapter_payload(
                    executable=self.root / "missing", executable_sha256="f" * 64
                ),
                {},
                "EXECUTABLE_MISSING",
            ),
            (
                "digest",
                self._adapter_payload(executable_sha256="0" * 64),
                {},
                "EXECUTABLE_HASH_MISMATCH",
            ),
            (
                "environment",
                self._adapter_payload(environment_variable_names=["HELIOS_BUILD_ABSENT"]),
                {},
                "ENVIRONMENT_MISSING",
            ),
            (
                "nonzero",
                self._adapter_payload(preflight_argv=["-c", "raise SystemExit(7)"]),
                {},
                "PREFLIGHT_EXIT_NONZERO",
            ),
            (
                "truncated",
                self._adapter_payload(
                    preflight_argv=["-c", "import sys;sys.stdout.write('x'*10000)"],
                    max_capture_bytes=32,
                ),
                {},
                "PREFLIGHT_OUTPUT_TRUNCATED",
            ),
            (
                "timeout",
                self._adapter_payload(
                    preflight_argv=[
                        "-c",
                        "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)",
                    ],
                    preflight_timeout_seconds=1,
                ),
                {},
                "PREFLIGHT_TIMEOUT",
            ),
        ]
        for name, payload, environment, reason_code in cases:
            with self.subTest(name=name), patch.dict(os.environ, environment, clear=True):
                started = time.monotonic()
                result = preflight_worker(self.paths, profile, self._load_adapter(payload))
                self.assertEqual((result.availability, result.reason_code), ("UNAVAILABLE", reason_code))
                if name == "timeout":
                    self.assertLess(time.monotonic() - started, 4)

    def test_bounded_process_drains_both_streams_without_deadlock(self) -> None:
        chunk_size = 128 * 1024
        result = run_bounded_process(
            [
                sys.executable,
                "-c",
                (
                    "import sys;"
                    f"sys.stdout.buffer.write(b'o'*{chunk_size});sys.stdout.flush();"
                    f"sys.stderr.buffer.write(b'e'*{chunk_size});sys.stderr.flush()"
                ),
            ],
            b"",
            self.root,
            {},
            2,
            64,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual((len(result.stdout), len(result.stderr)), (64, 64))
        self.assertEqual((result.stdout_truncated, result.stderr_truncated), (True, True))

    def test_descendant_retaining_stdout_and_stderr_times_out_the_process_group(self) -> None:
        started = time.monotonic()
        result = run_bounded_process(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess,sys;"
                    "subprocess.Popen([sys.executable,'-c','import time;time.sleep(3)'])"
                ),
            ],
            b"",
            self.root,
            {},
            1,
            4096,
        )
        self.assertTrue(result.timed_out)
        self.assertLess(time.monotonic() - started, 2.5)

    def test_descendant_retaining_blocked_stdin_times_out_the_process_group(self) -> None:
        started = time.monotonic()
        result = run_bounded_process(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess,sys;"
                    "subprocess.Popen([sys.executable,'-c','import time;time.sleep(3)'])"
                ),
            ],
            b"x" * (1024 * 1024),
            self.root,
            {},
            1,
            4096,
        )
        self.assertTrue(result.timed_out)
        self.assertLess(time.monotonic() - started, 2.5)

    def test_process_surfaces_drain_and_write_failures(self) -> None:
        for helper_name in ("_read_from_fd", "_write_to_fd"):
            with self.subTest(helper_name=helper_name), patch.object(
                process_module,
                helper_name,
                side_effect=OSError("injected I/O failure"),
                create=True,
            ):
                result = run_bounded_process(
                    [sys.executable, "-c", "import time;time.sleep(0.05)"],
                    b"input",
                    self.root,
                    {},
                    1,
                    4096,
                )
                self.assertTrue(getattr(result, "capture_incomplete", False))
                self.assertTrue(getattr(result, "io_errors", ()))

    def test_preflight_maps_capture_errors_to_output_truncated(self) -> None:
        incomplete = types.SimpleNamespace(
            returncode=0,
            stdout=b"",
            stderr=b"",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            capture_incomplete=True,
            io_errors=("stdout:OSError",),
        )
        with patch.object(doctor_module, "run_bounded_process", return_value=incomplete):
            result = preflight_worker(self.paths, self._profile(), self._load_adapter())
        self.assertEqual(
            (result.availability, result.reason_code),
            ("UNAVAILABLE", "PREFLIGHT_OUTPUT_TRUNCATED"),
        )

    def test_preflight_rejects_final_executable_symlink(self) -> None:
        link = self.root / "python-link"
        link.symlink_to(Path(sys.executable).resolve())
        adapter = self._load_adapter(self._adapter_payload(executable=link))
        result = preflight_worker(self.paths, self._profile(), adapter)
        self.assertEqual(
            (result.availability, result.reason_code),
            ("UNAVAILABLE", "EXECUTABLE_MISSING"),
        )

    def test_preflight_rejects_executable_fifo_without_blocking(self) -> None:
        fifo = self.root / "executable-fifo"
        os.mkfifo(fifo)
        adapter = self._load_adapter(
            self._adapter_payload(executable=fifo, executable_sha256="f" * 64)
        )
        started = time.monotonic()
        result = preflight_worker(self.paths, self._profile(), adapter)
        self.assertEqual(result.reason_code, "EXECUTABLE_MISSING")
        self.assertLess(time.monotonic() - started, 1)

    def test_preflight_launches_verified_inode_after_path_replacement(self) -> None:
        executable = self.root / "verified-true"
        replacement = self.root / "replacement"
        shutil.copy2(Path("/bin/true"), executable)
        shutil.copy2(Path("/bin/false"), replacement)
        adapter = self._load_adapter(
            self._adapter_payload(executable=executable, preflight_argv=["ignored"])
        )
        real_runner = process_module.run_bounded_process

        def replace_then_run(argv, *args, **kwargs):
            os.replace(replacement, executable)
            return real_runner(argv, *args, **kwargs)

        with patch.object(doctor_module, "run_bounded_process", side_effect=replace_then_run):
            result = preflight_worker(self.paths, self._profile(), adapter)
        self.assertEqual(
            (result.availability, result.reason_code),
            ("AVAILABLE", "PREFLIGHT_READY"),
        )

    def test_adapter_config_must_be_external_and_not_a_final_symlink(self) -> None:
        fixture_schemas = SchemaRegistry(self.fixture_repo)
        repository_config = self.fixture_repo / "adapter.json"
        repository_config.write_text(json.dumps(self._adapter_payload()), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "outside the repository"):
            load_adapter_configuration(repository_config, fixture_schemas)

        external_config = self.root / "external-adapter.json"
        external_config.write_text(json.dumps(self._adapter_payload()), encoding="utf-8")
        linked_config = self.root / "linked-adapter.json"
        linked_config.symlink_to(external_config)
        with self.assertRaisesRegex(ContractError, "symlink"):
            load_adapter_configuration(linked_config, fixture_schemas)

    def test_adapter_config_rejects_intermediate_symlink_and_fifo_without_blocking(self) -> None:
        fixture_schemas = SchemaRegistry(self.fixture_repo)
        real_directory = self.root / "real-config-parent"
        real_directory.mkdir()
        config = real_directory / "adapter.json"
        config.write_text(json.dumps(self._adapter_payload()), encoding="utf-8")
        linked_directory = self.root / "linked-config-parent"
        linked_directory.symlink_to(real_directory, target_is_directory=True)
        with self.assertRaisesRegex(ContractError, "symlink or non-directory"):
            load_adapter_configuration(linked_directory / "adapter.json", fixture_schemas)

        fifo = self.root / "adapter-fifo"
        os.mkfifo(fifo)
        started = time.monotonic()
        with self.assertRaisesRegex(ContractError, "regular file"):
            load_adapter_configuration(fifo, fixture_schemas)
        self.assertLess(time.monotonic() - started, 1)

    def test_adapter_config_parses_the_descriptor_pinned_before_path_replacement(self) -> None:
        fixture_schemas = SchemaRegistry(self.fixture_repo)
        config = self.root / "pinned-adapter.json"
        replacement = self.root / "replacement-adapter.json"
        config.write_text(json.dumps(self._adapter_payload()), encoding="utf-8")
        replacement.write_text("not valid JSON", encoding="utf-8")
        real_reader = profiles_module._read_config_bytes

        def replace_then_read(descriptor, path):
            os.replace(replacement, config)
            return real_reader(descriptor, path)

        with patch.object(profiles_module, "_read_config_bytes", side_effect=replace_then_read):
            adapters = load_adapter_configuration(config, fixture_schemas)
        self.assertEqual(adapters[0].worker_id, "claude")

    def test_raw_artifact_replay_rejects_symlink_and_non_private_mode(self) -> None:
        empty_digest = hashlib.sha256(b"").hexdigest()
        for defect in ("symlink", "mode", "fifo"):
            with self.subTest(defect=defect):
                isolated_root = self.root / defect
                isolated_paths = BuildPaths(
                    repo_root=self.fixture_repo,
                    state_root=isolated_root / "state",
                )
                adapter = self._load_adapter()
                first = preflight_worker(isolated_paths, self._profile(), adapter)
                self.assertEqual(first.reason_code, "PREFLIGHT_READY")
                raw = (
                    isolated_paths.state_root
                    / "preflight"
                    / "raw"
                    / "sha256"
                    / empty_digest[:2]
                    / f"{empty_digest}.stdout"
                )
                if defect == "symlink":
                    raw.unlink()
                    target = isolated_root / "target"
                    target.write_bytes(b"")
                    raw.symlink_to(target)
                elif defect == "fifo":
                    raw.unlink()
                    os.mkfifo(raw, mode=0o600)
                else:
                    raw.chmod(0o644)
                started = time.monotonic()
                with self.assertRaises(ContractError):
                    preflight_worker(isolated_paths, self._profile(), adapter)
                if defect == "fifo":
                    self.assertLess(time.monotonic() - started, 1)

    def test_popen_reported_cwd_failure_is_not_executable_missing(self) -> None:
        def fail_cwd(*args, **kwargs):
            cwd = kwargs["cwd"]
            raise FileNotFoundError(errno.ENOENT, "injected cwd failure", os.fspath(cwd))

        with patch.object(process_module.subprocess, "Popen", side_effect=fail_cwd):
            with self.assertRaisesRegex(ContractError, "cwd setup"):
                preflight_worker(self.paths, self._profile(), self._load_adapter())

    def test_failed_run_directory_verification_leaves_private_residue(self) -> None:
        real_verify = doctor_module._verify_private_directory

        def fail_run_directory(descriptor, label):
            if label == "preflight run directory":
                raise ContractError("injected run verification failure")
            return real_verify(descriptor, label)

        with patch.object(
            doctor_module,
            "_verify_private_directory",
            side_effect=fail_run_directory,
        ):
            with self.assertRaisesRegex(ContractError, "injected run verification failure"):
                preflight_worker(self.paths, self._profile(), self._load_adapter())
        residues = list((self.paths.state_root / "preflight").glob("run-*"))
        self.assertEqual(len(residues), 1)
        metadata = residues[0].stat(follow_symlinks=False)
        self.assertTrue(stat.S_ISDIR(metadata.st_mode))
        self.assertEqual(metadata.st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
        self.assertEqual(list(residues[0].iterdir()), [])

    def test_external_state_setup_failure_is_not_executable_missing(self) -> None:
        invalid_state_root = self.root / "state-file"
        invalid_state_root.write_bytes(b"not a directory")
        invalid_paths = BuildPaths(
            repo_root=self.fixture_repo,
            state_root=invalid_state_root,
        )
        try:
            preflight_worker(invalid_paths, self._profile(), self._load_adapter())
        except ContractError:
            pass
        except OSError as error:
            self.fail(f"state setup leaked OSError instead of ContractError: {error}")
        else:
            self.fail("state setup failure was mislabeled as a preflight result")

    def test_doctor_reports_each_committed_profile_without_claiming_unconfigured_availability(self) -> None:
        config_path = self.root / "adapters.json"
        config_path.write_text(json.dumps(self._adapter_payload()), encoding="utf-8")
        report = build_doctor_report(self.paths, config_path)
        by_profile = {item["profile_id"]: item for item in report["workers"]}
        self.assertEqual(by_profile["claude-builder-v1"]["reason_code"], "PREFLIGHT_READY")
        self.assertEqual(by_profile["kimi-builder-v1"]["reason_code"], "ADAPTER_NOT_CONFIGURED")
        self.assertEqual(by_profile["codex-control-v1"]["availability"], "UNAVAILABLE")
        self.assertNotIn(str(Path(sys.executable)), json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
