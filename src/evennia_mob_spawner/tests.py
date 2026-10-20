# SPDX-License-Identifier: BSD-3-Clause
"""Smoke tests for evennia-mob-spawner.

Proves the package installs, the test runner discovers tests correctly, and
the evennia-yaml-reader dependency is wired up. Real tests land alongside
the spawn-rule pipeline as it is built out.
"""

import os
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
    is_router_process,
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
        self.assertEqual(evennia_mob_spawner.__version__, "0.1.2")

    def test_area_tag_category_accessor_exported(self):
        from evennia_mob_spawner import config

        self.assertIn("get_area_tag_category", evennia_mob_spawner.__all__)
        self.assertIs(
            evennia_mob_spawner.get_area_tag_category, config.get_area_tag_category
        )

    def test_runtime_entry_points_exported(self):
        """PKG-04"""
        from evennia_mob_spawner import conditions, runtime

        for name in ("register_condition", "start_mob_spawner"):
            self.assertIn(name, evennia_mob_spawner.__all__)
        self.assertIs(
            evennia_mob_spawner.register_condition, conditions.register_condition
        )
        self.assertIs(evennia_mob_spawner.start_mob_spawner, runtime.start_mob_spawner)

    def test_spawn_rule_lookup_exported(self):
        """PKG-05"""
        from evennia_mob_spawner import runtime

        self.assertIn("spawn_rule_for", evennia_mob_spawner.__all__)
        self.assertIs(evennia_mob_spawner.spawn_rule_for, runtime.spawn_rule_for)

    def test_spawn_identity_exported(self):
        """PKG-06"""
        from evennia_mob_spawner import runtime

        self.assertIn("spawn_identity", evennia_mob_spawner.__all__)
        self.assertIs(getattr(evennia_mob_spawner, "spawn_identity", None), runtime.spawn_identity)


MOB_SPAWNER_LOG = "mob_spawner.log"


def clear_logs():
    """Empty LOG_DIR's files, so a line read back was written by this test.

    Truncated, never removed: Evennia's ``_open_log_file`` caches the handle
    after the first write, and removing the file leaves that handle appending
    to an unlinked inode — every later line silently vanishes. An append-mode
    handle seeks to the end on each write, so a truncated file stays live.
    """
    for name in os.listdir(settings.LOG_DIR):
        if name.endswith(".log"):
            with open(os.path.join(settings.LOG_DIR, name), "w"):
                pass


def read_back_log(filename):
    """Return one log file's contents from LOG_DIR, or "" if it never appeared."""
    path = os.path.join(settings.LOG_DIR, filename)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class LogShimSmokeTest(TestCase):
    """The binding reaches this library's own log file.

    Delivery is read back from disk rather than asserted through a mock. A
    call that raises nothing proves nothing about whether a line landed —
    which is how a dead log call survived a full suite in evennia-calendar.

    The mechanism itself belongs to evennia-logging-extension and is covered
    by that library's suite; what these pin is the binding.
    """

    def test_ms_log_writes_to_the_library_log_file(self):
        """LOG-01"""
        clear_logs()
        ms_log("smoke test: delivery")
        self.assertIn("smoke test: delivery", read_back_log(MOB_SPAWNER_LOG))

    def test_ms_log_carries_the_level_into_the_file(self):
        """LOG-03"""
        clear_logs()
        ms_log("smoke test: level path", level="WARN")
        logged = read_back_log(MOB_SPAWNER_LOG)
        self.assertIn("[WARN]", logged)
        self.assertIn("smoke test: level path", logged)


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


def _fake_module(name: str, **attrs):
    """A stand-in module exposing only the names detection imports."""
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _scaled_as(role: str, instance_id: str):
    """Stand this process up as a scaling instance, for the duration.

    The library's own test venv deliberately installs neither sibling, so
    the co-installed paths are exercised by injection. Both packages and
    their ``config`` submodules are supplied: ``from a.b import c`` resolves
    the parent as well as the module itself.

    The two halves come from different libraries by design — scaling mints
    no instance id of its own and reads multiplex's, so a second setting
    for the same fact would only give the two somewhere to disagree.
    """
    return mock.patch.dict(sys.modules, {
        "evennia_scaling": _fake_module("evennia_scaling"),
        "evennia_scaling.config": _fake_module(
            "evennia_scaling.config",
            ROLE_ROUTER="router",
            ROLE_SHARD="shard",
            get_role=lambda: role,
        ),
        "evennia_portal_multiplex": _fake_module("evennia_portal_multiplex"),
        "evennia_portal_multiplex.config": _fake_module(
            "evennia_portal_multiplex.config",
            get_instance_id=lambda: instance_id,
        ),
    })


def _not_scaled():
    """The standalone case: a None entry makes the import raise."""
    return mock.patch.dict(sys.modules, {
        "evennia_scaling": None,
        "evennia_scaling.config": None,
    })


class ActiveShardIdTest(TestCase):
    """Detection: this instance's id, or None when scaling is absent."""

    def test_scaling_not_installed_is_standalone(self):
        with _not_scaled():
            self.assertIsNone(active_shard_id())

    def test_shard_instance_reports_its_instance_id(self):
        with _scaled_as(role="shard", instance_id="shard0"):
            self.assertEqual(active_shard_id(), "shard0")

    def test_router_instance_reports_its_instance_id(self):
        # The router has an id like any other instance. Nothing here
        # refuses it — that is is_router_process's job, because a router
        # id is an arbitrary name and may match a rule set's.
        with _scaled_as(role="router", instance_id="hub"):
            self.assertEqual(active_shard_id(), "hub")


class IsRouterProcessTest(TestCase):
    """The role check that keeps rule sets off the router."""

    def test_scaling_not_installed_is_not_router(self):
        # A standalone game has no router to be.
        with _not_scaled():
            self.assertFalse(is_router_process())

    def test_shard_instance_is_not_router(self):
        with _scaled_as(role="shard", instance_id="shard0"):
            self.assertFalse(is_router_process())

    def test_router_instance_is_router(self):
        with _scaled_as(role="router", instance_id="hub"):
            self.assertTrue(is_router_process())


class CheckShardScopeTest(TestCase):
    """The ms_load gate. Every refusal is a no-op off a scaled deployment."""

    @staticmethod
    def _as(instance_id, router=False):
        """Stand the *gate* up as `instance_id`, optionally as the router.

        Patched at the gate rather than through `sys.modules`, so a failure
        here is the gate's logic rather than the detection underneath it —
        that is `ActiveShardIdTest`'s and `IsRouterProcessTest`'s to cover.
        """
        return mock.patch.multiple(
            "evennia_mob_spawner.commands",
            active_shard_id=mock.Mock(return_value=instance_id),
            is_router_process=mock.Mock(return_value=router),
        )

    # --- not a scaled deployment: nothing is refused ------------------

    def test_standalone_allows_all_scope(self):
        with self._as(None):
            self.assertIsNone(check_shard_scope({}))

    def test_standalone_allows_any_query(self):
        # Including one naming a shard level, which is just an ordinary
        # consumer-chosen level name when scaling isn't in play.
        with self._as(None):
            self.assertIsNone(check_shard_scope({SHARD_LEVEL: "shard9"}))
            self.assertIsNone(check_shard_scope({"zone": "millholm"}))

    # --- scaled: the refusals -----------------------------------------

    def test_all_scope_refused_when_scaled(self):
        # `all` spans the whole YAML manifest, which holds every instance's
        # rule sets — so it would deploy another instance's rules here.
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
        # The router holds no game world, so no rule set deploys there.
        with self._as("hub", router=True):
            self.assertIsNotNone(check_shard_scope({SHARD_LEVEL: "shard0"}))

    def test_router_refused_for_its_own_instance_id(self):
        # Refused by role, not by an id that never matches. Under scaling
        # SCALING_ROUTER_ID is an arbitrary name, so a rule-set folder can
        # be named after it — and an id check alone would let it through,
        # deploying scripts into the instance that holds no world.
        with self._as("hub", router=True):
            self.assertIsNotNone(check_shard_scope({SHARD_LEVEL: "hub"}))

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

    def test_standalone_accepts_any_levels(self):
        # Level names are consumer-chosen off a scaled deployment.
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
    "pack": "test_pack",
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


