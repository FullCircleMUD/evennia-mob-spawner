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
| `SHARD` | Shard gates on the command surface |
| `DEP` | `Deployer` — upsert, state preservation, purge |
| `TICK` | The tick loop (`at_repeat` / `_tick_one_rule`) |
| `PICK` | `_pick_room` — three-tier room selection |
| `SPAWN` | `_spawn_one` — tagging, attrs, hook, rollback |
| `POP` | Population contracts (`_count_living`, `_room_has_space`) |
| `RACE` | Race protocol — `stop_when_safe` / `force_stop` / drain |
| `STALL` | The stalled-script state and the commands that name it |
| `LOAD` | `ms_load`'s stall gate |
| `CMD` | Command surface — argument parsing, scope resolution, install |
| `CLI` | `ms-validate` standalone CLI |

## Fixtures

Most of the suite needs no database. The stages that create Evennia objects (`Deployer`, the tick
loop, `_spawn_one`) run against a real test database — `runtests.py` calls `evennia._init()`, so
`create_object` / `create_script` work without a consumer gamedir.

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
| `_TickLoopFixture` | Builds tagged rooms, deploys a rule, returns the script. A plain helper rather than a mixin so each test constructs exactly the world it needs |
| `_PersistsCheckFixture` | Plain (non-Evennia) class with and without `AttributeProperty` declarations, for `_persists_as_attribute` |

## PKG — package and dependencies

| ID | Case | Test function |
|---|---|---|
| PKG-01 | `__version__` is present and matches the packaged version | `PackageSmokeTest.test_version_present` |
| PKG-02 | `evennia-yaml-reader`'s primitives import in the library venv | `YamlReaderDependencyTest.test_can_import_reader_primitives` |

## LOG — `ms_log`

| ID | Case | Test function |
|---|---|---|
| LOG-01 | Callable at the default level without raising | `LogShimSmokeTest.test_ms_log_callable_at_default_level` |
| LOG-02 | An unknown level is coerced rather than raising | `LogShimSmokeTest.test_ms_log_unknown_level_coerced` |
| LOG-03 | Each of `INFO` / `WARN` / `ERROR` is accepted | `LogShimSmokeTest.test_ms_log_valid_levels` |
| LOG-05 | Silent no-op outside a running Evennia, rather than an import error | `LogShimSmokeTest.test_ms_log_is_a_no_op_without_evennia` |

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
| SHAPE-42 | `spawn_with_typeclass` of the wrong type is rejected | `OptionalStringPredicatesTest.test_spawn_with_typeclass_wrong_type` |
| SHAPE-43 | An empty `spawn_with_typeclass` is rejected | `OptionalStringPredicatesTest.test_spawn_with_typeclass_empty_rejected` |
| SHAPE-44 | `den_room_tag` of the wrong type is rejected | `OptionalStringPredicatesTest.test_den_room_tag_wrong_type` |
| SHAPE-45 | An empty `den_room_tag` is rejected | `OptionalStringPredicatesTest.test_den_room_tag_empty_rejected` |

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
| RESLV-06 | A resolvable `spawn_with_typeclass` passes | `Tier3ResolvabilityTest.test_spawn_with_typeclass_resolvable_passes` |
| RESLV-07 | `spawn_with_typeclass` absent passes | `Tier3ResolvabilityTest.test_spawn_with_typeclass_absent_passes` |
| RESLV-08 | An unimportable `spawn_with_typeclass` module is flagged | `Tier3ResolvabilityTest.test_spawn_with_typeclass_module_not_importable_flagged` |
| RESLV-09 | A `spawn_with_typeclass` resolving to a non-class is flagged | `Tier3ResolvabilityTest.test_spawn_with_typeclass_resolves_but_not_a_class_flagged` |
| RESLV-10 | A callable `ms_at_post_spawn` passes | `Tier3ResolvabilityTest.test_typeclass_with_callable_ms_at_post_spawn_passes` |
| RESLV-11 | A typeclass without the hook passes — the protocol is opt-in | `Tier3ResolvabilityTest.test_typeclass_without_ms_at_post_spawn_passes` |
| RESLV-12 | A non-callable `ms_at_post_spawn` is flagged | `Tier3ResolvabilityTest.test_typeclass_with_non_callable_ms_at_post_spawn_flagged` |
| RESLV-13 | The canonical zero-arg signature passes | `Tier3ResolvabilityTest.test_ms_at_post_spawn_canonical_signature_passes` |
| RESLV-14 | An extra required argument is flagged at validation, not at first spawn | `Tier3ResolvabilityTest.test_ms_at_post_spawn_extra_required_arg_flagged` |
| RESLV-15 | Extra params that are all defaulted pass | `Tier3ResolvabilityTest.test_ms_at_post_spawn_defaulted_args_pass` |
| RESLV-16 | A variadic signature passes | `Tier3ResolvabilityTest.test_ms_at_post_spawn_variadic_signature_passes` |
| RESLV-17 | Tier 3 does not run with `evennia_runtime=False` | `Tier3ResolvabilityTest.test_tier_3_not_run_when_evennia_runtime_false` |

