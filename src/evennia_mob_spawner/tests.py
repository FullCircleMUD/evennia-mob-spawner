# SPDX-License-Identifier: BSD-3-Clause
"""Smoke tests for evennia-mob-spawner.

Proves the package installs, the test runner discovers tests correctly, and
the evennia-yaml-reader dependency is wired up. Real tests land alongside
the spawn-rule pipeline as it is built out.
"""

import sys
import time
import types
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from evennia.typeclasses.attributes import AttributeProperty


class EvenniaWorldTestCase(TestCase):
    """Base for tests that create Evennia objects via ``create_object()``.

    Pre-creates a Limbo room and points ``settings.DEFAULT_HOME`` at it.
    Without this, ``create_object(...)`` calls that don't pass ``home=``
    produce objects with a foreign key reference to a non-existent row,
    which Django's SQLite constraint check eventually trips over.

    ``setUpTestData`` runs once per class inside the class's outer test
    transaction. Limbo persists across every test method in the class
    and is rolled back when the class finishes.
    """

    @classmethod
    def setUpTestData(cls):
        from evennia.utils.create import create_object
        from evennia import DefaultRoom

        cls.limbo = create_object(DefaultRoom, key="Limbo")
        settings.DEFAULT_HOME = f"#{cls.limbo.id}"

from evennia_yaml_reader import ReaderNotFoundError, ReaderResult

import evennia_mob_spawner
from evennia_mob_spawner.config import get_reader_class
from evennia_mob_spawner.definitions import Definitions
from evennia_mob_spawner.errors import (
    DefinitionsError,
    FinderManifestError,
    FinderQueryError,
    LoaderInvalidShapeError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
)
from evennia_mob_spawner.commands import (
    FORCE_VALIDATE_FLAG,
    SHARD_LEVEL,
    active_shard_id,
    check_shard_levels,
    check_shard_scope,
    should_pre_validate,
)
from evennia_mob_spawner.finder import Finder, FoundLocation
from evennia_mob_spawner.loader import Loader, LoadResult
from evennia_mob_spawner.validator import LoadedRule, Validator
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


class PipelineScaffoldSmokeTest(EvenniaWorldTestCase):
    """Each pipeline stage is importable, instantiable, and runs end-to-end.

    The stages do no real work yet — Definitions parses, Finder returns
    root, Loader returns empty, Validator passes, Deployer no-ops. This
    test exists to confirm the pipeline plumbing flows cleanly while real
    logic is written.
    """

    def test_pipeline_flows_end_to_end(self):
        from evennia_mob_spawner.deployer import Deployer
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
        # Loader walks SCAFFOLD root → 3 leaf rule-set files, all empty,
        # no file-level metadata on any of them.
        self.assertEqual(
            set(load_result.rule_sets.keys()),
            {"shard1.yaml", "shard0/millholm.yaml", "shard0/wilderness.yaml"},
        )
        self.assertEqual(load_result.file_metadata, {})

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


class LoaderTest(TestCase):
    """Verify Loader.load() against synthetic fixture trees."""

    def _make_loader(self, files: dict):
        reader = FixtureReader(files)
        defs = Definitions.from_reader(reader)
        return reader, Loader(reader, defs)

    def test_loads_single_file_with_rules(self):
        rule = {"rule_id": 1, "typeclass": "x.Y", "key": "a thing"}
        files = {
            "definitions.yaml": {"levels": []},
            "lone.yaml": {"rules": [rule]},
        }
        _, loader = self._make_loader(files)
        found = FoundLocation(path="lone.yaml", kind="file", location={})

        result = loader.load(found)
        self.assertEqual(result.rule_sets, {"lone.yaml": [rule]})
        self.assertEqual(result.file_metadata, {})

    def test_loads_folder_recursively(self):
        _, loader = self._make_loader(SCAFFOLD)
        found = FoundLocation(path="", kind="folder", location={})

        result = loader.load(found)
        self.assertEqual(
            set(result.rule_sets.keys()),
            {"shard1.yaml", "shard0/millholm.yaml", "shard0/wilderness.yaml"},
        )
        for rule_list in result.rule_sets.values():
            self.assertEqual(rule_list, [])
        self.assertEqual(result.file_metadata, {})

    def test_file_metadata_extracted_when_present(self):
        files = {
            "definitions.yaml": {"levels": []},
            "with_meta.yaml": {
                "rules": [{"rule_id": 1}],
                "display_name": "Test Zone",
                "frozen": True,
            },
        }
        _, loader = self._make_loader(files)
        found = FoundLocation(path="with_meta.yaml", kind="file", location={})

        result = loader.load(found)
        self.assertEqual(result.rule_sets, {"with_meta.yaml": [{"rule_id": 1}]})
        self.assertEqual(
            result.file_metadata,
            {"with_meta.yaml": {"display_name": "Test Zone", "frozen": True}},
        )

    def test_file_metadata_absent_when_only_rules_key(self):
        files = {
            "definitions.yaml": {"levels": []},
            "rules_only.yaml": {"rules": [{"rule_id": 1}]},
        }
        _, loader = self._make_loader(files)
        found = FoundLocation(path="rules_only.yaml", kind="file", location={})

        result = loader.load(found)
        # Path absent from file_metadata when no non-`rules:` keys exist —
        # the dict stays clean-by-default.
        self.assertNotIn("rules_only.yaml", result.file_metadata)

    def test_bare_list_rejected(self):
        files = {
            "definitions.yaml": {"levels": []},
            "bare.yaml": [{"rule_id": 1}],  # top-level list, not a mapping
        }
        _, loader = self._make_loader(files)
        found = FoundLocation(path="bare.yaml", kind="file", location={})

        with self.assertRaises(LoaderInvalidShapeError):
            loader.load(found)

    def test_top_level_missing_rules_key_rejected(self):
        files = {
            "definitions.yaml": {"levels": []},
            "no_rules.yaml": {"other_key": []},
        }
        _, loader = self._make_loader(files)
        found = FoundLocation(path="no_rules.yaml", kind="file", location={})

        with self.assertRaises(LoaderInvalidShapeError):
            loader.load(found)

    def test_rules_value_not_a_list_rejected(self):
        files = {
            "definitions.yaml": {"levels": []},
            "bad.yaml": {"rules": "not a list"},
        }
        _, loader = self._make_loader(files)
        found = FoundLocation(path="bad.yaml", kind="file", location={})

        with self.assertRaises(LoaderInvalidShapeError):
            loader.load(found)

    def test_missing_referenced_file_raises_missing_entry(self):
        # index.yaml claims `gone.yaml` exists, but the fixture omits it.
        files = {
            "definitions.yaml": {"levels": []},
            "index.yaml": {"entries": [{"name": "gone", "kind": "file"}]},
        }
        _, loader = self._make_loader(files)
        found = FoundLocation(path="", kind="folder", location={})

        with self.assertRaises(LoaderMissingEntryError):
            loader.load(found)

    def test_missing_folder_index_raises_missing_index(self):
        # index.yaml claims `sub` is a folder, but `sub/index.yaml` is absent.
        files = {
            "definitions.yaml": {"levels": []},
            "index.yaml": {"entries": [{"name": "sub", "kind": "folder"}]},
        }
        _, loader = self._make_loader(files)
        found = FoundLocation(path="", kind="folder", location={})

        with self.assertRaises(LoaderMissingIndexError):
            loader.load(found)


class ValidatorStructureTest(TestCase):
    """Verify the Validator's tier scaffold flows cleanly with no predicates.

    Predicate tuples are deliberately empty at this stage — these tests
    exercise the structure (Tier 1 dispatch, Tier 2 entry point,
    evennia_runtime gating, file_metadata pass, seen_ids state, the
    error-funnel discipline). Predicate-specific tests land alongside
    concrete predicates in later passes.
    """

    def _make_validator(self, **kwargs):
        defs = Definitions.from_dict({"levels": []})
        return Validator(defs, **kwargs)

    def test_empty_load_result_passes_clean(self):
        v = self._make_validator()
        v.validate(LoadResult())
        self.assertEqual(v.errors, [])
        self.assertEqual(v.messages, [])

    def test_load_result_with_valid_rules_passes(self):
        # Multiple files, multiple rules per file, all fully valid —
        # exercises the dispatch loop through real predicates.
        v = self._make_validator()
        valid_rule = {
            "rule_id": 1,
            "typeclass": "test.Foo",
            "key": "a thing",
            "area_tag": "test_area",
            "target": 3,
            "max_per_room": 1,
            "respawn_seconds": 60,
        }
        v.validate(LoadResult(
            rule_sets={
                "a.yaml": [valid_rule, {**valid_rule, "rule_id": 2}],
                "b.yaml": [{**valid_rule, "rule_id": 1}],
            },
        ))
        self.assertEqual(v.errors, [])

    def test_seen_ids_initialised_empty(self):
        v = self._make_validator()
        self.assertEqual(v.seen_ids, {})

    def test_active_predicates_excludes_tier_3_by_default(self):
        v = self._make_validator()
        # Both tuples are empty so the concatenation is also empty; the
        # property we care about is that evennia_runtime=False does not
        # include EVENNIA_ONLY_PREDICATES in the active set.
        self.assertEqual(v._active_predicates(), Validator.PER_RULE_PREDICATES)

    def test_active_predicates_includes_tier_3_when_engine_flag_set(self):
        v = self._make_validator(evennia_runtime=True)
        expected = Validator.PER_RULE_PREDICATES + Validator.EVENNIA_ONLY_PREDICATES
        self.assertEqual(v._active_predicates(), expected)

    def test_record_finding_appends_to_both_messages_and_errors(self):
        v = self._make_validator()
        v._record_finding("a finding")
        self.assertEqual(v.messages, ["a finding"])
        self.assertEqual(v.errors, ["a finding"])

    def test_validate_raises_when_errors_accumulated(self):
        # Synthesise a single Tier 1 predicate that always flags so we
        # can verify validate() raises ValidatorError after collecting.
        v = self._make_validator()

        def always_flags(loaded):
            return f"{loaded.path}: synthetic finding for rule_id {loaded.rule.get('rule_id')}"

        original = Validator.PER_RULE_PREDICATES
        Validator.PER_RULE_PREDICATES = (always_flags,)
        try:
            from evennia_mob_spawner.errors import ValidatorError
            with self.assertRaises(ValidatorError):
                v.validate(LoadResult(
                    rule_sets={"a.yaml": [{"rule_id": 1}, {"rule_id": 2}]},
                ))
            # Both rules should be checked before the raise — discipline:
            # gather every finding, then refuse.
            self.assertEqual(len(v.errors), 2)
        finally:
            Validator.PER_RULE_PREDICATES = original

    def test_loaded_rule_is_frozen_dataclass(self):
        loaded = LoadedRule(path="a.yaml", rule={"rule_id": 1})
        self.assertEqual(loaded.path, "a.yaml")
        self.assertEqual(loaded.rule, {"rule_id": 1})
        with self.assertRaises(Exception):
            loaded.path = "b.yaml"  # frozen — must refuse mutation


class ShouldPreValidateTest(TestCase):
    """The 2x2 of (setting, flag) → pre-validation decision."""

    def _defs(self, *, repo_ci: bool):
        return Definitions.from_dict({
            "levels": [],
            "repo-ci-pre-validation": repo_ci,
        })

    def test_setting_false_no_flag_runs_pre_validation(self):
        # Default safe — consumer has not claimed CI gating, so the
        # admin command pre-validates the whole repo every time.
        self.assertTrue(should_pre_validate(self._defs(repo_ci=False), flags=set()))

    def test_setting_true_no_flag_skips_pre_validation(self):
        # Consumer asserts CI gates the YAML; ms_load trusts the gate
        # and runs only scope-level checks.
        self.assertFalse(should_pre_validate(self._defs(repo_ci=True), flags=set()))

    def test_setting_true_with_flag_runs_pre_validation(self):
        # Flag overrides the trust signal.
        self.assertTrue(should_pre_validate(
            self._defs(repo_ci=True), flags={FORCE_VALIDATE_FLAG},
        ))

    def test_setting_false_with_flag_runs_pre_validation(self):
        # Flag is harmless when the setting already mandates pre-validation.
        self.assertTrue(should_pre_validate(
            self._defs(repo_ci=False), flags={FORCE_VALIDATE_FLAG},
        ))

    def test_unrelated_flags_do_not_trigger(self):
        # Only `force-validate` is the override; other flags are ignored.
        self.assertFalse(should_pre_validate(
            self._defs(repo_ci=True), flags={"verbose", "dry-run"},
        ))


