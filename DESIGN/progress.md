# Progress

Running log of milestones with links to evidence. Reverse chronological — newest first.

## 2026-05-15 (afternoon — latest)

**Pass C landed — race protocol primitives. `MobSpawnerScript.stop_when_safe` / `force_stop` + Deployer drain-before-swap (decision #13). The library is functionally complete against architecture v0.**

Implementation:

- **[script.py](../src/evennia_mob_spawner/script.py)** — added race-protocol cooperation:
  - `at_repeat` checks `ndb._stop_requested` at two points: pre-loop (returns immediately) and **between rules** (breaks out cleanly, partial state written back). Mid-rule interruption is not supported — half-applied state, partial spawn, etc. The between-rules check is the safe granularity.
  - `at_repeat` sets `ndb._tick_in_progress = True` for the duration of a tick (in a `try/finally` so it always clears).
  - **`stop_when_safe(timeout=60.0)`** primitive: sets `_stop_requested`, polls `_tick_in_progress` (100ms granularity) until the in-flight tick exits, then pauses. Returns True on clean drain, False on timeout. No-op on already-paused / already-stopped scripts (returns True immediately).
  - **`force_stop()`** primitive (internal-only per decision #13): sets `_stop_requested` and pauses. Doesn't actually halt a wedged tick — CPython can't interrupt a running function from another thread cleanly. A truly stuck tick runs to whatever conclusion it reaches; the swap proceeds anyway and the next un-pause restarts on the new rule table.
- **[deployer.py](../src/evennia_mob_spawner/deployer.py)** — switched from naive `pause()` / `unpause()` to the race-safe protocol:
  - `was_running = is_active and not _paused_time` discriminator (correctly reads paused vs running, fixing the same `is_active` pitfall that bit `ms_status` earlier).
  - Drains via `stop_when_safe(60)` only when the script was actually running.
  - On timeout, logs a WARN and falls back to `force_stop()`.
  - `_stop_requested` flag is unconditionally cleared in the `finally` block after the swap, so the next tick proceeds normally regardless of which stop path fired.
  - Behaviour change: a previously-paused or stopped script stays in its prior state after re-deploy. The operator's explicit stop isn't second-guessed.
- **11 new RaceProtocolTest cases** cover:
  - Tick clears `_tick_in_progress` after running (try/finally discipline).
  - `_stop_requested = True` before tick → tick exits immediately, no state writes.
  - Stop flag set mid-loop (via patched `_tick_one_rule`) → second rule skipped.
  - `stop_when_safe` returns True quickly for idle / paused scripts (no-op).
  - `stop_when_safe` clears the stop flag on success (so post-resume ticks work).
  - `force_stop` pauses an active script + sets the stop flag.
  - `force_stop` is idempotent on an already-paused script.
  - Deployer integration: re-deploy of a running script keeps it running; re-deploy of a paused script keeps it paused.

**164 tests green** (was 153, +11).

**The library is functionally complete against architecture v0.** The MVP runs end-to-end: YAML → validate → upsert → tick → spawn → death-detect → cooldown → respawn, with race-safe re-deployment under a live tick. One open question remains in architecture.md (the `at_server_start` consumer helper name, deferred until first integration with a real consumer hook lifecycle).

## 2026-05-15 (mid-day)

**Pass B landed — the tick loop is real. `MobSpawnerScript.at_repeat` implements the full observe / detect-deaths / cooldown-gate / population-gate / room-pick / spawn / save-state sequence per architecture.md "The tick loop". Library now actually spawns mobs.**

Implementation:

- **[script.py](../src/evennia_mob_spawner/script.py)** — `at_repeat` body replaced from no-op stub with the seven-step tick loop. Per-rule logic factored into `_tick_one_rule(rule, now, ...)` so error catching can wrap one rule at a time (decision #14: one bad rule never takes down the tick). State dicts (`last_spawn_times`, `last_death_times`, `last_observed_counts`, `spawned_last_tick`) are read once at tick start, mutated through the loop, and written back at the end — no per-rule round-trips through Evennia's Attribute machinery during iteration.
- **Helper methods on `MobSpawnerScript`:**
  - `_count_living(rule)` — instance method on `MobSpawnerScript`. Filters `ObjectDB` on the chained identity tags (`mob_spawner_rule` = `str(rule_id)`, `mob_spawner_file` = `self.db_key`) with `db_location__isnull=False` exclusion. Two `.filter()` calls (separate JOINs against the many-to-many tag table). Per decision #9 the population discriminator is (file, rule_id); typeclass and area_tag are not part of the count filter, so rules sharing typeclass+area_tag are counted independently — the indistinguishable-variant pattern now works without requiring distinct typeclass per variant.
  - `_cooldown_elapsed(rule, rule_id, now, last_spawn_times, last_death_times)` — branches on which cooldown field the rule declares (validator enforces exactly-one). `respawn_seconds` reads `last_spawn_times`; `death_cooldown_seconds` reads `last_death_times`. Separate dicts (clean separation; consumer used a single re-stamped dict — the library departure noted in architecture.md).
  - `_pick_room(rule)` — three-tier fallback per decision #22: pack (`spawn_with_typeclass`) → den (`den_room_tag`) → random within `area_tag` pool. All respect `max_per_room`. Room-vs-non-room disambiguation uses `db_location__isnull=True` (Evennia convention: rooms are top of the location hierarchy) rather than the consumer's FCM-specific `db_typeclass_path__contains="rooms."` heuristic.
  - `_room_has_space(room, rule)` — counts existing same-typeclass mobs in the room with the area_tag and compares against `max_per_room`.
  - `_spawn_one(rule, room)` — `create_object` → re-tag with `area_tag` (decision #2) → stamp identity tags (`mob_spawner_rule` = `str(rule_id)`, `mob_spawner_file` = script's `db_key`) → stamp YAML-declared `tags` (each entry a bare string or `{key, category?}` dict; reserved categories `mob_spawner_*` refused at validation) → apply `desc` override → apply `attrs` via `setattr` (works with `AttributeProperty` descriptors on modern Evennia typeclasses) → invoke `mob.ms_at_post_spawn()` if present (decision #23, errors caught and logged).

**Library departures from FCM's existing `zone_spawn_script.py`** (all pinned in architecture decisions; mostly decoupling cleanups):

- Death detection: observation-based (count delta), not callback-based.
- Cooldown timestamps: two separate dicts (`last_spawn_times` and `last_death_times`), not one cleverly re-stamped.
- `rule_id`: author-supplied integer, not derived from `f"{typeclass}:{area_tag}"`.
- Per-spawn customization: `mob.ms_at_post_spawn()` method, not a YAML dotted-path field.
- Default cooldown: no magic 60s fallback (validator enforces exactly-one cooldown is declared).
- Loot tags (`spawn_resources`/`spawn_gold`/etc.): not in the library — consumer concern via `attrs:` or `ms_at_post_spawn`.
- `spawn_zone` per-script tag for population disambiguation: not used — library relies on `area_tag` being globally unique across the manifest (consumer's existing convention).
- `mob.start_ai()`: not called by library — consumer concern.

**14 new TickLoopTest cases** exercise the tick logic end-to-end against real Evennia DB objects:

- Empty `spawn_table` is a no-op.
- Below target → spawns; at target → skips.
- Cooldown gates immediate respawn.
- `death_cooldown_seconds` uses death time, not spawn time (death detected, then respawn fires).
- No room with `area_tag` → silent skip with WARN log.
- `max_per_room` respected.
- `den_room_tag` selects the den room when present.
- `attrs`, `desc`, `area_tag` all applied to the spawned mob.
- State dicts (`last_observed_counts`, `spawned_last_tick`, `last_spawn_times`) updated correctly.
- Bad rule (unresolvable typeclass) doesn't crash the tick; co-deployed good rule still spawns.

**153 tests green** (was 139, +14). Tests run against real Evennia DB objects (DefaultObject, DefaultRoom via `create_object`); the test runner's `evennia._init()` setup makes this work without a gamedir.

Next stage: **Pass C — race primitives.** Add `stop_when_safe(timeout)` + `force_stop()` on `MobSpawnerScript` so the Deployer can drain in-flight ticks safely during `ms_load` (decision #13). At the moment the Deployer uses `pause()`/`unpause()` which doesn't drain an in-flight tick — fine while the tick is fast, but the race-safe protocol is what decision #13 specifies.

After Pass C: TBD `at_server_start` helper name (architecture.md last remaining open question).

## 2026-05-15 (mid-morning)

**Full admin command surface shipped: `ms_load` + `ms_stop` + `ms_restart` + `ms_delete` + `ms_status` (architecture decision #7's complete set). Pipeline runs end-to-end in vivo.**

Implementation:

- **[apps.py](../src/evennia_mob_spawner/apps.py) (new)** — `EvenniaMobSpawnerConfig` auto-installs the full command set into `AccountCmdSet`. Same wrap-`evennia._init` pattern as `evennia-world-builder` and `evennia-shards` — the patch is deferred until after `_init()` runs so the lazy `evennia.Command` exports are populated.
- **[commands.py](../src/evennia_mob_spawner/commands.py)** — extended from the standalone gating helper to ship all five admin commands:
  - **`ms_load`** — runs the full pipeline (Reader → Definitions → Finder → Loader → Validator with `evennia_runtime=True` → Deployer). Mirrors `wb_build`'s shape: argument parser (`all | <level>=<value>... [--force-validate]`), reactor-side validation followed by `run_async` worker handoff, every operator-facing line collected into a message list and flushed via `at_return`.
  - **`ms_stop`**, **`ms_restart`**, **`ms_delete`**, **`ms_status`** — share a `_MsOperateBase` scaffold (argument parsing, scope resolution, async dispatch, error handling). Subclasses override one `apply(script, messages)` method.
- **Scope resolution helper `_resolve_scope_to_scripts(query, reader, definitions)`** — empty query (`all`) bypasses the manifest and operates on every `MobSpawnerScript` instance in the DB (catches orphans whose source files were removed). Non-empty query walks the manifest via Finder; `kind=file` resolves to an exact `db_key` match, `kind=folder` resolves to a `db_key__startswith` prefix match.
- **`ms_status` output shape pinned** (resolves architecture.md open question): one operator-facing line per matched script: ``<path>: <active|stopped>, <N> rule(s), interval=<I>s, next=<T>s``. Per-rule detail is intentionally not in v0 — operators investigating a specific rule can read `mob_spawner.log` or use Evennia's built-in `@scripts <path>` for the full attribute dump.

**Live smoke verified.** Two `MobSpawnerScript` instances appear in `@scripts` after `ms_load all` (`shard0/millholm.yaml` + `shard0/wilderness.yaml`); both `<Global>`, both ticking at 15s with `start_delay=True` producing a 10s pre-first-tick delay. Operator-facing message confirming Pass A status (tick loop is a no-op stub) comes through cleanly. Full state-transition cycle (active → paused → active) verified end-to-end via `ms_stop` / `ms_restart` / `ms_status` against scope queries.

**Bug caught + fixed during smoke:** initial implementation used `script.is_active` as the state discriminator, but Evennia's `is_active` is True for BOTH running and paused scripts — it flips to False only on `stop()` (or never-started). The actual paused state is tracked via `db._paused_time` (set by `pause()`, cleared by `unpause()` / `_stop_task()`). Without this discrimination, `ms_status` reported paused scripts as "active" and `ms_restart` short-circuited with "already running" without unpausing. Fix: a `_script_state(script)` helper returning `"active"` / `"paused"` / `"stopped"` based on both `is_active` and `db._paused_time`. `ms_stop` only pauses an active script; `ms_restart` handles both transitions (`paused → unpause()`, `stopped → start()`); `ms_status` reports the actual state and only emits `next=` when active.

**139 tests still green** — no library-side regressions from the command additions. Command classes themselves are thin wrappers over already-tested pipeline pieces; the scope-resolution helper is the main new logic, exercisable via the live smoke (unit tests for the helper would require an Evennia test harness — deferred).

One open question resolved: `ms_status` output shape pinned. Architecture.md "Open questions" now contains only `at_server_start` helper name.

Next stage: **Pass B — the tick loop.** Implement `MobSpawnerScript.at_repeat` per architecture.md "The tick loop" (observe → detect deaths → cooldown gate → population gate → room selection → spawn → save state). Plus `stop_when_safe` / `force_stop` primitives for race-safe drain (decision #13), which `ms_load`'s deployer will then use in place of the current `pause()`/`unpause()` (Pass A's interim approach).

## 2026-05-15 (early)

**`ms_load` admin command shipped. Pipeline runs end-to-end in vivo against the test-yaml fixture; two persistent `MobSpawnerScript` instances created and ticking (no-op).**

Implementation:

- **[apps.py](../src/evennia_mob_spawner/apps.py) (new)** — `EvenniaMobSpawnerConfig` auto-installs `CmdMsLoad` into `AccountCmdSet`. Same wrap-`evennia._init` pattern as `evennia-world-builder` and `evennia-shards` — at `ready()` time the lazy `evennia.Command` exports are still `None`, so the patch is deferred until after `_init()` runs. Idempotent via the `_evennia_mob_spawner_cmdset_patched` flag.
- **[commands.py](../src/evennia_mob_spawner/commands.py)** — extended from the standalone gating helper to ship `CmdMsLoad`:
  - `key = "ms_load"`, `cmd:superuser()` locked, `help_category = "Mob Spawner"`.
  - Argument parser identical to `wb_build`'s: `ms_load all | <level>=<value>... [--force-validate]`.
  - `func()` validates args on the reactor thread, hands the pipeline off to a Twisted worker via `run_async`.
  - `_run_pipeline` walks Reader → Definitions → Finder → Loader → Validator (`evennia_runtime=True`, so Tier 3 + Tier 4 both fire) → Deployer. Each stage's exceptions caught and routed to operator-facing messages; only unexpected exceptions surface via `at_err`.
  - Gating uses `should_pre_validate(definitions, flags)` per decision #19.
  - End-of-run message indicates this stage of the implementation: scripts created, lifecycle working, but `at_repeat` is a no-op stub — Pass B will add the spawn tick.

**Live smoke verified.** Run against `evennia-mob-spawner-test-yaml` (2 files, 5 rules):
- `ms_load all` ran the full pipeline cleanly.
- Two `MobSpawnerScript` instances appear in `@scripts`: `shard0/millholm.yaml` (4 rules) and `shard0/wilderness.yaml` (1 rule). Both `<Global>`, both ticking at 15s.
- `start_delay = True` produces a 10s pre-first-tick delay (visible as `next: 10s` in `@scripts`); intended behaviour, prevents tick from firing instantly on creation before the operator has wired up world content.
- Operator-facing message indicating Pass B status came through correctly.

**139 tests still green** — no library-side regressions from the command addition.

Next stage: **Pass B — the tick loop.** Implement `MobSpawnerScript.at_repeat` per architecture.md "The tick loop" (observe → detect deaths → cooldown gate → population gate → room selection → spawn → save state). Plus `stop_when_safe` / `force_stop` primitives for race-safe drain (decision #13), which `ms_load`'s deployer will then use in place of the current `pause()`/`unpause()` (Pass A's interim approach).

## 2026-05-15

**Deployer Pass A landed: `MobSpawnerScript` typeclass + upsert protocol. Lifecycle plumbing without tick logic.**

Implementation:

- **[script.py](../src/evennia_mob_spawner/script.py) (new)** — `MobSpawnerScript` typeclass. Inherits dynamically from `settings.BASE_SCRIPT_TYPECLASS` via `class_from_module` (falls back to `evennia.DefaultScript`) per decision #25. `at_script_creation` initialises the bookkeeping dicts (`db.spawn_table`, `db.last_spawn_times`, `db.last_death_times`, `db.last_observed_counts`, `db.spawned_last_tick`) and sets `interval = get_tick_seconds()` + `persistent = True` + `start_delay = True`. `at_repeat` is a no-op stub at this stage — the spawn tick loop lands in Pass B. Always calls `super()` on hooks to compose with consumer-side customisations on their base script.
- **[deployer.py](../src/evennia_mob_spawner/deployer.py)** — scaffold replaced with real `Deployer.deploy(load_result)` implementing the upsert protocol per decision #6:
  1. Iterate `rule_sets.items()` per file.
  2. Find existing `MobSpawnerScript` by `db_key = path`, or create via `evennia.create_script`.
  3. Snapshot all four bookkeeping dicts.
  4. `script.pause()` (simple pause for now; the race-safe `stop_when_safe` / `force_stop` protocol from decision #13 lands in Pass C with `ms_load`, after the tick loop is implemented).
  5. Replace `db.spawn_table` with the new rule list.
  6. For each bookkeeping dict: keep entries whose `rule_id` survives the swap; purge entries whose `rule_id` vanished from YAML.
  7. `script.unpause()` if it was active.
  8. Log a summary line per file (`deployed <path>: N rule(s) (M preserved, K purged)`).
- **[config.py](../src/evennia_mob_spawner/config.py)** — new `get_tick_seconds()` helper, parallel to the existing `get_area_tag_category()`. Default `15s`, overridable via `MOB_SPAWNER_TICK_SECONDS`. Resolves the open architecture.md TBD ("Default tick interval value").
- **8 new tests** in `DeployerTest`:
  - Creates a new script when none exists
  - Separate files → separate scripts (same `rule_id` across files is fine)
  - Re-deploy reuses the existing script
  - Swap replaces `spawn_table`
  - State preserved for surviving rules (multi-rule case)
  - State purged for removed rules
  - Brand-new rule starts with no bookkeeping
  - Empty `LoadResult` creates no scripts

Tests use real Evennia `create_script` + DB queries against the test database — `runtests.py` calls `evennia._init()` which sets up the typeclass registry; `test_settings.py` puts `evennia/game_template/` on `sys.path` so `BASE_SCRIPT_TYPECLASS` resolves.

One decision added to [architecture.md](architecture.md), bringing the count to 25:

25. **`MobSpawnerScript` inherits from `settings.BASE_SCRIPT_TYPECLASS`** (not `evennia.DefaultScript` directly). Same pattern as `evennia-shards`. Consumer customisations on their base script compose into the library's scripts automatically; consumer never has to choose between library benefits and their own additions. Implicit contract: their base must behave like a `DefaultScript` subclass.

One open question resolved: default tick interval pinned at **15 seconds** (matches FCM's existing convention), overridable via `MOB_SPAWNER_TICK_SECONDS`. Architecture.md "Open questions" updated to drop the resolved TBD.

**139 tests green** (up from 131).

**Pass A is lifecycle plumbing only.** The script's `at_repeat` is a no-op stub; the script doesn't actually spawn anything yet. What works end-to-end now: validate → upsert into persistent scripts that survive server restarts, with cooldown/observation state preserved across YAML edits.

Next stages, in order:
1. **Pass B — the tick loop.** Fill in `MobSpawnerScript.at_repeat` with the observe / detect-deaths / cooldown-check / population-check / room-pick / spawn sequence per architecture.md "The tick loop". This is where `attrs` are applied, the rule's `area_tag` is re-stamped, and `mob.ms_at_post_spawn()` is invoked if present. Plus implement `stop_when_safe` / `force_stop` primitives for race-safe drain.
2. **Pass C — `ms_load` admin command.** Wires gating helper + validator (`evennia_runtime=True`) + deployer with the async race protocol (decision #13). The `at_server_start` consumer-driven hook (currently TBD on name) also lands here.

## 2026-05-14 (very late)

**Tier 3 signature check added; Tier 4 (deploy-time diagnostics) introduced. Validation surface now covers everything from "is the shape right" through "will the rule actually behave at deploy."**

Two additions on top of the previous Tier 3 pass:

- **Tier 3 signature check.** `_check_typeclass_ms_at_post_spawn_signature` added to `EVENNIA_ONLY_PREDICATES`. Uses `inspect.signature` to verify that — if the typeclass declares `ms_at_post_spawn` — the method is callable as `mob.ms_at_post_spawn()` (zero required args after `self`). Catches `def ms_at_post_spawn(self, extra_arg):` typos at validation time instead of at the first spawn's TypeError. Accepts canonical zero-arg shape, defaulted args, variadic `*args/**kwargs`, staticmethod / classmethod variants. Defers cleanly when `inspect.signature` can't introspect (rare C-extension callables).
- **Tier 4 introduced — deploy-time diagnostics.** A new tier with a distinct contract from predicates:
  - Functions have signature `(LoadedRule) -> None` (no return value to inspect).
  - Side effects allowed (typically `ms_log` calls).
  - Never refuse deployment.
  - Naming convention `_diagnostic_*` (vs predicates' `_check_*`) marks the difference at every call site.
  - Listed in a separate class-level tuple `EVENNIA_DIAGNOSTICS` on the Validator.
  - Runs only with `evennia_runtime=True`, only on rules that survived Tier 1 (so we don't diagnose rules that won't deploy).

Two Tier 4 diagnostics shipped:

- **`_diagnostic_area_tag_rooms_exist`** — queries `ObjectDB` for rooms tagged with the rule's `area_tag` under the configured category. If count is zero, emits a WARN log line. Operator sees one warning per deploy ("rule_id N's area_tag=X has 0 tagged rooms — spawns will be skipped until rooms are tagged") instead of 240+/hour from tick-time skip logging (decision #15).
- **`_diagnostic_den_room_tag_rooms_exist`** — parallel, for the optional `den_room_tag` field. Spawns fall through to area-random when the den is missing, so the message mentions the fallthrough behaviour.

Config helper added: **`get_area_tag_category()`** in `config.py`, defaulting to `"mob_area"`. Override via `settings.MOB_SPAWNER_AREA_TAG_CATEGORY`. Pins decision #1's "library-level setting" into actual settings dispatch.

Wiring: Tier 4 runs in the same per-rule loop as Tier 2, after the duplicate-rule_id check. It runs even when Tier 2 records a duplicate (the diagnostic still helps the operator). It's skipped when Tier 1 failed (no point diagnosing a broken rule).

**Predicate purity preserved.** The earlier proposal to put tag-existence checks inside predicates (with side effects + always returning None) was rejected during discussion — predicates stay pure, the diagnostic tier is a separate mechanism with its own contract. Worth the small extra tuple/method to keep the predicate convention clean.

Tests:

- **4 new Tier 3 signature tests** in `Tier3ResolvabilityTest`: canonical signature passes, extra-required-arg flagged, defaulted-args pass, variadic passes.
- **6 new Tier 4 diagnostic tests** in `Tier4DiagnosticTest`: area_tag with rooms (no warn), area_tag with zero rooms (warn), den_room_tag absent (no warn), den_room_tag with zero rooms (warn), Tier 4 gated off without evennia_runtime, Tier 4 skipped when rule failed Tier 1. Tests mock `ObjectDB.objects.filter().count()` via `unittest.mock.patch` and capture `ms_log` calls.
- 3 new fake-typeclass fixtures at module scope (`_FakeTypeclassWithBadHookSignature`, `_FakeTypeclassWithDefaultedHookSignature`, `_FakeTypeclassWithVariadicHook`).

One decision added to [architecture.md](architecture.md), bringing the count to 24:

24. **Tag-existence is a Tier 4 deploy-time diagnostic, not a validation refusal.** Separate `EVENNIA_DIAGNOSTICS` tuple, separate `_diagnostic_*` convention. Operator may be deploying world content in parallel; one deploy-time WARN per missing-tag rule, no spam at tick time.

Decision #19 reworded to note that the "no Tier 4" claim was true *for predicates* but Tier 4 *diagnostics* were added later under decision #24. Validation tiering table now has 4 rows + a "Refuses?" column distinguishing predicate tiers from the diagnostic tier.

**131 tests green** (up from 121: +10 new tests across the two additions).
**`ms-validate`** against the test-yaml fixture: clean, 0 findings (Tier 4 doesn't fire in the CLI path).

Next stage: **Deployer / upsert** terminal stage (decision #6). Per-rule-set-file persistent Script lookup-or-create, replace `db.spawn_table`, preserve `last_spawn_times` / `last_death_times` / observation history. Then the **`ms_load` admin command** wiring everything together (gating helper, validator with `evennia_runtime=True`, deployer, async race protocol per decision #13).

## 2026-05-14 (late night)

**Tier 3 (engine-runtime) predicates landed. `post_spawn_hook` YAML field dropped; replaced by `ms_at_post_spawn` method-on-typeclass protocol (decision #23).**

The dotted-path `post_spawn_hook` field in the rule schema has been replaced with a typeclass-method protocol. The library invokes `mob.ms_at_post_spawn()` if the spawned typeclass defines that method; absent, nothing happens. Duck-typed, opt-in, one method name. The `ms_` prefix marks library provenance; `at_` follows Evennia's `at_object_creation`/`at_post_move`/etc. convention.

Reasoning: the consumer's existing dotted-path field came from a system written without loose-coupling concerns. Scattered hook functions across shared modules become hard to track as authoring scales, and "per-rule customization of a shared typeclass" is more cleanly expressed by subclassing the typeclass than by indirecting through a string. The empirical case the user surfaced — `CombatMob` used by 5 rules with `set_ai_idle` AND 3 rules without — is the OO case for typeclass proliferation, not the case for a per-rule hook indirection. Migration cost on the consumer side is real but bounded; the library's job is to fix the coupling the original organic design didn't worry about.

Implementation:

- **`_check_post_spawn_hook_well_formed` removed** from Tier 1 and from `PER_RULE_PREDICATES`. Predicate function deleted.
- **Field removed from rule schema** in [architecture.md](architecture.md). Three optional fields remain (`desc`, `attrs`, `spawn_with_typeclass`, `den_room_tag` — wait, four).
- **Mechanisms section** rewritten: `post_spawn_hook` subsection replaced with `ms_at_post_spawn` description (duck-typed, opt-in, no inheritance demand).
- **`_resolve_dotted(path)` helper added** at module scope in `validator.py`. Returns `(resolved, None)` or `(None, reason)`. Used by all three Tier 3 predicates; mirrors world-builder's `_resolve_typeclass` pattern.
- **Three Tier 3 predicates added** to `EVENNIA_ONLY_PREDICATES`:
  - `_check_typeclass_resolvable` — resolves and is a class
  - `_check_spawn_with_typeclass_resolvable` — when present, resolves and is a class
  - `_check_typeclass_ms_at_post_spawn_callable` — if the resolved typeclass declares `ms_at_post_spawn`, it must be callable (catches `ms_at_post_spawn = "string"` typos at validation time, not runtime)
- **13 new Tier 3 tests** across `Tier3ResolvabilityTest`. Covers happy paths (resolvable class, callable hook), failure modes (module not importable, class missing, not a dotted path, not a class, hook not callable), spawn_with_typeclass parity cases, and the gating test (Tier 3 inactive when `evennia_runtime=False`). Test fixtures use synthetic classes defined at module scope in `tests.py` (`_FakeTypeclass`, `_FakeTypeclassWithHook`, `_FakeTypeclassWithBadHook`, `_fake_function`).
- **3 dropped tests** — the post_spawn_hook string-shape predicate tests from `OptionalStringPredicatesTest`.
- **`_VALID_RULE` cleaned** — `post_spawn_hook` removed.
- One new decision added to [architecture.md](architecture.md), bringing the count to 23:

  23. **Per-spawn behaviour is a typeclass method, not a YAML field.** Library invokes `mob.ms_at_post_spawn()` if present; absent is silent. Replaces the consumer's `post_spawn_hook: <dotted_path>` pattern. A measured exception to decision #3 (locality of post-spawn behaviour outweighs the tiny protocol surface).

Consumer migration:

- **`evennia-mob-spawner-test-yaml/shard0/millholm.yaml`** — `post_spawn_hook:` line removed from the chieftain rule.
- **`examples/demo_game/typeclasses/test_mobs.py`** — `KoboldChieftain` grew a `ms_at_post_spawn()` method that performs the reset previously done by the standalone hook function.
- **`examples/demo_game/typeclasses/test_hooks.py`** — deleted; its only content (the chieftain reset function) is now on the typeclass.

- **121 tests green** (up from 111). Net change: +13 Tier 3 tests, –3 dropped post_spawn_hook string tests.
- **`ms-validate`** against the test-yaml fixture: clean, 0 findings.

**Non-runtime AND runtime validation surfaces are now both complete.** Tier 1 (15 stateless per-rule predicates, after dropping post_spawn_hook), Tier 2 (rule_id uniqueness), Tier 3 (3 engine-runtime predicates), file-level shape check. The Validator's predicate set is comprehensive against the v0 rule schema.

Next stage: the **Deployer / upsert** terminal stage. This is the load-bearing distinction from world-builder (decision #6): per-rule-set-file persistent Script lookup-or-create, replace `db.spawn_table`, preserve `last_spawn_times` / `last_death_times` / observation history. Then the **`ms_load` admin command** that wires everything together.

## 2026-05-14 (night)

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
