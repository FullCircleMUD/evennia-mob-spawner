# Test plan

Every test case the library commits to covering, and the test function that covers it. The library is
built test-first: cases are agreed here, tests are written against them, then the implementation is
written to pass. The **Test function** column is the auditable trail — it is filled in as each test is
written, so an empty cell means the case is agreed but not yet covered.

Case IDs are stable and referenceable. Do not renumber; retire an ID rather than reuse it.

All test functions live in `src/evennia_mob_spawner/tests.py`, run via `python runtests.py`.

| Prefix | Covers |
|---|---|
| `PKG` | Package install + dependency resolution |
| `LOG` | `ms_log` shim (`log.py`) |
| `PIPE` | Pipeline scaffold end-to-end |
| `CFG` | Settings dispatch (`config.py`) |
| `FND` | `Finder.find` |
| `LDR` | `Loader.load` |
| `VS` | Validator structure and tier dispatch |
| `SHAPE` | Tier 1 — per-rule shape predicates |
| `UNIQ` | Tier 2 — `rule_id` uniqueness within a file |
| `FM` | File-level metadata shape |
| `RESLV` | Tier 3 — engine-runtime resolvability |
| `DIAG` | Tier 4 — deploy-time diagnostics |
| `GATE` | `should_pre_validate` — the validation gating decision |
| `SHARD` | Instance gates on the command surface |
| `MODEL` | The rule table — where rules and their timers are stored |
| `DEP` | `ms_load`'s upsert — rows, timers, purge, the in-memory swap |
| `BOOT` | The `at_server_start` helper — loading rules, registering the tick |
| `TICK` | The tick — slot selection, the enabled flag, the per-rule pass |
| `PICK` | Room selection — pack, then the area pool |
| `COND` | The condition registry |
| `LOOK` | `spawn_rule_for` — the rule a mob was spawned from |
| `SPAWN` | `_spawn_one` — tagging, attrs, hook, rollback |
| `POP` | Population contracts (`_count_living`, `_room_has_space`) |
| `CMD` | Command surface — argument parsing, scope resolution, install, the operating commands |
| `CLI` | `ms-validate` standalone CLI |

Retired prefixes, IDs not reused: `STATE`, `RACE`, `STALL`, `LOAD`.

## Fixtures

Most of the suite needs no database. The stages that create Evennia objects (`ms_load`'s upsert, the
tick, `_spawn_one`) run against a real test database — `runtests.py` calls `evennia._init()`, so
`create_object` works without a consumer gamedir.

| Fixture | Purpose |
|---|---|
| `EvenniaWorldTestCase` | Base for any test creating Evennia objects. Pre-creates a Limbo room and points `settings.DEFAULT_HOME` at it, so `create_object()` without an explicit `home` doesn't fail FK integrity |
| `FakeReader` | Module-scope dummy Reader, importable by dotted path — exercises `MOB_SPAWNER_READER` dispatch |
| `FixtureReader` | In-memory Reader mapping path → parsed data; raises `ReaderNotFoundError` for unknown paths. Drives `Finder` and `Loader` against synthetic manifest trees |
| `_FakeTypeclass` | Tier 3 fixture — a class with no `ms_at_post_spawn` |
| `_FakeTypeclassWithHook` | Tier 3 fixture — callable `ms_at_post_spawn` method |
| `_FakeTypeclassWithBadHook` | Tier 3 fixture — `ms_at_post_spawn` present but not callable |
| `_FakeTypeclassWithBadHookSignature` | Tier 3 fixture — hook requires an extra argument |
| `_FakeTypeclassWithDefaultedHookSignature` | Tier 3 fixture — extra params, all defaulted |
| `_FakeTypeclassWithVariadicHook` | Tier 3 fixture — `*args/**kwargs` hook |
| `_TickLoopFixture` | Builds tagged rooms, loads a rule, returns it. A plain helper rather than a mixin so each test constructs exactly the world it needs |
| `_PersistsCheckFixture` | Plain (non-Evennia) class with and without `AttributeProperty` declarations, for `_persists_as_attribute` |
| `SpawnerTestCase` | Base for tests that drive the running spawner. Reloads the in-memory table from the clean database before each test, so one test's rules never leak into the next |
| `_FakeDate` | Carries only `elapsed_real_seconds`, so the tick receiver is driven second by second without a running clock |
| `_sessioned` | Creates an object in a room reporting one session, for room occupancy |

## PKG — package and dependencies

| ID | Case | Test function |
|---|---|---|
| PKG-01 | `__version__` is present and matches the packaged version | `PackageSmokeTest.test_version_present` |
| PKG-02 | `evennia-yaml-reader`'s primitives import in the library venv | `YamlReaderDependencyTest.test_can_import_reader_primitives` |
| PKG-03 | `get_area_tag_category` is exported from the package root, so a consumer reading the area tags asks the same accessor the library does | `PackageSmokeTest.test_area_tag_category_accessor_exported` |
| PKG-04 | `register_condition` and the `at_server_start` helper are exported from the package root | `PackageSmokeTest.test_runtime_entry_points_exported` |
| PKG-05 | `spawn_rule_for` is exported from the package root | `PackageSmokeTest.test_spawn_rule_lookup_exported` |

## LOG — `ms_log`

`ms_log` binds `evennia-logging-extension`'s `make_logger` to this library's own
`mob_spawner.log`. The mechanism — the reactor branch, the level coercion, the traceback handling —
belongs to the extension and is covered by the extension's suite. What is pinned here is the
binding: that a line this library emits reaches the file this library names.

Both cases read the line back from disk. A case that asserts only "the call did not raise" passes
whether or not anything is ever written, which is how a dead log call survived a full suite in
`evennia-calendar`.

| ID | Case | Test function |
|---|---|---|
| LOG-01 | A logged line lands in `mob_spawner.log` under `LOG_DIR` | `LogShimSmokeTest.test_ms_log_writes_to_the_library_log_file` |
| LOG-03 | A line logged at `WARN` carries `[WARN]` in the file | `LogShimSmokeTest.test_ms_log_carries_the_level_into_the_file` |

Retired: LOG-02 (an unknown level is coerced) — the extension's behaviour, pinned in the extension's
suite. LOG-05 (silent no-op outside a running Evennia) — no such case exists: without a reactor the
extension writes synchronously rather than going quiet.

## PIPE — pipeline scaffold

| ID | Case | Test function |
|---|---|---|
| PIPE-01 | Every stage is importable, instantiable, and runs end to end | `PipelineScaffoldSmokeTest.test_pipeline_flows_end_to_end` |
| PIPE-02 | `Definitions` parses a minimal `definitions.yaml` | `PipelineScaffoldSmokeTest.test_definitions_parses_minimal_yaml` |
| PIPE-03 | An empty `definitions.yaml` yields the defaults | `PipelineScaffoldSmokeTest.test_definitions_empty_yaml_yields_defaults` |