def _fake_shards(role: str, shard_id):
    """A stand-in ``evennia_shards`` exposing only what the gate imports.

    The library's own test venv deliberately has no shards install, so the
    co-installed paths are exercised by injecting this into ``sys.modules``.
    """
    mod = types.ModuleType("evennia_shards")
    mod.ROLE_MONOLITH = "monolith"
    mod.get_role = lambda: role
    mod.get_shard_id = lambda: shard_id
    return mod


class ActiveShardIdTest(TestCase):
    """Detection: installed *and* not monolith, not merely importable."""

    def test_shards_not_installed_is_not_sharded(self):
        # A None entry in sys.modules makes the import raise, which is the
        # standalone case: no shards, no checks.
        with mock.patch.dict(sys.modules, {"evennia_shards": None}):
            self.assertIsNone(active_shard_id())

    def test_monolith_role_is_not_sharded(self):
        # Monolith is a non-sharded install — the import succeeding is not
        # enough, or the library would start refusing scopes on a game that
        # has no shards at all.
        fake = _fake_shards(role="monolith", shard_id=None)
        with mock.patch.dict(sys.modules, {"evennia_shards": fake}):
            self.assertIsNone(active_shard_id())

    def test_shard_role_reports_its_shard_id(self):
        fake = _fake_shards(role="shard", shard_id="shard0")
        with mock.patch.dict(sys.modules, {"evennia_shards": fake}):
            self.assertEqual(active_shard_id(), "shard0")

    def test_router_role_reports_its_own_id(self):
        # The router is "sharded" for gate purposes: it has an id, and that
        # id never matches a content shard, which is what refuses ms_load
        # there without a role check of its own.
        fake = _fake_shards(role="router", shard_id="router")
        with mock.patch.dict(sys.modules, {"evennia_shards": fake}):
            self.assertEqual(active_shard_id(), "router")


class CheckShardScopeTest(TestCase):
    """The ms_load shard gate. Every refusal is a no-op off a shard."""

    def _as(self, shard_id):
        return mock.patch(
            "evennia_mob_spawner.commands.active_shard_id",
            return_value=shard_id,
        )

    # --- not a sharded deployment: nothing is refused ----------------

    def test_unsharded_allows_all_scope(self):
        with self._as(None):
            self.assertIsNone(check_shard_scope({}))

    def test_unsharded_allows_any_query(self):
        # Including one naming a shard level, which is just an ordinary
        # consumer-chosen level name when shards isn't in play.
        with self._as(None):
            self.assertIsNone(check_shard_scope({SHARD_LEVEL: "shard9"}))
            self.assertIsNone(check_shard_scope({"zone": "millholm"}))

    # --- sharded: the three refusals ---------------------------------

    def test_all_scope_refused_when_sharded(self):
        with self._as("shard0"):
            refusal = check_shard_scope({})
        self.assertIsNotNone(refusal)
        self.assertIn("one shard at a time", refusal)

    def test_query_not_starting_with_shard_refused(self):
        # The mandate makes shard the first level, so a valid query always
        # leads with it. Refused synchronously here rather than waiting for
        # validate_query in the worker.
        with self._as("shard0"):
            refusal = check_shard_scope({"zone": "millholm"})
        self.assertIsNotNone(refusal)
        self.assertIn(SHARD_LEVEL, refusal)

    def test_shard_present_but_not_first_refused(self):
        # Ordering matters: the query must *start* with shard, not merely
        # mention it somewhere.
        with self._as("shard0"):
            refusal = check_shard_scope({"zone": "millholm", SHARD_LEVEL: "shard0"})
        self.assertIsNotNone(refusal)

    def test_foreign_shard_refused(self):
        with self._as("shard0"):
            refusal = check_shard_scope({SHARD_LEVEL: "shard1"})
        self.assertIsNotNone(refusal)

    def test_router_refused_for_content_shard(self):
        # The load-bearing case: deploying shard0's rules from the router
        # is what produced unstamped mobs.
        with self._as("router"):
            self.assertIsNotNone(check_shard_scope({SHARD_LEVEL: "shard0"}))

    def test_refusals_name_no_specific_shard(self):
        # Messages are generic by decision: interpolating shard ids led to
        # advice like `ms_load shard=router`, a command that cannot work.
        with self._as("shard0"):
            for query in ({}, {"zone": "x"}, {SHARD_LEVEL: "shard1"}):
                refusal = check_shard_scope(query)
                self.assertNotIn("shard0", refusal)
                self.assertNotIn("shard1", refusal)

    # --- sharded: the allowed case -----------------------------------

    def test_own_shard_allowed(self):
        with self._as("shard0"):
            self.assertIsNone(check_shard_scope({SHARD_LEVEL: "shard0"}))

    def test_own_shard_allowed_with_deeper_scope(self):
        with self._as("shard0"):
            self.assertIsNone(check_shard_scope({
                SHARD_LEVEL: "shard0", "zone": "millholm", "file": "town",
            }))


class CheckShardLevelsTest(TestCase):
    """The mandate check: definitions.yaml must declare `shard` first."""

    def _defs(self, levels):
        return Definitions.from_dict({"levels": list(levels)})

    def _as(self, shard_id):
        return mock.patch(
            "evennia_mob_spawner.commands.active_shard_id",
            return_value=shard_id,
        )

    def test_unsharded_accepts_any_levels(self):
        # Level names are consumer-chosen off a sharded deployment.
        with self._as(None):
            self.assertIsNone(check_shard_levels(self._defs(["zone", "area"])))

    def test_shard_first_accepted(self):
        with self._as("shard0"):
            self.assertIsNone(
                check_shard_levels(self._defs([SHARD_LEVEL, "zone", "file"]))
            )

    def test_mandate_not_adopted_refused(self):
        # The case nothing else catches: this query would validate cleanly
        # against the consumer's own declared levels.
        with self._as("shard0"):
            refusal = check_shard_levels(self._defs(["zone", "area"]))
        self.assertIsNotNone(refusal)
        self.assertIn(SHARD_LEVEL, refusal)

    def test_shard_declared_but_not_first_refused(self):
        with self._as("shard0"):
            refusal = check_shard_levels(self._defs(["zone", SHARD_LEVEL]))
        self.assertIsNotNone(refusal)

    def test_no_levels_declared_refused(self):
        with self._as("shard0"):
            self.assertIsNotNone(check_shard_levels(self._defs([])))


_VALID_RULE = {
    "rule_id": 1,
    "typeclass": "test.Foo",
    "key": "a thing",
    "area_tag": "test_area",
    "target": 3,
    "respawn_seconds": 30,
    "max_per_room": 2,
    "desc": "hello",
    "attrs": {"hp": 10},
    "spawn_with_typeclass": "test.Boss",
    "den_room_tag": "lair",
}


# Test fixtures for Tier 3 — resolvable importable classes / non-class objects
# that the predicates can use without depending on Evennia internals.

class _FakeTypeclass:
    """Tier 3 fixture — a class without ms_at_post_spawn."""


class _FakeTypeclassWithHook:
    """Tier 3 fixture — a class with a callable ms_at_post_spawn method."""

    def ms_at_post_spawn(self):
        pass


class _FakeTypeclassWithBadHook:
    """Tier 3 fixture — a class whose ms_at_post_spawn is NOT callable."""

    ms_at_post_spawn = "not a method"


class _FakeTypeclassWithBadHookSignature:
    """Tier 3 fixture — hook is callable but signature requires extra args."""

    def ms_at_post_spawn(self, extra_arg):
        pass


class _FakeTypeclassWithDefaultedHookSignature:
    """Tier 3 fixture — hook has extra params but all have defaults."""

    def ms_at_post_spawn(self, optional=None):
        pass


class _FakeTypeclassWithVariadicHook:
    """Tier 3 fixture — hook is variadic, callable as zero-arg."""

    def ms_at_post_spawn(self, *args, **kwargs):
        pass


def _fake_function():
    """Module-level function — resolves but is not a class."""
    pass


class FieldPredicatesHappyPathTest(TestCase):
    """Every Tier 1 predicate accepts a fully-valid rule."""

    def test_all_predicates_return_none_for_valid_rule(self):
        loaded = LoadedRule("a.yaml", _VALID_RULE)
        # death_cooldown_seconds is absent → that predicate's happy path
        # is the absent-case (returns None on missing optional). All
        # other predicates see a valid field value.
        for predicate in Validator.PER_RULE_PREDICATES:
            with self.subTest(predicate=predicate.__name__):
                self.assertIsNone(predicate(loaded))

    def test_field_predicates_short_circuit_on_non_dict(self):
        # _check_rule_is_mapping owns the non-mapping finding; the other
        # 13 predicates must return None to avoid cascading findings on
        # the same rule.
        from evennia_mob_spawner import validator as v
        non_mapping_predicates = [
            p for p in Validator.PER_RULE_PREDICATES
            if p is not v._check_rule_is_mapping
        ]
        for predicate in non_mapping_predicates:
            with self.subTest(predicate=predicate.__name__):
                self.assertIsNone(predicate(LoadedRule("a.yaml", "not a dict")))
                self.assertIsNone(predicate(LoadedRule("a.yaml", [1, 2])))
                self.assertIsNone(predicate(LoadedRule("a.yaml", None)))


class RuleMappingPredicateTest(TestCase):
    """`_check_rule_is_mapping` produces one clean finding for non-dict rules."""

    def _predicate(self):
        from evennia_mob_spawner.validator import _check_rule_is_mapping
        return _check_rule_is_mapping

    def test_dict_passes(self):
        self.assertIsNone(self._predicate()(LoadedRule("p", {"rule_id": 1})))

    def test_list_rejected(self):
        finding = self._predicate()(LoadedRule("p", ["x"]))
        self.assertIn("rule entries must be mappings", finding)
        self.assertIn("got list", finding)

    def test_string_rejected(self):
        finding = self._predicate()(LoadedRule("p", "scalar"))
        self.assertIn("got str", finding)

    def test_none_rejected(self):
        finding = self._predicate()(LoadedRule("p", None))
        self.assertIn("got NoneType", finding)