## DIAG — Tier 4, deploy-time diagnostics

Diagnostics warn; they never refuse.

| ID | Case | Test function |
|---|---|---|
| DIAG-01 | An `area_tag` with matching rooms produces no warning | `Tier4DiagnosticTest.test_area_tag_with_rooms_no_warning` |
| DIAG-02 | An `area_tag` with zero rooms logs a warning and still deploys | `Tier4DiagnosticTest.test_area_tag_with_zero_rooms_logs_warning` |
| DIAG-03 | `den_room_tag` absent produces no warning | `Tier4DiagnosticTest.test_den_room_tag_absent_no_warning` |
| DIAG-04 | A `den_room_tag` with zero rooms logs a warning | `Tier4DiagnosticTest.test_den_room_tag_with_zero_rooms_logs_warning` |
| DIAG-05 | Tier 4 does not run with `evennia_runtime=False` | `Tier4DiagnosticTest.test_tier_4_not_run_when_evennia_runtime_false` |
| DIAG-06 | Tier 4 is skipped for a rule that failed Tier 1 | `Tier4DiagnosticTest.test_tier_4_skipped_when_rule_failed_tier_1` |

## GATE — `should_pre_validate`

The 2×2 of the `repo-ci-pre-validation` setting against `--force-validate`.

| ID | Case | Test function |
|---|---|---|
| GATE-01 | Setting false, no flag → pre-validate | `ShouldPreValidateTest.test_setting_false_no_flag_runs_pre_validation` |
| GATE-02 | Setting true, no flag → skip | `ShouldPreValidateTest.test_setting_true_no_flag_skips_pre_validation` |
| GATE-03 | Setting true, flag → pre-validate | `ShouldPreValidateTest.test_setting_true_with_flag_runs_pre_validation` |
| GATE-04 | Setting false, flag → pre-validate | `ShouldPreValidateTest.test_setting_false_with_flag_runs_pre_validation` |
| GATE-05 | An unrelated flag does not trigger the override | `ShouldPreValidateTest.test_unrelated_flags_do_not_trigger` |

## SHARD — shard gates

`evennia-shards` is an optional integration; every gate must no-op when it is absent.

### Detection

| ID | Case | Test function |
|---|---|---|
| SHARD-01 | Shards not installed reads as not sharded | `ActiveShardIdTest.test_shards_not_installed_is_not_sharded` |
| SHARD-02 | The `monolith` role reads as not sharded — installed is not enough | `ActiveShardIdTest.test_monolith_role_is_not_sharded` |
| SHARD-03 | A shard role reports its own shard id | `ActiveShardIdTest.test_shard_role_reports_its_shard_id` |
| SHARD-04 | The router reports its own id, so it fails a shard match without a role check | `ActiveShardIdTest.test_router_role_reports_its_own_id` |

### `check_shard_scope` — the `ms_load` gate

