"""Command-line entry point. Never installs or runs a packaged experiment."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from . import __version__
from .catalog import describe_catalog, describe_recipe
from .core import PackageError, MAX_TOTAL, bounded_read, canonical, compile_package, load_recipe, verify_bytes, write_once
from .finalization import finalize_package
from .revision import revise_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment-compiler")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("compile", help="build reviewed local inputs into a deterministic ZIP")
    build.add_argument("recipe", type=Path)
    build.add_argument("--output", type=Path)
    describe = commands.add_parser("describe", help="derive display metadata from one compiled experiment")
    describe.add_argument("recipe", type=Path)
    describe.add_argument("--output", type=Path)
    catalog = commands.add_parser("catalog", help="discover compiled experiments and derive an aggregate catalog")
    catalog.add_argument("root", type=Path)
    catalog.add_argument("--output", type=Path)
    check = commands.add_parser("verify", help="verify ZIP integrity without executing or extracting it")
    check.add_argument("package", type=Path)
    check.add_argument("--recipe", type=Path)
    check.add_argument("--expected-sha256")
    revise = commands.add_parser("revise", help="compile a new plan revision from one verified attempt")
    revise.add_argument("parent_package", type=Path)
    revise.add_argument("--expected-sha256", required=True,
                        help="exact digest of the parent attempt package")
    revise.add_argument("--attempt-id", required=True,
                        help="explicit attempt ID whose protocol is being revised")
    revise.add_argument("--recipe", required=True, type=Path,
                        help="source-owned recipe for the new prospective plan")
    revise.add_argument("--output", required=True, type=Path,
                        help="new immutable plan ZIP; writes a sibling .source closure")
    finalize = commands.add_parser("finalize", help="bind explicit decision and review records to one attempt")
    finalize.add_argument("parent_package", type=Path)
    finalize.add_argument("--expected-sha256", required=True,
                          help="exact digest of the selected attempt package")
    finalize.add_argument("--attempt-id", required=True,
                          help="explicit executed attempt ID being finalized")
    finalize.add_argument("--id", required=True, dest="experiment_id",
                          help="new immutable final artifact recipe ID")
    finalize.add_argument("--title", required=True,
                          help="human-authored title for the final artifact")
    finalize.add_argument("--decision", required=True, type=Path,
                          help="source-authored scientific decision and interpretation record")
    finalize.add_argument("--decision-summary", required=True,
                          help="source-authored Schema.org abstract for the decision record")
    finalize.add_argument("--review", required=True, type=Path,
                          help="source-authored publication review record")
    finalize.add_argument("--review-summary", required=True,
                          help="source-authored Schema.org reviewBody for the review record")
    finalize.add_argument("--reviewer-name", required=True,
                          help="reviewer identity recorded as a Schema.org Person")
    finalize.add_argument("--output", required=True, type=Path,
                          help="new immutable final ZIP; writes a sibling .source closure")
    for command in (build, check):
        command.add_argument("--receipt", type=Path, help="write an integrity-only JSON receipt")
    args = parser.parse_args(argv)
    try:
        if args.command == "describe":
            result = describe_recipe(args.recipe)
            if args.output:
                write_once(args.output, canonical(result))
        elif args.command == "catalog":
            result = describe_catalog(args.root)
            if args.output:
                write_once(args.output, canonical(result))
        elif args.command == "compile":
            recipe = load_recipe(args.recipe)
            output = args.output or Path("dist") / (recipe["id"] + ".zip")
            if args.receipt and args.receipt.resolve() == output.resolve():
                raise PackageError("Receipt must not replace package")
            result = compile_package(args.recipe, output)
        elif args.command == "revise":
            protected = {args.parent_package.resolve(), args.recipe.resolve()}
            if args.output.resolve() in protected:
                raise PackageError("Revision output must not replace its parent package or source recipe")
            result = revise_package(args.parent_package, args.recipe, args.output,
                                    expected_sha256=args.expected_sha256,
                                    attempt_id=args.attempt_id)
        elif args.command == "finalize":
            protected = {args.parent_package.resolve(), args.decision.resolve(), args.review.resolve()}
            if args.output.resolve() in protected:
                raise PackageError("Final output must not replace its parent package or source record")
            result = finalize_package(
                args.parent_package, args.output,
                expected_sha256=args.expected_sha256, attempt_id=args.attempt_id,
                experiment_id=args.experiment_id, title=args.title,
                decision_path=args.decision, decision_summary=args.decision_summary,
                review_path=args.review, review_summary=args.review_summary,
                reviewer_name=args.reviewer_name,
            )
        else:
            if args.receipt and args.receipt.resolve() == args.package.resolve():
                raise PackageError("Receipt must not replace package")
            recipe = load_recipe(args.recipe) if args.recipe else None
            result = verify_bytes(bounded_read(args.package, MAX_TOTAL),
                                  expected_sha256=args.expected_sha256, recipe=recipe)
        receipt = getattr(args, "receipt", None)
        if receipt:
            write_once(receipt, canonical(result))
        sys.stdout.buffer.write(canonical(result))
        return 0
    except (PackageError, OSError) as exc:
        print(f"experiment-compiler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