class RequiredFieldPredicatesTest(TestCase):
    """Required fields: missing / wrong-type / bad-value cases."""

    def _without(self, field):
        return {k: v for k, v in _VALID_RULE.items() if k != field}

    def _with(self, **overrides):
        return {**_VALID_RULE, **overrides}

    # rule_id ---------------------------------------------------------

    def test_rule_id_missing(self):
        from evennia_mob_spawner.validator import _check_rule_id_well_formed
        finding = _check_rule_id_well_formed(LoadedRule("p", self._without("rule_id")))
        self.assertIn("missing required field 'rule_id'", finding)

    def test_rule_id_wrong_type(self):
        from evennia_mob_spawner.validator import _check_rule_id_well_formed
        finding = _check_rule_id_well_formed(LoadedRule("p", self._with(rule_id="1")))
        self.assertIn("'rule_id' must be an integer", finding)
        self.assertIn("got str", finding)

    def test_rule_id_bool_rejected(self):
        # bool is technically int in Python — exclude explicitly.
        from evennia_mob_spawner.validator import _check_rule_id_well_formed
        finding = _check_rule_id_well_formed(LoadedRule("p", self._with(rule_id=True)))
        self.assertIn("'rule_id' must be an integer", finding)
        self.assertIn("got bool", finding)

    def test_rule_id_negative_rejected(self):
        from evennia_mob_spawner.validator import _check_rule_id_well_formed
        finding = _check_rule_id_well_formed(LoadedRule("p", self._with(rule_id=-1)))
        self.assertIn("must be non-negative", finding)

    def test_rule_id_zero_accepted(self):
        from evennia_mob_spawner.validator import _check_rule_id_well_formed
        self.assertIsNone(
            _check_rule_id_well_formed(LoadedRule("p", self._with(rule_id=0)))
        )

    # typeclass -------------------------------------------------------

    def test_typeclass_missing(self):
        from evennia_mob_spawner.validator import _check_typeclass_well_formed
        finding = _check_typeclass_well_formed(LoadedRule("p", self._without("typeclass")))
        self.assertIn("missing required field 'typeclass'", finding)

    def test_typeclass_wrong_type(self):
        from evennia_mob_spawner.validator import _check_typeclass_well_formed
        finding = _check_typeclass_well_formed(LoadedRule("p", self._with(typeclass=42)))
        self.assertIn("'typeclass' must be a string", finding)

    def test_typeclass_empty_rejected(self):
        from evennia_mob_spawner.validator import _check_typeclass_well_formed
        finding = _check_typeclass_well_formed(LoadedRule("p", self._with(typeclass="  ")))
        self.assertIn("must be a non-empty string", finding)

    # key -------------------------------------------------------------

    def test_key_missing(self):
        from evennia_mob_spawner.validator import _check_key_well_formed
        finding = _check_key_well_formed(LoadedRule("p", self._without("key")))
        self.assertIn("missing required field 'key'", finding)

    def test_key_wrong_type(self):
        from evennia_mob_spawner.validator import _check_key_well_formed
        finding = _check_key_well_formed(LoadedRule("p", self._with(key=None)))
        self.assertIn("'key' must be a string", finding)

    def test_key_empty_rejected(self):
        from evennia_mob_spawner.validator import _check_key_well_formed
        finding = _check_key_well_formed(LoadedRule("p", self._with(key="")))
        self.assertIn("must be a non-empty string", finding)

    # area_tag --------------------------------------------------------

    def test_area_tag_missing(self):
        from evennia_mob_spawner.validator import _check_area_tag_well_formed
        finding = _check_area_tag_well_formed(LoadedRule("p", self._without("area_tag")))
        self.assertIn("missing required field 'area_tag'", finding)

    def test_area_tag_wrong_type(self):
        from evennia_mob_spawner.validator import _check_area_tag_well_formed
        finding = _check_area_tag_well_formed(LoadedRule("p", self._with(area_tag=["a"])))
        self.assertIn("'area_tag' must be a string", finding)

    def test_area_tag_empty_rejected(self):
        from evennia_mob_spawner.validator import _check_area_tag_well_formed
        finding = _check_area_tag_well_formed(LoadedRule("p", self._with(area_tag="")))
        self.assertIn("must be a non-empty string", finding)

    # target ----------------------------------------------------------

    def test_target_missing(self):
        from evennia_mob_spawner.validator import _check_target_well_formed
        finding = _check_target_well_formed(LoadedRule("p", self._without("target")))
        self.assertIn("missing required field 'target'", finding)

    def test_target_wrong_type(self):
        from evennia_mob_spawner.validator import _check_target_well_formed
        finding = _check_target_well_formed(LoadedRule("p", self._with(target=1.5)))
        self.assertIn("'target' must be an integer", finding)

    def test_target_bool_rejected(self):
        from evennia_mob_spawner.validator import _check_target_well_formed
        finding = _check_target_well_formed(LoadedRule("p", self._with(target=True)))
        self.assertIn("got bool", finding)

    def test_target_zero_rejected(self):
        from evennia_mob_spawner.validator import _check_target_well_formed
        finding = _check_target_well_formed(LoadedRule("p", self._with(target=0)))
        self.assertIn("'target' must be at least 1", finding)

    # max_per_room ----------------------------------------------------

    def test_max_per_room_missing(self):
        from evennia_mob_spawner.validator import _check_max_per_room_well_formed
        finding = _check_max_per_room_well_formed(
            LoadedRule("p", self._without("max_per_room")),
        )
        self.assertIn("missing required field 'max_per_room'", finding)

    def test_max_per_room_wrong_type(self):
        from evennia_mob_spawner.validator import _check_max_per_room_well_formed
        finding = _check_max_per_room_well_formed(
            LoadedRule("p", self._with(max_per_room=1.5)),
        )
        self.assertIn("'max_per_room' must be an integer", finding)

    def test_max_per_room_zero_rejected(self):
        from evennia_mob_spawner.validator import _check_max_per_room_well_formed
        finding = _check_max_per_room_well_formed(
            LoadedRule("p", self._with(max_per_room=0)),
        )
        self.assertIn("must be at least 1", finding)


class CooldownExclusivityPredicateTest(TestCase):
    """`_check_cooldown_exclusivity` — exactly one of the cooldown pair."""

    def _predicate(self):
        from evennia_mob_spawner.validator import _check_cooldown_exclusivity
        return _check_cooldown_exclusivity

    def _rule(self, **overrides):
        # Strip both cooldowns from _VALID_RULE, then add what each test wants.
        base = {k: v for k, v in _VALID_RULE.items()
                if k not in ("respawn_seconds", "death_cooldown_seconds")}
        base.update(overrides)
        return base

    def test_only_respawn_seconds_passes(self):
        self.assertIsNone(self._predicate()(
            LoadedRule("p", self._rule(respawn_seconds=60)),
        ))

    def test_only_death_cooldown_seconds_passes(self):
        self.assertIsNone(self._predicate()(
            LoadedRule("p", self._rule(death_cooldown_seconds=120)),
        ))

    def test_both_present_rejected(self):
        finding = self._predicate()(LoadedRule("p", self._rule(
            respawn_seconds=60, death_cooldown_seconds=120,
        )))
        self.assertIn("mutually exclusive", finding)

    def test_neither_present_rejected(self):
        finding = self._predicate()(LoadedRule("p", self._rule()))
        self.assertIn("must declare exactly one of", finding)

    def test_presence_is_dict_key_level_not_value_validity(self):
        # Author writes `respawn_seconds: null` plus a real
        # death_cooldown_seconds. Exclusivity sees BOTH keys present —
        # rejects. (Value validity is the per-field predicate's concern.)
        finding = self._predicate()(LoadedRule("p", self._rule(
            respawn_seconds=None, death_cooldown_seconds=60,
        )))
        self.assertIn("mutually exclusive", finding)


class OptionalNumericPredicatesTest(TestCase):
    """Optional numeric fields: absent / wrong-type / bad-value cases."""

    def _rule(self, **fields):
        # Strip both cooldowns so each test can put one back as appropriate
        # without colliding with the cooldown_exclusivity predicate.
        base = {k: v for k, v in _VALID_RULE.items()
                if k not in ("respawn_seconds", "death_cooldown_seconds")}
        base.update(fields)
        return base

    # respawn_seconds -------------------------------------------------

    def test_respawn_seconds_absent_passes(self):
        from evennia_mob_spawner.validator import _check_respawn_seconds_well_formed
        self.assertIsNone(_check_respawn_seconds_well_formed(LoadedRule("p", self._rule())))

    def test_respawn_seconds_wrong_type(self):
        from evennia_mob_spawner.validator import _check_respawn_seconds_well_formed
        finding = _check_respawn_seconds_well_formed(
            LoadedRule("p", self._rule(respawn_seconds="60")),
        )
        self.assertIn("'respawn_seconds' must be a number", finding)

    def test_respawn_seconds_bool_rejected(self):
        from evennia_mob_spawner.validator import _check_respawn_seconds_well_formed
        finding = _check_respawn_seconds_well_formed(
            LoadedRule("p", self._rule(respawn_seconds=False)),
        )
        self.assertIn("must be a number", finding)
        self.assertIn("got bool", finding)

    def test_respawn_seconds_negative_rejected(self):
        from evennia_mob_spawner.validator import _check_respawn_seconds_well_formed
        finding = _check_respawn_seconds_well_formed(
            LoadedRule("p", self._rule(respawn_seconds=-1)),
        )
        self.assertIn("must be non-negative", finding)

    def test_respawn_seconds_zero_accepted(self):
        # 0 means "no cooldown" — explicit, valid.
        from evennia_mob_spawner.validator import _check_respawn_seconds_well_formed
        self.assertIsNone(_check_respawn_seconds_well_formed(
            LoadedRule("p", self._rule(respawn_seconds=0)),
        ))

    def test_respawn_seconds_float_accepted(self):
        from evennia_mob_spawner.validator import _check_respawn_seconds_well_formed
        self.assertIsNone(_check_respawn_seconds_well_formed(
            LoadedRule("p", self._rule(respawn_seconds=1.5)),
        ))

    # death_cooldown_seconds — share-validation contract is identical;
    # one negative case is enough to confirm the predicate is wired.

    def test_death_cooldown_seconds_wrong_type(self):
        from evennia_mob_spawner.validator import _check_death_cooldown_seconds_well_formed
        finding = _check_death_cooldown_seconds_well_formed(
            LoadedRule("p", self._rule(death_cooldown_seconds="60")),
        )
        self.assertIn("'death_cooldown_seconds' must be a number", finding)

    def test_death_cooldown_seconds_negative_rejected(self):
        from evennia_mob_spawner.validator import _check_death_cooldown_seconds_well_formed
        finding = _check_death_cooldown_seconds_well_formed(
            LoadedRule("p", self._rule(death_cooldown_seconds=-5)),
        )
        self.assertIn("must be non-negative", finding)


class OptionalStringPredicatesTest(TestCase):
    """Optional string fields: absent / wrong-type / empty cases."""

    def _rule(self, **fields):
        base = {k: v for k, v in _VALID_RULE.items()
                if k not in ("desc", "spawn_with_typeclass", "den_room_tag")}
        base.update(fields)
        return base

    # desc — empty IS allowed (override-to-empty semantic).

    def test_desc_absent_passes(self):
        from evennia_mob_spawner.validator import _check_desc_well_formed
        self.assertIsNone(_check_desc_well_formed(LoadedRule("p", self._rule())))

    def test_desc_empty_string_accepted(self):
        from evennia_mob_spawner.validator import _check_desc_well_formed
        self.assertIsNone(_check_desc_well_formed(
            LoadedRule("p", self._rule(desc="")),
        ))

    def test_desc_wrong_type(self):
        from evennia_mob_spawner.validator import _check_desc_well_formed
        finding = _check_desc_well_formed(LoadedRule("p", self._rule(desc=42)))
        self.assertIn("'desc' must be a string", finding)

    # spawn_with_typeclass

    def test_spawn_with_typeclass_wrong_type(self):
        from evennia_mob_spawner.validator import _check_spawn_with_typeclass_well_formed
        finding = _check_spawn_with_typeclass_well_formed(
            LoadedRule("p", self._rule(spawn_with_typeclass=[])),
        )
        self.assertIn("'spawn_with_typeclass' must be a string", finding)

    def test_spawn_with_typeclass_empty_rejected(self):
        from evennia_mob_spawner.validator import _check_spawn_with_typeclass_well_formed
        finding = _check_spawn_with_typeclass_well_formed(
            LoadedRule("p", self._rule(spawn_with_typeclass="   ")),
        )
        self.assertIn("must be a non-empty string", finding)

    # den_room_tag

    def test_den_room_tag_wrong_type(self):
        from evennia_mob_spawner.validator import _check_den_room_tag_well_formed
        finding = _check_den_room_tag_well_formed(
            LoadedRule("p", self._rule(den_room_tag=None)),
        )
        self.assertIn("'den_room_tag' must be a string", finding)

    def test_den_room_tag_empty_rejected(self):
        from evennia_mob_spawner.validator import _check_den_room_tag_well_formed
        finding = _check_den_room_tag_well_formed(
            LoadedRule("p", self._rule(den_room_tag="")),
        )
        self.assertIn("must be a non-empty string", finding)