class RuleFieldPredicateTest(TestCase):
    """The fields a rule may carry are the values of ``RuleField``."""

    def test_an_unknown_key_is_refused(self):
        """SHAPE-79"""
        from evennia_mob_spawner.validator import _check_no_unknown_fields

        finding = _check_no_unknown_fields(
            LoadedRule("p", dict(_VALID_RULE, pakc="wolves")),
        )
        self.assertIn("'pakc'", finding)

    def test_every_unknown_key_is_named(self):
        """SHAPE-80"""
        from evennia_mob_spawner.validator import _check_no_unknown_fields

        finding = _check_no_unknown_fields(
            LoadedRule("p", dict(_VALID_RULE, pakc="wolves", den_room_tag="lair")),
        )
        self.assertIn("'pakc'", finding)
        self.assertIn("'den_room_tag'", finding)

    def test_rules_using_every_field_pass(self):
        """SHAPE-81"""
        # Written out by hand rather than built from RuleField, so a field
        # the predicates check but the enum lacks is caught here. Two rules,
        # because the two cooldowns are mutually exclusive.
        shared = {
            "typeclass": "test.Foo",
            "key": "a thing",
            "area_tag": "test_area",
            "target": 3,
            "max_per_room": 2,
            "desc": "hello",
            "attrs": {"hp": 10},
            "pack": "test_pack",
            "condition": "night_only",
            "tags": ["plain", {"key": "spawn_gold", "category": "spawn_gold"}],
        }
        rules = [
            dict(shared, rule_id=1, respawn_seconds=30),
            dict(shared, rule_id=2, death_cooldown_seconds=600),
        ]
        validator = Validator(Definitions.from_dict({"levels": []}))
        validator.validate(LoadResult(rule_sets={"a.yaml": rules}))
        self.assertEqual(validator.errors, [])


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
                if k not in ("desc", "pack", "condition")}
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

    # pack

    def test_pack_absent_passes(self):
        """SHAPE-73"""
        from evennia_mob_spawner.validator import _check_pack_well_formed
        self.assertIsNone(_check_pack_well_formed(LoadedRule("p", self._rule())))

    def test_pack_wrong_type(self):
        """SHAPE-74"""
        from evennia_mob_spawner.validator import _check_pack_well_formed
        finding = _check_pack_well_formed(LoadedRule("p", self._rule(pack=[])))
        self.assertIn("'pack' must be a string", finding)

    def test_pack_empty_rejected(self):
        """SHAPE-75"""
        from evennia_mob_spawner.validator import _check_pack_well_formed
        finding = _check_pack_well_formed(LoadedRule("p", self._rule(pack="   ")))
        self.assertIn("'pack' must be a non-empty string", finding)

    # condition

    def test_condition_absent_passes(self):
        """SHAPE-76"""
        from evennia_mob_spawner.validator import _check_condition_well_formed
        self.assertIsNone(
            _check_condition_well_formed(LoadedRule("p", self._rule()))
        )

    def test_condition_wrong_type(self):
        """SHAPE-77"""
        from evennia_mob_spawner.validator import _check_condition_well_formed
        finding = _check_condition_well_formed(
            LoadedRule("p", self._rule(condition=7)),
        )
        self.assertIn("'condition' must be a string", finding)

    def test_condition_empty_rejected(self):
        """SHAPE-78"""
        from evennia_mob_spawner.validator import _check_condition_well_formed
        finding = _check_condition_well_formed(
            LoadedRule("p", self._rule(condition="")),
        )
        self.assertIn("'condition' must be a non-empty string", finding)


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
    (CLI) leaves it off. Three checks:
    - `typeclass` resolves to a class
    - the resolved `typeclass`'s optional `ms_at_post_spawn` is callable
    - `condition` (when present) names a registered condition
    """

    def _make_validator(self):
        return Validator(
            Definitions.from_dict({"levels": []}),
            evennia_runtime=True,
        )

    def _rule(self, **overrides):
        # Strip the fields Tier 3 resolves from _VALID_RULE; each test fills
        # in what it wants.
        base = {k: v for k, v in _VALID_RULE.items()
                if k not in ("typeclass", "condition")}
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

    # condition --------------------------------------------------------

    def test_registered_condition_passes(self):
        """RESLV-18"""
        v = self._make_validator()
        with mock.patch.dict(
            "evennia_mob_spawner.conditions._conditions",
            {"night_only": lambda rule: True},
        ):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(typeclass=_FAKE_TC, condition="night_only"),
            ]}))
        self.assertEqual(v.errors, [])

    def test_unregistered_condition_flagged(self):
        """RESLV-19"""
        v = self._make_validator()
        from evennia_mob_spawner.errors import ValidatorError
        with mock.patch.dict(
            "evennia_mob_spawner.conditions._conditions", {}, clear=True,
        ):
            with self.assertRaises(ValidatorError):
                v.validate(LoadResult(rule_sets={"a.yaml": [
                    self._rule(typeclass=_FAKE_TC, condition="night_only"),
                ]}))
        self.assertIn("'condition'", v.errors[0])
        self.assertIn("night_only", v.errors[0])

    def test_condition_absent_passes(self):
        """RESLV-20"""
        v = self._make_validator()
        with mock.patch.dict(
            "evennia_mob_spawner.conditions._conditions", {}, clear=True,
        ):
            v.validate(LoadResult(rule_sets={"a.yaml": [
                self._rule(typeclass=_FAKE_TC),
            ]}))
        self.assertEqual(v.errors, [])

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
                if k not in ("typeclass", "condition")}
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


DEFAULT_OBJECT = "evennia.objects.objects.DefaultObject"


class _FakeDate:
    """A calendar date carrying only ``elapsed_real_seconds``.

    The tick reads nothing else from the date, so this drives it second by
    second without a running clock.
    """

    def __init__(self, second):
        self.elapsed_real_seconds = float(second)


def _sessioned(room, key="a player"):
    """An object in ``room`` reporting one session — a connected player."""
    from evennia import DefaultCharacter
    from evennia.utils.create import create_object

    obj = create_object(DefaultCharacter, key=key, location=room)
    obj.sessions.count = lambda: 1
    return obj


class SpawnerTestCase(EvenniaWorldTestCase):
    """Base for tests that drive the running spawner.

    The in-memory table is module state, so it outlives each test's database
    rollback. Reloading it from the clean database at the start of every test
    stops one test's rules leaking into the next.
    """

    def setUp(self):
        super().setUp()
        from evennia_mob_spawner import runtime

        runtime.load_rules()


class SpawnRuleModelTest(EvenniaWorldTestCase):
    """The rule table — where rules and their timers are stored."""

    def test_the_table_is_in_the_default_database(self):
        """MODEL-01"""
        from django.db import connections, router
        from evennia_mob_spawner.models import SpawnRule

        self.assertEqual(router.db_for_write(SpawnRule), "default")
        self.assertIn(
            SpawnRule._meta.db_table,
            connections["default"].introspection.table_names(),
        )

    def test_file_and_rule_id_are_unique_together(self):
        """MODEL-02"""
        from django.db import IntegrityError, transaction
        from evennia_mob_spawner.models import SpawnRule

        SpawnRule.objects.create(file="a.yaml", rule_id=1, rule={})
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SpawnRule.objects.create(file="a.yaml", rule_id=1, rule={})
        # The same rule_id in another file is a different rule.
        SpawnRule.objects.create(file="b.yaml", rule_id=1, rule={})


class DeployerTest(SpawnerTestCase):
    """``ms_load``'s upsert: one row per rule, keyed on ``(file, rule_id)``.

    The Deployer writes the rows. The in-memory table the tick reads is
    swapped from them on the reactor once the load has completed.
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
            "typeclass": DEFAULT_OBJECT,
            "key": "a thing",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 30,
        }
        base.update(overrides)
        return base

    def _deploy(self, rule_sets):
        self._make_deployer().deploy(LoadResult(rule_sets=rule_sets))

    @staticmethod
    def _rows(path="a.yaml"):
        from evennia_mob_spawner.models import SpawnRule
        return {row.rule_id: row for row in SpawnRule.objects.filter(file=path)}

    @staticmethod
    def _set_timers(path, rule_id, spawn, death):
        from evennia_mob_spawner.models import SpawnRule
        SpawnRule.objects.filter(file=path, rule_id=rule_id).update(
            last_spawn_time=spawn, last_death_time=death,
        )

    def test_state_preserved_for_surviving_rules(self):
        """DEP-05"""
        self._deploy({"a.yaml": [self._rule(1), self._rule(2)]})
        self._set_timers("a.yaml", 1, 1000.0, 1500.0)
        self._set_timers("a.yaml", 2, 2000.0, 2500.0)

        # Re-deploy with both rules unchanged.
        self._deploy({"a.yaml": [self._rule(1), self._rule(2)]})

        rows = self._rows()
        self.assertEqual(
            (rows[1].last_spawn_time, rows[1].last_death_time), (1000.0, 1500.0),
        )
        self.assertEqual(
            (rows[2].last_spawn_time, rows[2].last_death_time), (2000.0, 2500.0),
        )

    def test_state_purged_for_removed_rules(self):
        """DEP-06"""
        self._deploy({"a.yaml": [self._rule(1), self._rule(2)]})

        # Re-deploy with rule 1 removed.
        self._deploy({"a.yaml": [self._rule(2)]})

        self.assertEqual(set(self._rows()), {2})

    def test_new_rule_starts_with_no_bookkeeping(self):
        """DEP-07"""
        self._deploy({"a.yaml": [self._rule(1)]})
        self._set_timers("a.yaml", 1, 1000.0, 1500.0)

        self._deploy({"a.yaml": [self._rule(1), self._rule(3)]})

        rows = self._rows()
        self.assertEqual(rows[1].last_spawn_time, 1000.0)
        self.assertIsNone(rows[3].last_spawn_time)
        self.assertIsNone(rows[3].last_death_time)

    def test_a_first_load_writes_a_row_per_rule(self):
        """DEP-09"""
        rule = self._rule(1, key="a wolf")
        self._deploy({"a.yaml": [rule]})

        rows = self._rows()
        self.assertEqual(set(rows), {1})
        self.assertEqual(rows[1].rule, rule)

    def test_an_edited_rule_keeps_its_row(self):
        """DEP-10"""
        self._deploy({"a.yaml": [self._rule(1, key="a wolf")]})
        pk = self._rows()[1].pk

        self._deploy({"a.yaml": [self._rule(1, key="a grey wolf")]})

        row = self._rows()[1]
        self.assertEqual(row.pk, pk, "an edited rule moved to a new row")
        self.assertEqual(row.rule["key"], "a grey wolf")

    def test_rows_outside_the_scope_are_untouched(self):
        """DEP-11"""
        self._deploy({
            "a.yaml": [self._rule(1)],
            "b.yaml": [self._rule(1), self._rule(2)],
        })
        before = {rid: (row.pk, row.rule) for rid, row in self._rows("b.yaml").items()}

        self._deploy({"a.yaml": [self._rule(3)]})

        after = {rid: (row.pk, row.rule) for rid, row in self._rows("b.yaml").items()}
        self.assertEqual(after, before)
        self.assertEqual(set(self._rows("a.yaml")), {3})

    def test_the_next_tick_runs_newly_loaded_rules(self):
        """DEP-12"""
        from evennia_mob_spawner import runtime
        from evennia_mob_spawner.commands import CmdMsLoad

        _TickLoopFixture.make_room("Test Room 1")
        self._deploy({"a.yaml": [self._rule(1)]})

        # What ms_load does on the reactor once its worker has deployed.
        cmd = CmdMsLoad()
        cmd.caller = mock.Mock()
        cmd._on_async_return((["ms_load: deployment complete"], True))

        with override_settings(MOB_SPAWNER_TICK_SECONDS=1):
            runtime.tick(current=_FakeDate(0))
        self.assertEqual(_TickLoopFixture.count_mobs(DEFAULT_OBJECT), 1)

    def test_a_refused_load_changes_nothing(self):
        """DEP-13"""
        from evennia_mob_spawner import runtime
        from evennia_mob_spawner.commands import CmdMsLoad
        from evennia_mob_spawner.models import SpawnRule

        # A rule already deployed and loaded, from another file.
        state = _TickLoopFixture.deploy_rule(self._rule(1), path="existing.yaml")
        state.last_observed = 5
        before = [
            (s.pk, s.file, s.rule, s.last_observed) for s in runtime.rule_states()
        ]

        reader = FixtureReader({
            "definitions.yaml": {"levels": []},
            "index.yaml": {"entries": [{"name": "bad", "kind": "file"}]},
            # rule_id alone: every other required field is missing.
            "bad.yaml": {"rules": [{"rule_id": 1}]},
        })
        cmd = CmdMsLoad()
        cmd.caller = mock.Mock()
        with mock.patch(
            "evennia_mob_spawner.commands.get_configured_reader",
            return_value=reader,
        ):
            result = cmd._run_pipeline({}, set())
        cmd._on_async_return(result)

        self.assertFalse(SpawnRule.objects.filter(file="bad.yaml").exists())
        after = [
            (s.pk, s.file, s.rule, s.last_observed) for s in runtime.rule_states()
        ]
        self.assertEqual(after, before)