## CFG — settings dispatch

| ID | Case | Test function |
|---|---|---|
| CFG-01 | No setting returns the default `GitHubReader` | `GetReaderClassTest.test_default_returns_github_reader` |
| CFG-02 | `MOB_SPAWNER_READER` overrides the reader class | `GetReaderClassTest.test_override_via_settings` |
| CFG-03 | A bad dotted path raises rather than falling back silently | `GetReaderClassTest.test_bad_dotted_path_raises` |
| CFG-08 | `Definitions.validate_query` refuses a query naming an undeclared level | `ValidateQueryTest.test_undeclared_level_is_refused` |
| CFG-09 | A query forming a contiguous prefix of the declared levels is accepted | `ValidateQueryTest.test_a_contiguous_prefix_is_accepted` |
| CFG-10 | A query skipping an intermediate level is refused | `ValidateQueryTest.test_a_skipped_level_is_refused` |

## FND — `Finder.find(query)`

| ID | Case | Test function |
|---|---|---|
| FND-01 | An empty query resolves to the repo root | `FinderTest.test_empty_query_returns_root` |
| FND-02 | A shard-level query resolves to a folder location | `FinderTest.test_shard_folder_query_returns_folder_location` |
| FND-03 | A shard-level query resolving to a leaf gives a file location | `FinderTest.test_shard_file_query_returns_file_location` |
| FND-04 | A full-depth query walks every declared level | `FinderTest.test_full_path_query` |
| FND-05 | A key that isn't a declared level raises | `FinderTest.test_invalid_key_raises` |
| FND-06 | Skipping a level raises rather than guessing | `FinderTest.test_skipped_level_raises` |
| FND-07 | A value absent from the index raises | `FinderTest.test_value_not_in_index_raises` |
| FND-08 | A zone not under the named shard raises | `FinderTest.test_zone_not_in_shard_raises` |
| FND-09 | A missing `index.yaml` raises `FinderManifestError` | `FinderTest.test_missing_index_raises_manifest_error` |

## LDR — `Loader.load(found)`

| ID | Case | Test function |
|---|---|---|
| LDR-01 | A single file's `rules:` list loads | `LoaderTest.test_loads_single_file_with_rules` |
| LDR-02 | A folder location loads every file beneath it, recursively | `LoaderTest.test_loads_folder_recursively` |
| LDR-03 | Non-`rules:` top-level keys land in `file_metadata` | `LoaderTest.test_file_metadata_extracted_when_present` |
| LDR-04 | A file declaring only `rules:` produces no `file_metadata` entry | `LoaderTest.test_file_metadata_absent_when_only_rules_key` |
| LDR-05 | A bare top-level list is rejected | `LoaderTest.test_bare_list_rejected` |
| LDR-06 | A mapping with no `rules:` key is rejected | `LoaderTest.test_top_level_missing_rules_key_rejected` |
| LDR-07 | `rules:` whose value isn't a list is rejected | `LoaderTest.test_rules_value_not_a_list_rejected` |
| LDR-08 | A file the index references but that doesn't exist raises `LoaderMissingEntryError` | `LoaderTest.test_missing_referenced_file_raises_missing_entry` |
| LDR-09 | A folder with no `index.yaml` raises `LoaderMissingIndexError` | `LoaderTest.test_missing_folder_index_raises_missing_index` |

## VS — Validator structure and dispatch

| ID | Case | Test function |
|---|---|---|
| VS-01 | An empty `LoadResult` passes clean | `ValidatorStructureTest.test_empty_load_result_passes_clean` |
| VS-02 | Valid rules pass | `ValidatorStructureTest.test_load_result_with_valid_rules_passes` |
| VS-03 | `seen_ids` starts empty | `ValidatorStructureTest.test_seen_ids_initialised_empty` |
| VS-04 | Tier 3 predicates are excluded by default | `ValidatorStructureTest.test_active_predicates_excludes_tier_3_by_default` |
| VS-05 | Tier 3 predicates are included with `evennia_runtime=True` | `ValidatorStructureTest.test_active_predicates_includes_tier_3_when_engine_flag_set` |
| VS-06 | A finding lands in both `messages` and `errors` | `ValidatorStructureTest.test_record_finding_appends_to_both_messages_and_errors` |
| VS-07 | Accumulated errors raise `ValidatorError` — every finding reported, none half-applied | `ValidatorStructureTest.test_validate_raises_when_errors_accumulated` |
| VS-08 | `LoadedRule` is a frozen dataclass — predicates cannot mutate what they inspect | `ValidatorStructureTest.test_loaded_rule_is_frozen_dataclass` |
| VS-09 | Every Tier 1 predicate returns `None` for a fully-valid rule | `FieldPredicatesHappyPathTest.test_all_predicates_return_none_for_valid_rule` |
| VS-10 | Field predicates short-circuit on a non-dict rule, so one bad rule gives one finding | `FieldPredicatesHappyPathTest.test_field_predicates_short_circuit_on_non_dict` |
| VS-11 | A valid rule passes the full `validate()` path | `ValidatorPredicateIntegrationTest.test_valid_rule_passes_validate` |
| VS-12 | Multiple missing required fields accumulate, rather than stopping at the first | `ValidatorPredicateIntegrationTest.test_rule_missing_multiple_required_fields_accumulates_findings` |
| VS-13 | A non-dict rule produces exactly one clean finding through `validate()` | `ValidatorPredicateIntegrationTest.test_non_dict_rule_produces_one_clean_finding` |

## SHAPE — Tier 1 per-rule predicates

### Rule is a mapping

| ID | Case | Test function |
|---|---|---|
| SHAPE-01 | A dict passes | `RuleMappingPredicateTest.test_dict_passes` |
| SHAPE-02 | A list is rejected | `RuleMappingPredicateTest.test_list_rejected` |
| SHAPE-03 | A string is rejected | `RuleMappingPredicateTest.test_string_rejected` |
| SHAPE-04 | `None` is rejected | `RuleMappingPredicateTest.test_none_rejected` |

### Rule fields

The fields a rule may carry are the values of `RuleField`. Every predicate takes its field name from
the enum.

| ID | Case | Test function |
|---|---|---|
| SHAPE-79 | A key that is not a rule field is refused, and the finding names it | `RuleFieldPredicateTest.test_an_unknown_key_is_refused` |
| SHAPE-80 | Several unknown keys in one rule are each named | `RuleFieldPredicateTest.test_every_unknown_key_is_named` |
| SHAPE-81 | Rules using every field pass, so a field the predicates check cannot be missing from `RuleField` | `RuleFieldPredicateTest.test_rules_using_every_field_pass` |

### Required fields

