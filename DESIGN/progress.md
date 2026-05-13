# Progress

Running log of milestones with links to evidence. Reverse chronological — newest first.

## 2026-05-13 (afternoon — latest)

Ten additional decisions landed in [architecture.md](architecture.md), bringing the count to 18:

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