class _TickLoopFixture:
    """Builds tagged rooms, loads rules, and runs them.

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
    def load_rules(rules, path="test.yaml"):
        """Deploy ``rules`` as one file, load them, return their states in order."""
        from evennia_mob_spawner import runtime
        from evennia_mob_spawner.deployer import Deployer
        defs = Definitions.from_dict({"levels": []})
        Deployer(defs).deploy(LoadResult(rule_sets={path: list(rules)}))
        runtime.load_rules()
        by_id = {
            s.rule["rule_id"]: s for s in runtime.rule_states() if s.file == path
        }
        return [by_id[rule["rule_id"]] for rule in rules]

    @staticmethod
    def deploy_rule(rule, path="test.yaml"):
        """Deploy and load one rule; return its state."""
        return _TickLoopFixture.load_rules([rule], path=path)[0]

    @staticmethod
    def run(state):
        """One pass over ``state``'s rule, now."""
        from evennia_mob_spawner import runtime
        runtime.run_rule(state, time.time())

    @staticmethod
    def tick(second, n=1):
        """One calendar second, with ``N`` slots."""
        from evennia_mob_spawner import runtime
        with override_settings(MOB_SPAWNER_TICK_SECONDS=n):
            runtime.tick(current=_FakeDate(second))

    @staticmethod
    def count_mobs(typeclass, area_tag="test_area"):
        from evennia.objects.models import ObjectDB
        return ObjectDB.objects.filter(
            db_typeclass_path=typeclass,
            db_tags__db_key=area_tag,
            db_tags__db_category="mob_area",
        ).count()


class BootTest(SpawnerTestCase):
    """``start_mob_spawner`` — what the game's ``at_server_start()`` calls."""

    def tearDown(self):
        from evennia_calendar.service import unregister_signal
        from evennia_mob_spawner.config import _TICK_SIGNAL_NAME

        unregister_signal(_TICK_SIGNAL_NAME)
        super().tearDown()

    @staticmethod
    def _row(rule_id, **fields):
        from evennia_mob_spawner.models import SpawnRule
        rule = {
            "rule_id": rule_id,
            "typeclass": DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "death_cooldown_seconds": 3600,
        }
        return SpawnRule.objects.create(
            file="boot.yaml", rule_id=rule_id, rule=rule, **fields,
        )

    def _assert_registered(self):
        # The calendar refuses a name already in use, so a refusal is the
        # proof the tick holds it.
        from django.dispatch import Signal
        from evennia_calendar.service import register_signal
        from evennia_mob_spawner.config import _TICK_SIGNAL_NAME

        with self.assertRaises(ValueError):
            register_signal(Signal(), key=lambda date: 0, name=_TICK_SIGNAL_NAME)

    def test_loads_every_row(self):
        """BOOT-01"""
        from evennia_mob_spawner import runtime

        rows = [self._row(1), self._row(2)]
        runtime.start_mob_spawner()
        self.assertEqual(
            [s.pk for s in runtime.rule_states()], [row.pk for row in rows],
        )

    def test_registers_the_tick_with_the_calendar(self):
        """BOOT-02"""
        from evennia_mob_spawner import runtime

        runtime.start_mob_spawner()
        self._assert_registered()

    def test_a_second_call_is_harmless(self):
        """BOOT-03"""
        from evennia_mob_spawner import runtime

        runtime.start_mob_spawner()
        runtime.start_mob_spawner()
        self._assert_registered()

    def test_timers_come_back_from_the_rows(self):
        """BOOT-04"""
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Boss Room")
        # Killed ten seconds before the restart, an hour's cooldown.
        self._row(1, last_death_time=time.time() - 10)

        runtime.start_mob_spawner()
        _TickLoopFixture.tick(0)

        self.assertEqual(
            _TickLoopFixture.count_mobs(DEFAULT_OBJECT), 0,
            "a boss killed before the restart respawned straight after it",
        )