| ID | Case | Test function |
|---|---|---|
| SHARD-05 | Unsharded allows the `all` scope | `CheckShardScopeTest.test_unsharded_allows_all_scope` |
| SHARD-06 | Unsharded allows any query | `CheckShardScopeTest.test_unsharded_allows_any_query` |
| SHARD-07 | `all` is refused on a sharded deployment | `CheckShardScopeTest.test_all_scope_refused_when_sharded` |
| SHARD-08 | A query not starting with `shard=` is refused | `CheckShardScopeTest.test_query_not_starting_with_shard_refused` |
| SHARD-09 | `shard` present but not first is refused | `CheckShardScopeTest.test_shard_present_but_not_first_refused` |
| SHARD-10 | A foreign shard is refused | `CheckShardScopeTest.test_foreign_shard_refused` |
| SHARD-11 | The router is refused for a content shard's scope | `CheckShardScopeTest.test_router_refused_for_content_shard` |
| SHARD-12 | Refusals name no specific shard — the operator isn't sent to a wrong process | `CheckShardScopeTest.test_refusals_name_no_specific_shard` |
| SHARD-13 | The process's own shard is allowed | `CheckShardScopeTest.test_own_shard_allowed` |
| SHARD-14 | The own shard is allowed with a deeper scope | `CheckShardScopeTest.test_own_shard_allowed_with_deeper_scope` |

### `check_cluster_wide_scope` — the census gate

| ID | Case | Test function |
|---|---|---|
| SHARD-15 | A shard is refused — it can only see its own share | `CheckClusterWideScopeTest.test_shard_is_refused` |
| SHARD-16 | The router is allowed | `CheckClusterWideScopeTest.test_router_is_allowed` |
| SHARD-17 | Monolith is allowed | `CheckClusterWideScopeTest.test_monolith_is_allowed` |
| SHARD-18 | Standalone is allowed | `CheckClusterWideScopeTest.test_standalone_is_allowed` |

### `check_shard_levels` — the definitions mandate

| ID | Case | Test function |
|---|---|---|
| SHARD-19 | Unsharded accepts any declared levels | `CheckShardLevelsTest.test_unsharded_accepts_any_levels` |
| SHARD-20 | `shard` declared first is accepted | `CheckShardLevelsTest.test_shard_first_accepted` |
| SHARD-21 | A consumer keeping their own level names is refused | `CheckShardLevelsTest.test_mandate_not_adopted_refused` |
| SHARD-22 | `shard` declared but not first is refused | `CheckShardLevelsTest.test_shard_declared_but_not_first_refused` |
| SHARD-23 | No declared levels is refused | `CheckShardLevelsTest.test_no_levels_declared_refused` |

### Which command carries which gate

| ID | Case | Test function |
|---|---|---|
| SHARD-24 | Commands touching the live task are shard-scoped | `CommandScopeGateTest.test_commands_touching_the_live_task_are_shard_scoped` |
| SHARD-25 | The census is not shard-scoped | `CommandScopeGateTest.test_the_census_is_not_shard_scoped` |
| SHARD-26 | Only the census is cluster-wide | `CommandScopeGateTest.test_only_the_census_is_cluster_wide` |
| SHARD-27 | A refusal names the invoking command, not a hard-coded one | `CommandScopeGateTest.test_refusal_names_the_invoking_command` |
| SHARD-28 | The default command key is `ms_load` | `CommandScopeGateTest.test_default_command_key_is_ms_load` |

## DEP — Deployer upsert

| ID | Case | Test function |
|---|---|---|
| DEP-01 | Deploy creates a script when none exists | `DeployerTest.test_deploy_creates_new_script` |
| DEP-02 | Separate files get separate scripts | `DeployerTest.test_deploy_separate_files_creates_separate_scripts` |
| DEP-03 | Re-deploy reuses the existing script rather than duplicating it | `DeployerTest.test_redeploy_reuses_existing_script` |
| DEP-04 | The swap replaces `db.spawn_table` | `DeployerTest.test_swap_replaces_spawn_table` |
| DEP-05 | Bookkeeping survives for rules that survive the swap — reloading YAML must not reset a boss cooldown | `DeployerTest.test_state_preserved_for_surviving_rules` |
| DEP-06 | Bookkeeping is purged for rules removed from YAML | `DeployerTest.test_state_purged_for_removed_rules` |
| DEP-07 | A new rule starts with no bookkeeping | `DeployerTest.test_new_rule_starts_with_no_bookkeeping` |
| DEP-08 | An empty `LoadResult` creates no scripts | `DeployerTest.test_empty_load_result_creates_no_scripts` |

