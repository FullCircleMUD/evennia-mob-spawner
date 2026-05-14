# Progress

Running log of milestones with links to evidence. Reverse chronological — newest first.

## 2026-05-14 (night — latest)

**Tier 2 `rule_id` uniqueness landed; file-level shape pass body landed. Non-runtime validation surface is complete.**

Implementation:

- **`_check_and_record_unique_rule_id` (Tier 2)** — body filled in. Stateful per-file check: `seen_ids: {file_path: set[rule_id]}` accumulated as the per-rule loop walks. Each rule's `rule_id` is checked against the set for its file; duplicate → finding, else → recorded. Runs only on rules that passed Tier 1 (existing dispatch discipline — bad data doesn't pollute the state). Per-file uniqueness only; cross-file ID reuse is permitted by design.
- **`_check_file_metadata_shape` (file-level)** — body filled in. Iterates `load_result.file_metadata.items()`, refuses any value that isn't a mapping. The Loader's own output always satisfies this; the check defends `LoadResult` against direct construction with malformed metadata (tests, future callers). No specific file-level keys recognised at v0 — when per-file directives are pinned, their per-key shape checks slot into this method (parallel to world-builder's `_check_incoming_exits_shape` / `_check_links_shape`).
- **10 new tests** across two classes:
  - `Tier2UniqueRuleIdTest` (6) — unique-ids-pass, same-file-duplicate-flagged, cross-file-same-id-passes, three-copies-of-same-id-produce-two-findings, Tier-2-skipped-when-rule-fails-Tier-1, `seen_ids` populated correctly after pass.
  - `FileMetadataShapeTest` (4) — empty file_metadata passes, mapping value passes, non-mapping rejected, multiple bad entries each flagged.
- **111 tests green** (up from 101).

**Non-runtime validation surface is now complete:** Tier 1 (16 stateless per-rule predicates), Tier 2 (rule_id uniqueness), file-level shape check. The `ms-validate` CLI can now flag every authoring error that doesn't require an Evennia engine. The only remaining Validator work is Tier 3 (engine-runtime importability of `typeclass`, `post_spawn_hook`, `spawn_with_typeclass`), which needs the engine and so lands later as part of `ms_load`.

Next stages:
1. Tier 3: `_check_typeclass_resolvable`, `_check_post_spawn_hook_resolvable`, `_check_spawn_with_typeclass_resolvable` — engine-runtime importability via `importlib.import_module` (mirrors world-builder's `_resolve_typeclass` + `_check_typeclass_resolvable`).
2. Deployer / upsert pass: the terminal stage that distinguishes this library from world-builder. Per-rule-set-file persistent Script lookup-or-create, replace `db.spawn_table`, preserve `last_spawn_times` / `last_death_times` / observation history (decision #6's "upsert with state preservation, not clean+rebuild" — the load-bearing distinction).
3. `ms_load` admin command — the operator entry point that wires the gating helper, the validator's `evennia_runtime=True` invocation, and the deployer together (decision #13's race protocol).

## 2026-05-14 (late evening)

**Schema calibrated against consumer baseline; `max_per_room` promoted to required; cooldown exclusivity predicate landed.**

The mandatory-vs-optional split was reviewed against the existing FCM `world/spawns/*.json` corpus (87 production rules across 10 zone files) and the consumer's `zone_spawn_script.py`. Findings:

- `typeclass`, `key`, `area_tag`, `target`, `max_per_room`, `desc` appear in **100%** of consumer rules.
- Exactly one of `respawn_seconds` / `death_cooldown_seconds` appears in **every** rule — never both, never neither.
- The remaining "optional" fields (`attrs`, `den_room_tag`, `post_spawn_hook`, `spawn_with_typeclass`) appear in 9–26% of rules; truly optional by both consumer and library standards.

Resolved as a result:

- **`max_per_room` promoted from optional to required (>= 1).** The consumer's script defaults to 0 (= unlimited) if absent, but 0 is also "never spawn" in the library — the silent-vs-unlimited ambiguity is exactly why we want authored values. Empirical: 87/87 consumer rules set it. Architecture.md open-question TBD on default removed.
- **Cooldown pair semantics tightened from "one or the other" to "exactly one of, never both, never neither".** The architecture-decision phrasing was ambiguous; the consumer data resolves it definitively. New `_check_cooldown_exclusivity` cross-field predicate added (Tier 1 — pure dict-key presence check; value validity stays the per-field shape predicate's concern).
- **`desc: ""` left allowed by predicate.** Discussed: empty-string-clears-default vs. typo-likely-refuse. User confirmed keeping the permissive shape — empty-string is rare but harmless.

Implementation:

- `_check_max_per_room_well_formed` flipped: removed the absent-short-circuit, added "missing required field" branch. Existing `>=1` / non-bool / int-type checks unchanged.
- `_check_cooldown_exclusivity` predicate added — dict-key presence check, refuses both-present and neither-present, accepts exactly-one. Wired into `PER_RULE_PREDICATES` alongside the two cooldown shape predicates.
- 5 new tests in `CooldownExclusivityPredicateTest` (only-respawn, only-death, both-rejected, neither-rejected, presence-is-dict-key-level). `max_per_room` tests moved from `OptionalNumericPredicatesTest` to `RequiredFieldPredicatesTest` and gained a "missing" case. Integration test that counted findings on an empty rule updated from 5 → 7 expected (added: `max_per_room` missing, cooldown exclusivity).
- One new decision added to [architecture.md](architecture.md), bringing the count to 21:

  21. **Mandatory-vs-optional follows the consumer baseline.** Required-field set is calibrated against the FCM production spawn JSON corpus; the principle is "mandatory there → mandatory here; absent there → not yet needed here." `rule_id` is the lone exception (new in the library, replaces the consumer's `{typeclass}:{area_tag}` bookkeeping handle).

- Rule schema table in architecture.md expanded — each field now carries a brief description of what it does, in addition to required-or-optional and type constraints.
- **101 tests green** (up from 96).

**One follow-up decision pinned in conversation** (added as decision #22 in [architecture.md](architecture.md)): room-selection patterns layer rather than mutex. A rule may declare any combination of `spawn_with_typeclass`, `den_room_tag`, and the implicit random-area default; the algorithm walks them in fixed order and uses the first that yields an eligible room. Mixing is the intentional way to author "prefer pack-spawn, fall back to den, fall back to random" choreography. No mutex predicate; current behaviour is correct as-is. Resolves the open question that came up during the consumer-baseline audit.

Next stages, in order:
1. Tier 2: `_check_and_record_unique_rule_id` body — currently a stub. Stateful per-file uniqueness check.
2. File-level: `_check_file_metadata_shape` body — "mapping if present", and any specific keys if pinned.
3. Tier 3: `_check_typeclass_resolvable`, `_check_post_spawn_hook_resolvable`, `_check_spawn_with_typeclass_resolvable` — engine-runtime importability.

## 2026-05-14 (evening)

**Tier 1 predicates landed for the v0 rule schema — exists + well-formed.**

Implementation status:

- **14 Tier 1 predicates** wired into `PER_RULE_PREDICATES`, covering "x exists" for required fields and "x is well-formed" for required + optional fields:
  - **Mapping check (1):** `_check_rule_is_mapping` — clean upfront finding for non-dict rules; the other 13 predicates short-circuit to None on non-mappings so the operator sees one message, not 14. A small UX departure from world-builder's defer-cleanly cascade.
  - **Required (5):** `_check_rule_id_well_formed` (int ≥ 0, bool excluded), `_check_typeclass_well_formed` (non-empty string), `_check_key_well_formed` (non-empty string), `_check_area_tag_well_formed` (non-empty string), `_check_target_well_formed` (int ≥ 1, bool excluded — `target: 0` would silence the rule by typo).
  - **Optional numeric (3):** `_check_respawn_seconds_well_formed`, `_check_death_cooldown_seconds_well_formed` (number ≥ 0; 0 means "no cooldown" — meaningful config), `_check_max_per_room_well_formed` (int ≥ 1; 0 silences the rule).
  - **Optional string (4):** `_check_desc_well_formed` (string, empty allowed — `desc: ""` is the override-to-empty semantic), `_check_post_spawn_hook_well_formed`, `_check_spawn_with_typeclass_well_formed`, `_check_den_room_tag_well_formed` (all non-empty string).
  - **Optional mapping (1):** `_check_attrs_well_formed` (mapping if present).
- **Dotted-path validity is NOT a Tier 1 concern** — `typeclass`, `post_spawn_hook`, `spawn_with_typeclass` only enforce non-empty-string shape here. Importability lands in Tier 3 in a later pass. Mirrors world-builder's structural-vs-resolvable split.
- **Cross-field exclusivity is NOT addressed in this pass** — `respawn_seconds` xor `death_cooldown_seconds` and the spawn-pattern mutex (`spawn_with_typeclass` vs `den_room_tag`) need separate predicates. Scoped out deliberately; "exists + well-formed" only.
- **52 new tests** across 7 test classes: `FieldPredicatesHappyPathTest` (whole-tuple happy path + the short-circuit-on-non-dict contract), `RuleMappingPredicateTest`, `RequiredFieldPredicatesTest`, `OptionalNumericPredicatesTest`, `OptionalStringPredicatesTest`, `OptionalAttrsPredicateTest`, `ValidatorPredicateIntegrationTest` (end-to-end through `Validator.validate()`, verifying findings accumulate and a non-dict rule produces exactly one finding).
- **96 tests green** (up from 44).

Next stages, in order:
1. Cross-field exclusivity predicates (cooldown XOR, spawn-pattern mutex, "one of {respawn_seconds, death_cooldown_seconds} required").
2. Tier 2: `_check_and_record_unique_rule_id` body (currently a stub).
3. Tier 3: `_check_typeclass_resolvable`, `_check_post_spawn_hook_resolvable`, `_check_spawn_with_typeclass_resolvable`.
4. File-level: `_check_file_metadata_shape` body — "mapping if present", and any per-file directives if any have been pinned by then.

## 2026-05-14 (afternoon)

**Validator structure wired (no predicates yet); gating helper pulled into a named function.**

Implementation status:

- **Three-tier Validator scaffold lands**, mirroring world-builder:
  - **Tier 1** (`PER_RULE_PREDICATES`, class-level tuple) — pure `(LoadedRule) -> str | None`. Always active.
  - **Tier 2** (`_check_and_record_unique_rule_id`) — stateful, reads / updates `seen_ids: {file_path: set[int]}`. Runs only on rules that passed Tier 1 (so garbage doesn't pollute the state).
  - **Tier 3** (`EVENNIA_ONLY_PREDICATES`, class-level tuple) — same predicate-function shape as Tier 1, active only when caller passes `evennia_runtime=True`. `ms_load` will pass True; `ms-validate` (CLI) leaves it False because the consumer's gamedir isn't on `sys.path` in CI.
  Plus a file-level pass (`_check_file_metadata_shape`) decoupled from the per-rule loop. Single `_record_finding()` funnel; end-of-pass `ValidatorError` raise — the "gather everything, then refuse" discipline from CLAUDE.md principle 4.
- **`LoadedRule(path, rule)` dataclass** — small frozen dataclass paired with each rule so predicates can name the source file in findings. Symmetric with world-builder's `LoadedEntity`; predicate signature is `(LoadedRule) -> str | None`.
- **All predicate tuples are deliberately empty.** Structure first — concrete predicates land in subsequent passes, after walking world-builder's predicate list to identify the equivalents (and gaps) against the v0 rule schema.
- **Gating helper landed in new [`commands.py`](../src/evennia_mob_spawner/commands.py).** Holds `FORCE_VALIDATE_FLAG = "force-validate"` and `should_pre_validate(definitions, flags) -> bool`. World-builder inlines the two-line policy inside `wb_build.func()`; mob-spawner pulls it out so the decision lives in one named, testable place reachable from any future caller. Decision #19's contract is implemented verbatim: `(not definitions.repo_ci_pre_validation) or "force-validate" in flags`.
- **13 new tests** across `ValidatorStructureTest` (8) and `ShouldPreValidateTest` (5). Structure tests cover empty-load pass, rules-with-no-predicates pass, `seen_ids` initialised empty, Tier 3 gating by `evennia_runtime`, `_record_finding` funnel, accumulation discipline (both rules in a multi-rule file get checked before the raise), `LoadedRule` is frozen. Gating tests cover the 2×2 of (setting × flag) plus the "unrelated flags ignored" edge case.
- **44 tests green** (up from 31).

Next stage: walk world-builder's `PER_ENTITY_PREDICATES` and `EVENNIA_ONLY_PREDICATES` lists alongside the v0 rule schema; for each predicate, decide whether mob-spawner has an equivalent, identify any rule-schema-specific gaps, then add Tier 1 + Tier 2 + Tier 3 predicates one at a time.

## 2026-05-14 (morning)

**Loader implementation landed; `file_metadata` slot opened mirroring world-builder; two-venv layout pinned.**

Implementation status:

- **Real Loader.** Walks `FoundLocation` (file or folder) following `index.yaml` entries; reads each leaf rule-set file, enforces top-level shape (mapping with a `rules:` list), surfaces `LoadResult.rule_sets` keyed by file path. The flatten-vs-per-file-with-`file_metadata` question flagged in the previous entry is resolved: per-file with `file_metadata`, matching world-builder.
- **`LoadResult.file_metadata`** is the parallel to world-builder's: any top-level key besides `rules:` lands in `file_metadata[path]`. The library doesn't curate which keys exist; downstream stages look up the keys they care about. A file appears in `file_metadata` only if it declared at least one such key (clean-by-default). No file-level keys are recognised today — the slot is open for future per-file directives without a schema break.
- **Loader error subclasses landed** under `LoaderError`: `LoaderMissingEntryError` (file referenced in index can't be read), `LoaderMissingIndexError` (folder has no / malformed `index.yaml`), `LoaderInvalidShapeError` (top-level file shape rejected).
- **9 new `LoaderTest` cases** cover single-file load, folder recursion, `file_metadata` extracted / absent, three shape-rejection paths, missing-file, missing-index. Existing pipeline + CLI smoke tests updated for the real Loader (the CLI smoke now creates a minimal `index.yaml: {entries: []}` alongside `definitions.yaml`, reflecting the Loader's contract that a content repo always has a root manifest).
- **Two-venv layout pinned.** `evennia-mob-spawner/venv/` (library tests) holds Evennia + `evennia-yaml-reader` + `evennia-mob-spawner` editable, **without** `evennia-world-builder`; the absence enforces architectural independence between the two libraries (an accidental `import evennia_world_builder` in library code fails fast in tests instead of passing silently). `evennia-mob-spawner/examples/venv/` (demo gamedir) holds the same three plus world-builder. Rationale captured in CLAUDE.md "Tools and environment."
- **31 tests green** in the library-root venv (up from 22).

One decision added to [architecture.md](architecture.md), bringing the count to 20:

20. **Loader uses world-builder's `file_metadata` pattern.** Each leaf rule-set file is a top-level mapping with one canonical list-bearing key (`rules:`); any other top-level keys are bagged into `LoadResult.file_metadata[path]` for downstream stages to look up.

Next stage: real Validator. Tier 1 (rule shape — required fields, types, field-pair exclusivity for `respawn_seconds` xor `death_cooldown_seconds`) + Tier 2 (`rule_id` unique within file) + a shape check on `file_metadata` ("must be a mapping if present").

## 2026-05-13 (evening)

**Pipeline scaffold + Finder implemented; demo gamedir running end-to-end against world-builder.**

Implementation status:

- **Pipeline scaffold landed** (`errors`, `definitions`, `finder`, `loader`, `validator`, `deployer`). Each stage has a class and method that returns the right empty shape — pipeline flows end-to-end with zero real logic and is ready for incremental fill-in.
- **`ms-validate` CLI shipped** — runs Reader → Definitions → Finder → Loader → Validator (Tier 1+2). Exit 0 clean / 1 on findings. UTF-8 stdio reconfigure for Windows. Registered as `[project.scripts]` entry point.
- **`evennia-mob-spawner-test-yaml` populated** with 4 rules (cover the indistinguishable-variant pattern, death-cooldown semantics, den targeting, post_spawn_hook, pack spawning) plus a wilderness rabbit rule. Validates clean via `ms-validate --reader local`.
- **`evennia-mob-spawner-test-world` populated** — 6 rooms with the `mob_area` tags the spawn rules expect, 10 exits, a navigable star topology from an untagged hub.
- **`config.py` implemented** — settings dispatch (`get_reader_class`, `get_configured_reader`) parallel to world-builder's. Settings keys: `MOB_SPAWNER_READER` / `MOB_SPAWNER_READER_KWARGS`.
- **`__init__.py` populated for the implemented surface only** — yaml-reader passthroughs (Reader / ReaderResult / GitHubReader / LocalReader and the five typed exceptions), settings dispatch (`get_reader_class` / `get_configured_reader`), Definitions / DefinitionsError. Scaffold-stage stages and their error types deliberately not promoted until they have real behaviour.
- **`Finder` implemented (real, not scaffold)** — ported verbatim from `evennia-world-builder`. Walks the manifest tree (`definitions.yaml` + per-folder `index.yaml`) following a scope query, returns `FoundLocation(path, kind, location)`. Scaffold's `kind="root"` divergence dropped. 9 tests ported.
- **`ms_log` shim implemented** — routes library log lines to `mob_spawner.log` in `settings.LOG_DIR` via Evennia's `log_file()`; silent no-op outside Evennia.
- **Demo gamedir wired up** at `examples/demo_game/`: both `evennia_world_builder` and `evennia_mob_spawner` in `INSTALLED_APPS`; `WORLDBUILDER_*` and `MOB_SPAWNER_*` `GITHUB_PAT/_REPO/_REF` constants with env-var defaults; `secret_settings.py` holds the operator's PATs.
- **End-to-end smoke green:** `wb_build all` from inside the demo gamedir fetches `evennia-mob-spawner-test-world` via GitHubReader, validates, builds 6 rooms + 10 exits (16 objects). `mob_area` tags land correctly on every tagged room, including the double-tagged `kobold_warren` + `kobold_warren_den` on the den room.
- **22 tests green** in the library's own `runtests.py`.

One TBD resolved since the previous entry: settings-name prefix is no longer deferred — `MOB_SPAWNER_*` is now in production via the demo gamedir's settings.py and the library's config.py. Open TBDs that remain: `max_per_room` default, default tick interval, `at_server_start` helper name, `ms_status` output shape.

Next stage: real Loader implementation. The flatten-vs-per-file-with-file_metadata question discussed mid-session is the first thing to settle when that work begins.

## 2026-05-13 (afternoon)

Eleven additional decisions landed in [architecture.md](architecture.md), bringing the count to 19:

9. **Typeclass count matching is exact, not subclass-inclusive.** Mirrors how `create_object` spawns exact typeclasses. Enables the "indistinguishable variant" pattern (same `key`, different typeclass, different loot — see FCM's `Rabbit`/`RabbitRich`/`RabbitFat`) where population ratios produce emergent loot variation while keeping issuance deterministic — compliance-relevant.
10. **Rule identity = author-supplied `rule_id` integer, unique within file.** Same pattern as world-builder's `deployment_id`. Bookkeeping keyed on `rule_id` alone (file implicit per-script); global identity `(rule_file, rule_id)` surfaces on operator commands only.
11. **First-tick init: `last_observed_count = 0`, `spawned_last_tick = 0`.** Death-detection formula naturally handles pre-existing populations (non-positive deaths → no event); no first-tick branch needed.
12. **Rule schema v0 = today's FCM JSON fields + `rule_id`.** Fields documented in architecture.md's Rule schema section. Iterate when concrete needs arise.
13. **`ms_load` race protocol: validate → snapshot → drain (60s timeout) → force-stop if needed → swap → resume.** Runs async. State preserved by snapshotting before the stop. `force_stop` is internal — not an operator command.
14. **Spawn-time errors caught and logged, not raised.** `create_object` and hook invocations run inside `try / except`. One bad rule doesn't take down the script's tick.
15. **Empty `area_tag` queries logged.** Pre-spawn check — if zero rooms match, log a warning and skip. Repeats per tick (operators may still be deploying world content).
16. **`den_room_tag` uses the same tag category as `area_tag`.** No second category. Comments in YAML are the consumer's documentation tool if they want to distinguish "group" vs "single" intent.
17. **Library logs via Evennia's `evennia.utils.logger.log_file()`** to `mob_spawner.log` in `settings.LOG_DIR` — colocated with `server.log`/`portal.log`, distinct file, thread-safe, zero consumer plumbing. Errors, warnings, and lifecycle events route here, never to `server.log`.
18. **Add `ms_restart` to the command set.** Kicks the ticker on an existing script without re-reading YAML; preserves state. Recovery action for stuck / stopped scripts; works when YAML is unavailable (Reader fetch failed). Sits between `ms_status` and `ms_load` on the escalation ladder.
19. **Validation gating mirrors world-builder.** Three tiers (shape / per-file uniqueness / engine-resolvability); no Tier 4 (no cross-rule references). `repo-ci-pre-validation` flag in `definitions.yaml` lets a consumer skip whole-repo Tier 1+2 in `ms_load` when CI has already gated the YAML; `--force-validate` per-invocation override. Standalone **`ms-validate`** CLI runs Tier 1+2 without Evennia for local iteration / pre-commit / CI.

## 2026-05-13 (morning)

- **Architecture pass captured in [architecture.md](architecture.md).** Eight design decisions pinned via conversation; full list in the doc. Summary:

  1. **Area-tag category is a library-level setting**, not a per-rule field. One game = one category name (default `"mob_area"`).
  2. **The library re-tags the spawned mob with the rule's `area_tag`** in the configured category. Useful for the consumer's AI / wander logic to read.
  3. **Tick interval is a library-level setting** (likely `MOB_SPAWNER_TICK_SECONDS`). One tuning knob for the whole system; per-rule / per-script overrides rejected.
  4. **One persistent Script per rule-set YAML file**, not one global script. Plays naturally with sharding — each shard loads only its own files; router loads none. Per-script CPU stays bounded by per-file rule count.
  5. **Library observes deaths via tick-time count delta, not via callback.** Per tick: `deaths = (last_observed_count + spawned_last_tick) - current_count`. No breadcrumb attributes on spawned mobs, no `on_death()` API, no callback from the typeclass to the library. The typeclass's `die()` does whatever the consumer wants without touching the spawn system. *(This supersedes the earlier breadcrumb-and-callback framing discussed mid-conversation — the observation model removes the need for any return path from typeclass to library.)*
  6. **Pipeline shape mirrors world-builder up to the terminal stage:** Reader → Definitions → Finder → Loader → Validator → Upsert. The terminal stage is upsert-with-state-preservation (find/create script, replace `db.spawn_table`, **keep** `last_spawn_times` / `last_death_times` / observation history), not tag-sweep-and-rebuild. Load-bearing distinction from world-builder, derived from CLAUDE.md principle 5.
  7. **Same operator command pattern as world-builder.** Scope-aware admin commands (`ms_load all` / `ms_load shard=X` / etc.), auto-installed into `AccountCmdSet`, `cmd:superuser()` locked, `ms_` prefix. Four operations: Load (upsert), Stop, Delete, Status. Scope semantics identical to world-builder.
  8. **`at_server_start` integration is consumer-driven.** Library exposes a helper; consumer's gamedir wires it into `at_server_startstop.py` with its shard's scope. Cold start creates missing scripts; warm restart finds existing scripts (state survived via Evennia's script persistence) and updates rules in place.

  **Guiding principle made explicit in the doc:** *settings belong in YAML; behaviour belongs in typeclasses.* The library reads settings, runs the loop, observes the world. Everything that happens *to* a spawned mob — AI, combat, death — is the typeclass's. The library and the typeclass meet at exactly one moment: spawn.

  **Edge-case behaviour pinned:** rule-removed-from-YAML purges its bookkeeping on load; rule-set-file-removed-from-manifest leaves the existing script alone (operator-driven cleanup via Stop / Delete; silent vanishing too easy to trigger accidentally).

  **The seam with `evennia-world-builder`** remains a single string convention: the tag category name. World-builder doesn't know `mob_area` means anything; it just places the tags the consumer's YAML declares. Mob-spawner queries that category. The two libraries never import each other; they meet at the Evennia tag table. Both depend on `evennia-yaml-reader` for YAML fetching.

  **Open questions still flagged** as `[TBD]` in the doc: `den_room_tag` category vs separate, exact command names, default tick interval value, settings-name prefix exact form, `at_server_start` helper name. All deferred until implementation.

## 2026-05-12

- **Repository bootstrapped.** LIBRARY_STANDARDS scaffold in place: `pyproject.toml`, `runtests.py`, `tests/test_settings.py` + `tests/urls.py`, `src/evennia_mob_spawner/__init__.py` (version 0.0.1), smoke tests, `CLAUDE.md`, `README.md`, `DESIGN/INDEX.md`, `DESIGN/progress.md`, `DESIGN/documentation-structure.md`, `DESIGN/archive/`.

  Tests use Django's test runner via `runtests.py` (standard LIBRARY_STANDARDS pattern — the library will depend on Evennia at runtime once code lands).

- **`evennia-yaml-reader` wired in as a dependency.** Declared in `pyproject.toml` so a consumer install gets it transitively. Smoke test `YamlReaderDependencyTest.test_can_import_reader_primitives` verifies the dependency resolves in the venv: `GitHubReader`, `LocalReader`, `Reader`, `ReaderError`, and `ReaderResult` all importable from `evennia_yaml_reader`. Confirms the library can lean on the shared Reader infrastructure rather than duplicating it.

  Context: the Reader was extracted from `evennia-world-builder` into `evennia-yaml-reader` so that mob-spawner (and future declarative-content libraries) can share it. Settings dispatch — choosing *which* Reader to instantiate at runtime, and with which kwargs — will be a mob-spawner-side concern, named under its own setting keys (e.g. `MOB_SPAWNER_READER`, `MOB_SPAWNER_READER_KWARGS`) when that machinery lands.