class OptionalAttrsPredicateTest(TestCase):
    """`attrs` must be a mapping if present."""

    def _rule(self, **fields):
        base = {k: v for k, v in _VALID_RULE.items() if k != "attrs"}
        base.update(fields)
        return base

    def test_attrs_absent_passes(self):
        from evennia_mob_spawner.validator import _check_attrs_well_formed
        self.assertIsNone(_check_attrs_well_formed(LoadedRule("p", self._rule())))

    def test_attrs_empty_dict_passes(self):
        from evennia_mob_spawner.validator import _check_attrs_well_formed
        self.assertIsNone(_check_attrs_well_formed(LoadedRule("p", self._rule(attrs={}))))

    def test_attrs_list_rejected(self):
        from evennia_mob_spawner.validator import _check_attrs_well_formed
        finding = _check_attrs_well_formed(LoadedRule("p", self._rule(attrs=["x"])))
        self.assertIn("'attrs' must be a mapping", finding)

    def test_attrs_string_rejected(self):
        from evennia_mob_spawner.validator import _check_attrs_well_formed
        finding = _check_attrs_well_formed(LoadedRule("p", self._rule(attrs="x")))
        self.assertIn("'attrs' must be a mapping", finding)


class TagsFieldShapeTest(TestCase):
    """`tags` field shape — list of strings or {key, category?} dicts."""

    def _rule(self, **fields):
        base = {k: v for k, v in _VALID_RULE.items() if k != "tags"}
        base.update(fields)
        return base

    def _check(self, **fields):
        from evennia_mob_spawner.validator import _check_tags_field_shape
        return _check_tags_field_shape(LoadedRule("p", self._rule(**fields)))

    def test_tags_absent_passes(self):
        self.assertIsNone(self._check())

    def test_tags_empty_list_passes(self):
        self.assertIsNone(self._check(tags=[]))

    def test_tags_bare_string_passes(self):
        self.assertIsNone(self._check(tags=["my_tag"]))

    def test_tags_dict_key_only_passes(self):
        self.assertIsNone(self._check(tags=[{"key": "my_tag"}]))

    def test_tags_dict_key_and_category_passes(self):
        self.assertIsNone(
            self._check(tags=[{"key": "my_tag", "category": "my_cat"}])
        )

    def test_tags_mixed_shapes_passes(self):
        self.assertIsNone(self._check(tags=[
            "bare",
            {"key": "only_key"},
            {"key": "with_cat", "category": "cat"},
        ]))

    def test_tags_not_a_list_rejected(self):
        finding = self._check(tags={"key": "x"})
        self.assertIn("'tags' must be a list", finding)

    def test_tags_entry_not_str_or_dict_rejected(self):
        finding = self._check(tags=[7])
        self.assertIn("tags[0]", finding)
        self.assertIn("string or mapping", finding)

    def test_tags_empty_string_entry_rejected(self):
        finding = self._check(tags=["  "])
        self.assertIn("tags[0]", finding)
        self.assertIn("non-empty string", finding)

    def test_tags_dict_missing_key_rejected(self):
        finding = self._check(tags=[{"category": "cat"}])
        self.assertIn("tags[0]", finding)
        self.assertIn("missing required 'key'", finding)

    def test_tags_dict_empty_key_rejected(self):
        finding = self._check(tags=[{"key": ""}])
        self.assertIn("tags[0]", finding)
        self.assertIn("'key' must be a non-empty string", finding)

    def test_tags_dict_non_string_key_rejected(self):
        finding = self._check(tags=[{"key": 5}])
        self.assertIn("tags[0]", finding)
        self.assertIn("'key' must be a string", finding)

    def test_tags_dict_non_string_category_rejected(self):
        finding = self._check(tags=[{"key": "k", "category": 5}])
        self.assertIn("tags[0]", finding)
        self.assertIn("'category' must be a string", finding)

    def test_tags_dict_empty_category_rejected(self):
        finding = self._check(tags=[{"key": "k", "category": ""}])
        self.assertIn("tags[0]", finding)
        self.assertIn("'category' must be a non-empty string", finding)

    def test_tags_dict_extra_keys_rejected(self):
        finding = self._check(tags=[{"key": "k", "data": "x"}])
        self.assertIn("tags[0]", finding)
        self.assertIn("unsupported key", finding)


class TagsReservedCategoryTest(TestCase):
    """`tags` entries cannot use library-reserved `mob_spawner_*` categories."""

    def _rule(self, **fields):
        base = {k: v for k, v in _VALID_RULE.items() if k != "tags"}
        base.update(fields)
        return base

    def _check(self, **fields):
        from evennia_mob_spawner.validator import _check_tags_no_reserved_category
        return _check_tags_no_reserved_category(LoadedRule("p", self._rule(**fields)))

    def test_tags_absent_passes(self):
        self.assertIsNone(self._check())

    def test_non_reserved_category_passes(self):
        self.assertIsNone(
            self._check(tags=[{"key": "k", "category": "spawn_resources"}])
        )

    def test_bare_string_passes(self):
        # Bare strings have no category — can't collide with reserved prefix.
        self.assertIsNone(self._check(tags=["plain"]))

    def test_dict_without_category_passes(self):
        self.assertIsNone(self._check(tags=[{"key": "k"}]))

    def test_reserved_rule_category_rejected(self):
        finding = self._check(tags=[
            {"key": "1", "category": "mob_spawner_rule"},
        ])
        self.assertIn("tags[0]", finding)
        self.assertIn("reserved category", finding)
        self.assertIn("mob_spawner_", finding)

    def test_reserved_file_category_rejected(self):
        finding = self._check(tags=[
            {"key": "any.yaml", "category": "mob_spawner_file"},
        ])
        self.assertIn("tags[0]", finding)
        self.assertIn("reserved category", finding)

    def test_reserved_prefix_anything_rejected(self):
        # Anything starting with mob_spawner_ is refused, not just the
        # currently-used categories.
        finding = self._check(tags=[
            {"key": "k", "category": "mob_spawner_future_thing"},
        ])
        self.assertIn("tags[0]", finding)
        self.assertIn("reserved category", finding)

    def test_mixed_legal_and_reserved_rejected_on_first_reserved(self):
        # Two legal entries surrounding one reserved — finding identifies the
        # reserved entry by index.
        finding = self._check(tags=[
            {"key": "k1", "category": "spawn_resources"},
            {"key": "k2", "category": "mob_spawner_rule"},
            {"key": "k3", "category": "spawn_gold"},
        ])
        self.assertIn("tags[1]", finding)


class ValidatorPredicateIntegrationTest(TestCase):
    """End-to-end through Validator.validate() exercising real predicates."""

    def _make_validator(self):
        return Validator(Definitions.from_dict({"levels": []}))

    def test_valid_rule_passes_validate(self):
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={"a.yaml": [_VALID_RULE]}))
        self.assertEqual(v.errors, [])

    def test_rule_missing_multiple_required_fields_accumulates_findings(self):
        # Empty rule → all 6 required-field predicates flag (rule_id,
        # typeclass, key, area_tag, target, max_per_room) plus
        # cooldown_exclusivity ("neither declared"). 7 findings.
        # rule_is_mapping passes because the rule IS a dict.
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [{}]}))
        self.assertEqual(len(v.errors), 7)

    def test_non_dict_rule_produces_one_clean_finding(self):
        # The other 13 predicates short-circuit; only _check_rule_is_mapping
        # fires. Operator sees one clear message, not 14.
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": ["not a dict"]}))
        self.assertEqual(len(v.errors), 1)
        self.assertIn("rule entries must be mappings", v.errors[0])


class Tier2UniqueRuleIdTest(TestCase):
    """Tier 2 — `rule_id` unique within each file."""

    def _make_validator(self):
        return Validator(Definitions.from_dict({"levels": []}))

    def _rule(self, rule_id):
        return {**_VALID_RULE, "rule_id": rule_id}

    def test_unique_ids_within_file_pass(self):
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(1), self._rule(2), self._rule(3),
        ]}))
        self.assertEqual(v.errors, [])

    def test_duplicate_id_within_same_file_flagged(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(1), self._rule(1),
            ]}))
        self.assertEqual(len(v.errors), 1)
        self.assertIn("duplicate rule_id 1", v.errors[0])

    def test_same_id_across_different_files_passes(self):
        # rule_id uniqueness is per-file, not global.
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={
            "a.yaml": [self._rule(1)],
            "b.yaml": [self._rule(1)],
        }))
        self.assertEqual(v.errors, [])

    def test_three_copies_of_same_id_produce_two_findings(self):
        # Each duplicate is its own finding — operator can see how many
        # collisions there are at a glance.
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(5), self._rule(5), self._rule(5),
            ]}))
        self.assertEqual(len(v.errors), 2)

    def test_tier_2_skipped_when_rule_fails_tier_1(self):
        # Two rules both with rule_id=7, but the second is missing
        # required fields. Tier 1 flags the missing fields → Tier 2 is
        # skipped for the second rule → no duplicate-rule_id finding.
        v = self._make_validator()
        bad = {"rule_id": 7}  # missing everything else
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(7),  # passes Tier 1
                bad,             # fails Tier 1 — skipped by Tier 2
            ]}))
        # All errors should be Tier 1 failures from `bad`; none should
        # mention duplicate rule_id.
        for err in v.errors:
            self.assertNotIn("duplicate rule_id", err)

    def test_seen_ids_populated_after_pass(self):
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={
            "a.yaml": [self._rule(1), self._rule(2)],
            "b.yaml": [self._rule(1)],
        }))
        self.assertEqual(v.seen_ids, {
            "a.yaml": {1, 2},
            "b.yaml": {1},
        })


class FileMetadataShapeTest(TestCase):
    """File-level shape pass — `_check_file_metadata_shape`."""

    def _make_validator(self):
        return Validator(Definitions.from_dict({"levels": []}))

    def test_empty_file_metadata_passes(self):
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={}, file_metadata={}))
        self.assertEqual(v.errors, [])

    def test_mapping_value_passes(self):
        v = self._make_validator()
        v.validate(LoadResult(
            rule_sets={},
            file_metadata={"a.yaml": {"display_name": "Test Zone"}},
        ))
        self.assertEqual(v.errors, [])

    def test_non_mapping_value_rejected(self):
        # The Loader never produces this, but defends LoadResult
        # against direct construction with bad metadata.
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(
                rule_sets={},
                file_metadata={"a.yaml": "not a mapping"},
            ))
        self.assertEqual(len(v.errors), 1)
        self.assertIn("file metadata must be a mapping", v.errors[0])
        self.assertIn("got str", v.errors[0])

    def test_multiple_bad_metadata_entries_all_flagged(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(
                rule_sets={},
                file_metadata={
                    "a.yaml": "scalar",
                    "b.yaml": ["a", "list"],
                },
            ))
        # Each malformed entry surfaces its own finding.
        self.assertEqual(len(v.errors), 2)


_FAKE_TC = "evennia_mob_spawner.tests._FakeTypeclass"
_FAKE_TC_HOOK = "evennia_mob_spawner.tests._FakeTypeclassWithHook"
_FAKE_TC_BAD_HOOK = "evennia_mob_spawner.tests._FakeTypeclassWithBadHook"
_FAKE_FUNC = "evennia_mob_spawner.tests._fake_function"


