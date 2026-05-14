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
    LoaderInvalidShapeError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
)
from evennia_mob_spawner.commands import FORCE_VALIDATE_FLAG, should_pre_validate
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


class PipelineScaffoldSmokeTest(TestCase):
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


class DeployerTest(TestCase):
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
