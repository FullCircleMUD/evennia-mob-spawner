# SPDX-License-Identifier: BSD-3-Clause
"""Smoke tests for evennia-mob-spawner.

Proves the package installs, the test runner discovers tests correctly, and
the evennia-yaml-reader dependency is wired up. Real tests land alongside
the spawn-rule pipeline as it is built out.
"""

from django.test import TestCase, override_settings

from evennia_yaml_reader import ReaderNotFoundError, ReaderResult

import evennia_mob_spawner
from evennia_mob_spawner.config import get_reader_class
from evennia_mob_spawner.definitions import Definitions
from evennia_mob_spawner.errors import (
    DefinitionsError,
    FinderManifestError,
    FinderQueryError,
)
from evennia_mob_spawner.finder import Finder, FoundLocation
from evennia_mob_spawner.log import ms_log


class FakeReader:
    """Used by GetReaderClassTest to verify dispatch via @override_settings.

    Defined at module scope so it is importable as
    ``evennia_mob_spawner.tests.FakeReader``.
    """


class FixtureReader:
    """An in-memory Reader for tests of Finder (and later Loader).

    Maps path → parsed-data; raises ReaderNotFoundError for unknown paths.
    """

    def __init__(self, files: dict):
        self.files = files

    def read(self, path: str) -> ReaderResult:
        if path not in self.files:
            raise ReaderNotFoundError(f"FixtureReader: path {path!r} not in fixtures")
        data = self.files[path]
        return ReaderResult(raw_bytes=repr(data).encode(), parsed=data)


# Synthetic manifest used by FinderTest. Mirrors the layout in
# evennia-mob-spawner-test-yaml so unit-test behaviour matches the
# live fixture repo.
SCAFFOLD = {
    "definitions.yaml": {"levels": ["shard", "zone"]},
    "index.yaml": {"entries": [
        {"name": "shard0", "kind": "folder"},
        {"name": "shard1", "kind": "file"},
    ]},
    "shard0/index.yaml": {"entries": [
        {"name": "millholm", "kind": "file"},
        {"name": "wilderness", "kind": "file"},
    ]},
    "shard1.yaml": {"rules": []},
    "shard0/millholm.yaml": {"rules": []},
    "shard0/wilderness.yaml": {"rules": []},
}


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
        from evennia_mob_spawner.deployer import Deployer
        from evennia_mob_spawner.loader import Loader, LoadResult
        from evennia_mob_spawner.validator import Validator

        reader = FixtureReader(SCAFFOLD)
        definitions = Definitions.from_reader(reader)
        finder = Finder(reader, definitions)
        loader = Loader(reader, definitions)
        validator = Validator(definitions)
        deployer = Deployer(definitions)

        # Empty query → root location (Finder doesn't need to read any index)
        found = finder.find()
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


class GetReaderClassTest(TestCase):
    """Verify settings-based dispatch via MOB_SPAWNER_READER."""

    def test_default_returns_github_reader(self):
        from evennia_yaml_reader import GitHubReader

        self.assertIs(get_reader_class(), GitHubReader)

    @override_settings(MOB_SPAWNER_READER="evennia_mob_spawner.tests.FakeReader")
    def test_override_via_settings(self):
        self.assertIs(get_reader_class(), FakeReader)

    @override_settings(MOB_SPAWNER_READER="evennia_mob_spawner.does_not_exist.Nope")
    def test_bad_dotted_path_raises(self):
        with self.assertRaises((ImportError, AttributeError)):
            get_reader_class()


class FinderTest(TestCase):
    """Verify Finder.find() against a synthetic manifest tree."""

    def _make_finder(self):
        reader = FixtureReader(SCAFFOLD)
        defs = Definitions.from_reader(reader)
        return Finder(reader, defs)

    def test_empty_query_returns_root(self):
        found = self._make_finder().find()
        self.assertEqual(found.path, "")
        self.assertEqual(found.kind, "folder")
        self.assertEqual(found.location, {})

    def test_shard_folder_query_returns_folder_location(self):
        found = self._make_finder().find({"shard": "shard0"})
        self.assertEqual(found.path, "shard0")
        self.assertEqual(found.kind, "folder")
        self.assertEqual(found.location, {"shard": "shard0"})

    def test_shard_file_query_returns_file_location(self):
        found = self._make_finder().find({"shard": "shard1"})
        self.assertEqual(found.path, "shard1.yaml")
        self.assertEqual(found.kind, "file")
        self.assertEqual(found.location, {"shard": "shard1"})

    def test_full_path_query(self):
        found = self._make_finder().find({"shard": "shard0", "zone": "millholm"})
        self.assertEqual(found.path, "shard0/millholm.yaml")
        self.assertEqual(found.kind, "file")
        self.assertEqual(found.location, {"shard": "shard0", "zone": "millholm"})

    def test_invalid_key_raises(self):
        # Keys-not-in-levels is DefinitionsError (Definitions owns the
        # query-shape validation; Finder only validates manifest content).
        with self.assertRaises(DefinitionsError):
            self._make_finder().find({"area": "town"})

    def test_skipped_level_raises(self):
        # levels=[shard, zone]; can't query just {zone: X}
        with self.assertRaises(DefinitionsError):
            self._make_finder().find({"zone": "millholm"})

    def test_value_not_in_index_raises(self):
        with self.assertRaises(FinderQueryError):
            self._make_finder().find({"shard": "nonexistent"})

    def test_zone_not_in_shard_raises(self):
        with self.assertRaises(FinderQueryError):
            self._make_finder().find({"shard": "shard0", "zone": "nonexistent"})

    def test_missing_index_raises_manifest_error(self):
        scaffold = {
            "definitions.yaml": {"levels": ["shard"]},
            # no index.yaml at root
        }
        reader = FixtureReader(scaffold)
        defs = Definitions.from_reader(reader)
        finder = Finder(reader, defs)
        with self.assertRaises(FinderManifestError):
            finder.find({"shard": "x"})


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