class Tier3ResolvabilityTest(TestCase):
    """Tier 3 — engine-runtime predicates for dotted-path resolution.

    Tier 3 fires only with ``evennia_runtime=True`` (ms_load); ms-validate
    (CLI) leaves it off. Three predicates:
    - `typeclass` resolves to a class
    - `spawn_with_typeclass` (when present) resolves to a class
    - the resolved `typeclass`'s optional `ms_at_post_spawn` is callable
    """

    def _make_validator(self):
        return Validator(
            Definitions.from_dict({"levels": []}),
            evennia_runtime=True,
        )

    def _rule(self, **overrides):
        # Strip the dotted-path fields from _VALID_RULE; each test fills
        # in what it wants.
        base = {k: v for k, v in _VALID_RULE.items()
                if k not in ("typeclass", "spawn_with_typeclass")}
        base.update(overrides)
        return base

    # typeclass --------------------------------------------------------

    def test_resolvable_typeclass_passes(self):
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass=_FAKE_TC),
        ]}))
        self.assertEqual(v.errors, [])

    def test_typeclass_module_not_importable_flagged(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(typeclass="does.not.exist.AnyClass"),
            ]}))
        self.assertIn("could not be imported", v.errors[0])

    def test_typeclass_module_loads_but_class_missing(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(typeclass="evennia_mob_spawner.tests.NonExistent"),
            ]}))
        self.assertIn("not found", v.errors[0])

    def test_typeclass_not_a_dotted_path_flagged(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(typeclass="Foo"),
            ]}))
        self.assertIn("not a dotted path", v.errors[0])

    def test_typeclass_resolves_but_is_not_a_class_flagged(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(typeclass=_FAKE_FUNC),  # function, not class
            ]}))
        self.assertIn("is not a class", v.errors[0])

    # spawn_with_typeclass ---------------------------------------------

    def test_spawn_with_typeclass_resolvable_passes(self):
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass=_FAKE_TC, spawn_with_typeclass=_FAKE_TC),
        ]}))
        self.assertEqual(v.errors, [])

    def test_spawn_with_typeclass_absent_passes(self):
        # Absent is fine — optional field.
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass=_FAKE_TC),
        ]}))
        self.assertEqual(v.errors, [])

    def test_spawn_with_typeclass_module_not_importable_flagged(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(
                    typeclass=_FAKE_TC,
                    spawn_with_typeclass="missing.module.Class",
                ),
            ]}))
        self.assertIn("'spawn_with_typeclass'", v.errors[0])

    def test_spawn_with_typeclass_resolves_but_not_a_class_flagged(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(
                    typeclass=_FAKE_TC,
                    spawn_with_typeclass=_FAKE_FUNC,
                ),
            ]}))
        self.assertIn("'spawn_with_typeclass'", v.errors[0])
        self.assertIn("is not a class", v.errors[0])

    # ms_at_post_spawn -------------------------------------------------

    def test_typeclass_with_callable_ms_at_post_spawn_passes(self):
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass=_FAKE_TC_HOOK),
        ]}))
        self.assertEqual(v.errors, [])

    def test_typeclass_without_ms_at_post_spawn_passes(self):
        # Method is optional — its absence is silent, not a finding.
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass=_FAKE_TC),
        ]}))
        self.assertEqual(v.errors, [])

    def test_typeclass_with_non_callable_ms_at_post_spawn_flagged(self):
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(typeclass=_FAKE_TC_BAD_HOOK),
            ]}))
        self.assertIn("ms_at_post_spawn", v.errors[0])
        self.assertIn("not callable", v.errors[0])

    # ms_at_post_spawn signature --------------------------------------

    def test_ms_at_post_spawn_canonical_signature_passes(self):
        # def ms_at_post_spawn(self): ...
        v = self._make_validator()
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass=_FAKE_TC_HOOK),
        ]}))
        self.assertEqual(v.errors, [])

    def test_ms_at_post_spawn_extra_required_arg_flagged(self):
        # def ms_at_post_spawn(self, extra_arg): ...
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        bad_path = "evennia_mob_spawner.tests._FakeTypeclassWithBadHookSignature"
        with self.assertRaises(ValidatorError):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(typeclass=bad_path),
            ]}))
        self.assertIn("requires additional arguments", v.errors[0])
        self.assertIn("extra_arg", v.errors[0])

    def test_ms_at_post_spawn_defaulted_args_pass(self):
        # def ms_at_post_spawn(self, optional=None): ...
        v = self._make_validator()
        good_path = "evennia_mob_spawner.tests._FakeTypeclassWithDefaultedHookSignature"
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass=good_path),
        ]}))
        self.assertEqual(v.errors, [])

    def test_ms_at_post_spawn_variadic_signature_passes(self):
        # def ms_at_post_spawn(self, *args, **kwargs): ...
        v = self._make_validator()
        good_path = "evennia_mob_spawner.tests._FakeTypeclassWithVariadicHook"
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass=good_path),
        ]}))
        self.assertEqual(v.errors, [])

    # Tier 3 gating ----------------------------------------------------

    def test_tier_3_not_run_when_evennia_runtime_false(self):
        # Bad typeclass path passes silently if engine flag is off.
        v = Validator(
            Definitions.from_dict({"levels": []}),
            evennia_runtime=False,
        )
        v.validate(LoadResult(rule_sets={"a.yaml": [
            self._rule(typeclass="does.not.exist.AnyClass"),
        ]}))
        self.assertEqual(v.errors, [])


class Tier4DiagnosticTest(TestCase):
    """Tier 4 — deploy-time diagnostic warnings (decision #24).

    Diagnostics never refuse, only log. Same gating as Tier 3
    (``evennia_runtime=True``), runs after the predicate-driven loop,
    only on rules that survived Tier 1. The DB query is mocked because
    the test database is isolated from the diagnostic's tag table; the
    happy path stubs ``count() = N``, the warning path stubs ``count() = 0``.
    """

    def _make_validator(self):
        return Validator(
            Definitions.from_dict({"levels": []}),
            evennia_runtime=True,
        )

    def _resolvable_rule(self, **overrides):
        # Use a real importable class so Tier 3 passes; the diagnostic
        # is what we're isolating.
        base = {k: v for k, v in _VALID_RULE.items()
                if k not in ("typeclass", "spawn_with_typeclass")}
        base["typeclass"] = _FAKE_TC
        base.update(overrides)
        return base

    def _validate_with_db_count(self, validator, load_result, *, count):
        """Run validate with ObjectDB.objects.filter().count() stubbed.

        Returns a list of (message, level) tuples captured from
        ``ms_log`` calls.
        """
        from unittest.mock import patch, MagicMock

        captured = []

        def fake_log(message, level="INFO"):
            captured.append((message, level))

        fake_qs = MagicMock()
        fake_qs.count.return_value = count

        with patch(
            "evennia.objects.models.ObjectDB.objects",
            new=MagicMock(filter=MagicMock(return_value=fake_qs)),
        ), patch("evennia_mob_spawner.validator.ms_log", new=fake_log):
            validator.validate(load_result)

        return captured

    # area_tag ---------------------------------------------------------

    def test_area_tag_with_rooms_no_warning(self):
        v = self._make_validator()
        logs = self._validate_with_db_count(
            v,
            LoadResult(rule_sets={"a.yaml": [self._resolvable_rule()]}),
            count=3,
        )
        area_warnings = [m for m, lvl in logs
                         if lvl == "WARN" and "area_tag" in m]
        self.assertEqual(area_warnings, [])

    def test_area_tag_with_zero_rooms_logs_warning(self):
        v = self._make_validator()
        logs = self._validate_with_db_count(
            v,
            LoadResult(rule_sets={"a.yaml": [self._resolvable_rule()]}),
            count=0,
        )
        area_warnings = [m for m, lvl in logs
                         if lvl == "WARN" and "area_tag" in m]
        self.assertEqual(len(area_warnings), 1)
        self.assertIn("0 tagged rooms", area_warnings[0])

    # den_room_tag -----------------------------------------------------

    def test_den_room_tag_absent_no_warning(self):
        # _VALID_RULE has den_room_tag — strip it for this test.
        v = self._make_validator()
        rule = self._resolvable_rule()
        rule.pop("den_room_tag", None)
        logs = self._validate_with_db_count(
            v, LoadResult(rule_sets={"a.yaml": [rule]}), count=0,
        )
        den_warnings = [m for m, lvl in logs
                        if lvl == "WARN" and "den_room_tag" in m]
        self.assertEqual(den_warnings, [])

    def test_den_room_tag_with_zero_rooms_logs_warning(self):
        v = self._make_validator()
        logs = self._validate_with_db_count(
            v,
            LoadResult(rule_sets={"a.yaml": [self._resolvable_rule()]}),
            count=0,
        )
        den_warnings = [m for m, lvl in logs
                        if lvl == "WARN" and "den_room_tag" in m]
        self.assertEqual(len(den_warnings), 1)

    # Gating: Tier 4 stays off without evennia_runtime ----------------

    def test_tier_4_not_run_when_evennia_runtime_false(self):
        # The CLI path. Tier 4 should not fire at all — no DB query,
        # no log line. Importing evennia.objects.models inside the
        # diagnostic shouldn't even be attempted.
        v = Validator(
            Definitions.from_dict({"levels": []}),
            evennia_runtime=False,
        )
        from unittest.mock import patch

        captured = []

        def fake_log(message, level="INFO"):
            captured.append((message, level))

        with patch("evennia_mob_spawner.validator.ms_log", new=fake_log):
            v.validate(LoadResult(rule_sets={"a.yaml": [self._resolvable_rule()]}))

        self.assertEqual(captured, [])

    # Gating: Tier 4 skipped on rules that failed Tier 1 --------------

    def test_tier_4_skipped_when_rule_failed_tier_1(self):
        # Rule missing typeclass — Tier 1 flags it, Tier 4 must not run
        # (no point diagnosing a rule that won't deploy).
        v = self._make_validator()
        rule = self._resolvable_rule()
        rule.pop("typeclass")
        from evennia_mob_spawner.errors import ValidatorError
        from unittest.mock import patch

        captured = []

        def fake_log(message, level="INFO"):
            captured.append((message, level))

        with patch("evennia_mob_spawner.validator.ms_log", new=fake_log):
            with self.assertRaises(ValidatorError):
                v.validate(LoadResult(rule_sets={"a.yaml": [rule]}))

        # No WARN about area_tag — diagnostic was skipped.
        warnings = [m for m, lvl in captured if lvl == "WARN"]
        self.assertEqual(warnings, [])


