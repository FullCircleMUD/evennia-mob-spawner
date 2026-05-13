# SPDX-License-Identifier: BSD-3-Clause
"""Smoke tests for evennia-mob-spawner.

Proves the package installs, the test runner discovers tests correctly, and
the evennia-yaml-reader dependency is wired up. Real tests land alongside
the spawn-rule pipeline as it is built out.
"""

from django.test import TestCase

import evennia_mob_spawner
from evennia_mob_spawner.log import ms_log


class PackageSmokeTest(TestCase):
    def test_version_present(self):
        self.assertEqual(evennia_mob_spawner.__version__, "0.0.1")


class LogShimSmokeTest(TestCase):
    """ms_log is importable and callable without raising."""

    def test_ms_log_callable_at_default_level(self):
        # Must not raise even if Evennia logger isn't fully bootstrapped
        # in this test context (the shim swallows ImportError silently).
        ms_log("smoke test: default level")

    def test_ms_log_unknown_level_coerced(self):
        # Unknown levels degrade to INFO rather than rejecting — the shim
        # contract is "never raise into the caller."
        ms_log("smoke test: unknown level", level="NONSENSE")

    def test_ms_log_valid_levels(self):
        for level in ("INFO", "WARN", "ERROR"):
            ms_log(f"smoke test: level {level}", level=level)


class PipelineScaffoldSmokeTest(TestCase):
    """Each pipeline stage is importable, instantiable, and runs end-to-end.

    The stages do no real work yet — Definitions parses, Finder returns
    root, Loader returns empty, Validator passes, Deployer no-ops. This
    test exists to confirm the pipeline plumbing flows cleanly while real
    logic is written.
    """

    def test_pipeline_flows_end_to_end(self):
        from evennia_mob_spawner.definitions import Definitions
        from evennia_mob_spawner.deployer import Deployer
        from evennia_mob_spawner.finder import Finder, FoundLocation
        from evennia_mob_spawner.loader import Loader, LoadResult
        from evennia_mob_spawner.validator import Validator

        class _NullReader:
            """Minimal Reader stand-in for scaffold-stage tests."""

            def read(self, path):  # pragma: no cover
                raise NotImplementedError(
                    "scaffold tests must not require Reader I/O"
                )

        reader = _NullReader()
        definitions = Definitions.from_dict({"levels": ["shard", "zone"]})
        finder = Finder(reader, definitions)
        loader = Loader(reader, definitions)
        validator = Validator(definitions)
        deployer = Deployer(definitions)

        found = finder.find({"shard": "shard0"})
        self.assertIsInstance(found, FoundLocation)

        load_result = loader.load(found)
        self.assertIsInstance(load_result, LoadResult)
        self.assertEqual(load_result.rule_sets, {})

        validator.validate(load_result)  # no errors → no raise
        deployer.deploy(load_result)  # no-op

    def test_definitions_parses_minimal_yaml(self):
        from evennia_mob_spawner.definitions import Definitions

        d = Definitions.from_dict(
            {"levels": ["shard", "zone"], "repo-ci-pre-validation": True}
        )
        self.assertEqual(d.levels, ("shard", "zone"))
        self.assertTrue(d.repo_ci_pre_validation)

    def test_definitions_empty_yaml_yields_defaults(self):
        from evennia_mob_spawner.definitions import Definitions

        d = Definitions.from_dict(None)
        self.assertEqual(d.levels, ())
        self.assertFalse(d.repo_ci_pre_validation)


class CliScaffoldSmokeTest(TestCase):
    """ms-validate CLI module is importable; parser builds; validate() runs."""

    def test_parser_builds(self):
        from evennia_mob_spawner.cli import _build_parser

        parser = _build_parser()
        # Sanity: the parser knows about --reader and --root.
        ns = parser.parse_args(["--reader", "local", "--root", "/some/path"])
        self.assertEqual(ns.reader, "local")
        self.assertEqual(ns.root, "/some/path")

    def test_validate_runs_against_empty_repo(self):
        """End-to-end smoke: empty definitions.yaml -> clean validate pass."""
        import tempfile
        from pathlib import Path
        from evennia_mob_spawner.cli import validate

        with tempfile.TemporaryDirectory() as tmp:
            # Minimal valid definitions.yaml — empty levels.
            (Path(tmp) / "definitions.yaml").write_text("levels: []\n")
            exit_code = validate(["--reader", "local", "--root", tmp])
            self.assertEqual(exit_code, 0)


class YamlReaderDependencyTest(TestCase):
    """Confirms the evennia-yaml-reader dependency resolves in this venv."""

    def test_can_import_reader_primitives(self):
        # The dependency contributes the Reader contract and the typed
        # exception hierarchy that mob-spawner will use for rule fetching.
        from evennia_yaml_reader import (
            GitHubReader,
            LocalReader,
            Reader,
            ReaderError,
            ReaderResult,
        )

        # Sanity: the imported classes are the expected types.
        self.assertTrue(issubclass(GitHubReader, Reader))
        self.assertTrue(issubclass(LocalReader, Reader))
        self.assertTrue(issubclass(ReaderError, Exception))
        # ReaderResult is a dataclass with the documented two fields.
        self.assertEqual(
            set(ReaderResult.__dataclass_fields__.keys()),
            {"raw_bytes", "parsed"},
        )
