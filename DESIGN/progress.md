# Progress

Running log of milestones with links to evidence. Reverse chronological — newest first.

## 2026-05-14

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