| ID | Case | Test function |
|---|---|---|
| SHAPE-05 | `rule_id` missing is rejected | `RequiredFieldPredicatesTest.test_rule_id_missing` |
| SHAPE-06 | `rule_id` of the wrong type is rejected | `RequiredFieldPredicatesTest.test_rule_id_wrong_type` |
| SHAPE-07 | `rule_id` as a bool is rejected — `True` is an int in Python and must not slip through | `RequiredFieldPredicatesTest.test_rule_id_bool_rejected` |
| SHAPE-08 | A negative `rule_id` is rejected | `RequiredFieldPredicatesTest.test_rule_id_negative_rejected` |
| SHAPE-09 | `rule_id: 0` is accepted | `RequiredFieldPredicatesTest.test_rule_id_zero_accepted` |
| SHAPE-10 | `typeclass` missing is rejected | `RequiredFieldPredicatesTest.test_typeclass_missing` |
| SHAPE-11 | `typeclass` of the wrong type is rejected | `RequiredFieldPredicatesTest.test_typeclass_wrong_type` |
| SHAPE-12 | An empty `typeclass` is rejected | `RequiredFieldPredicatesTest.test_typeclass_empty_rejected` |
| SHAPE-13 | `key` missing is rejected | `RequiredFieldPredicatesTest.test_key_missing` |
| SHAPE-14 | `key` of the wrong type is rejected | `RequiredFieldPredicatesTest.test_key_wrong_type` |
| SHAPE-15 | An empty `key` is rejected | `RequiredFieldPredicatesTest.test_key_empty_rejected` |
| SHAPE-16 | `area_tag` missing is rejected | `RequiredFieldPredicatesTest.test_area_tag_missing` |
| SHAPE-17 | `area_tag` of the wrong type is rejected | `RequiredFieldPredicatesTest.test_area_tag_wrong_type` |
| SHAPE-18 | An empty `area_tag` is rejected | `RequiredFieldPredicatesTest.test_area_tag_empty_rejected` |
| SHAPE-19 | `target` missing is rejected | `RequiredFieldPredicatesTest.test_target_missing` |
| SHAPE-20 | `target` of the wrong type is rejected | `RequiredFieldPredicatesTest.test_target_wrong_type` |
| SHAPE-21 | `target` as a bool is rejected | `RequiredFieldPredicatesTest.test_target_bool_rejected` |
| SHAPE-22 | `target: 0` is rejected — a population cap of zero is never what an author meant | `RequiredFieldPredicatesTest.test_target_zero_rejected` |
| SHAPE-23 | `max_per_room` missing is rejected | `RequiredFieldPredicatesTest.test_max_per_room_missing` |
| SHAPE-24 | `max_per_room` of the wrong type is rejected | `RequiredFieldPredicatesTest.test_max_per_room_wrong_type` |
| SHAPE-25 | `max_per_room: 0` is rejected — the unlimited-vs-never ambiguity decision #21 removes | `RequiredFieldPredicatesTest.test_max_per_room_zero_rejected` |

### Cooldown exclusivity

| ID | Case | Test function |
|---|---|---|
| SHAPE-26 | `respawn_seconds` alone passes | `CooldownExclusivityPredicateTest.test_only_respawn_seconds_passes` |
| SHAPE-27 | `death_cooldown_seconds` alone passes | `CooldownExclusivityPredicateTest.test_only_death_cooldown_seconds_passes` |
| SHAPE-28 | Both present is rejected | `CooldownExclusivityPredicateTest.test_both_present_rejected` |
| SHAPE-29 | Neither present is rejected | `CooldownExclusivityPredicateTest.test_neither_present_rejected` |
| SHAPE-30 | Exclusivity is a key-presence check, not a value-validity check — the per-field predicate owns value shape | `CooldownExclusivityPredicateTest.test_presence_is_dict_key_level_not_value_validity` |

### Optional numeric fields

| ID | Case | Test function |
|---|---|---|
| SHAPE-31 | `respawn_seconds` absent passes | `OptionalNumericPredicatesTest.test_respawn_seconds_absent_passes` |
| SHAPE-32 | `respawn_seconds` of the wrong type is rejected | `OptionalNumericPredicatesTest.test_respawn_seconds_wrong_type` |
| SHAPE-33 | `respawn_seconds` as a bool is rejected | `OptionalNumericPredicatesTest.test_respawn_seconds_bool_rejected` |
| SHAPE-34 | A negative `respawn_seconds` is rejected | `OptionalNumericPredicatesTest.test_respawn_seconds_negative_rejected` |
| SHAPE-35 | `respawn_seconds: 0` is accepted — no effective cooldown | `OptionalNumericPredicatesTest.test_respawn_seconds_zero_accepted` |
| SHAPE-36 | A float `respawn_seconds` is accepted | `OptionalNumericPredicatesTest.test_respawn_seconds_float_accepted` |
| SHAPE-37 | `death_cooldown_seconds` of the wrong type is rejected | `OptionalNumericPredicatesTest.test_death_cooldown_seconds_wrong_type` |
| SHAPE-38 | A negative `death_cooldown_seconds` is rejected | `OptionalNumericPredicatesTest.test_death_cooldown_seconds_negative_rejected` |

### Optional string fields

| ID | Case | Test function |
|---|---|---|
| SHAPE-39 | `desc` absent passes | `OptionalStringPredicatesTest.test_desc_absent_passes` |
| SHAPE-40 | `desc: ""` is accepted — empty-string-clears-default is permitted | `OptionalStringPredicatesTest.test_desc_empty_string_accepted` |
| SHAPE-41 | `desc` of the wrong type is rejected | `OptionalStringPredicatesTest.test_desc_wrong_type` |
| SHAPE-73 | `pack` absent passes | `OptionalStringPredicatesTest.test_pack_absent_passes` |
| SHAPE-74 | `pack` of the wrong type is rejected | `OptionalStringPredicatesTest.test_pack_wrong_type` |
| SHAPE-75 | An empty `pack` is rejected | `OptionalStringPredicatesTest.test_pack_empty_rejected` |
| SHAPE-76 | `condition` absent passes | `OptionalStringPredicatesTest.test_condition_absent_passes` |
| SHAPE-77 | `condition` of the wrong type is rejected | `OptionalStringPredicatesTest.test_condition_wrong_type` |
| SHAPE-78 | An empty `condition` is rejected | `OptionalStringPredicatesTest.test_condition_empty_rejected` |

Retired IDs, not reused: SHAPE-42..45 (`spawn_with_typeclass`, `den_room_tag`).

### `attrs`

| ID | Case | Test function |
|---|---|---|
| SHAPE-46 | `attrs` absent passes | `OptionalAttrsPredicateTest.test_attrs_absent_passes` |
| SHAPE-47 | An empty `attrs` mapping passes | `OptionalAttrsPredicateTest.test_attrs_empty_dict_passes` |
| SHAPE-48 | `attrs` as a list is rejected | `OptionalAttrsPredicateTest.test_attrs_list_rejected` |
| SHAPE-49 | `attrs` as a string is rejected | `OptionalAttrsPredicateTest.test_attrs_string_rejected` |

