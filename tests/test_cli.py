"""Tests that catch a CLI bootstrap that leaves a local database unusable."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.cli import main
from helios_takeoff_core.db import Database


class CliTests(unittest.TestCase):
    """The production change caught is a CLI init command that does not apply the canonical migrations."""

    def test_init_creates_a_ready_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p0.sqlite3"

            exit_code = main(["init", "--database", str(path)])

            self.assertEqual(exit_code, 0)
            with Database(path).connection() as connection:
                self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 17)

    def test_serve_rejects_a_non_loopback_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p0.sqlite3"
            for host in ("0.0.0.0", "192.168.1.10", "localhost"):
                with self.subTest(host=host):
                    with patch("helios_takeoff_core.cli.make_server") as make_server:
                        with redirect_stderr(io.StringIO()) as error_output:
                            with self.assertRaises(SystemExit) as raised:
                                main(["serve", "--database", str(path), "--host", host])
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn("loopback", error_output.getvalue())
                    make_server.assert_not_called()

    def test_serve_binds_a_loopback_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p0.sqlite3"
            for host in ("127.0.0.1", "::1"):
                with self.subTest(host=host):
                    seen: dict[str, object] = {}

                    class Server:
                        def __enter__(self) -> "Server":
                            return self

                        def __exit__(self, *_exc: object) -> bool:
                            return False

                        def serve_forever(self) -> None:
                            return None

                    def fake_make_server(bound_host: str, port: int, application: object) -> Server:
                        seen["host"] = bound_host
                        seen["port"] = port
                        seen["application"] = application
                        return Server()

                    with patch("helios_takeoff_core.cli.make_server", fake_make_server):
                        exit_code = main(["serve", "--database", str(path), "--host", host])

                    self.assertEqual(exit_code, 0)
                    self.assertEqual(seen["host"], host)
                    self.assertEqual(seen["port"], 8787)


if __name__ == "__main__":
    unittest.main()