class DeployerTest(EvenniaWorldTestCase):
    """End-to-end deployment: script lookup-or-create, swap, preserve, purge.

    Uses real Evennia ``create_script`` / DB queries against the test
    database (``runtests.py`` calls ``evennia._init()`` which sets up
    the typeclass registry). The script's ``at_repeat`` is a no-op
    stub at this stage — these tests cover lifecycle plumbing only,
    not the (still-pending) tick loop.
    """

    def _make_deployer(self):
        from evennia_mob_spawner.deployer import Deployer
        defs = Definitions.from_dict({"levels": []})
        return Deployer(defs)

    def _rule(self, rule_id, **overrides):
        # Build a minimally-valid rule shape. The Deployer doesn't
        # re-validate — rules arrive already validated.
        base = {
            "rule_id": rule_id,
            "typeclass": "evennia.objects.objects.DefaultObject",
            "key": "a thing",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 30,
        }
        base.update(overrides)
        return base

    def tearDown(self):
        # Tests share the in-memory DB across methods within a class;
        # Django's TestCase resets per-test, but ScriptDB rows created
        # via create_script can persist into the next test if not
        # cleaned up. Belt-and-suspenders.
        from evennia_mob_spawner.script import MobSpawnerScript
        for script in MobSpawnerScript.objects.all():
            script.delete()

    def test_deploy_creates_new_script(self):
        deployer = self._make_deployer()
        deployer.deploy(LoadResult(rule_sets={
            "a.yaml": [self._rule(1)],
        }))

        from evennia_mob_spawner.script import MobSpawnerScript
        scripts = MobSpawnerScript.objects.filter(db_key="a.yaml")
        self.assertEqual(scripts.count(), 1)
        script = scripts.first()
        self.assertEqual(len(script.db.spawn_table), 1)
        self.assertEqual(script.db.spawn_table[0]["rule_id"], 1)

    def test_deploy_separate_files_creates_separate_scripts(self):
        deployer = self._make_deployer()
        deployer.deploy(LoadResult(rule_sets={
            "a.yaml": [self._rule(1)],
            "b.yaml": [self._rule(1)],   # same rule_id, different file
        }))

        from evennia_mob_spawner.script import MobSpawnerScript
        self.assertEqual(MobSpawnerScript.objects.filter(db_key="a.yaml").count(), 1)
        self.assertEqual(MobSpawnerScript.objects.filter(db_key="b.yaml").count(), 1)

    def test_redeploy_reuses_existing_script(self):
        # Two deploys of the same path should NOT create two scripts.
        deployer = self._make_deployer()
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule(1)]}))
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule(1)]}))

        from evennia_mob_spawner.script import MobSpawnerScript
        self.assertEqual(MobSpawnerScript.objects.filter(db_key="a.yaml").count(), 1)

    def test_swap_replaces_spawn_table(self):
        deployer = self._make_deployer()
        # First deploy: rules 1, 2.
        deployer.deploy(LoadResult(rule_sets={
            "a.yaml": [self._rule(1), self._rule(2)],
        }))
        # Second deploy: rules 2, 3 — rule 1 vanished, rule 3 is new.
        deployer.deploy(LoadResult(rule_sets={
            "a.yaml": [self._rule(2), self._rule(3)],
        }))

        from evennia_mob_spawner.script import MobSpawnerScript
        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        ids = {r["rule_id"] for r in script.db.spawn_table}
        self.assertEqual(ids, {2, 3})

    def test_state_preserved_for_surviving_rules(self):
        deployer = self._make_deployer()
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule(1), self._rule(2)]}))

        from evennia_mob_spawner.script import MobSpawnerScript
        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        # Simulate runtime accumulation of state across ticks.
        script.db.last_spawn_times = {1: 1000.0, 2: 2000.0}
        script.db.last_death_times = {1: 1500.0, 2: 2500.0}
        script.db.last_observed_counts = {1: 5, 2: 3}

        # Re-deploy with both rules unchanged.
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule(1), self._rule(2)]}))

        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        self.assertEqual(script.db.last_spawn_times, {1: 1000.0, 2: 2000.0})
        self.assertEqual(script.db.last_death_times, {1: 1500.0, 2: 2500.0})
        self.assertEqual(script.db.last_observed_counts, {1: 5, 2: 3})

    def test_state_purged_for_removed_rules(self):
        deployer = self._make_deployer()
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule(1), self._rule(2)]}))

        from evennia_mob_spawner.script import MobSpawnerScript
        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        script.db.last_spawn_times = {1: 1000.0, 2: 2000.0}
        script.db.last_death_times = {1: 1500.0, 2: 2500.0}
        script.db.last_observed_counts = {1: 5, 2: 3}

        # Re-deploy with rule 1 removed.
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule(2)]}))

        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        self.assertEqual(script.db.last_spawn_times, {2: 2000.0})
        self.assertEqual(script.db.last_death_times, {2: 2500.0})
        self.assertEqual(script.db.last_observed_counts, {2: 3})

    def test_new_rule_starts_with_no_bookkeeping(self):
        # rule_id 3 is brand new — no entry in any state dict.
        deployer = self._make_deployer()
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule(1)]}))

        from evennia_mob_spawner.script import MobSpawnerScript
        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        script.db.last_spawn_times = {1: 1000.0}

        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule(1), self._rule(3)]}))

        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        self.assertEqual(script.db.last_spawn_times, {1: 1000.0})
        self.assertNotIn(3, script.db.last_spawn_times)

    def test_empty_load_result_creates_no_scripts(self):
        deployer = self._make_deployer()
        deployer.deploy(LoadResult(rule_sets={}))

        from evennia_mob_spawner.script import MobSpawnerScript
        self.assertEqual(MobSpawnerScript.objects.count(), 0)


class _TickLoopFixture:
    """Helper for TickLoopTest — sets up rooms, deploys a rule, returns script.

    Kept as a plain helper class rather than a TestCase mixin so the
    test methods can construct exactly the fixture they need with
    different rules / room layouts.
    """

    @staticmethod
    def make_room(key, *, area_tag="test_area", extra_tag=None):
        from evennia import DefaultRoom
        from evennia.utils.create import create_object
        room = create_object(DefaultRoom, key=key)
        room.tags.add(area_tag, category="mob_area")
        if extra_tag:
            room.tags.add(extra_tag, category="mob_area")
        return room

    @staticmethod
    def deploy_rule(rule, path="test.yaml"):
        from evennia_mob_spawner.deployer import Deployer
        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(rule_sets={path: [rule]}))
        from evennia_mob_spawner.script import MobSpawnerScript
        return MobSpawnerScript.objects.filter(db_key=path).first()

    @staticmethod
    def count_mobs(typeclass, area_tag="test_area"):
        from evennia.objects.models import ObjectDB
        return ObjectDB.objects.filter(
            db_typeclass_path=typeclass,
            db_tags__db_key=area_tag,
            db_tags__db_category="mob_area",
        ).count()


class TickLoopTest(EvenniaWorldTestCase):
    """Tick-loop behaviour: observe / cooldown / spawn / death detection.

    Each test calls ``script.at_repeat()`` directly rather than
    waiting for Evennia's timer — gives deterministic control over
    when ticks fire.
    """

    def tearDown(self):
        # Clean up scripts created during the test. Django's TestCase
        # rolls back the DB so rooms/mobs don't need explicit cleanup.
        from evennia_mob_spawner.script import MobSpawnerScript
        for s in MobSpawnerScript.objects.all():
            s.delete()

    DEFAULT_OBJECT = "evennia.objects.objects.DefaultObject"

    def _basic_rule(self, **overrides):
        rule = {
            "rule_id": 1,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,  # spawn immediately on first tick
        }
        rule.update(overrides)
        return rule

    def test_empty_spawn_table_does_nothing(self):
        # Script with no rules at all — tick is a no-op, no mobs spawn.
        from evennia_mob_spawner.script import MobSpawnerScript
        from evennia.utils.create import create_script
        script = create_script(
            typeclass=MobSpawnerScript,
            key="empty.yaml",
            persistent=True,
        )
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 0)

    def test_tick_spawns_when_below_target(self):
        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(self._basic_rule(target=1))
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    def test_tick_skips_when_at_target(self):
        _TickLoopFixture.make_room("Test Room 1")
        _TickLoopFixture.make_room("Test Room 2")
        script = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=2, max_per_room=1),
        )
        # First tick spawns mob 1.
        script.at_repeat()
        # Second tick spawns mob 2 (cooldown=0).
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 2)
        # Third tick should NOT spawn — already at target.
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 2)

    def test_cooldown_blocks_immediate_respawn(self):
        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=2, max_per_room=2, respawn_seconds=3600),
        )
        # First tick spawns mob 1.
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)
        # Second tick should NOT spawn — cooldown hasn't elapsed.
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    def test_death_cooldown_uses_death_time_not_spawn_time(self):
        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=1, death_cooldown_seconds=0)
        )
        # Strip respawn_seconds (the _basic_rule default).
        rule = script.db.spawn_table[0]
        rule.pop("respawn_seconds", None)
        script.db.spawn_table = [rule]

        # First tick spawns. Population = 1, target = 1.
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)
        # Same tick again, no death yet — at target, no spawn.
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)
        # Simulate a death by deleting the mob.
        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(
            db_typeclass_path=self.DEFAULT_OBJECT,
        ).first()
        mob.delete()
        # Next tick should detect the death AND respawn (cooldown=0).
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)
        # last_death_times should have been stamped for the rule.
        self.assertIn(1, script.db.last_death_times)

    def test_no_room_with_area_tag_skips_silently(self):
        # No rooms with the area_tag exist — the rule can't find a room.
        # Should not spawn, should not raise; tick-time WARN logged.
        script = _TickLoopFixture.deploy_rule(self._basic_rule())
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 0)

    def test_max_per_room_respected(self):
        # max_per_room=1, target=3, but only one room — should spawn
        # exactly one mob and skip subsequent attempts.
        _TickLoopFixture.make_room("Only Room")
        script = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=3, max_per_room=1),
        )
        script.at_repeat()
        script.at_repeat()
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    def test_den_room_tag_used_when_present(self):
        # Two rooms; one tagged as the den. Mob should spawn in the den.
        ordinary = _TickLoopFixture.make_room("Ordinary Room")
        den = _TickLoopFixture.make_room("Den Room", extra_tag="test_den")
        script = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=1, den_room_tag="test_den"),
        )
        script.at_repeat()
        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(db_typeclass_path=self.DEFAULT_OBJECT).first()
        self.assertEqual(mob.location, den)
        self.assertNotEqual(mob.location, ordinary)

    def test_attrs_applied_to_spawned_mob(self):
        # DEFAULT_OBJECT (stock DefaultObject) declares neither "hp" nor
        # "is_alpha" as an AttributeProperty, so setattr() below sets
        # plain, non-persisted Python attributes — readable on this same
        # live instance, but never actually written as Attributes. This
        # is the exact shape of the real bug _persists_as_attribute's
        # WARN exists to catch; assert both here rather than silently
        # exercising it.
        from unittest.mock import patch

        captured = []

        def fake_log(message, level="INFO"):
            captured.append((message, level))

        _TickLoopFixture.make_room("Test Room 1")
        with patch("evennia_mob_spawner.script.ms_log", new=fake_log):
            script = _TickLoopFixture.deploy_rule(
                self._basic_rule(attrs={"hp": 42, "is_alpha": True}),
            )
            script.at_repeat()

        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(db_typeclass_path=self.DEFAULT_OBJECT).first()
        # setattr() was applied; readable on the live instance regardless
        # of whether it actually persisted.
        self.assertEqual(getattr(mob, "hp", None), 42)
        self.assertEqual(getattr(mob, "is_alpha", None), True)

        warnings = [m for m, lvl in captured if lvl == "WARN"]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("attrs.hp" in m for m in warnings))
        self.assertTrue(any("attrs.is_alpha" in m for m in warnings))

    def test_desc_override_applied(self):
        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(
            self._basic_rule(desc="A fearsome test mob."),
        )
        script.at_repeat()
        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(db_typeclass_path=self.DEFAULT_OBJECT).first()
        self.assertEqual(mob.db.desc, "A fearsome test mob.")

    def test_area_tag_stamped_on_spawned_mob(self):
        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(self._basic_rule())
        script.at_repeat()
        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(db_typeclass_path=self.DEFAULT_OBJECT).first()
        self.assertTrue(mob.tags.get("test_area", category="mob_area"))

    def test_observed_counts_updated_after_tick(self):
        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(self._basic_rule(target=1))
        script.at_repeat()
        # last_observed_counts should reflect post-spawn population.
        self.assertEqual(script.db.last_observed_counts.get(1), 1)
        self.assertEqual(script.db.spawned_last_tick.get(1), 1)

    def test_last_spawn_time_set_after_spawn(self):
        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(self._basic_rule())
        before = time.time()
        script.at_repeat()
        after = time.time()
        ts = script.db.last_spawn_times.get(1)
        self.assertIsNotNone(ts)
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)

    def test_bad_rule_does_not_break_tick(self):
        # Decision #14: one bad rule doesn't take down the whole tick.
        # Rule 2 has an unresolvable typeclass; Rule 1 is fine.
        _TickLoopFixture.make_room("Test Room 1")
        good = self._basic_rule(rule_id=1, target=1, max_per_room=1)
        bad = {
            "rule_id": 2,
            "typeclass": "does.not.exist.AnyClass",
            "key": "a broken mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }
        from evennia_mob_spawner.deployer import Deployer
        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(rule_sets={"mixed.yaml": [good, bad]}))
        from evennia_mob_spawner.script import MobSpawnerScript
        script = MobSpawnerScript.objects.filter(db_key="mixed.yaml").first()
        # Tick should NOT raise even though rule 2's typeclass is bogus.
        script.at_repeat()
        # The good rule did spawn its mob.
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)