### `tags` shape

| ID | Case | Test function |
|---|---|---|
| SHAPE-50 | `tags` absent passes | `TagsFieldShapeTest.test_tags_absent_passes` |
| SHAPE-51 | An empty `tags` list passes | `TagsFieldShapeTest.test_tags_empty_list_passes` |
| SHAPE-52 | A bare string entry passes | `TagsFieldShapeTest.test_tags_bare_string_passes` |
| SHAPE-53 | A dict with `key` only passes | `TagsFieldShapeTest.test_tags_dict_key_only_passes` |
| SHAPE-54 | A dict with `key` and `category` passes | `TagsFieldShapeTest.test_tags_dict_key_and_category_passes` |
| SHAPE-55 | Mixed string and dict entries in one list pass | `TagsFieldShapeTest.test_tags_mixed_shapes_passes` |
| SHAPE-56 | `tags` that isn't a list is rejected | `TagsFieldShapeTest.test_tags_not_a_list_rejected` |
| SHAPE-57 | An entry that is neither string nor dict is rejected | `TagsFieldShapeTest.test_tags_entry_not_str_or_dict_rejected` |
| SHAPE-58 | An empty-string entry is rejected | `TagsFieldShapeTest.test_tags_empty_string_entry_rejected` |
| SHAPE-59 | A dict entry with no `key` is rejected | `TagsFieldShapeTest.test_tags_dict_missing_key_rejected` |
| SHAPE-60 | A dict entry with an empty `key` is rejected | `TagsFieldShapeTest.test_tags_dict_empty_key_rejected` |
| SHAPE-61 | A dict entry with a non-string `key` is rejected | `TagsFieldShapeTest.test_tags_dict_non_string_key_rejected` |
| SHAPE-62 | A dict entry with a non-string `category` is rejected | `TagsFieldShapeTest.test_tags_dict_non_string_category_rejected` |
| SHAPE-63 | A dict entry with an empty `category` is rejected | `TagsFieldShapeTest.test_tags_dict_empty_category_rejected` |
| SHAPE-64 | A dict entry carrying keys beyond `key`/`category` is rejected | `TagsFieldShapeTest.test_tags_dict_extra_keys_rejected` |

### `tags` reserved categories

| ID | Case | Test function |
|---|---|---|
| SHAPE-65 | `tags` absent passes | `TagsReservedCategoryTest.test_tags_absent_passes` |
| SHAPE-66 | A non-reserved category passes | `TagsReservedCategoryTest.test_non_reserved_category_passes` |
| SHAPE-67 | A bare string passes — untyped tags cannot collide with a category | `TagsReservedCategoryTest.test_bare_string_passes` |
| SHAPE-68 | A dict with no `category` passes | `TagsReservedCategoryTest.test_dict_without_category_passes` |
| SHAPE-69 | `mob_spawner_rule` is rejected — spoofing the rule discriminator | `TagsReservedCategoryTest.test_reserved_rule_category_rejected` |
| SHAPE-70 | `mob_spawner_file` is rejected — spoofing the file discriminator | `TagsReservedCategoryTest.test_reserved_file_category_rejected` |
| SHAPE-71 | Any `mob_spawner_*` category is rejected, not just the two in use | `TagsReservedCategoryTest.test_reserved_prefix_anything_rejected` |
| SHAPE-72 | A legal entry before a reserved one still produces the finding | `TagsReservedCategoryTest.test_mixed_legal_and_reserved_rejected_on_first_reserved` |

## UNIQ — Tier 2, `rule_id` unique within a file

| ID | Case | Test function |
|---|---|---|
| UNIQ-01 | Unique IDs within a file pass | `Tier2UniqueRuleIdTest.test_unique_ids_within_file_pass` |
| UNIQ-02 | A duplicate within the same file is flagged | `Tier2UniqueRuleIdTest.test_duplicate_id_within_same_file_flagged` |
| UNIQ-03 | The same ID in two different files passes — `(file, rule_id)` is the identity | `Tier2UniqueRuleIdTest.test_same_id_across_different_files_passes` |
| UNIQ-04 | Three copies of one ID produce two findings, not one | `Tier2UniqueRuleIdTest.test_three_copies_of_same_id_produce_two_findings` |
| UNIQ-05 | Tier 2 is skipped for a rule that failed Tier 1 — bad data doesn't pollute `seen_ids` | `Tier2UniqueRuleIdTest.test_tier_2_skipped_when_rule_fails_tier_1` |
| UNIQ-06 | `seen_ids` is populated after a clean pass | `Tier2UniqueRuleIdTest.test_seen_ids_populated_after_pass` |

## FM — file-level metadata shape

| ID | Case | Test function |
|---|---|---|
| FM-01 | Empty `file_metadata` passes | `FileMetadataShapeTest.test_empty_file_metadata_passes` |
| FM-02 | A mapping value passes | `FileMetadataShapeTest.test_mapping_value_passes` |
| FM-03 | A non-mapping value is rejected | `FileMetadataShapeTest.test_non_mapping_value_rejected` |
| FM-04 | Several bad entries are each flagged | `FileMetadataShapeTest.test_multiple_bad_metadata_entries_all_flagged` |

## RESLV — Tier 3, engine-runtime resolvability

Runs only with `evennia_runtime=True` — `ms_load`, never the CLI.

| ID | Case | Test function |
|---|---|---|
| RESLV-01 | A resolvable `typeclass` passes | `Tier3ResolvabilityTest.test_resolvable_typeclass_passes` |
| RESLV-02 | An unimportable module is flagged | `Tier3ResolvabilityTest.test_typeclass_module_not_importable_flagged` |
| RESLV-03 | A module that imports but has no such class is flagged | `Tier3ResolvabilityTest.test_typeclass_module_loads_but_class_missing` |
| RESLV-04 | A `typeclass` that isn't a dotted path is flagged | `Tier3ResolvabilityTest.test_typeclass_not_a_dotted_path_flagged` |
| RESLV-05 | A path resolving to a non-class is flagged | `Tier3ResolvabilityTest.test_typeclass_resolves_but_is_not_a_class_flagged` |
| RESLV-10 | A callable `ms_at_post_spawn` passes | `Tier3ResolvabilityTest.test_typeclass_with_callable_ms_at_post_spawn_passes` |
| RESLV-11 | A typeclass without the hook passes — the protocol is opt-in | `Tier3ResolvabilityTest.test_typeclass_without_ms_at_post_spawn_passes` |
| RESLV-12 | A non-callable `ms_at_post_spawn` is flagged | `Tier3ResolvabilityTest.test_typeclass_with_non_callable_ms_at_post_spawn_flagged` |
| RESLV-13 | The canonical zero-arg signature passes | `Tier3ResolvabilityTest.test_ms_at_post_spawn_canonical_signature_passes` |
| RESLV-14 | An extra required argument is flagged at validation, not at first spawn | `Tier3ResolvabilityTest.test_ms_at_post_spawn_extra_required_arg_flagged` |
| RESLV-15 | Extra params that are all defaulted pass | `Tier3ResolvabilityTest.test_ms_at_post_spawn_defaulted_args_pass` |
| RESLV-16 | A variadic signature passes | `Tier3ResolvabilityTest.test_ms_at_post_spawn_variadic_signature_passes` |
| RESLV-17 | Tier 3 does not run with `evennia_runtime=False` | `Tier3ResolvabilityTest.test_tier_3_not_run_when_evennia_runtime_false` |
| RESLV-18 | A `condition` naming a registered condition passes | `Tier3ResolvabilityTest.test_registered_condition_passes` |
| RESLV-19 | A `condition` naming no registered condition is flagged, so `ms_load` writes nothing | `Tier3ResolvabilityTest.test_unregistered_condition_flagged` |
| RESLV-20 | `condition` absent passes | `Tier3ResolvabilityTest.test_condition_absent_passes` |