## TICK — the tick loop

| ID | Case | Test function |
|---|---|---|
| TICK-01 | An empty spawn table does nothing | `TickLoopTest.test_empty_spawn_table_does_nothing` |
| TICK-02 | Below target spawns | `TickLoopTest.test_tick_spawns_when_below_target` |
| TICK-03 | At target skips | `TickLoopTest.test_tick_skips_when_at_target` |
| TICK-04 | `respawn_seconds` blocks an immediate respawn | `TickLoopTest.test_cooldown_blocks_immediate_respawn` |
| TICK-05 | `death_cooldown_seconds` measures from death, not from spawn | `TickLoopTest.test_death_cooldown_uses_death_time_not_spawn_time` |
| TICK-06 | No room carrying the `area_tag` skips silently, with a WARN | `TickLoopTest.test_no_room_with_area_tag_skips_silently` |
| TICK-07 | `max_per_room` is respected | `TickLoopTest.test_max_per_room_respected` |
| TICK-08 | `den_room_tag` selects the den room | `TickLoopTest.test_den_room_tag_used_when_present` |
| TICK-09 | `attrs` are applied to the spawned mob | `TickLoopTest.test_attrs_applied_to_spawned_mob` |
| TICK-10 | A `desc` override is applied | `TickLoopTest.test_desc_override_applied` |
| TICK-11 | The `area_tag` is stamped on the spawned mob | `TickLoopTest.test_area_tag_stamped_on_spawned_mob` |
| TICK-12 | Observed counts are updated after a tick | `TickLoopTest.test_observed_counts_updated_after_tick` |
| TICK-13 | `last_spawn_time` is set after a spawn | `TickLoopTest.test_last_spawn_time_set_after_spawn` |
| TICK-14 | One bad rule does not break the tick — a co-deployed good rule still spawns | `TickLoopTest.test_bad_rule_does_not_break_tick` |
| TICK-15 | The script's own spawn last tick is not counted as a death — the `spawned_last_tick` term of the detection formula | `TickLoopTest.test_own_spawn_is_not_counted_as_a_death` |
| TICK-16 | A first tick against pre-existing mobs produces no death event (decision #11) | `TickLoopTest.test_first_tick_against_pre_existing_mobs_detects_no_death` |

## PICK — room selection

Three-tier fallback, decision #22. The den and random steps are reached today through `TICK`; the
pack step and the fall-through edges are not.

| ID | Case | Test function |
|---|---|---|
| PICK-01 | With a living leader in the area, the spawn lands in the leader's room | `PickRoomTest.test_pack_step_returns_the_leaders_room` |
| PICK-02 | No living leader falls through to the next step | `PickRoomTest.test_no_living_leader_falls_through` |
| PICK-03 | A leader whose room is at `max_per_room` falls through | `PickRoomTest.test_leader_room_at_max_per_room_falls_through` |
| PICK-04 | A leader outside the rule's `area_tag` is not chosen | `PickRoomTest.test_leader_outside_the_area_tag_is_not_chosen` |
| PICK-05 | The den room is chosen when tagged and not full | `TickLoopTest.test_den_room_tag_used_when_present` |
| PICK-06 | A full den falls through to a random room in the area | `PickRoomTest.test_full_den_falls_through_to_the_area_pool` |
| PICK-07 | With neither pack nor den, a room from the area pool is chosen | `TickLoopTest.test_tick_spawns_when_below_target` |
| PICK-08 | An empty area pool returns no room | `TickLoopTest.test_no_room_with_area_tag_skips_silently` |
| PICK-09 | Every room at `max_per_room` returns no room | `TickLoopTest.test_max_per_room_respected` |

## SPAWN — `_spawn_one`

### Identity tags

| ID | Case | Test function |
|---|---|---|
| SPAWN-01 | The spawned mob carries the `rule_id` tag | `SpawnIdentityTagTest.test_spawned_mob_carries_rule_id_tag` |
| SPAWN-02 | The spawned mob carries the source-file tag | `SpawnIdentityTagTest.test_spawned_mob_carries_file_path_tag` |
| SPAWN-03 | The `area_tag` is retained alongside the identity tags | `SpawnIdentityTagTest.test_spawned_mob_retains_area_tag` |
| SPAWN-04 | Two rules in one file stamp distinct rule tags | `SpawnIdentityTagTest.test_two_rules_in_same_file_get_distinct_rule_tags` |

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

Decision #23's runtime half. Tier 3 validates the hook's shape (`RESLV-10`–`RESLV-16`); nothing yet
exercises the call.

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

## RACE — race protocol

| ID | Case | Test function |
|---|---|---|
| RACE-01 | A tick sets and clears `_tick_in_progress` — the `try/finally` discipline | `RaceProtocolTest.test_tick_sets_and_clears_tick_in_progress` |
| RACE-02 | A stop requested before the tick exits immediately, writing no state | `RaceProtocolTest.test_tick_exits_early_when_stop_requested_at_start` |
| RACE-03 | A stop requested mid-loop breaks between rules, not mid-rule | `RaceProtocolTest.test_tick_exits_early_between_rules_when_stop_requested` |
| RACE-04 | `stop_when_safe` returns True immediately for an idle script | `RaceProtocolTest.test_stop_when_safe_returns_true_for_idle_script` |
| RACE-05 | `stop_when_safe` is a no-op on an already-paused script | `RaceProtocolTest.test_stop_when_safe_returns_true_for_already_paused_script` |
| RACE-06 | `stop_when_safe` clears the stop flag on success, so post-resume ticks run | `RaceProtocolTest.test_stop_when_safe_clears_stop_flag_on_success` |
| RACE-07 | `force_stop` pauses an active script | `RaceProtocolTest.test_force_stop_pauses_active_script` |
| RACE-08 | `force_stop` sets the stop flag | `RaceProtocolTest.test_force_stop_sets_stop_flag` |
| RACE-09 | `force_stop` is idempotent on an already-paused script | `RaceProtocolTest.test_force_stop_idempotent_on_already_paused_script` |
| RACE-10 | The Deployer resumes a script that was running before the swap | `RaceProtocolTest.test_deployer_resumes_running_script_after_swap` |
| RACE-11 | The Deployer does not resume a script the operator had paused | `RaceProtocolTest.test_deployer_does_not_resume_paused_script` |

## STALL — the stalled state

Decision #26. A script marked active carrying no live tick.

### Detection

| ID | Case | Test function |
|---|---|---|
| STALL-01 | Active with no task is stalled | `StalledScriptTest.test_active_without_a_task_is_stalled` |
| STALL-02 | Active with a task is not stalled | `StalledScriptTest.test_active_with_a_task_is_not_stalled` |
| STALL-03 | A paused script is not stalled — someone meant it | `StalledScriptTest.test_paused_script_is_not_stalled` |
| STALL-04 | A stopped script is not stalled | `StalledScriptTest.test_stopped_script_is_not_stalled` |

### `ms_stop`

| ID | Case | Test function |
|---|---|---|
| STALL-05 | Reports the stall rather than claiming a pause | `StalledScriptTest.test_ms_stop_reports_the_stall_rather_than_success` |
| STALL-06 | Leaves a stalled script untouched | `StalledScriptTest.test_ms_stop_leaves_a_stalled_script_untouched` |
| STALL-07 | Still pauses a genuinely running script | `StalledScriptTest.test_ms_stop_still_pauses_a_running_script` |
| STALL-08 | Reports a paused script as unchanged | `StalledScriptTest.test_ms_stop_reports_a_paused_script_unchanged` |
| STALL-09 | Reports a stopped script as unchanged | `StalledScriptTest.test_ms_stop_reports_a_stopped_script_unchanged` |

### `ms_restart`

| ID | Case | Test function |
|---|---|---|
| STALL-10 | Recovers a stalled script | `StalledScriptTest.test_ms_restart_recovers_a_stalled_script` |
| STALL-11 | Names the stall, distinct from an ordinary stop | `StalledScriptTest.test_ms_restart_names_the_stall_apart_from_a_stop` |
| STALL-12 | Leaves a healthy running script alone | `StalledScriptTest.test_ms_restart_leaves_a_running_script_alone` |
| STALL-13 | Still unpauses a paused script | `StalledScriptTest.test_ms_restart_still_unpauses_a_paused_script` |
| STALL-14 | Still starts a stopped script | `StalledScriptTest.test_ms_restart_still_starts_a_stopped_script` |
| STALL-15 | Reports a start that did not take — a failed recovery is not reported as success | `StalledScriptTest.test_ms_restart_reports_a_start_that_did_not_take` |
| STALL-16 | Says nothing extra when the tick attaches cleanly | `StalledScriptTest.test_ms_restart_says_nothing_extra_when_the_tick_attaches` |
| STALL-17 | Reports an unpause that did not take | `StalledScriptTest.test_ms_restart_reports_an_unpause_that_did_not_take` |

### Logging

| ID | Case | Test function |
|---|---|---|
| STALL-18 | `ms_stop` logs the stall at WARN | `StalledScriptTest.test_ms_stop_logs_the_stall_at_warn` |
| STALL-19 | `ms_restart` logs the stall at WARN | `StalledScriptTest.test_ms_restart_logs_the_stall_at_warn` |
| STALL-20 | A normal stop is not logged at WARN | `StalledScriptTest.test_a_normal_stop_is_not_logged_at_warn` |

### `ms_status`

| ID | Case | Test function |
|---|---|---|
| STALL-21 | Names a stalled script — the state word, not an absent `next=`, is the tell | `StalledScriptTest.test_ms_status_names_a_stalled_script` |
| STALL-22 | Gives a stalled script no `next=` estimate | `StalledScriptTest.test_ms_status_gives_a_stalled_script_no_next_tick` |
| STALL-23 | Reports a ticking script as active | `StalledScriptTest.test_ms_status_reports_a_ticking_script_as_active` |
| STALL-24 | Reports a paused script as paused | `StalledScriptTest.test_ms_status_reports_a_paused_script_as_paused` |
| STALL-25 | Reports a stopped script as stopped | `StalledScriptTest.test_ms_status_reports_a_stopped_script_as_stopped` |
| STALL-26 | A stalled script reads red | `StalledScriptTest.test_a_stalled_script_reads_red` |
| STALL-27 | A ticking script reads green | `StalledScriptTest.test_a_ticking_script_reads_green` |
| STALL-28 | A paused script reads yellow | `StalledScriptTest.test_a_paused_script_reads_yellow` |
| STALL-29 | Only the state word is coloured, so a list scans | `StalledScriptTest.test_only_the_state_word_is_coloured` |

## LOAD — `ms_load`'s stall gate

| ID | Case | Test function |
|---|---|---|
| LOAD-01 | A healthy scope is not refused | `MsLoadStallGateTest.test_a_healthy_scope_is_not_refused` |
| LOAD-02 | A scope with no scripts yet is not refused | `MsLoadStallGateTest.test_a_scope_with_no_scripts_yet_is_not_refused` |
| LOAD-03 | A stall outside the scope is not refused | `MsLoadStallGateTest.test_a_stall_outside_the_scope_is_not_refused` |
| LOAD-04 | A stalled script refuses the load | `MsLoadStallGateTest.test_a_stalled_script_refuses_the_load` |
| LOAD-05 | Every stalled script is named, so one restart clears them all | `MsLoadStallGateTest.test_every_stalled_script_is_named` |
| LOAD-06 | One stalled script blocks the whole scope — a partial deploy reported as complete is the failure being guarded against | `MsLoadStallGateTest.test_one_stalled_script_blocks_the_whole_scope` |
| LOAD-07 | The refusal points at `ms_restart` over the same scope | `MsLoadStallGateTest.test_refusal_points_at_ms_restart_over_the_same_scope` |
| LOAD-08 | A whole-repo scope refusal points at a whole-repo restart | `MsLoadStallGateTest.test_refusal_on_a_whole_repo_scope_points_at_restart_all` |
| LOAD-09 | The refusal names `ms_load` as the command to re-run | `MsLoadStallGateTest.test_refusal_names_ms_load_as_the_command_to_re_run` |

## CMD — command surface

Argument parsing and scope resolution run on every command, so their happy paths are exercised
end to end through `LOAD` and `STALL`. What is covered here is the refusals — the only thing standing
between an operator typo and a traceback — and the two commands whose `apply` body nothing else
reaches.

| ID | Case | Test function |
|---|---|---|
| CMD-04 | No scope token raises `ValueError` | `ParseArgsTest.test_no_scope_token_is_refused` |
| CMD-05 | A positional token with no `=` raises `ValueError` | `ParseArgsTest.test_a_positional_without_an_equals_is_refused` |
| CMD-06 | An empty key or value either side of `=` raises `ValueError` | `ParseArgsTest.test_an_empty_key_or_value_is_refused` |
| CMD-10 | `ms_delete` removes the script row entirely | `OperateCommandBehaviourTest.test_ms_delete_removes_the_script_row` |
| CMD-11 | `ms_spawn_report` lists current-vs-target living counts per rule | `OperateCommandBehaviourTest.test_ms_spawn_report_gives_current_against_target_per_rule` |
| CMD-14 | `ms_spawn_report` says so for a script holding no rules, rather than reporting nothing | `OperateCommandBehaviourTest.test_ms_spawn_report_says_so_when_a_script_holds_no_rules` |

## CLI — `ms-validate`

The CLI invariant: it never imports Evennia, so it runs where there is no engine.

| ID | Case | Test function |
|---|---|---|
| CLI-01 | The argument parser builds | `CliScaffoldSmokeTest.test_parser_builds` |
| CLI-02 | A clean run against an empty repo completes, exiting `0` | `CliScaffoldSmokeTest.test_validate_runs_against_empty_repo` |
| CLI-04 | Any finding exits `1`, so a CI gate fails the merge | `CliScaffoldSmokeTest.test_a_finding_exits_non_zero` |

## Open decisions

- **`at_server_start` helper.** Not yet implemented; no cases allocated. Follows
  [architecture.md](architecture.md) § Open questions.

Gaps in the ID sequences are retired cases — checks that asserted a constant against itself, or
re-covered ground `LOAD` / `STALL` already walk end to end. An ID is never reused.

Settled and reflected above:

- **The validator refuses whole, never partially.** Every finding is accumulated and reported
  together (VS-07, VS-12); a rule failing Tier 1 is not carried into Tier 2 or Tier 4 (UNIQ-05,
  DIAG-06).
- **Diagnostics warn, predicates refuse.** Tier 4 never blocks a deploy (DIAG-02, DIAG-04) — an
  operator may be deploying world content in parallel.
- **Population identity is `(file, rule_id)`.** Counting and `max_per_room` both key on the identity
  tags, never on typeclass or `area_tag` (POP-01..05).
- **One bad rule never takes down a tick, and never leaves an orphan.** Per-rule errors are caught
  and logged (TICK-14); a partial spawn is rolled back and re-raised (SPAWN-13..16).
- **The two halves of the death formula stay disjoint.** `last_observed_counts` holds the headcount
  as observed in step 1; `spawned_last_tick` holds what the tick then added. Step 2 sums them. Folding
  the spawn into both makes the surplus read as a death (TICK-15, and TICK-12 pins the storage).
- **A gate that cannot answer refuses rather than guessing.** Shard gates no-op entirely off a
  sharded deployment (SHARD-01..02, SHARD-05..06, SHARD-19) and refuse without naming a shard they
  cannot verify (SHARD-12).
- **A failed operation is never reported as success.** The stalled state is named by every command
  that touches it, and a recovery that did not take is reported as such (STALL-05, STALL-15,
  STALL-17, LOAD-04).
