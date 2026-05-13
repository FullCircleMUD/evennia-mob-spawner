# SPDX-License-Identifier: BSD-3-Clause
"""Standalone CLI entry points for evennia-mob-spawner.

Designed to run independently of any Evennia process so the same pipeline
can be invoked from a developer's shell, a pre-commit hook, or CI.

Currently shipped:

- ``ms-validate`` — runs the pre-Evennia pipeline (Reader → Definitions →
  Finder → Loader → Validator at Tier 1+2) against a rule-set repo and
  prints any validator messages. Tier 3 (Evennia-runtime predicates) is
  not run here — that tier requires the engine and only fires inside
  ``ms_load``.
"""
import argparse
import sys

from evennia_yaml_reader import LocalReader, ReaderError

from .definitions import Definitions
from .errors import DefinitionsError, FinderError, LoaderError, ValidatorError
from .finder import Finder
from .loader import Loader
from .validator import Validator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ms-validate",
        description=(
            "Validate a mob-spawner rule-set repo. Runs Reader -> "
            "Definitions -> Finder -> Loader -> Validator (Tier 1+2) and "
            "prints any validator messages."
        ),
    )
    parser.add_argument(
        "--reader",
        choices=["local"],
        default="local",
        help="Reader implementation to use (default: local).",
    )
    parser.add_argument(
        "--root",
        help="Filesystem path to the rule-set repo root (required when --reader=local).",
    )
    return parser


def _build_reader(args: argparse.Namespace):
    """Construct the configured Reader from CLI args.

    Encapsulated so future reader variants (e.g. GitHub) slot in by
    extending the choices + branch here without touching the rest of the
    CLI.
    """
    if args.reader == "local":
        if not args.root:
            raise SystemExit("ms-validate: --root is required when --reader=local")
        return LocalReader(root=args.root)
    raise SystemExit(f"ms-validate: unknown reader {args.reader!r}")


def validate(argv: list[str] | None = None) -> int:
    """Entry point for the ``ms-validate`` console script.

    Runs the pre-Evennia pipeline (Tier 1+2 only — Tier 3 requires the
    engine). Returns 0 on a clean run, 1 on any pipeline failure (reader,
    definitions, finder, loader, or validator error).
    """
    args = _build_parser().parse_args(argv)
    reader = _build_reader(args)

    try:
        definitions = Definitions.from_reader(reader)
        finder = Finder(reader, definitions)
        loader = Loader(reader, definitions)
        load_result = loader.load(finder.find())
        # CLI path: evennia_runtime=False so Tier 3 predicates don't fire
        # (they require the engine; CI / pre-commit can't satisfy them).
        validator = Validator(definitions, evennia_runtime=False)
    except (ReaderError, DefinitionsError, FinderError, LoaderError) as e:
        print(f"ms-validate: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    exit_code = 0
    try:
        validator.validate(load_result)
    except ValidatorError:
        exit_code = 1

    print(f"ms-validate: reader={args.reader} root={getattr(args, 'root', None)}")
    print(f"ms-validate: loaded {len(load_result.rule_sets)} rule-set file(s)")
    for msg in validator.messages:
        print(msg)
    if exit_code:
        n = len(validator.errors)
        print(
            f"ms-validate: refusing -- {n} validation error{'s' if n != 1 else ''}",
            file=sys.stderr,
        )
    return exit_code


def _validate_main() -> None:
    """Console-script shim: forward sys.argv and propagate exit code."""
    # Windows default stdio inherits the legacy ANSI codepage which can
    # crash on non-ASCII glyphs in argparse help text. macOS/Linux are
    # already UTF-8; this reconfigure is a no-op there.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    sys.exit(validate(sys.argv[1:]))