Retired IDs, not reused: RESLV-06..09 (`spawn_with_typeclass`).

## DIAG — Tier 4, deploy-time diagnostics

Diagnostics warn; they never refuse.

| ID | Case | Test function |
|---|---|---|
| DIAG-01 | An `area_tag` with matching rooms produces no warning | `Tier4DiagnosticTest.test_area_tag_with_rooms_no_warning` |
| DIAG-02 | An `area_tag` with zero rooms logs a warning and still deploys | `Tier4DiagnosticTest.test_area_tag_with_zero_rooms_logs_warning` |
| DIAG-05 | Tier 4 does not run with `evennia_runtime=False` | `Tier4DiagnosticTest.test_tier_4_not_run_when_evennia_runtime_false` |
| DIAG-06 | Tier 4 is skipped for a rule that failed Tier 1 | `Tier4DiagnosticTest.test_tier_4_skipped_when_rule_failed_tier_1` |

Retired IDs, not reused: DIAG-03, DIAG-04 (`den_room_tag`).

## GATE — `should_pre_validate`

The 2×2 of the `repo-ci-pre-validation` setting against `--force-validate`.

| ID | Case | Test function |
|---|---|---|
| GATE-01 | Setting false, no flag → pre-validate | `ShouldPreValidateTest.test_setting_false_no_flag_runs_pre_validation` |
| GATE-02 | Setting true, no flag → skip | `ShouldPreValidateTest.test_setting_true_no_flag_skips_pre_validation` |
| GATE-03 | Setting true, flag → pre-validate | `ShouldPreValidateTest.test_setting_true_with_flag_runs_pre_validation` |
| GATE-04 | Setting false, flag → pre-validate | `ShouldPreValidateTest.test_setting_false_with_flag_runs_pre_validation` |
| GATE-05 | An unrelated flag does not trigger the override | `ShouldPreValidateTest.test_unrelated_flags_do_not_trigger` |

## SHARD — instance gates

`evennia-scaling` is an optional integration; every gate must no-op when it is absent. Scaling runs
each instance on its own database, so the gates answer one question: is this the instance the rule set
names? An instance's id is multiplex's `MULTIPLEX_INSTANCE_ID` — scaling mints none of its own — and
its role is scaling's `SCALING_ROLE`, either `shard` or `router`.

The level name stays `shard`, matching scaling's own vocabulary (`SCALING_SHARDS`, `SCALING_ROLE`), so
a consumer's `definitions.yaml` is unchanged.

Retired IDs, not reused: SHARD-02 (`monolith`, a role scaling does not have), SHARD-15..18 and
SHARD-26 (`check_cluster_wide_scope`, deleted with the census), SHARD-24 and SHARD-25 (script-scoped
commands and the census refusal).

### Detection — `active_shard_id`

| ID | Case | Test function |
|---|---|---|
| SHARD-01 | Scaling not installed reads as standalone | `ActiveShardIdTest.test_scaling_not_installed_is_standalone` |
| SHARD-03 | A shard instance reports its own instance id | `ActiveShardIdTest.test_shard_instance_reports_its_instance_id` |
| SHARD-04 | A router instance reports its own instance id | `ActiveShardIdTest.test_router_instance_reports_its_instance_id` |

### Detection — `is_router_process`

The router is refused by role rather than by an id that never matches. Under scaling
`SCALING_ROUTER_ID` is an arbitrary name, so a rule-set folder can be named after it (SHARD-32).

| ID | Case | Test function |
|---|---|---|
| SHARD-29 | Scaling not installed is not a router process | `IsRouterProcessTest.test_scaling_not_installed_is_not_router` |
| SHARD-30 | A shard instance is not a router process | `IsRouterProcessTest.test_shard_instance_is_not_router` |
| SHARD-31 | A router instance is a router process | `IsRouterProcessTest.test_router_instance_is_router` |

### `check_shard_scope` — the `ms_load` gate

| ID | Case | Test function |
|---|---|---|
| SHARD-05 | Standalone allows the `all` scope | `CheckShardScopeTest.test_standalone_allows_all_scope` |
| SHARD-06 | Standalone allows any query | `CheckShardScopeTest.test_standalone_allows_any_query` |
| SHARD-07 | `all` is refused on a scaled deployment — the manifest spans every instance | `CheckShardScopeTest.test_all_scope_refused_when_scaled` |
| SHARD-08 | A query not starting with `shard=` is refused | `CheckShardScopeTest.test_query_not_starting_with_shard_refused` |
| SHARD-09 | `shard` present but not first is refused | `CheckShardScopeTest.test_shard_present_but_not_first_refused` |
| SHARD-10 | A foreign instance is refused | `CheckShardScopeTest.test_foreign_shard_refused` |
| SHARD-11 | The router is refused for a content shard's scope | `CheckShardScopeTest.test_router_refused_for_content_shard` |
| SHARD-32 | The router is refused for a scope naming its own instance id | `CheckShardScopeTest.test_router_refused_for_its_own_instance_id` |
| SHARD-12 | Refusals name no specific instance — the operator isn't sent to a wrong process | `CheckShardScopeTest.test_refusals_name_no_specific_shard` |
| SHARD-13 | The process's own instance is allowed | `CheckShardScopeTest.test_own_shard_allowed` |
| SHARD-14 | The own instance is allowed with a deeper scope | `CheckShardScopeTest.test_own_shard_allowed_with_deeper_scope` |

### `check_shard_levels` — the definitions mandate

