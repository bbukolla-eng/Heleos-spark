"""Minimal local-only CLI for database initialization and loopback serving."""

from __future__ import annotations

import argparse
from pathlib import Path
from wsgiref.simple_server import make_server

from .api import create_wsgi_app
from .db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="helios-takeoff-core")
    subcommands = parser.add_subparsers(dest="command", required=True)
    initialize = subcommands.add_parser("init", help="create or upgrade a local P0 database")
    initialize.add_argument("--database", required=True, type=Path)
    serve = subcommands.add_parser("serve", help="serve the local JSON API on loopback by default")
    serve.add_argument("--database", required=True, type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8787, type=int)
    arguments = parser.parse_args(argv)
    if arguments.command == "init":
        Database(arguments.database).initialize()
        print(f"initialized {arguments.database}")
        return 0
    application = create_wsgi_app(arguments.database)
    with make_server(arguments.host, arguments.port, application) as server:
        print(f"HELIOS P0 API listening on http://{arguments.host}:{arguments.port}")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