class TickLoopTest(SpawnerTestCase):
    """The tick: slot selection, the enabled flag, and the per-rule pass.

    Tests either run one rule's pass directly (``_TickLoopFixture.run``) or
    drive the calendar receiver second by second (``_TickLoopFixture.tick``).
    """

    DEFAULT_OBJECT = DEFAULT_OBJECT

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

    def _death_cooldown_rule(self, seconds, **overrides):
        rule = self._basic_rule(death_cooldown_seconds=seconds, **overrides)
        rule.pop("respawn_seconds")
        return rule

    @staticmethod
    def _kill_one():
        from evennia.objects.models import ObjectDB
        ObjectDB.objects.filter(db_typeclass_path=DEFAULT_OBJECT).first().delete()

    def test_empty_spawn_table_does_nothing(self):
        """TICK-01"""
        _TickLoopFixture.make_room("Test Room 1")
        _TickLoopFixture.tick(0)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 0)

    def test_tick_spawns_when_below_target(self):
        """TICK-02"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule(target=1))
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    def test_tick_skips_when_at_target(self):
        """TICK-03"""
        _TickLoopFixture.make_room("Test Room 1")
        _TickLoopFixture.make_room("Test Room 2")
        state = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=2, max_per_room=1),
        )
        _TickLoopFixture.run(state)
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 2)
        # A third pass should NOT spawn — already at target.
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 2)

    def test_cooldown_blocks_immediate_respawn(self):
        """TICK-04"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=2, max_per_room=2, respawn_seconds=3600),
        )
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)
        # A second pass should NOT spawn — cooldown hasn't elapsed.
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    def test_death_cooldown_uses_death_time_not_spawn_time(self):
        """TICK-05"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._death_cooldown_rule(0))

        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)
        # Again, no death yet — at target, no spawn.
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

        self._kill_one()
        # The next pass detects the death AND respawns (cooldown 0).
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)
        self.assertIsNotNone(state.last_death_time)

    def test_no_room_with_area_tag_skips_silently(self):
        """TICK-06"""
        state = _TickLoopFixture.deploy_rule(self._basic_rule())
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 0)

    def test_max_per_room_respected(self):
        """TICK-07"""
        # max_per_room=1, target=3, but only one room — should spawn
        # exactly one mob and skip subsequent attempts.
        _TickLoopFixture.make_room("Only Room")
        state = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=3, max_per_room=1),
        )
        _TickLoopFixture.run(state)
        _TickLoopFixture.run(state)
        _TickLoopFixture.run(state)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

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
        with patch("evennia_mob_spawner.runtime.ms_log", new=fake_log):
            state = _TickLoopFixture.deploy_rule(
                self._basic_rule(attrs={"hp": 42, "is_alpha": True}),
            )
            _TickLoopFixture.run(state)

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
        """TICK-10"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(
            self._basic_rule(desc="A fearsome test mob."),
        )
        _TickLoopFixture.run(state)
        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(db_typeclass_path=self.DEFAULT_OBJECT).first()
        self.assertEqual(mob.db.desc, "A fearsome test mob.")

    def test_area_tag_stamped_on_spawned_mob(self):
        """TICK-11"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule())
        _TickLoopFixture.run(state)
        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(db_typeclass_path=self.DEFAULT_OBJECT).first()
        self.assertTrue(mob.tags.get("test_area", category="mob_area"))

    def test_observed_counts_updated_after_tick(self):
        """TICK-12"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule(target=1))
        _TickLoopFixture.run(state)
        # The two are stored disjoint: last_observed holds the headcount as
        # seen in step 1 (0, the world before this pass acted), and
        # spawned_last holds what the pass then added. Step 2 sums them on
        # the next pass.
        self.assertEqual(state.last_observed, 0)
        self.assertEqual(state.spawned_last, 1)

    def test_last_spawn_time_set_after_spawn(self):
        """TICK-13"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule())
        before = time.time()
        _TickLoopFixture.run(state)
        after = time.time()
        self.assertIsNotNone(state.last_spawn_time)
        self.assertGreaterEqual(state.last_spawn_time, before)
        self.assertLessEqual(state.last_spawn_time, after)

    def test_own_spawn_is_not_counted_as_a_death(self):
        """TICK-15"""
        # The `spawned_last` term of the death formula exists so the pass's
        # own spawn — which inflates the population between one observation
        # and the next — is not read as a kill on the following pass. A
        # false death stamps last_death_time and, for a death_cooldown rule,
        # restarts the clock on every pass.
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=2, max_per_room=2),
        )
        _TickLoopFixture.run(state)             # spawns one
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

        _TickLoopFixture.run(state)             # nothing died in between
        self.assertIsNone(
            state.last_death_time, "the pass read its own spawn as a death",
        )

    def test_first_tick_against_pre_existing_mobs_detects_no_death(self):
        """TICK-16"""
        # Decision #11: a freshly loaded rule starts with last_observed = 0,
        # so deaths comes out non-positive when mobs are already in the
        # world. No special-case branch.
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule(target=1))
        runtime.spawn_one(state, room)          # pre-existing, tagged mob

        _TickLoopFixture.run(state)             # the rule's first pass
        self.assertIsNone(
            state.last_death_time,
            "a pre-existing population was read as a wave of deaths",
        )

    def test_bad_rule_does_not_break_tick(self):
        """TICK-14"""
        # Decision #14: one bad rule doesn't take down the whole tick.
        # The bad rule is first in the file, so it runs first.
        _TickLoopFixture.make_room("Test Room 1")
        bad = self._basic_rule(rule_id=1, typeclass="does.not.exist.AnyClass")
        good = self._basic_rule(rule_id=2)
        _TickLoopFixture.load_rules([bad, good], path="mixed.yaml")

        _TickLoopFixture.tick(0)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    # -- the calendar receiver ---------------------------------------

    def test_a_disabled_spawner_does_nothing(self):
        """TICK-17"""
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Test Room 1")
        _TickLoopFixture.deploy_rule(self._basic_rule())
        runtime.set_enabled(False)

        _TickLoopFixture.tick(0)
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 0)

    def test_only_the_seconds_slot_runs(self):
        """TICK-18"""
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Test Room 1")
        states = _TickLoopFixture.load_rules(
            [self._basic_rule(rule_id=i) for i in (1, 2, 3)],
        )

        _TickLoopFixture.tick(states[0].pk, n=3)

        self.assertEqual(
            [runtime.count_living(s) for s in states], [1, 0, 0],
        )

    def test_every_rule_runs_once_per_cycle(self):
        """TICK-19"""
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Test Room 1")
        # A target of 5 would let a rule that ran twice show it.
        states = _TickLoopFixture.load_rules([
            self._basic_rule(rule_id=i, target=5, max_per_room=5)
            for i in (1, 2, 3)
        ])

        for second in (100, 101, 102):
            _TickLoopFixture.tick(second, n=3)

        self.assertEqual(
            [runtime.count_living(s) for s in states], [1, 1, 1],
        )

    # -- what reaches the row ----------------------------------------

    @staticmethod
    def _row(state):
        from evennia_mob_spawner.models import SpawnRule
        return SpawnRule.objects.get(pk=state.pk)

    def test_a_spawn_time_reaches_the_row(self):
        """TICK-20"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule())
        _TickLoopFixture.run(state)

        self.assertIsNotNone(state.last_spawn_time)
        self.assertEqual(self._row(state).last_spawn_time, state.last_spawn_time)

    def test_a_death_time_reaches_the_row(self):
        """TICK-21"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._death_cooldown_rule(3600))
        _TickLoopFixture.run(state)
        self._kill_one()
        _TickLoopFixture.run(state)

        self.assertIsNotNone(state.last_death_time)
        self.assertEqual(self._row(state).last_death_time, state.last_death_time)

    def test_rules_are_read_from_memory(self):
        """TICK-22"""
        from evennia_mob_spawner.models import SpawnRule

        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(
            self._basic_rule(target=1, max_per_room=3),
        )
        edited = dict(state.rule, target=3)
        SpawnRule.objects.filter(pk=state.pk).update(rule=edited)

        for second in (0, 1, 2):
            _TickLoopFixture.tick(second)

        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    # -- conditions --------------------------------------------------

    def _with_conditions(self, conditions):
        return mock.patch.dict(
            "evennia_mob_spawner.conditions._conditions", conditions, clear=True,
        )

    def test_a_condition_answering_no_blocks_the_spawn(self):
        """TICK-23"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule(condition="never"))

        with self._with_conditions({"never": lambda rule: False}):
            _TickLoopFixture.run(state)

        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 0)
        self.assertIsNone(state.last_spawn_time)
        self.assertIsNone(state.last_death_time)

    def test_a_condition_answering_yes_lets_it_spawn(self):
        """TICK-24"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule(condition="always"))

        with self._with_conditions({"always": lambda rule: True}):
            _TickLoopFixture.run(state)

        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)

    def test_the_condition_is_called_with_the_rule(self):
        """TICK-25"""
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._basic_rule(condition="record"))
        seen = []

        def record(rule):
            seen.append(rule)
            return True

        with self._with_conditions({"record": record}):
            _TickLoopFixture.run(state)

        self.assertEqual(seen, [state.rule])

    def test_a_raising_condition_skips_only_its_rule(self):
        """TICK-26"""
        _TickLoopFixture.make_room("Test Room 1")
        _TickLoopFixture.load_rules([
            self._basic_rule(rule_id=1, condition="boom"),
            self._basic_rule(rule_id=2),
        ])
        captured = []

        def boom(rule):
            raise RuntimeError("condition exploded")

        with self._with_conditions({"boom": boom}), mock.patch(
            "evennia_mob_spawner.runtime.ms_log",
            new=lambda m, level="INFO": captured.append((m, level)),
        ):
            _TickLoopFixture.tick(0)

        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1)
        self.assertTrue(
            [m for m, lvl in captured if lvl == "ERROR"],
            f"a raising condition was not logged at ERROR: {captured}",
        )


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
        from evennia_mob_spawner.runtime import _persists_as_attribute
        obj = _PersistsCheckFixture()
        self.assertTrue(_persists_as_attribute(obj, "real_attr"))

    def test_plain_class_attribute_returns_false(self):
        from evennia_mob_spawner.runtime import _persists_as_attribute
        obj = _PersistsCheckFixture()
        self.assertFalse(_persists_as_attribute(obj, "plain_attr"))

    def test_undeclared_name_returns_false(self):
        # Not a descriptor, not even a plain attribute — absent entirely.
        from evennia_mob_spawner.runtime import _persists_as_attribute
        obj = _PersistsCheckFixture()
        self.assertFalse(_persists_as_attribute(obj, "does_not_exist_at_all"))


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

    def test_a_finding_exits_non_zero(self):
        """CLI-04. The exit code is the CI gate — a finding must fail it."""
        import tempfile
        from pathlib import Path
        from evennia_mob_spawner.cli import validate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "definitions.yaml").write_text("levels: []\n")
            (root / "index.yaml").write_text(
                "entries:\n  - name: bad\n    kind: file\n"
            )
            # rule_id alone — every other required field is missing, so
            # Tier 1 refuses without needing an engine.
            (root / "bad.yaml").write_text("rules:\n  - rule_id: 1\n")
            exit_code = validate(["--reader", "local", "--root", tmp])
            self.assertEqual(exit_code, 1)


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


class SpawnIdentityTagTest(SpawnerTestCase):
    """Every spawned mob carries identity tags for rule + source file.

    Stamped by ``spawn_one`` alongside the existing ``area_tag``:
      - ``mob_spawner_rule`` category, key = ``str(rule_id)``
      - ``mob_spawner_file`` category, key = the rule's file
      - ``mob_spawner_pack`` category, key = the pack key, for a pack rule

    Together they let callers query "every mob from rule X in file Y"
    without relying on (typeclass, area_tag) being a unique discriminator.
    """

    DEFAULT_OBJECT = DEFAULT_OBJECT

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

    def _spawned_mob(self, path="identity_test.yaml", rule_id=7, **overrides):
        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(
            self._rule(rule_id=rule_id, **overrides), path=path,
        )
        _TickLoopFixture.run(state)
        from evennia.objects.models import ObjectDB
        mob = ObjectDB.objects.filter(
            db_typeclass_path=self.DEFAULT_OBJECT,
            db_tags__db_key="test_area",
            db_tags__db_category="mob_area",
        ).first()
        self.assertIsNotNone(mob, "fixture did not spawn a mob")
        return mob, state

    def test_spawned_mob_carries_rule_id_tag(self):
        mob, _ = self._spawned_mob(rule_id=7)
        rule_tags = mob.tags.get(
            category="mob_spawner_rule", return_list=True,
        ) or []
        self.assertEqual(rule_tags, ["7"])

    def test_spawned_mob_carries_file_path_tag(self):
        mob, state = self._spawned_mob(path="identity_test.yaml")
        file_tags = mob.tags.get(
            category="mob_spawner_file", return_list=True,
        ) or []
        self.assertEqual(file_tags, [state.file])

    def test_a_pack_rule_stamps_the_pack_tag(self):
        """SPAWN-21"""
        from evennia_mob_spawner.config import _PACK_TAG_CATEGORY

        mob, _ = self._spawned_mob(pack="wolves_ne")
        self.assertEqual(
            mob.tags.get(category=_PACK_TAG_CATEGORY, return_list=True),
            ["wolves_ne"],
        )

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
        from evennia.objects.models import ObjectDB

        _TickLoopFixture.make_room("Test Room A", area_tag="area_a")
        _TickLoopFixture.make_room("Test Room B", area_tag="area_b")

        states = _TickLoopFixture.load_rules([
            self._rule(rule_id=1, area_tag="area_a"),
            self._rule(rule_id=2, area_tag="area_b"),
        ], path="two_rules.yaml")
        for state in states:
            _TickLoopFixture.run(state)

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


class CountLivingContractTest(SpawnerTestCase):
    """``count_living`` keys on (file, rule_id), not (typeclass, area_tag).

    Two rules targeting the same typeclass + area_tag are counted as
    independent populations, because the identity tags stamped at spawn
    time differ between them.
    """

    DEFAULT_OBJECT = DEFAULT_OBJECT

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
        # Two rules in one file sharing the same typeclass + area_tag,
        # distinct rule_ids. Each rule's count_living returns just its own
        # rule's mob, so neither stalls thinking the other met its target.
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Room 1")
        _TickLoopFixture.make_room("Room 2")

        states = _TickLoopFixture.load_rules(
            [self._rule(rule_id=1), self._rule(rule_id=2)], path="shared.yaml",
        )
        for _ in range(2):
            for state in states:
                _TickLoopFixture.run(state)

        self.assertEqual(runtime.count_living(states[0]), 1)
        self.assertEqual(runtime.count_living(states[1]), 1)
        # Two distinct mobs exist in the world.
        self.assertEqual(
            _TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 2,
        )

    def test_rule_id_collision_across_files_isolated(self):
        # Two files each with rule_id=1, same typeclass + area_tag.
        # The file tag prevents cross-file bleed in the count.
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Room 1")

        state_a = _TickLoopFixture.deploy_rule(self._rule(rule_id=1), path="file_a.yaml")
        state_b = _TickLoopFixture.deploy_rule(self._rule(rule_id=1), path="file_b.yaml")

        # Only file_a's rule runs → it spawns one mob.
        _TickLoopFixture.run(state_a)

        self.assertEqual(runtime.count_living(state_a), 1)
        # file_b's count for ITS rule_id=1 must be 0, not 1 — the
        # mob_spawner_file tag stops file_a's mob bleeding in.
        self.assertEqual(runtime.count_living(state_b), 0)

    def test_count_excludes_mobs_lacking_identity_tags(self):
        # Manually create a mob with the same typeclass + area_tag as
        # a deployed rule but WITHOUT the identity tags. Proves the count
        # is driven by the identity tags, not by a typeclass + area_tag
        # fallback.
        from evennia.utils.create import create_object
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Room 1")
        state = _TickLoopFixture.deploy_rule(self._rule(rule_id=1))

        # Create an "interloper" mob bypassing spawn_one. It carries
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

        # But count_living must return 0 — no rule produced the
        # interloper, so it has no identity tags, so it's invisible to
        # the per-rule count.
        self.assertEqual(runtime.count_living(state), 0)


class YamlDeclaredTagsTest(SpawnerTestCase):
    """The `tags:` rule field stamps additional tags on each spawned mob.

    Each entry is a bare string (untyped) or a `{key, category?}` dict.
    Library-stamped tags (area_tag, identity tags) are unaffected.
    """

    DEFAULT_OBJECT = DEFAULT_OBJECT

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
        from evennia.objects.models import ObjectDB

        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._rule(tags=tags))
        _TickLoopFixture.run(state)
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
        from evennia.objects.models import ObjectDB

        _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._rule())  # no tags key
        _TickLoopFixture.run(state)
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


class RoomHasSpaceContractTest(SpawnerTestCase):
    """``room_has_space`` keys on (file, rule_id), not (typeclass, area_tag).

    The per-room ``max_per_room`` cap is per-rule. Two rules sharing
    typeclass + area_tag enforce their caps independently — a room can
    hold one mob from each rule rather than one mob TOTAL across both.
    """

    DEFAULT_OBJECT = DEFAULT_OBJECT

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
        # max_per_room=1. One shared room. Each rule's cap is enforced
        # against only its own mobs, so both spawn into the same room.
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Shared Room")

        states = _TickLoopFixture.load_rules(
            [self._rule(rule_id=1), self._rule(rule_id=2)], path="shared.yaml",
        )
        for _ in range(2):
            for state in states:
                _TickLoopFixture.run(state)

        # Each rule should have hit its target (1 mob).
        self.assertEqual(runtime.count_living(states[0]), 1)
        self.assertEqual(runtime.count_living(states[1]), 1)
        # Two mobs total in the room — confirms the per-room cap
        # was enforced per-rule, not as a shared pool.
        self.assertEqual(
            _TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 2,
        )

    def test_max_per_room_still_blocks_within_a_single_rule(self):
        # One rule, target=3, max_per_room=1, one room — only one mob
        # should ever exist there.
        _TickLoopFixture.make_room("Only Room")
        state = _TickLoopFixture.deploy_rule(
            self._rule(rule_id=1, target=3, max_per_room=1),
        )

        for _ in range(3):
            _TickLoopFixture.run(state)

        self.assertEqual(
            _TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 1,
        )



class CommandScopeGateTest(TestCase):
    """How the shared scope gate words its refusals."""

    def test_refusal_names_the_invoking_command(self):
        # The gate is shared, so its messages must not hardcode ms_load.
        with mock.patch(
            "evennia_mob_spawner.commands.active_shard_id", return_value="shard0"
        ):
            self.assertIn("ms_stop", check_shard_scope({}, "ms_stop"))
            self.assertIn(
                "ms_delete", check_shard_scope({"zone": "x"}, "ms_delete")
            )
            self.assertIn(
                "ms_delete",
                check_shard_scope({SHARD_LEVEL: "shard1"}, "ms_delete"),
            )

    def test_default_command_key_is_ms_load(self):
        with mock.patch(
            "evennia_mob_spawner.commands.active_shard_id", return_value="shard0"
        ):
            self.assertIn("ms_load", check_shard_scope({}))


class SpawnRollbackTest(SpawnerTestCase):
    """``spawn_one`` is all-or-nothing from creation through attrs.

    ``create_object()`` commits the mob before any tag is stamped, and the
    caller catches per-rule and carries on (decision #14). Without a
    rollback, a failure in between leaves a real, located mob wearing none
    of its identity tags — and because populations are counted by querying
    those tags, it is invisible: never counted, never culled, never
    replaced. Orphans accumulate silently for the life of the database.
    """

    DEFAULT_OBJECT = DEFAULT_OBJECT

    def _rule(self):
        return {
            "rule_id": 1,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }

    def _objects_named(self, key):
        from evennia.objects.models import ObjectDB
        return list(ObjectDB.objects.filter(db_key=key))

    def _spawn_with_failure_at(self, patch_target, rule=None):
        """Run one spawn with *patch_target* raising, return the state."""
        from evennia_mob_spawner import runtime

        rule = rule or self._rule()
        room = _TickLoopFixture.make_room("Rollback Room")
        state = _TickLoopFixture.deploy_rule(rule)
        with mock.patch(patch_target, side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                runtime.spawn_one(state, room)
        return state

    def test_tagging_failure_leaves_no_mob_behind(self):
        """The orphan case: exception while stamping identity tags."""
        self._spawn_with_failure_at(
            "evennia_mob_spawner.runtime.get_area_tag_category"
        )
        self.assertEqual(
            self._objects_named("a test mob"), [],
            "a partially-built mob survived a failed spawn",
        )

    def test_attrs_failure_leaves_no_mob_behind(self):
        """Failure later in the sequence rolls back just the same.

        Needs a rule that actually carries ``attrs`` — without one the
        loop never runs and the patched failure is never reached.
        """
        rule = dict(self._rule(), attrs={"some_value": 3})
        self._spawn_with_failure_at(
            "evennia_mob_spawner.runtime._persists_as_attribute", rule=rule
        )
        self.assertEqual(
            self._objects_named("a test mob"), [],
            "a mob survived a failure during attribute application",
        )

    def test_successful_spawn_still_produces_a_tagged_mob(self):
        """Control — the rollback must not fire on the happy path."""
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Happy Room")
        state = _TickLoopFixture.deploy_rule(self._rule())
        mob = runtime.spawn_one(state, room)
        self.assertEqual(len(self._objects_named("a test mob")), 1)
        self.assertTrue(mob.tags.get("test_area", category="mob_area"))

    def test_a_rollback_that_itself_fails_is_logged_at_error(self):
        """SPAWN-17. The one case that does leave an orphan must be visible.

        If ``delete()`` fails too, the untagged mob stays in the world —
        exactly what the rollback exists to prevent. Logging it at ERROR
        is what stops it being silent as well as invisible.
        """
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Doomed Room")
        state = _TickLoopFixture.deploy_rule(self._rule())
        captured = []

        with mock.patch(
            "evennia_mob_spawner.runtime.ms_log",
            new=lambda m, level="INFO": captured.append((m, level)),
        ), mock.patch(
            "evennia_mob_spawner.runtime.get_area_tag_category",
            side_effect=RuntimeError("boom"),
        ), mock.patch(
            "evennia.objects.objects.DefaultObject.delete",
            side_effect=RuntimeError("delete failed too"),
        ):
            with self.assertRaises(RuntimeError):
                runtime.spawn_one(state, room)

        errors = [m for m, lvl in captured if lvl == "ERROR"]
        self.assertTrue(
            any("rollback failed" in m for m in errors),
            f"a failed rollback was not reported at ERROR: {captured}",
        )

    def test_failure_is_reraised_so_the_caller_still_logs_it(self):
        """Rollback must not swallow the error the caller reports on."""
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Reraise Room")
        state = _TickLoopFixture.deploy_rule(self._rule())
        with mock.patch(
            "evennia_mob_spawner.runtime.get_area_tag_category",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                runtime.spawn_one(state, room)


class PickRoomTest(SpawnerTestCase):
    """``pick_room``: pack first, then the area pool.

    Every step skips a room at ``max_per_room`` or holding anything with a
    session. Each test calls ``pick_room`` directly — the pass would gate
    on cooldown and target first, and the question here is only which room
    comes back.
    """

    DEFAULT_OBJECT = DEFAULT_OBJECT

    def _rule(self, rule_id=1, **overrides):
        rule = {
            "rule_id": rule_id,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 3,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }
        rule.update(overrides)
        return rule

    @staticmethod
    def _untagged_room(key):
        """A room outside the rule's area pool.

        The pool can never return this room, so a result of this room proves
        the pack step fired rather than the pool happening to agree.
        """
        from evennia import DefaultRoom
        from evennia.utils.create import create_object
        return create_object(DefaultRoom, key=key)

    def _pick(self, state, times=10):
        """Every distinct room ``pick_room`` returns over several calls.

        Selection is random within a step, so one call can agree with the
        expected room by chance.
        """
        from evennia_mob_spawner import runtime
        return {runtime.pick_room(state) for _ in range(times)}

    # -- the pack step -----------------------------------------------

    def test_a_pack_member_draws_the_spawn_to_its_room(self):
        """PICK-10"""
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Area Room")
        lair = self._untagged_room("The Lair")
        state = _TickLoopFixture.deploy_rule(self._rule(pack="wolves", max_per_room=4))
        runtime.spawn_one(state, lair)          # an existing member

        self.assertEqual(self._pick(state), {lair})

    def test_any_rule_sharing_the_pack_key_counts(self):
        """PICK-11"""
        from evennia_mob_spawner import runtime

        _TickLoopFixture.make_room("Area Room")
        lair = self._untagged_room("The Lair")
        # Rule 1 is the leader and has none alive; rule 2's member does.
        leader, member, newcomer = _TickLoopFixture.load_rules([
            self._rule(rule_id=1, pack="wolves"),
            self._rule(rule_id=2, pack="wolves"),
            self._rule(rule_id=3, pack="wolves"),
        ])
        runtime.spawn_one(member, lair)

        self.assertEqual(self._pick(newcomer), {lair})

    def test_a_pack_member_outside_the_area_is_not_chosen(self):
        """PICK-12"""
        from evennia_mob_spawner import runtime

        area_room = _TickLoopFixture.make_room("Area Room")
        elsewhere = self._untagged_room("Another Area")
        stray, newcomer = _TickLoopFixture.load_rules([
            self._rule(rule_id=1, pack="wolves", area_tag="other_area"),
            self._rule(rule_id=2, pack="wolves"),
        ])
        runtime.spawn_one(stray, elsewhere)

        self.assertEqual(self._pick(newcomer), {area_room})

    def test_no_living_member_uses_the_area_pool(self):
        """PICK-13"""
        area_room = _TickLoopFixture.make_room("Area Room")
        state = _TickLoopFixture.deploy_rule(self._rule(pack="wolves"))

        self.assertEqual(self._pick(state), {area_room})

    def test_a_blocked_member_room_uses_the_area_pool(self):
        """PICK-14"""
        from evennia_mob_spawner import runtime

        area_room = _TickLoopFixture.make_room("Area Room")
        lair = self._untagged_room("The Lair")
        state = _TickLoopFixture.deploy_rule(self._rule(pack="wolves", max_per_room=1))
        runtime.spawn_one(state, lair)          # a member, and the room is full

        self.assertEqual(self._pick(state), {area_room})

        # Room for more, but a player is standing in it.
        state.rule["max_per_room"] = 4
        _sessioned(lair)
        self.assertEqual(self._pick(state), {area_room})

    # -- occupancy ---------------------------------------------------

    def test_a_room_with_a_session_is_never_chosen(self):
        """PICK-15"""
        occupied = _TickLoopFixture.make_room("Occupied Room")
        free = _TickLoopFixture.make_room("Free Room")
        _sessioned(occupied)
        state = _TickLoopFixture.deploy_rule(self._rule())

        self.assertEqual(self._pick(state), {free})

    def test_objects_without_a_session_do_not_block(self):
        """PICK-16"""
        from evennia import DefaultCharacter
        from evennia.utils.create import create_object
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Busy Room")
        create_object(DefaultCharacter, key="a disconnected player", location=room)
        other = _TickLoopFixture.deploy_rule(self._rule(rule_id=1), path="other.yaml")
        runtime.spawn_one(other, room)          # a mob from another rule
        state = _TickLoopFixture.deploy_rule(self._rule(rule_id=2))

        self.assertEqual(self._pick(state), {room})

    def test_every_room_occupied_returns_none_quietly(self):
        """PICK-17"""
        from evennia_mob_spawner import runtime

        _sessioned(_TickLoopFixture.make_room("Room 1"))
        _sessioned(_TickLoopFixture.make_room("Room 2"))
        state = _TickLoopFixture.deploy_rule(self._rule())

        self.assertIsNone(runtime.pick_room(state))

        captured = []
        with mock.patch(
            "evennia_mob_spawner.runtime.ms_log",
            new=lambda m, level="INFO": captured.append((m, level)),
        ):
            _TickLoopFixture.run(state)
        self.assertEqual([m for m, lvl in captured if lvl == "WARN"], [])
        self.assertEqual(_TickLoopFixture.count_mobs(self.DEFAULT_OBJECT), 0)


class _HookMob:
    """Records that ``ms_at_post_spawn`` ran, and when.

    Patched over the spawned mob rather than registered as a typeclass —
    the library looks the hook up with ``getattr`` on the instance, so a
    plain attribute is the whole protocol surface.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


class MsAtPostSpawnInvocationTest(SpawnerTestCase):
    """Decision #23's runtime half — the library actually calling the hook.

    Tier 3 validates that a declared ``ms_at_post_spawn`` is callable with
    the right signature (``RESLV-10``..``RESLV-16``); these cover the call
    itself, the silence when it is absent, and what happens when it raises.
    """

    DEFAULT_OBJECT = DEFAULT_OBJECT

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

    def test_a_declared_hook_is_invoked_after_spawn(self):
        # SPAWN-18.
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Hook Room")
        state = _TickLoopFixture.deploy_rule(self._rule())
        hook = _HookMob()

        # The hook is looked up on the spawned mob, so it goes on the
        # rule's typeclass — not on the room.
        with mock.patch(
            f"{self.DEFAULT_OBJECT}.ms_at_post_spawn", hook, create=True,
        ):
            mob = runtime.spawn_one(state, room)

        self.assertEqual(hook.calls, 1)
        # The hook runs after the mob is fully built: its identity tags
        # are already stamped by the time it fires.
        self.assertTrue(mob.tags.get("test_area", category="mob_area"))

    def test_a_typeclass_without_the_hook_is_never_touched(self):
        # SPAWN-19. The protocol is opt-in; absence is silent, not an error.
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Quiet Room")
        state = _TickLoopFixture.deploy_rule(self._rule())
        captured = []

        with mock.patch(
            "evennia_mob_spawner.runtime.ms_log",
            new=lambda m, level="INFO": captured.append((m, level)),
        ):
            mob = runtime.spawn_one(state, room)

        self.assertFalse(hasattr(mob, "ms_at_post_spawn"))
        self.assertEqual(
            [m for m, lvl in captured if lvl in ("WARN", "ERROR")], [],
        )

    def test_a_raising_hook_is_logged_and_the_mob_survives(self):
        # SPAWN-20. The mob is fully built and tagged before the hook runs,
        # so a consumer bug in the hook must not roll it back — that would
        # leave the population permanently short with no mob to show for it.
        from evennia_mob_spawner import runtime

        room = _TickLoopFixture.make_room("Angry Room")
        state = _TickLoopFixture.deploy_rule(self._rule())
        captured = []

        def boom(self):
            raise RuntimeError("hook exploded")

        with mock.patch(
            "evennia_mob_spawner.runtime.ms_log",
            new=lambda m, level="INFO": captured.append((m, level)),
        ), mock.patch(
            f"{self.DEFAULT_OBJECT}.ms_at_post_spawn", boom, create=True,
        ):
            mob = runtime.spawn_one(state, room)

        self.assertIsNotNone(mob.pk, "a hook failure deleted the mob")
        errors = [m for m, lvl in captured if lvl == "ERROR"]
        self.assertTrue(
            any("ms_at_post_spawn" in m for m in errors),
            f"a raising hook was not reported at ERROR: {captured}",
        )


class ValidateQueryTest(TestCase):
    """``Definitions.validate_query`` — the operator's scope, checked early.

    A query naming a level the consumer never declared cannot resolve, and
    saying so here means the refusal names the level rather than surfacing
    as a manifest walk that finds nothing.
    """

    def setUp(self):
        self.defs = Definitions.from_dict({"levels": ["shard", "zone"]})

    def test_undeclared_level_is_refused(self):
        # CFG-08.
        with self.assertRaises(DefinitionsError):
            self.defs.validate_query({"region": "north"})

    def test_a_contiguous_prefix_is_accepted(self):
        self.assertIsNone(self.defs.validate_query({"shard": "shard0"}))

    def test_a_skipped_level_is_refused(self):
        with self.assertRaises(DefinitionsError):
            self.defs.validate_query({"zone": "millholm"})


class ParseArgsTest(TestCase):
    """``_parse_args`` — the operator-facing half of every command.

    The happy paths are exercised end-to-end through the command tests;
    these are the refusals, which are the only way a typo becomes a
    message rather than a traceback.
    """

    def setUp(self):
        from evennia_mob_spawner.commands import _parse_args
        self.parse = _parse_args

    def test_no_scope_token_is_refused(self):
        # CMD-04. Flags alone are not a scope.
        with self.assertRaises(ValueError):
            self.parse("--force-validate")

    def test_a_positional_without_an_equals_is_refused(self):
        # CMD-05.
        with self.assertRaises(ValueError):
            self.parse("shard0")

    def test_an_empty_key_or_value_is_refused(self):
        # CMD-06. Both sides, since "=x" and "x=" fail differently in the
        # partition but must give the operator the same answer.
        with self.assertRaises(ValueError):
            self.parse("=shard0")
        with self.assertRaises(ValueError):
            self.parse("shard=")


class OperatingCommandTest(SpawnerTestCase):
    """``ms_status``, ``ms_start`` / ``ms_stop`` and ``ms_delete``."""

    DEFAULT_OBJECT = DEFAULT_OBJECT

    def _rule(self, rule_id=1, **overrides):
        rule = {
            "rule_id": rule_id,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a test mob",
            "area_tag": "test_area",
            "target": 3,
            "max_per_room": 3,
            "respawn_seconds": 0,
        }
        rule.update(overrides)
        return rule

    @staticmethod
    def _call(cmd_class, args=""):
        cmd = cmd_class()
        cmd.caller = mock.Mock()
        cmd.args = args
        cmd.func()
        return cmd.caller

    @staticmethod
    def _line_for(lines, state):
        """The ``ms_status`` row naming ``state``'s id."""
        rows = [line for line in lines if line.split() and line.split()[0] == str(state.pk)]
        assert len(rows) == 1, f"expected one row for id {state.pk}: {lines}"
        return rows[0]

    def test_the_installed_commands(self):
        """CMD-15"""
        from evennia.commands.default.cmdset_account import AccountCmdSet

        keys = {cmd.key for cmd in AccountCmdSet().commands if cmd.key.startswith("ms_")}
        self.assertEqual(
            keys, {"ms_load", "ms_status", "ms_start", "ms_stop", "ms_delete"},
        )

    def test_stop_and_start_toggle_the_spawner(self):
        """CMD-16"""
        from evennia_mob_spawner import runtime
        from evennia_mob_spawner.commands import CmdMsStart, CmdMsStop

        self._call(CmdMsStop)
        self.assertFalse(runtime.is_enabled())
        self._call(CmdMsStart)
        self.assertTrue(runtime.is_enabled())

    def test_the_flag_survives_a_restart(self):
        """CMD-17"""
        from evennia.server.models import ServerConfig
        from evennia_mob_spawner import runtime
        from evennia_mob_spawner.commands import CmdMsStop
        from evennia_mob_spawner.config import _ENABLED_CONFIG_KEY

        self._call(CmdMsStop)
        self.assertIs(ServerConfig.objects.conf(_ENABLED_CONFIG_KEY), False)

        # What a boot reads back.
        runtime.load_rules()
        self.assertFalse(runtime.is_enabled())

    def test_a_fresh_database_reads_as_enabled(self):
        """CMD-18"""
        from evennia_mob_spawner import runtime

        self.assertTrue(runtime.is_enabled())

    def test_status_shows_the_spawner_state(self):
        """CMD-19"""
        from evennia_mob_spawner.commands import CmdMsStop, status_lines

        with override_settings(MOB_SPAWNER_TICK_SECONDS=20):
            header = status_lines()[0]
            self.assertIn("enabled", header)
            self.assertIn("N=20", header)

            self._call(CmdMsStop)
            self.assertIn("disabled", status_lines()[0])

    def test_status_lists_one_row_per_rule(self):
        """CMD-20"""
        from evennia_mob_spawner.commands import status_lines

        first, second = _TickLoopFixture.load_rules([
            self._rule(rule_id=4, key="a grey wolf", area_tag="wolves_ne", target=3),
            self._rule(rule_id=9, key="a direwolf", area_tag="wolves_ne", target=1),
        ], path="shard0/southern.yaml")

        lines = status_lines()
        for state, rule_id, key, target in (
            (first, "4", "a grey wolf", 3), (second, "9", "a direwolf", 1),
        ):
            line = self._line_for(lines, state)
            for token in ("shard0/southern.yaml", rule_id, key, "wolves_ne", f"0/{target}"):
                self.assertIn(token, line)

    def test_the_live_count_is_the_ticks_count(self):
        """CMD-21"""
        from evennia.utils.create import create_object
        from evennia_mob_spawner import runtime
        from evennia_mob_spawner.commands import status_lines

        room = _TickLoopFixture.make_room("Test Room 1")
        state = _TickLoopFixture.deploy_rule(self._rule())
        _TickLoopFixture.run(state)
        # Same typeclass and area, but no rule produced it.
        interloper = create_object(self.DEFAULT_OBJECT, key="interloper", location=room)
        interloper.tags.add("test_area", category="mob_area")

        self.assertEqual(runtime.count_living(state), 1)
        self.assertIn("1/3", self._line_for(status_lines(), state))

    def test_delete_removes_the_rule(self):
        """CMD-22"""
        from evennia_mob_spawner import runtime
        from evennia_mob_spawner.commands import delete_rule
        from evennia_mob_spawner.models import SpawnRule

        doomed, kept = _TickLoopFixture.load_rules([self._rule(1), self._rule(2)])

        delete_rule(str(doomed.pk))

        self.assertFalse(SpawnRule.objects.filter(pk=doomed.pk).exists())
        self.assertEqual([s.pk for s in runtime.rule_states()], [kept.pk])

    def test_delete_refuses_an_unknown_id(self):
        """CMD-23"""
        from evennia_mob_spawner.commands import delete_rule
        from evennia_mob_spawner.models import SpawnRule

        state = _TickLoopFixture.deploy_rule(self._rule())
        unknown = state.pk + 100

        message = delete_rule(str(unknown))

        self.assertIn(str(unknown), message)
        self.assertEqual(SpawnRule.objects.count(), 1)

    def test_delete_refuses_a_non_integer_id(self):
        """CMD-24"""
        from evennia_mob_spawner.commands import delete_rule
        from evennia_mob_spawner.models import SpawnRule

        _TickLoopFixture.deploy_rule(self._rule())

        message = delete_rule("wolves")

        self.assertIn("wolves", message)
        self.assertEqual(SpawnRule.objects.count(), 1)


class SpawnRuleLookupTest(SpawnerTestCase):
    """``spawn_rule_for`` — the rule a mob was spawned from."""

    DEFAULT_OBJECT = DEFAULT_OBJECT

    def _rule(self, rule_id=1, **overrides):
        rule = {
            "rule_id": rule_id,
            "typeclass": self.DEFAULT_OBJECT,
            "key": "a wolf",
            "area_tag": "test_area",
            "target": 1,
            "max_per_room": 1,
            "respawn_seconds": 0,
        }
        rule.update(overrides)
        return rule

    def setUp(self):
        super().setUp()
        self.room = _TickLoopFixture.make_room("Lookup Room")

    def test_a_spawned_mob_returns_its_rule(self):
        """LOOK-01"""
        from evennia_mob_spawner import runtime

        rule = self._rule(max_per_room=3)
        mob = runtime.spawn_one(_TickLoopFixture.deploy_rule(rule), self.room)

        self.assertEqual(runtime.spawn_rule_for(mob), rule)

    def test_a_mob_no_rule_produced_returns_none(self):
        """LOOK-02"""
        from evennia.utils.create import create_object
        from evennia_mob_spawner import runtime

        _TickLoopFixture.deploy_rule(self._rule())
        stranger = create_object(self.DEFAULT_OBJECT, key="a wolf", location=self.room)

        self.assertIsNone(runtime.spawn_rule_for(stranger))

    def test_a_removed_rule_returns_none(self):
        """LOOK-03"""
        from evennia_mob_spawner import runtime

        doomed, _ = _TickLoopFixture.load_rules([self._rule(1), self._rule(2)])
        mob = runtime.spawn_one(doomed, self.room)

        # The file is reloaded without rule 1; its mob stays in the world.
        _TickLoopFixture.load_rules([self._rule(2)])

        self.assertIsNone(runtime.spawn_rule_for(mob))

    def test_an_edited_rule_is_seen_at_once(self):
        """LOOK-04"""
        from evennia_mob_spawner import runtime

        state = _TickLoopFixture.deploy_rule(self._rule(key="a wolf"))
        mob = runtime.spawn_one(state, self.room)

        _TickLoopFixture.deploy_rule(self._rule(key="a grey wolf"))

        self.assertEqual(runtime.spawn_rule_for(mob)["key"], "a grey wolf")

    def test_the_mobs_own_file_decides(self):
        """LOOK-05"""
        from evennia_mob_spawner import runtime

        _TickLoopFixture.deploy_rule(self._rule(1, key="a wolf"), path="a.yaml")
        rat_rule = _TickLoopFixture.deploy_rule(self._rule(1, key="a rat"), path="b.yaml")
        mob = runtime.spawn_one(rat_rule, self.room)

        self.assertEqual(runtime.spawn_rule_for(mob)["key"], "a rat")

    def test_changing_the_result_leaves_the_live_rule_alone(self):
        """LOOK-06"""
        from evennia_mob_spawner import runtime

        state = _TickLoopFixture.deploy_rule(self._rule(tags=["vermin"]))
        mob = runtime.spawn_one(state, self.room)

        result = runtime.spawn_rule_for(mob)
        result["target"] = 99
        result["tags"].append("edited")

        live = runtime.rule_states()[0].rule
        self.assertEqual(live["target"], 1)
        self.assertEqual(live["tags"], ["vermin"])


class SpawnIdentityTest(SpawnerTestCase):
    """``spawn_identity`` — which rule a mob was spawned from, as its file and
    ``rule_id``. The rule and room are ``SpawnRuleLookupTest``'s."""

    DEFAULT_OBJECT = DEFAULT_OBJECT
    _rule = SpawnRuleLookupTest._rule

    def setUp(self):
        super().setUp()
        self.room = _TickLoopFixture.make_room("Identity Room")

    def test_a_spawned_mob_returns_its_file_and_rule_id(self):
        """IDENT-01"""
        from evennia_mob_spawner import runtime

        state = _TickLoopFixture.deploy_rule(self._rule(7, max_per_room=2))
        first = runtime.spawn_one(state, self.room)
        second = runtime.spawn_one(state, self.room)

        self.assertEqual(runtime.spawn_identity(first), ("test.yaml", "7"))
        self.assertEqual(runtime.spawn_identity(second), runtime.spawn_identity(first))

    def test_an_unspawned_or_ambiguous_mob_returns_none(self):
        """IDENT-02"""
        from evennia.utils.create import create_object
        from evennia_mob_spawner import runtime
        from evennia_mob_spawner.config import _FILE_TAG_CATEGORY

        stranger = create_object(self.DEFAULT_OBJECT, key="a wolf", location=self.room)
        self.assertIsNone(runtime.spawn_identity(stranger))

        mob = runtime.spawn_one(_TickLoopFixture.deploy_rule(self._rule()), self.room)
        mob.tags.add("other.yaml", category=_FILE_TAG_CATEGORY)
        self.assertIsNone(runtime.spawn_identity(mob))

    def test_the_file_tells_rules_apart(self):
        """IDENT-03"""
        from evennia_mob_spawner import runtime

        wolf_rule = _TickLoopFixture.deploy_rule(self._rule(1, key="a wolf"), path="a.yaml")
        rat_rule = _TickLoopFixture.deploy_rule(self._rule(1, key="a rat"), path="b.yaml")
        wolf = runtime.spawn_one(wolf_rule, self.room)
        rat = runtime.spawn_one(rat_rule, _TickLoopFixture.make_room("Rat Room"))

        self.assertNotEqual(runtime.spawn_identity(wolf), runtime.spawn_identity(rat))

    def test_a_removed_rule_keeps_its_identity(self):
        """IDENT-04"""
        from evennia_mob_spawner import runtime

        doomed, _ = _TickLoopFixture.load_rules([self._rule(1), self._rule(2)])
        mob = runtime.spawn_one(doomed, self.room)
        _TickLoopFixture.load_rules([self._rule(2)])

        self.assertEqual(runtime.spawn_identity(mob), ("test.yaml", "1"))


class ConditionRegistryTest(TestCase):
    """``register_condition`` — the game's conditions, by name."""

    def setUp(self):
        patcher = mock.patch.dict(
            "evennia_mob_spawner.conditions._conditions", {}, clear=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_registered_condition_is_found_by_name(self):
        """COND-01"""
        from evennia_mob_spawner.conditions import get_condition, register_condition

        def night_only(rule):
            return True

        register_condition("night_only", night_only)
        self.assertIs(get_condition("night_only"), night_only)

    def test_a_name_in_use_is_refused_not_replaced(self):
        """COND-02"""
        from evennia_mob_spawner.conditions import get_condition, register_condition

        def first(rule):
            return True

        def second(rule):
            return False

        register_condition("night_only", first)
        with self.assertRaises(ValueError):
            register_condition("night_only", second)
        self.assertIs(get_condition("night_only"), first)

