"""Compact JSON CLI for the finite HELIOS P1B engine surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .db import Database
from .engine.compiler import compile_domain_pack
from .engine.evaluator import evaluate_subject, resolve_assembly
from .engine.repository import EngineRepository
from .errors import ValidationError


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValidationError(message)


def _emit(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _repository(database_path: Path) -> EngineRepository:
    database = Database(database_path)
    database.initialize()
    return EngineRepository(database)


def _stored_pack(
    repository: EngineRepository, *, pack_code: str, version: str
):
    document = repository.get_domain_pack(pack_code=pack_code, version=version)
    return compile_domain_pack(document)


def _add_pack_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--pack-code", required=True)
    parser.add_argument("--version", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="helios-engine")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_JsonArgumentParser)

    compile_command = commands.add_parser("compile", help="compile one strict domain pack")
    compile_command.add_argument("--input", required=True, type=Path)
    compile_command.add_argument("--output", type=Path)

    import_command = commands.add_parser("import", help="import one immutable domain pack")
    import_command.add_argument("--database", required=True, type=Path)
    import_command.add_argument("--input", required=True, type=Path)

    pack = commands.add_parser("pack", help="query domain packs")
    pack_commands = pack.add_subparsers(
        dest="pack_command", required=True, parser_class=_JsonArgumentParser
    )
    pack_show = pack_commands.add_parser("show", help="show one exact canonical pack")
    _add_pack_identity(pack_show)

    item = commands.add_parser("item", help="query catalog items")
    item_commands = item.add_subparsers(
        dest="item_command", required=True, parser_class=_JsonArgumentParser
    )
    item_show = item_commands.add_parser("show", help="show one item and its typed relations")
    _add_pack_identity(item_show)
    item_show.add_argument("--item-code", required=True)

    evaluate = commands.add_parser("evaluate", help="evaluate supplied runtime context")
    _add_pack_identity(evaluate)
    evaluate.add_argument("--input", required=True, type=Path)

    assembly = commands.add_parser("assembly", help="resolve structural assemblies")
    assembly_commands = assembly.add_subparsers(
        dest="assembly_command", required=True, parser_class=_JsonArgumentParser
    )
    assembly_resolve = assembly_commands.add_parser(
        "resolve", help="resolve one nested structural assembly"
    )
    _add_pack_identity(assembly_resolve)
    assembly_resolve.add_argument("--item-code", required=True)

    acceptance = commands.add_parser("acceptance", help="run finite blank-database acceptance")
    acceptance.add_argument("--work-root", required=True, type=Path)
    return parser


def _run(arguments: argparse.Namespace) -> int:
    if arguments.command == "compile":
        compiled = compile_domain_pack(_load_json(arguments.input))
        if arguments.output is not None:
            arguments.output.write_bytes(compiled.canonical_bytes)
        _emit({"document": json.loads(compiled.canonical_bytes), "sha256": compiled.sha256})
        return 0

    if arguments.command == "acceptance":
        from .engine.acceptance import build_engine_foundation_acceptance

        _emit(build_engine_foundation_acceptance(arguments.work_root))
        return 0

    repository = _repository(arguments.database)
    if arguments.command == "import":
        compiled = compile_domain_pack(_load_json(arguments.input))
        pack_id = repository.import_domain_pack(compiled)
        _emit(
            {
                "pack_code": compiled.canonical_document["pack_code"],
                "pack_id": pack_id,
                "sha256": compiled.sha256,
                "status": "IMPORTED",
                "version": compiled.canonical_document["version"],
            }
        )
        return 0
    if arguments.command == "pack":
        _emit(
            repository.get_domain_pack(
                pack_code=arguments.pack_code, version=arguments.version
            )
        )
        return 0
    if arguments.command == "item":
        _emit(
            {
                "item": repository.get_catalog_item(
                    pack_code=arguments.pack_code,
                    version=arguments.version,
                    item_code=arguments.item_code,
                ),
                "relations": repository.list_catalog_relations(
                    pack_code=arguments.pack_code,
                    version=arguments.version,
                    item_code=arguments.item_code,
                ),
            }
        )
        return 0

    stored_pack = _stored_pack(
        repository, pack_code=arguments.pack_code, version=arguments.version
    )
    if arguments.command == "evaluate":
        context = _load_json(arguments.input)
        if not isinstance(context, dict) or set(context) != {
            "subject_code",
            "observations",
            "evidence_kinds",
        }:
            raise ValidationError(
                "evaluation context fields must be subject_code, observations, and evidence_kinds"
            )
        result = evaluate_subject(
            stored_pack,
            subject_code=context["subject_code"],
            observations=context["observations"],
            evidence_kinds=context["evidence_kinds"],
        )
    else:
        result = resolve_assembly(stored_pack, item_code=arguments.item_code)
    _emit(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        return _run(arguments)
    except Exception as error:
        _emit({"error": {"code": type(error).__name__, "message": str(error)}})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