class _PersistsCheckFixture:
    """Plain (non-Evennia) class for exercising ``_persists_as_attribute``.

    A bare descriptor check, not a spawn — no DB, no typeclass, no
    ``create_object``. ``AttributeProperty`` is a plain descriptor and
    works on any class.
    """

    real_attr = AttributeProperty(0)
    plain_attr = "not a descriptor"


class PersistsAsAttributeTest(TestCase):
    """``_persists_as_attribute`` — the setattr-will-it-stick check.

    Guards the WARN in ``_spawn_one``'s attrs loop (decision: warn
    when a rule's ``attrs:`` entry has no matching ``AttributeProperty``
    and would silently vanish with the object).
    """

    def test_declared_attribute_property_returns_true(self):
        from evennia_mob_spawner.script import _persists_as_attribute
        obj = _PersistsCheckFixture()
        self.assertTrue(_persists_as_attribute(obj, "real_attr"))

    def test_plain_class_attribute_returns_false(self):
        from evennia_mob_spawner.script import _persists_as_attribute
        obj = _PersistsCheckFixture()
        self.assertFalse(_persists_as_attribute(obj, "plain_attr"))

    def test_undeclared_name_returns_false(self):
        # Not a descriptor, not even a plain attribute — absent entirely.
        from evennia_mob_spawner.script import _persists_as_attribute
        obj = _PersistsCheckFixture()
        self.assertFalse(_persists_as_attribute(obj, "does_not_exist_at_all"))


class RaceProtocolTest(EvenniaWorldTestCase):
    """Decision #13's race-safe drain: stop_when_safe + force_stop.

    The tick loop checks ``ndb._stop_requested`` between rules and
    sets ``ndb._tick_in_progress`` for the duration of a tick.
    These tests exercise the flag plumbing directly — true
    mid-tick interruption requires concurrency that's awkward to
    test deterministically, so we cover the synchronous side: flag
    set BEFORE the tick blocks the tick; flag clearing on a
    successful drain; force_stop pauses regardless of flag state.
    """

    DEFAULT_OBJECT = "evennia.objects.objects.DefaultObject"

    def tearDown(self):
        from evennia_mob_spawner.script import MobSpawnerScript
        for s in MobSpawnerScript.objects.all():
            s.delete()

    def _rule(self, rule_id=1):
        return {
            "rule_id": rule_id,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }

    def _script_with_rule(self):
        return _TickLoopFixture.deploy_rule(self._rule())

    # at_repeat's flag handling -----------------------------------------

    def test_tick_sets_and_clears_tick_in_progress(self):
        # Sanity: ``_tick_in_progress`` is False before, True during
        # (we can't observe the True easily without concurrency), and
        # False after. The pre/post check is sufficient — if the
        # finally-block didn't fire, the flag would leak across ticks.
        _TickLoopFixture.make_room("Test Room 1")
        script = self._script_with_rule()
        self.assertFalse(bool(script.ndb._tick_in_progress))
        script.at_repeat()
        self.assertFalse(bool(script.ndb._tick_in_progress))

    def test_tick_exits_early_when_stop_requested_at_start(self):
        # Set the stop flag before calling at_repeat. The tick should
        # exit immediately without spawning anything. Deliberately no
        # room created — the stop check fires BEFORE the rule loop
        # would have queried for a room.
        script = self._script_with_rule()
        script.ndb._stop_requested = True
        script.at_repeat()
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 0)
        # No state updates either (we never entered the rule loop).
        self.assertEqual(script.db.last_spawn_times or {}, {})

    def test_tick_exits_early_between_rules_when_stop_requested(self):
        # Two rules in the table. Manually inject a stop request that
        # fires for the second rule. Use a wrapped at_repeat that sets
        # the flag mid-pass by patching _tick_one_rule to set the flag
        # after processing rule 1.
        _TickLoopFixture.make_room("Test Room 1")
        _TickLoopFixture.make_room("Test Room 2")

        rule_1 = self._rule(rule_id=1)
        rule_2 = self._rule(rule_id=2)
        from evennia_mob_spawner.deployer import Deployer
        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(
            rule_sets={"twin.yaml": [rule_1, rule_2]},
        ))

        from evennia_mob_spawner.script import MobSpawnerScript
        script = MobSpawnerScript.objects.filter(db_key="twin.yaml").first()

        # Wrap _tick_one_rule so that processing rule 1 sets the stop
        # flag — rule 2's iteration should then bail at the start-of-
        # iteration check, with no spawn.
        original = script._tick_one_rule

        def patched(rule, *args, **kwargs):
            original(rule, *args, **kwargs)
            if rule["rule_id"] == 1:
                script.ndb._stop_requested = True

        script._tick_one_rule = patched
        try:
            script.at_repeat()
        finally:
            script._tick_one_rule = original

        # Exactly one mob should have spawned (rule 1 only).
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    # stop_when_safe semantics ------------------------------------------

    def test_stop_when_safe_returns_true_for_idle_script(self):
        # No tick in flight, script just sitting there → drains
        # trivially and pauses.
        _TickLoopFixture.make_room("Test Room 1")
        script = self._script_with_rule()
        drained = script.stop_when_safe(timeout=1.0)
        self.assertTrue(drained)
        # Script should now be paused.
        self.assertTrue(bool(script.db._paused_time))

    def test_stop_when_safe_returns_true_for_already_paused_script(self):
        # Already paused → return True without touching state.
        _TickLoopFixture.make_room("Test Room 1")
        script = self._script_with_rule()
        script.pause()
        # _stop_requested should be untouched by a no-op stop_when_safe.
        self.assertFalse(bool(script.ndb._stop_requested))
        drained = script.stop_when_safe(timeout=0.5)
        self.assertTrue(drained)
        self.assertFalse(bool(script.ndb._stop_requested))

    def test_stop_when_safe_clears_stop_flag_on_success(self):
        # After a successful drain the flag is False so the next tick
        # (after unpause) proceeds normally.
        _TickLoopFixture.make_room("Test Room 1")
        script = self._script_with_rule()
        script.stop_when_safe(timeout=1.0)
        self.assertFalse(bool(script.ndb._stop_requested))

    # force_stop semantics ----------------------------------------------

    def test_force_stop_pauses_active_script(self):
        _TickLoopFixture.make_room("Test Room 1")
        script = self._script_with_rule()
        # Sanity: script starts active.
        self.assertTrue(script.is_active)
        self.assertFalse(bool(script.db._paused_time))
        script.force_stop()
        self.assertTrue(bool(script.db._paused_time))

    def test_force_stop_sets_stop_flag(self):
        # force_stop also signals the stop flag — a wedged in-flight
        # tick that DOES eventually reach its between-rules check will
        # exit. Deployer is responsible for clearing the flag after
        # the swap; force_stop itself doesn't.
        _TickLoopFixture.make_room("Test Room 1")
        script = self._script_with_rule()
        script.force_stop()
        self.assertTrue(bool(script.ndb._stop_requested))

    def test_force_stop_idempotent_on_already_paused_script(self):
        # Calling force_stop on a paused script shouldn't unpause it
        # or cause any error.
        _TickLoopFixture.make_room("Test Room 1")
        script = self._script_with_rule()
        script.pause()
        script.force_stop()
        self.assertTrue(bool(script.db._paused_time))

    # Deployer integration ----------------------------------------------

    def test_deployer_resumes_running_script_after_swap(self):
        # Deploy once (script created + running). Deploy again — the
        # script should still be running afterward.
        _TickLoopFixture.make_room("Test Room 1")
        from evennia_mob_spawner.deployer import Deployer
        defs = Definitions.from_dict({"levels": []})
        deployer = Deployer(defs)
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule()]}))
        from evennia_mob_spawner.script import MobSpawnerScript
        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        self.assertTrue(script.is_active)
        self.assertFalse(bool(script.db._paused_time))

        # Re-deploy — should drain via stop_when_safe (which succeeds
        # trivially since no tick is in flight) and unpause at the end.
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule()]}))

        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        self.assertTrue(script.is_active)
        self.assertFalse(bool(script.db._paused_time))
        # Stop flag should have been cleared by the Deployer's finally.
        self.assertFalse(bool(script.ndb._stop_requested))

    def test_deployer_does_not_resume_paused_script(self):
        # If a script was paused before re-deploy, it should remain
        # paused afterward (the user explicitly stopped it; don't
        # second-guess).
        _TickLoopFixture.make_room("Test Room 1")
        from evennia_mob_spawner.deployer import Deployer
        defs = Definitions.from_dict({"levels": []})
        deployer = Deployer(defs)
        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule()]}))
        from evennia_mob_spawner.script import MobSpawnerScript
        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        script.pause()
        self.assertTrue(bool(script.db._paused_time))

        deployer.deploy(LoadResult(rule_sets={"a.yaml": [self._rule()]}))

        script = MobSpawnerScript.objects.filter(db_key="a.yaml").first()
        # Should still be paused.
        self.assertTrue(bool(script.db._paused_time))


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
            # Minimal valid repo — empty levels, empty manifest. A content
            # repo always has a root index.yaml; the Loader's contract is
            # to refuse if it's missing.
            (Path(tmp) / "definitions.yaml").write_text("levels: []\n")
            (Path(tmp) / "index.yaml").write_text("entries: []\n")
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


class SpawnIdentityTagTest(EvenniaWorldTestCase):
    """Every spawned mob carries identity tags for rule + source file.

    Stamped by ``_spawn_one`` alongside the existing ``area_tag``:
      - ``mob_spawner_rule`` category, key = ``str(rule_id)``
      - ``mob_spawner_file`` category, key = ``script.db_key``

    Together they let callers query "every mob from rule X in file Y"
    without relying on (typeclass, area_tag) being a unique discriminator.
    """

    DEFAULT_OBJECT = "evennia.objects.objects.DefaultObject"

    def tearDown(self):
        from evennia_mob_spawner.script import MobSpawnerScript
        for s in MobSpawnerScript.objects.all():
            s.delete()

    def _rule(self, rule_id, **overrides):
        rule = {
            "rule_id": rule_id,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }
        rule.update(overrides)
        return rule

    def _spawned_mob(self, path="identity_test.yaml", rule_id=7):
        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(
            self._rule(rule_id=rule_id), path=path,
        )
        script.at_repeat()
        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(
            db_typeclass_path=self.DEFAULT_OBJECT,
            db_tags__db_key="test_area",
            db_tags__db_category="mob_area",
        ).first()
        self.assertIsNotNone(mob, "fixture did not spawn a mob")
        return mob, script

    def test_spawned_mob_carries_rule_id_tag(self):
        mob, _ = self._spawned_mob(rule_id=7)
        rule_tags = mob.tags.get(
            category="mob_spawner_rule", return_list=True,
        ) or []
        self.assertEqual(rule_tags, ["7"])

    def test_spawned_mob_carries_file_path_tag(self):
        mob, script = self._spawned_mob(path="identity_test.yaml")
        file_tags = mob.tags.get(
            category="mob_spawner_file", return_list=True,
        ) or []
        self.assertEqual(file_tags, [script.db_key])

    def test_spawned_mob_retains_area_tag(self):
        # Identity-tag stamping must not displace area_tag.
        mob, _ = self._spawned_mob()
        area_tags = mob.tags.get(
            category="mob_area", return_list=True,
        ) or []
        self.assertIn("test_area", area_tags)

    def test_two_rules_in_same_file_get_distinct_rule_tags(self):
        # Two mobs from the same file but different rule_ids must end
        # up with different rule-id tag values.
        from evennia_mob_spawner.deployer import Deployer
        from evennia_mob_spawner.script import MobSpawnerScript
        from evennia.objects.models import ObjectDB

        _TickLoopFixture.make_room("Test Room A", area_tag="area_a")
        _TickLoopFixture.make_room("Test Room B", area_tag="area_b")

        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(rule_sets={
            "two_rules.yaml": [
                self._rule(rule_id=1, area_tag="area_a"),
                self._rule(rule_id=2, area_tag="area_b"),
            ],
        }))
        script = MobSpawnerScript.objects.filter(db_key="two_rules.yaml").first()
        script.at_repeat()

        mob_a = ObjectDB.objects.filter(
            db_typeclass_path=self.DEFAULT_OBJECT,
            db_tags__db_key="area_a", db_tags__db_category="mob_area",
        ).first()
        mob_b = ObjectDB.objects.filter(
            db_typeclass_path=self.DEFAULT_OBJECT,
            db_tags__db_key="area_b", db_tags__db_category="mob_area",
        ).first()
        self.assertIsNotNone(mob_a)
        self.assertIsNotNone(mob_b)
        self.assertEqual(
            mob_a.tags.get(category="mob_spawner_rule", return_list=True),
            ["1"],
        )
        self.assertEqual(
            mob_b.tags.get(category="mob_spawner_rule", return_list=True),
            ["2"],
        )