| ID | Case | Test function |
|---|---|---|
| SHARD-19 | Standalone accepts any declared levels | `CheckShardLevelsTest.test_standalone_accepts_any_levels` |
| SHARD-20 | `shard` declared first is accepted | `CheckShardLevelsTest.test_shard_first_accepted` |
| SHARD-21 | A consumer keeping their own level names is refused | `CheckShardLevelsTest.test_mandate_not_adopted_refused` |
| SHARD-22 | `shard` declared but not first is refused | `CheckShardLevelsTest.test_shard_declared_but_not_first_refused` |
| SHARD-23 | No declared levels is refused | `CheckShardLevelsTest.test_no_levels_declared_refused` |

### Refusal wording

| ID | Case | Test function |
|---|---|---|
| SHARD-27 | A refusal names the invoking command, not a hard-coded one | `CommandScopeGateTest.test_refusal_names_the_invoking_command` |
| SHARD-28 | The default command key is `ms_load` | `CommandScopeGateTest.test_default_command_key_is_ms_load` |

## MODEL — the rule table

Rules live in the game's default database: they are a copy of the YAML, and `ms_load` rebuilds them.

| ID | Case | Test function |
|---|---|---|
| MODEL-01 | The rule table is in the default database once migrations have run | `SpawnRuleModelTest.test_the_table_is_in_the_default_database` |
| MODEL-02 | `(file, rule_id)` is unique — a second row for the same pair is refused by the database | `SpawnRuleModelTest.test_file_and_rule_id_are_unique_together` |

## DEP — `ms_load` upsert

`ms_load` validates, upserts one row per rule keyed on `(file, rule_id)`, deletes the rows of rules gone
from the files it loaded, and swaps the in-memory table the tick reads.

| ID | Case | Test function |
|---|---|---|
| DEP-05 | Timers survive for rules that survive the load — reloading YAML must not reset a boss cooldown | `DeployerTest.test_state_preserved_for_surviving_rules` |
| DEP-06 | Rows for rules removed from a loaded file are deleted | `DeployerTest.test_state_purged_for_removed_rules` |
| DEP-07 | A new rule starts with no timers | `DeployerTest.test_new_rule_starts_with_no_bookkeeping` |
| DEP-09 | A rule loaded for the first time gets a row | `DeployerTest.test_a_first_load_writes_a_row_per_rule` |
| DEP-10 | Reloading an edited rule updates its row in place — same primary key, so its tick slot does not move | `DeployerTest.test_an_edited_rule_keeps_its_row` |
| DEP-11 | Rows belonging to files outside the load's scope are untouched | `DeployerTest.test_rows_outside_the_scope_are_untouched` |
| DEP-12 | After a load, the next tick runs the new rules, with no restart | `DeployerTest.test_the_next_tick_runs_newly_loaded_rules` |
| DEP-13 | A load refused by validation writes no row and leaves the in-memory table as it was | `DeployerTest.test_a_refused_load_changes_nothing` |

Retired IDs, not reused: DEP-01..04 and DEP-08 (script creation, reuse and swap).

## BOOT — the `at_server_start` helper

The consumer calls it from `at_server_start()`. Calendar registrations are process state, so it runs on
every boot and reload.

| ID | Case | Test function |
|---|---|---|
| BOOT-01 | The helper loads every row into the in-memory table | `BootTest.test_loads_every_row` |
| BOOT-02 | The helper registers the tick with the calendar | `BootTest.test_registers_the_tick_with_the_calendar` |
| BOOT-03 | Calling the helper a second time in one process neither raises nor registers a second tick | `BootTest.test_a_second_call_is_harmless` |
| BOOT-04 | Timers come back from the rows — a boss killed before a restart does not respawn after it until its `death_cooldown_seconds` has run | `BootTest.test_timers_come_back_from_the_rows` |

## TICK — the tick

The calendar's one-second signal drives the tick. Each second it runs the rules whose primary key falls
in that second's slot: `pk % N == second % N`, where `N` is `MOB_SPAWNER_TICK_SECONDS`.

