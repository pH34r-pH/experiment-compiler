"""Command-line entry point. Never installs or runs a packaged experiment."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from . import __version__
from .catalog import describe_recipe
from .core import PackageError, MAX_TOTAL, bounded_read, canonical, compile_package, load_recipe, verify_bytes, write_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment-compiler")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("compile", help="build reviewed local inputs into a deterministic ZIP")
    build.add_argument("recipe", type=Path)
    build.add_argument("--output", type=Path)
    describe = commands.add_parser("describe", help="derive display metadata from authoritative compiled experiment artifacts")
    describe.add_argument("recipe", type=Path)
    describe.add_argument("--output", type=Path)
    check = commands.add_parser("verify", help="verify ZIP integrity without executing or extracting it")
    check.add_argument("package", type=Path)
    check.add_argument("--recipe", type=Path)
    check.add_argument("--expected-sha256")
    for command in (build, check):
        command.add_argument("--receipt", type=Path, help="write an integrity-only JSON receipt")
    args = parser.parse_args(argv)
    try:
        if args.command == "describe":
            result = describe_recipe(args.recipe)
            if args.output:
                write_once(args.output, canonical(result))
        elif args.command == "compile":
            recipe = load_recipe(args.recipe)
            output = args.output or Path("dist") / (recipe["id"] + ".zip")
            if args.receipt and args.receipt.resolve() == output.resolve():
                raise PackageError("Receipt must not replace package")
            result = compile_package(args.recipe, output)
        else:
            if args.receipt and args.receipt.resolve() == args.package.resolve():
                raise PackageError("Receipt must not replace package")
            recipe = load_recipe(args.recipe) if args.recipe else None
            result = verify_bytes(bounded_read(args.package, MAX_TOTAL),
                                  expected_sha256=args.expected_sha256, recipe=recipe)
        if args.receipt:
            write_once(args.receipt, canonical(result))
        sys.stdout.buffer.write(canonical(result))
        return 0
    except (PackageError, OSError) as exc:
        print(f"experiment-compiler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