class CountLivingContractTest(EvenniaWorldTestCase):
    """``_count_living`` keys on (file, rule_id), not (typeclass, area_tag).

    The contract change enables shared-typeclass loot variants: two
    rules targeting the same typeclass + area_tag are counted as
    independent populations, because the identity tags stamped at
    spawn time differ between them.
    """

    DEFAULT_OBJECT = "evennia.objects.objects.DefaultObject"

    def tearDown(self):
        from evennia_mob_spawner.script import MobSpawnerScript
        for s in MobSpawnerScript.objects.all():
            s.delete()

    def _rule(self, rule_id, **overrides):
        rule = {
            "rule_id": rule_id,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }
        rule.update(overrides)
        return rule

    def test_two_rules_same_typeclass_and_area_tag_counted_independently(self):
        # Headline contract test: two rules in one file sharing the
        # same typeclass + area_tag, distinct rule_ids. Under the old
        # (typeclass, area_tag) discriminator both rules would see
        # current=2 after two ticks and one would be "stuck" thinking
        # the other satisfied its target. The new keying counts them
        # independently — each rule's _count_living returns just its
        # own rule's mob.
        from evennia_mob_spawner.deployer import Deployer
        from evennia_mob_spawner.script import MobSpawnerScript

        _TickLoopFixture.make_room("Room 1")
        _TickLoopFixture.make_room("Room 2")

        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(rule_sets={
            "shared.yaml": [self._rule(rule_id=1), self._rule(rule_id=2)],
        }))
        script = MobSpawnerScript.objects.filter(db_key="shared.yaml").first()

        # Two ticks → each rule fills its target=1 independently
        # (respawn_seconds=0 means no cooldown between ticks).
        script.at_repeat()
        script.at_repeat()

        rule1 = script.db.spawn_table[0]
        rule2 = script.db.spawn_table[1]
        self.assertEqual(script._count_living(rule1), 1)
        self.assertEqual(script._count_living(rule2), 1)
        # Sanity: two distinct mobs exist in the world. If the old
        # contract were in force, this would have spawned only one and
        # the second rule would have stalled at the population gate.
        self.assertEqual(
            _TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 2,
        )

    def test_rule_id_collision_across_files_isolated(self):
        # Two files each with rule_id=1, same typeclass + area_tag.
        # The file tag prevents cross-file bleed in the count.
        from evennia_mob_spawner.deployer import Deployer
        from evennia_mob_spawner.script import MobSpawnerScript

        _TickLoopFixture.make_room("Room 1")

        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(rule_sets={
            "file_a.yaml": [self._rule(rule_id=1)],
            "file_b.yaml": [self._rule(rule_id=1)],
        }))
        script_a = MobSpawnerScript.objects.filter(db_key="file_a.yaml").first()
        script_b = MobSpawnerScript.objects.filter(db_key="file_b.yaml").first()

        # Only script_a ticks → its rule spawns one mob.
        script_a.at_repeat()

        rule_a = script_a.db.spawn_table[0]
        rule_b = script_b.db.spawn_table[0]
        self.assertEqual(script_a._count_living(rule_a), 1)
        # script_b's count for ITS rule_id=1 must be 0, not 1 — the
        # mob_spawner_file tag prevents script_a's mob from bleeding in.
        self.assertEqual(script_b._count_living(rule_b), 0)

    def test_count_excludes_mobs_lacking_identity_tags(self):
        # Manually create a mob with the same typeclass + area_tag as
        # a deployed rule but WITHOUT the new identity tags. Proves the
        # count is driven by the identity tags, not by a typeclass +
        # area_tag fallback.
        from evennia.utils.create import create_object
        from evennia_mob_spawner.deployer import Deployer
        from evennia_mob_spawner.script import MobSpawnerScript

        room = _TickLoopFixture.make_room("Room 1")

        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(rule_sets={
            "test.yaml": [self._rule(rule_id=1)],
        }))
        script = MobSpawnerScript.objects.filter(db_key="test.yaml").first()
        rule = script.db.spawn_table[0]

        # Create an "interloper" mob bypassing _spawn_one. It carries
        # the same typeclass + area_tag the rule would produce, but
        # lacks the mob_spawner_rule and mob_spawner_file identity tags.
        interloper = create_object(
            self.DEFAULT_OBJECT, key="interloper", location=room,
        )
        interloper.tags.add("test_area", category="mob_area")

        # Sanity: the interloper IS findable by the old contract's
        # discriminator (typeclass + area_tag), so any logic still
        # using that would count it.
        self.assertEqual(
            _TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1,
        )

        # But the new _count_living must return 0 — no rule produced
        # the interloper, so it has no identity tags, so it's invisible
        # to the per-rule count.
        self.assertEqual(script._count_living(rule), 0)


class YamlDeclaredTagsTest(EvenniaWorldTestCase):
    """The `tags:` rule field stamps additional tags on each spawned mob.

    Each entry is a bare string (untyped) or a `{key, category?}` dict.
    Library-stamped tags (area_tag, identity tags) are unaffected.
    """

    DEFAULT_OBJECT = "evennia.objects.objects.DefaultObject"

    def tearDown(self):
        from evennia_mob_spawner.script import MobSpawnerScript
        for s in MobSpawnerScript.objects.all():
            s.delete()

    def _rule(self, **overrides):
        rule = {
            "rule_id": 1,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }
        rule.update(overrides)
        return rule

    def _spawn_mob_with(self, tags):
        from evennia_mob_spawner.script import MobSpawnerScript
        from evennia.objects.models import ObjectDB

        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(self._rule(tags=tags))
        script.at_repeat()
        mob = ObjectDB.objects.filter(
            db_typeclass_path=self.DEFAULT_OBJECT,
            db_tags__db_key="test_area",
            db_tags__db_category="mob_area",
        ).first()
        self.assertIsNotNone(mob, "fixture did not spawn a mob")
        return mob

    def test_bare_string_tag_stamped_untyped(self):
        mob = self._spawn_mob_with(["plain_flag"])
        # An "untyped" Evennia tag has category=None — query under None.
        keys = mob.tags.get(category=None, return_list=True) or []
        self.assertIn("plain_flag", keys)

    def test_dict_with_category_stamped_correctly(self):
        mob = self._spawn_mob_with([
            {"key": "spawn_resources", "category": "spawn_resources"},
        ])
        keys = mob.tags.get(category="spawn_resources", return_list=True) or []
        self.assertEqual(keys, ["spawn_resources"])

    def test_dict_without_category_stamped_untyped(self):
        mob = self._spawn_mob_with([{"key": "key_only"}])
        keys = mob.tags.get(category=None, return_list=True) or []
        self.assertIn("key_only", keys)

    def test_mixed_shapes_all_stamped(self):
        mob = self._spawn_mob_with([
            "bare",
            {"key": "untyped_via_dict"},
            {"key": "spawn_gold", "category": "spawn_gold"},
        ])
        untyped = mob.tags.get(category=None, return_list=True) or []
        self.assertIn("bare", untyped)
        self.assertIn("untyped_via_dict", untyped)
        gold = mob.tags.get(category="spawn_gold", return_list=True) or []
        self.assertEqual(gold, ["spawn_gold"])

    def test_tags_field_absent_no_extra_tags(self):
        # Without a `tags:` field, the mob carries ONLY the library-stamped
        # tags (area_tag + identity tags) — no extras leak in.
        from evennia_mob_spawner.script import MobSpawnerScript
        from evennia.objects.models import ObjectDB

        _TickLoopFixture.make_room("Test Room 1")
        script = _TickLoopFixture.deploy_rule(self._rule())  # no tags key
        script.at_repeat()
        mob = ObjectDB.objects.filter(
            db_typeclass_path=self.DEFAULT_OBJECT,
            db_tags__db_key="test_area",
            db_tags__db_category="mob_area",
        ).first()
        self.assertIsNotNone(mob)

        # Library-stamped tags are present.
        self.assertIn(
            "test_area",
            mob.tags.get(category="mob_area", return_list=True) or [],
        )
        self.assertIn(
            "1",
            mob.tags.get(category="mob_spawner_rule", return_list=True) or [],
        )
        # No untyped tags (library never stamps any).
        untyped = mob.tags.get(category=None, return_list=True) or []
        self.assertEqual(untyped, [])


class RoomHasSpaceContractTest(EvenniaWorldTestCase):
    """``_room_has_space`` keys on (file, rule_id), not (typeclass, area_tag).

    The contract change makes the per-room ``max_per_room`` cap truly
    per-rule. Two rules sharing typeclass + area_tag enforce their caps
    independently — a room can hold one mob from each rule rather than
    one mob TOTAL across both. Completes the contract migration started
    with ``_count_living``.
    """

    DEFAULT_OBJECT = "evennia.objects.objects.DefaultObject"

    def tearDown(self):
        from evennia_mob_spawner.script import MobSpawnerScript
        for s in MobSpawnerScript.objects.all():
            s.delete()

    def _rule(self, rule_id, **overrides):
        rule = {
            "rule_id": rule_id,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }
        rule.update(overrides)
        return rule

    def test_two_rules_share_typeclass_and_area_tag_but_max_per_room_is_independent(self):
        # Two rules in one file sharing typeclass + area_tag, each with
        # max_per_room=1. One shared room. Under the old (typeclass,
        # area_tag) discriminator the second rule would see the first
        # rule's mob and skip ("room full"). Under the new (file,
        # rule_id) discriminator each rule's cap is enforced against
        # only its own mobs, so both spawn into the same room.
        from evennia_mob_spawner.deployer import Deployer
        from evennia_mob_spawner.script import MobSpawnerScript

        _TickLoopFixture.make_room("Shared Room")

        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(rule_sets={
            "shared.yaml": [self._rule(rule_id=1), self._rule(rule_id=2)],
        }))
        script = MobSpawnerScript.objects.filter(db_key="shared.yaml").first()

        script.at_repeat()
        script.at_repeat()

        rule1 = script.db.spawn_table[0]
        rule2 = script.db.spawn_table[1]
        # Each rule should have hit its target (1 mob).
        self.assertEqual(script._count_living(rule1), 1)
        self.assertEqual(script._count_living(rule2), 1)
        # Two mobs total in the room — confirms the per-room cap
        # was enforced per-rule, not as a shared pool.
        self.assertEqual(
            _TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 2,
        )

    def test_max_per_room_still_blocks_within_a_single_rule(self):
        # Defensive: the change is about *which* mobs count, not about
        # removing the cap. One rule, target=3, max_per_room=1, one
        # room — only one mob should ever exist there.
        from evennia_mob_spawner.script import MobSpawnerScript

        _TickLoopFixture.make_room("Only Room")
        script = _TickLoopFixture.deploy_rule(
            self._rule(rule_id=1, target=3, max_per_room=1),
        )

        script.at_repeat()
        script.at_repeat()
        script.at_repeat()

        self.assertEqual(
            _TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1,
        )