| ID | Case | Test function |
|---|---|---|
| TICK-01 | An empty spawn table does nothing | `TickLoopTest.test_empty_spawn_table_does_nothing` |
| TICK-02 | Below target spawns | `TickLoopTest.test_tick_spawns_when_below_target` |
| TICK-03 | At target skips | `TickLoopTest.test_tick_skips_when_at_target` |
| TICK-04 | `respawn_seconds` blocks an immediate respawn | `TickLoopTest.test_cooldown_blocks_immediate_respawn` |
| TICK-05 | `death_cooldown_seconds` measures from death, not from spawn | `TickLoopTest.test_death_cooldown_uses_death_time_not_spawn_time` |
| TICK-06 | No room carrying the `area_tag` skips silently, with a WARN | `TickLoopTest.test_no_room_with_area_tag_skips_silently` |
| TICK-07 | `max_per_room` is respected | `TickLoopTest.test_max_per_room_respected` |
| TICK-09 | `attrs` are applied to the spawned mob | `TickLoopTest.test_attrs_applied_to_spawned_mob` |
| TICK-10 | A `desc` override is applied | `TickLoopTest.test_desc_override_applied` |
| TICK-11 | The `area_tag` is stamped on the spawned mob | `TickLoopTest.test_area_tag_stamped_on_spawned_mob` |
| TICK-12 | Observed counts are updated after a tick | `TickLoopTest.test_observed_counts_updated_after_tick` |
| TICK-13 | `last_spawn_time` is set after a spawn | `TickLoopTest.test_last_spawn_time_set_after_spawn` |
| TICK-14 | One bad rule does not break the tick — a good rule in the same slot still spawns | `TickLoopTest.test_bad_rule_does_not_break_tick` |
| TICK-15 | The tick's own spawn last pass is not counted as a death — the `spawned_last_tick` term of the detection formula | `TickLoopTest.test_own_spawn_is_not_counted_as_a_death` |
| TICK-16 | A first pass against pre-existing mobs produces no death event (decision #11) | `TickLoopTest.test_first_tick_against_pre_existing_mobs_detects_no_death` |
| TICK-17 | While the spawner is disabled the tick does nothing | `TickLoopTest.test_a_disabled_spawner_does_nothing` |
| TICK-18 | On a given second only the rules in that second's slot run | `TickLoopTest.test_only_the_seconds_slot_runs` |
| TICK-19 | Over `N` consecutive seconds every rule runs exactly once | `TickLoopTest.test_every_rule_runs_once_per_cycle` |
| TICK-20 | A spawn's `last_spawn_time` reaches the rule's row as well as memory | `TickLoopTest.test_a_spawn_time_reaches_the_row` |
| TICK-21 | An observed death's `last_death_time` reaches the rule's row as well as memory | `TickLoopTest.test_a_death_time_reaches_the_row` |
| TICK-22 | The tick reads rules from memory — a row edited directly in the database is not seen until the next load or boot | `TickLoopTest.test_rules_are_read_from_memory` |
| TICK-23 | A rule whose condition answers no does not spawn, and its timers are unchanged | `TickLoopTest.test_a_condition_answering_no_blocks_the_spawn` |
| TICK-24 | A rule whose condition answers yes spawns as normal | `TickLoopTest.test_a_condition_answering_yes_lets_it_spawn` |
| TICK-25 | The condition is called with the rule | `TickLoopTest.test_the_condition_is_called_with_the_rule` |
| TICK-26 | A condition that raises is logged at ERROR and its rule skipped; other rules in the slot still run | `TickLoopTest.test_a_raising_condition_skips_only_its_rule` |

Retired IDs, not reused: TICK-08 (`den_room_tag`).

## PICK — room selection

Pack first, then the area pool. Every step skips a room at `max_per_room` or holding anything with a
session.

| ID | Case | Test function |
|---|---|---|
| PICK-07 | With no pack, a room from the area pool is chosen | `TickLoopTest.test_tick_spawns_when_below_target` |
| PICK-08 | An empty area pool returns no room | `TickLoopTest.test_no_room_with_area_tag_skips_silently` |
| PICK-09 | Every room at `max_per_room` returns no room | `TickLoopTest.test_max_per_room_respected` |
| PICK-10 | With a living pack member in the area, the spawn lands in that member's room | `PickRoomTest.test_a_pack_member_draws_the_spawn_to_its_room` |
| PICK-11 | A member spawned by another rule sharing the pack key counts — the leader need not be alive | `PickRoomTest.test_any_rule_sharing_the_pack_key_counts` |
| PICK-12 | A pack member outside the rule's `area_tag` is not chosen | `PickRoomTest.test_a_pack_member_outside_the_area_is_not_chosen` |
| PICK-13 | With no living pack member, a room from the area pool is chosen | `PickRoomTest.test_no_living_member_uses_the_area_pool` |
| PICK-14 | When every living member's room is full or holds a session, a room from the area pool is chosen | `PickRoomTest.test_a_blocked_member_room_uses_the_area_pool` |
| PICK-15 | A room holding an object with a session is never chosen | `PickRoomTest.test_a_room_with_a_session_is_never_chosen` |
| PICK-16 | Objects without a session — mobs, disconnected characters — do not block a room | `PickRoomTest.test_objects_without_a_session_do_not_block` |
| PICK-17 | When every room in the area holds a session, no room is returned and nothing is logged at WARN | `PickRoomTest.test_every_room_occupied_returns_none_quietly` |

Retired IDs, not reused: PICK-01..06 (`spawn_with_typeclass`, `den_room_tag`).

## COND — the condition registry

The game registers each condition by name at startup; a rule's `condition` names one.

| ID | Case | Test function |
|---|---|---|
| COND-01 | A registered condition is returned by name | `ConditionRegistryTest.test_a_registered_condition_is_found_by_name` |
| COND-02 | Registering a name already in use is refused, never replaced | `ConditionRegistryTest.test_a_name_in_use_is_refused_not_replaced` |

## LOOK — `spawn_rule_for`

Returns a copy of the rule a mob was spawned from, or `None`. Read from the in-memory table by the
mob's identity tags, so it costs no query against the rule table.

| ID | Case | Test function |
|---|---|---|
| LOOK-01 | A mob the spawner produced returns its rule | `SpawnRuleLookupTest.test_a_spawned_mob_returns_its_rule` |
| LOOK-02 | A mob no rule produced returns `None` | `SpawnRuleLookupTest.test_a_mob_no_rule_produced_returns_none` |
| LOOK-03 | A mob whose rule has since been removed returns `None` | `SpawnRuleLookupTest.test_a_removed_rule_returns_none` |
| LOOK-04 | After `ms_load` edits the rule, the lookup returns the edited rule | `SpawnRuleLookupTest.test_an_edited_rule_is_seen_at_once` |
| LOOK-05 | The same `rule_id` in two files resolves to the mob's own file | `SpawnRuleLookupTest.test_the_mobs_own_file_decides` |
| LOOK-06 | Changing the returned rule does not change what the tick runs | `SpawnRuleLookupTest.test_changing_the_result_leaves_the_live_rule_alone` |

## SPAWN — `_spawn_one`

### Identity tags

| ID | Case | Test function |
|---|---|---|
| SPAWN-01 | The spawned mob carries the `rule_id` tag | `SpawnIdentityTagTest.test_spawned_mob_carries_rule_id_tag` |
| SPAWN-02 | The spawned mob carries the source-file tag | `SpawnIdentityTagTest.test_spawned_mob_carries_file_path_tag` |
| SPAWN-03 | The `area_tag` is retained alongside the identity tags | `SpawnIdentityTagTest.test_spawned_mob_retains_area_tag` |
| SPAWN-04 | Two rules in one file stamp distinct rule tags | `SpawnIdentityTagTest.test_two_rules_in_same_file_get_distinct_rule_tags` |
| SPAWN-21 | A rule with `pack` stamps the pack tag on the spawned mob | `SpawnIdentityTagTest.test_a_pack_rule_stamps_the_pack_tag` |

### YAML-declared tags

| ID | Case | Test function |
|---|---|---|
| SPAWN-05 | A bare string entry is stamped untyped | `YamlDeclaredTagsTest.test_bare_string_tag_stamped_untyped` |
| SPAWN-06 | A dict with a category is stamped under it | `YamlDeclaredTagsTest.test_dict_with_category_stamped_correctly` |
| SPAWN-07 | A dict without a category is stamped untyped | `YamlDeclaredTagsTest.test_dict_without_category_stamped_untyped` |
| SPAWN-08 | Mixed shapes in one list are all stamped | `YamlDeclaredTagsTest.test_mixed_shapes_all_stamped` |
| SPAWN-09 | No `tags` field means no extra tags | `YamlDeclaredTagsTest.test_tags_field_absent_no_extra_tags` |

### `attrs` persistence check

| ID | Case | Test function |
|---|---|---|
| SPAWN-10 | A declared `AttributeProperty` reads as persisting | `PersistsAsAttributeTest.test_declared_attribute_property_returns_true` |
| SPAWN-11 | A plain class attribute reads as not persisting | `PersistsAsAttributeTest.test_plain_class_attribute_returns_false` |
| SPAWN-12 | An undeclared name reads as not persisting | `PersistsAsAttributeTest.test_undeclared_name_returns_false` |

### Rollback

| ID | Case | Test function |
|---|---|---|
| SPAWN-13 | A tagging failure leaves no mob behind | `SpawnRollbackTest.test_tagging_failure_leaves_no_mob_behind` |
| SPAWN-14 | An `attrs` failure — later in the sequence — rolls back the same way | `SpawnRollbackTest.test_attrs_failure_leaves_no_mob_behind` |
| SPAWN-15 | The happy path still produces a fully-tagged mob | `SpawnRollbackTest.test_successful_spawn_still_produces_a_tagged_mob` |
| SPAWN-16 | The failure is re-raised so the caller still logs it | `SpawnRollbackTest.test_failure_is_reraised_so_the_caller_still_logs_it` |
| SPAWN-17 | A rollback that itself fails logs ERROR naming the mob left behind, and still re-raises | `SpawnRollbackTest.test_a_rollback_that_itself_fails_is_logged_at_error` |

### `ms_at_post_spawn` invocation

Decision #23's runtime half. Tier 3 validates the hook's shape (`RESLV-10`–`RESLV-16`).

| ID | Case | Test function |
|---|---|---|
| SPAWN-18 | A typeclass declaring the hook has it invoked after the mob is fully built | `MsAtPostSpawnInvocationTest.test_a_declared_hook_is_invoked_after_spawn` |
| SPAWN-19 | A typeclass without the hook is never touched after spawn | `MsAtPostSpawnInvocationTest.test_a_typeclass_without_the_hook_is_never_touched` |
| SPAWN-20 | A hook that raises is caught and logged; the mob survives and is not rolled back | `MsAtPostSpawnInvocationTest.test_a_raising_hook_is_logged_and_the_mob_survives` |

## POP — population contracts

| ID | Case | Test function |
|---|---|---|
| POP-01 | Two rules sharing typeclass and `area_tag` are counted independently | `CountLivingContractTest.test_two_rules_same_typeclass_and_area_tag_counted_independently` |
| POP-02 | The same `rule_id` in two files stays isolated | `CountLivingContractTest.test_rule_id_collision_across_files_isolated` |
| POP-03 | Mobs lacking the identity tags are excluded from the count | `CountLivingContractTest.test_count_excludes_mobs_lacking_identity_tags` |
| POP-04 | `max_per_room` is independent per rule under a shared typeclass and `area_tag` | `RoomHasSpaceContractTest.test_two_rules_share_typeclass_and_area_tag_but_max_per_room_is_independent` |
| POP-05 | `max_per_room` still blocks within a single rule | `RoomHasSpaceContractTest.test_max_per_room_still_blocks_within_a_single_rule` |

## CMD — command surface

`ms_load`'s argument parsing and scope resolution are exercised end to end through `DEP`. What is
covered here is the refusals and the four operating commands. The enabled flag lives in Evennia's
`ServerConfig`.

Retired IDs, not reused: CMD-10, CMD-11 and CMD-14.

| ID | Case | Test function |
|---|---|---|
| CMD-04 | No scope token raises `ValueError` | `ParseArgsTest.test_no_scope_token_is_refused` |
| CMD-05 | A positional token with no `=` raises `ValueError` | `ParseArgsTest.test_a_positional_without_an_equals_is_refused` |
| CMD-06 | An empty key or value either side of `=` raises `ValueError` | `ParseArgsTest.test_an_empty_key_or_value_is_refused` |
| CMD-15 | The installed commands are exactly `ms_load`, `ms_status`, `ms_start`, `ms_stop` and `ms_delete` | `OperatingCommandTest.test_the_installed_commands` |
| CMD-16 | `ms_stop` turns the spawner off and `ms_start` turns it back on | `OperatingCommandTest.test_stop_and_start_toggle_the_spawner` |
| CMD-17 | The enabled flag survives a restart — read back fresh from `ServerConfig` | `OperatingCommandTest.test_the_flag_survives_a_restart` |
| CMD-18 | A fresh database reads as enabled | `OperatingCommandTest.test_a_fresh_database_reads_as_enabled` |
| CMD-19 | `ms_status` shows whether the spawner is enabled, and `N` | `OperatingCommandTest.test_status_shows_the_spawner_state` |
| CMD-20 | `ms_status` lists one row per rule: id, file, `rule_id`, key, `area_tag`, live count and `target` | `OperatingCommandTest.test_status_lists_one_row_per_rule` |
| CMD-21 | The live count `ms_status` shows is the tick's own population count | `OperatingCommandTest.test_the_live_count_is_the_ticks_count` |
| CMD-22 | `ms_delete <id>` deletes that rule's row and drops it from the in-memory table | `OperatingCommandTest.test_delete_removes_the_rule` |
| CMD-23 | `ms_delete` with an id matching no rule is refused, naming the id | `OperatingCommandTest.test_delete_refuses_an_unknown_id` |
| CMD-24 | `ms_delete` with a non-integer id is refused | `OperatingCommandTest.test_delete_refuses_a_non_integer_id` |

## CLI — `ms-validate`

The CLI invariant: it never imports Evennia, so it runs where there is no engine.

| ID | Case | Test function |
|---|---|---|
| CLI-01 | The argument parser builds | `CliScaffoldSmokeTest.test_parser_builds` |
| CLI-02 | A clean run against an empty repo completes, exiting `0` | `CliScaffoldSmokeTest.test_validate_runs_against_empty_repo` |
| CLI-04 | Any finding exits `1`, so a CI gate fails the merge | `CliScaffoldSmokeTest.test_a_finding_exits_non_zero` |

## Settled and reflected above

Gaps in the ID sequences are retired cases. An ID is never reused.

- **The validator refuses whole, never partially.** Every finding is accumulated and reported
  together (VS-07, VS-12); a rule failing Tier 1 is not carried into Tier 2 or Tier 4 (UNIQ-05,
  DIAG-06).
- **Diagnostics warn, predicates refuse.** Tier 4 never blocks a deploy (DIAG-02) — an operator may be
  deploying world content in parallel.
- **Population identity is `(file, rule_id)`.** Counting and `max_per_room` both key on the identity
  tags, never on typeclass or `area_tag` (POP-01..05).
- **One bad rule never takes down a tick, and never leaves an orphan.** Per-rule errors are caught
  and logged (TICK-14, TICK-26); a partial spawn is rolled back and re-raised (SPAWN-13..16).
- **The two halves of the death formula stay disjoint.** `last_observed_counts` holds the headcount
  as observed in step 1; `spawned_last_tick` holds what the pass then added. Step 2 sums them. Folding
  the spawn into both makes the surplus read as a death (TICK-15, and TICK-12 pins the storage).
- **Timers are the only runtime state that persists.** Observed counts live in memory, and a first
  pass after boot detects no death (TICK-16); timers come back from the rows (BOOT-04).
- **A gate that cannot answer refuses rather than guessing.** Instance gates no-op entirely off a
  scaled deployment (SHARD-01, SHARD-05..06, SHARD-19) and refuse without naming an instance they
  cannot verify (SHARD-12).
- **A refused operation changes nothing.** A load that fails validation writes no row (DEP-13);
  `ms_delete` refuses an id it cannot match rather than reporting success (CMD-23).
