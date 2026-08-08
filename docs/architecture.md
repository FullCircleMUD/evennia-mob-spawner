# Architecture

High-level mapping of the spawn system's mechanisms and the library / consumer boundary for each. The doc tracks alongside the implementation — every decision below is reflected in code, every behaviour in code is reflected here. If you find a gap, treat it as a bug in this doc.

## Guiding principle

**Settings belong in YAML. Behaviour belongs in typeclasses.** The library reads settings from YAML (what spawns, where, how often, max count) and runs the loop that observes the world and creates objects. Everything that happens *to* a spawned mob — its AI, its combat, its death — is the typeclass's. The library and the typeclass meet at exactly one moment: spawn time. After that the mob is the typeclass's concern entirely.

This means: **the library has no callbacks into the typeclass and demands no base class.** It observes the world — counting living mobs by the passive identity tags it stamps at spawn (`mob_spawner_rule` + `mob_spawner_file`, see [Tags applied at spawn time](#tags-applied-at-spawn-time)) — and acts on what it observes. The one optional protocol surface is the duck-typed `ms_at_post_spawn()` method (decision #23); a typeclass that doesn't define it is never touched after spawn.

## Pipeline shape

The same pipeline as [`evennia-world-builder`](https://github.com/FullCircleMUD/evennia-world-builder), up to but not including the terminal stage:

```
Reader → Definitions → Finder → Loader → Validator → Upsert
```

- **Reader** — fetches YAML from a configured source ([`evennia-yaml-reader`](https://github.com/FullCircleMUD/evennia-yaml-reader)).
- **Definitions** — parses a `definitions.yaml` at the root of the rule-set content repo. Declares levels (e.g. `levels: [shard, zone]` for FCM) so scoped commands can navigate the manifest tree.
- **Finder** — walks the per-folder `index.yaml` manifest following an operator query.
- **Loader** — reads matching rule-set files into the rule data structure. Each file is a top-level mapping with a `rules:` list; any other top-level keys ride alongside in `LoadResult.file_metadata` (see [Decision 20](#agreed-decisions)).
- **Validator** — predicate-tier checks (rule shape, typeclass resolvability, …) before any DB mutation.
- **Upsert** — terminal stage. **Different from world-builder.** For each rule-set file in scope, find the existing persistent Script (or create one), replace its in-memory rule table with the YAML's current rules, **preserve runtime state** (cooldown / observation history). The script's tick continues uninterrupted.

The Reader / Definitions / Finder / Loader / Validator stages are conceptually identical to world-builder's. They are duplicated, not shared, until a third consumer makes extraction worthwhile.

## Validation tiering and gating

Same model as world-builder; predicates split by whether they need a running Evennia engine:

| Tier | Checks | Refuses? | Needs Evennia? | Where it runs |
|---|---|---|---|---|
| 1 — Shape | Required fields, types, well-formedness, field-pair exclusivity (`respawn_seconds` xor `death_cooldown_seconds`) | yes | No | `ms-validate` CLI, `ms_load` |
| 2 — Per-file uniqueness | `rule_id` unique within file | yes | No | `ms-validate` CLI, `ms_load` |
| 3 — Engine resolvability | `typeclass` / `spawn_with_typeclass` importable + is-a-class; `ms_at_post_spawn` (if declared) callable with correct signature | yes | Yes | `ms_load` only |
| 4 — Deploy-time diagnostics | Tag-existence preflight (`area_tag`, `den_room_tag`) — log WARN if 0 tagged rooms exist | **no** — never refuses | Yes | `ms_load` only |
| ~~Cross-refs~~ | *(omitted; mob-spawner has no cross-rule references)* | n/a | n/a | n/a |

**Tier 4 vs Tier 3.** Tier 3 predicates refuse deployment on failure (collected into the errors list, ValidatorError raised). Tier 4 *diagnostics* never refuse — they emit deploy-time WARN logs and return None. Different contract, separate tuple (`EVENNIA_DIAGNOSTICS`), separate naming convention (`_diagnostic_*` vs predicates' `_check_*`). The operator sees the warning once at deploy time; tick-time logging (decision #15) is the runtime fallback for tags that go missing post-deploy.

### Gating

A flag in the consumer's `definitions.yaml` — same name as world-builder's — controls whether `ms_load` pre-validates the whole repo on every invocation:

```yaml
repo-ci-pre-validation: true   # default: false
```

- **`false`** (default): `ms_load` walks the whole repo and runs Tier 1+2 before any in-scope work. Safe; expensive at scale.
- **`true`**: `ms_load` trusts the consumer's CI gate. Runs Tier 3 + Tier 4 on the in-scope files (these need the engine and can't run in CI). Whole-repo Tier 1+2 is skipped on the assumption that CI has already enforced it on the YAML at merge time.

**Per-invocation override:** `ms_load <scope> --force-validate` runs whole-repo Tier 1+2 regardless of the flag. Same semantics as `wb_build --force-validate`.

### `ms-validate` CLI

A standalone console-script (entry point in `pyproject.toml`) that runs Reader → Definitions → Finder → Loader → Validator at Tier 1+2 against a content repo. No Evennia bootstrap required; designed for:

- **Local iteration** — author edits a rule, runs `ms-validate --reader local --root ../mob-spawner-content` to confirm the YAML is well-formed before committing.
- **Pre-commit hook** — same invocation, called by `pre-commit` or a `husky`-style runner.
- **CI gate** — GitHub Actions / equivalent runs the CLI against the PR branch; merge blocked on findings.

Exit `0` on a clean run, `1` on any finding. Same `--reader local --root <path>` / future-GitHub variant as `wb-validate`. The CLI invariant: it never imports Evennia, so it can run in environments without an engine.

## One Script per rule-set file

The unit is **one persistent Evennia Script per rule-set YAML file**. Multiple scripts in the running game; each owns its own subset of rules. The consumer's `definitions.yaml` declares what a file represents — for FCM, one file per zone. A different consumer might slice by district, by faction, by encounter type.

Why per-file rather than one global script:

- **Scope independence.** Each script holds at most one file's rules. Per-tick CPU is bounded by per-file rule count (typically tens, not hundreds or thousands).
- **Modular shard assignment.** Each shard loads only its own rule-set files at server start; reassigning a zone between shards is moving the file path from one shard's bootstrap to another's. The router node loads nothing.
- **Surgical reload.** Editing one file and reloading it affects only that file's script; other zones' cooldown / population state is untouched.
- **Simpler failure mode.** If one script fails, only that rule-set's spawning is affected — others continue.

## Rule identity

Every rule carries a mandatory author-supplied **`rule_id` integer, unique within its file**. Same pattern as world-builder's `deployment_id`.

- **Bookkeeping keys** (`last_spawn_times`, `last_death_times`, `last_observed_counts`) use `rule_id` alone within a script (the file is implicit — the script owns one file).
- **Global identity** is `(rule_file, rule_id)`. Surfaces only on operator-facing output (`ms_status`, log lines).
- **Stability**: editing fields on a rule keeps its identity; reordering rules in YAML doesn't shift IDs. Changing the `rule_id` is the explicit signal that this is a different rule (cooldown history does not carry forward).
- **Validator enforces uniqueness** within each file (Tier 2 predicate).

## Rule schema (v0)

Inherits today's FCM JSON shape, plus `rule_id`. Mandatory-vs-optional split is calibrated against the consumer baseline (decision #21). Fields settle as work lands; iterate when concrete needs surface.

| Field | Required | What it does |
|---|---|---|
| `rule_id` | ✓ | Author-supplied integer, unique within file. The persistent Script's bookkeeping (cooldown clocks, observation history) is keyed by it. Stable across YAML reordering / field edits; changing the ID is the explicit signal that this is a different rule and cooldown history does not carry forward (decision #10). |
| `typeclass` | ✓ | Dotted path to the typeclass to instantiate at spawn time. Pure creation instruction — population counting discriminates on (file, rule_id) via identity tags (decision #9), so multiple rules can share the same typeclass without colliding. |
| `key` | ✓ | The spawned mob's `key` (display name). Same key across rules / typeclasses is permitted. |
| `area_tag` | ✓ | Tag key (under the configured `mob_area` category) defining the rule's world: the room pool spawns can land in, the set the library counts population against, and the tag re-stamped on each spawned mob for the consumer's AI / wander logic (decision #2). |
| `target` | ✓ | Population cap — how many of this rule's mobs should be alive at once. Positive integer (>= 1). |
| `max_per_room` | ✓ | Per-room cap for this rule's mobs; respected by all three room-selection patterns. Positive integer (>= 1). |
| `respawn_seconds` / `death_cooldown_seconds` | exactly one of | The rule's cooldown gate. `respawn_seconds`: clock measured from `last_spawn_time` — rate-limits spawn pace, applies regardless of whether the previous mob is alive or dead. `death_cooldown_seconds`: clock measured from `last_death_time` — grace period after a kill, useful for bosses where the area should feel depleted post-kill. Non-negative number; 0 means no effective cooldown. Mutually exclusive — never both, never neither. |
| `desc` | optional | Description override applied after `at_object_creation`. Falls back to the typeclass default when absent. |
| `attrs` | optional | Mapping of `{attribute_name: value}` overrides applied to the spawned mob. The library applies them generically; semantics belong to the consumer's typeclass. A value only persists past the current object if the typeclass declares `attribute_name` as an `AttributeProperty` — otherwise `setattr()` sets a plain, non-persisted Python attribute and the library logs a `WARN` (see [logging.md](logging.md)). |
| `spawn_with_typeclass` | optional | Pack-spawn trigger (Step 1 of room selection): spawn into the room currently containing a living instance of this typeclass within the rule's `area_tag`. Falls through to den / random if no leader is found. |
| `den_room_tag` | optional | Single-room lair (Step 2 of room selection): spawn into the one room tagged with this key. Uses the same `mob_area` tag category as `area_tag` (decision #16); the distinction is the rule field that references the tag, not the category. Falls through to random `area_tag` pool if the den is full. |
| `tags` | optional | List of tags to stamp on each spawned mob, in addition to the library-stamped identity tags (`area_tag`, `mob_spawner_rule`, `mob_spawner_file`). Each entry is a bare string (untyped tag) or a mapping with `key` (required, non-empty string) and optional `category` (non-empty string). Mirrors the YAML shape accepted by `evennia-world-builder`. Reserved category prefix `mob_spawner_` is refused at validation time to prevent authors from spoofing the population discriminator. |

## The tick loop

Each script ticks every `MOB_SPAWNER_TICK_SECONDS` (library-level setting, single value for the whole consuming game). On each tick, for each rule the script holds:

1. **Observe** — count living mobs produced by this rule, identified by the `mob_spawner_rule` (rule_id) and `mob_spawner_file` (script's source-file path) tags stamped on each mob at spawn time. The (file, rule_id) pair is the population discriminator (decision #9); typeclass and area_tag are not part of the count filter.
2. **Detect deaths** — `deaths = (last_observed_count + spawned_last_tick) - current_count`. If positive, stamp `last_death_time = now`. (`spawned_last_tick` accounts for the script's own spawns inflating the population between observations.)
3. **Cooldown check** — if the rule uses `respawn_seconds` or `death_cooldown_seconds`, compare against `last_spawn_time` or `last_death_time` respectively. Skip if the cooldown hasn't elapsed.
4. **Population check** — skip if the rule is already at `target`.
5. **Room selection** — pick an eligible room (see [Room selection](#room-selection-three-tier-fallback)).
6. **Spawn** — `create_object(typeclass=..., location=..., ...)`. Re-tag with the rule's `area_tag` under the configured category. Apply `desc` override + `attrs` overrides — each `attrs` entry is checked against the typeclass's declared `AttributeProperty` descriptors first (`_persists_as_attribute()`); a value with no matching descriptor still gets `setattr()`'d for backward-compatible behaviour, but logs a `WARN` since it will not survive past the current object. Invoke `mob.ms_at_post_spawn()` if the typeclass defines it (decision #23).
7. **Save state** — `last_observed_count = current_count`, `spawned_last_tick = spawned_this_tick`, `last_spawn_time = now` (if spawned).

Step 1 happens *before* the spawn decision, so each tick's observation reflects the world as left by previous ticks — without double-counting this tick's own spawn.

**Initial values on first tick of a fresh script:** `last_observed_count = 0`, `spawned_last_tick = 0`. The death-detection formula naturally handles this — when pre-existing mobs are in the world, `deaths` comes out non-positive (no event). No special-case branch needed.

## Mechanisms

### Targeting — rooms eligible to spawn into

Rooms carry an Evennia tag whose **category is consumer-configured** (default `"mob_area"`) and whose **key is the rule's `area_tag`**. The library finds spawn-eligible rooms via a tag query against that category + key. The library has no opinion on how the tags get onto the rooms — in the FCM stack they're authored in YAML and placed by `evennia-world-builder`, but mob-spawner reads the tag table regardless of who wrote it.

### Room-selection (three-tier fallback)

Three patterns, **layered** within a single rule — a rule may declare any combination of them and the algorithm walks them in fixed order, using the first that yields an eligible room (see [Decision 22](#agreed-decisions)):

1. **Pack spawning** — `spawn_with_typeclass: <dotted_path>` means "spawn me in a room that already contains a living instance of that typeclass" (e.g. a chieftain spawns where its pack already is).
2. **Den / lair** — `den_room_tag: <key>` means "spawn in this one specific tagged room" (single-room boss lair). Used as Step 2 when `spawn_with_typeclass` is absent or its leader can't be found / its room is full.
3. **Random within area** — implicit default; uniform pick from all rooms in the rule's `area_tag` that haven't hit `max_per_room`. Used when neither Step 1 nor Step 2 yielded a room.

All three respect `max_per_room`, which is enforced **per-rule** via the identity tags stamped at spawn (decision #9) — two rules sharing typeclass + area_tag cap their populations independently. No room dbrefs ever travel through rule data. Mixing two or more patterns in one rule is the intentional way to author "prefer pack-spawn, fall back to den, fall back to random" choreography (e.g. a champion that spawns next to its commander but retreats to its den if the commander is dead).

### Population maintenance

- `target: N` — how many of this rule's mobs should be alive at once.
- `respawn_seconds` — cooldown from the last spawn attempt for this rule. Counted from `last_spawn_time`. No callback required from the consumer.
- `death_cooldown_seconds` — cooldown from the kill time. Counted from `last_death_time` (set by the library's observation-based death detection). Still no callback required.

A rule sets one or the other, not both.

### Tags applied at spawn time

The library stamps four categories of tag on each new mob:

- **`area_tag`** under the configured category (decision #2) — drives the consumer's AI / wander constraint and is the room-pool key for spawn selection.
- **`mob_spawner_rule`** with value `str(rule_id)` — identity breadcrumb.
- **`mob_spawner_file`** with value of the script's `db_key` (the rule-set file path) — identity breadcrumb. Together with `mob_spawner_rule` it's the population discriminator the tick loop counts on (decision #9).
- **YAML-declared `tags`** from the rule's optional `tags:` field — bare strings (untyped) or `{key, category?}` dicts. Lets authors stamp arbitrary tags (e.g. FCM's `spawn_resources` / `spawn_gold` eligibility flags) without consumer-side code. Reserved category prefix `mob_spawner_` is refused at validation to prevent spoofing the identity tags.

The identity tags are *passive identifiers* — the library queries them, the typeclass doesn't read or write them. They are not a callback protocol; the library still observes the world rather than receiving notifications (decision #5).

### Death detection (observation, not notification)

The library observes deaths by comparing observed population to expected population per tick (see [The tick loop](#the-tick-loop) step 2). No callback from the typeclass is required or accepted.

**What this gives up:** sub-tick precision on death timing. Death is detected at the next tick after it happens (±tick_seconds). For cooldown timescales (typically minutes to hours), this is irrelevant.

**What this preserves:** the typeclass's complete ownership of its own death pipeline. The mob's `die()` does whatever the consumer wants (corpse, loot, XP, events) without calling anything on the library.

**Edge case — mobs leaving the area for non-death reasons** (teleport, charm-and-follow): observed count drops; library interprets as a death. False positive. Effect: cooldown clock restarts unnecessarily; one extra delay before the next spawn. Harmless and rare.

### ms_at_post_spawn

Per-spawn behaviour belongs to the typeclass, not the rule. If a typeclass defines a method named `ms_at_post_spawn(self) -> None`, the library invokes it on the new mob after the mob has been fully constructed and the rule's `attrs` overrides have been applied. If the method is absent, nothing happens — no error, no warning.

The library does NOT declare a YAML field for this hook. The consumer's existing pattern of a dotted-path string in the rule pointing at a module-level function has been deliberately replaced: scattered hook modules become hard to track as authoring scales, and "per-rule customization of a shared typeclass" is better expressed by subclassing the typeclass than by indirecting through a string.

The library's contract is duck-typed (`hasattr(mob, 'ms_at_post_spawn')` → invoke), one optional method name. See [Decision 23](#agreed-decisions).

## Script lifecycle operations

Mirroring world-builder's command pattern: scope-aware admin commands auto-installed into `AccountCmdSet`, `cmd:superuser()` locked, `ms_` prefix.

| Operation | Purpose | State preserved? |
|---|---|---|
| **Load** (upsert) | For each rule-set file in scope: validate → drain → swap → resume (see [Load protocol](#load-protocol)). Find or create its Script; replace `db.spawn_table` with current YAML; purge stale entries for removed rules. | Yes — `last_spawn_times`, `last_death_times`, `last_observed_counts` survive (snapshot before stop, restored after swap). |
| **Restart** | Drain → start the existing script's ticker without re-reading YAML. Recovery action for a script that appears stuck or stopped; works when YAML is currently unavailable (Reader fetch failed). | Yes — state preserved; rules unchanged. |
| **Stop** | Stop the tick on a script; keep the persistent script + state. Resumable via Restart or Load. | Yes — state preserved. |
| **Delete** | Remove the script entirely from the DB. | No — full clean slate. |
| **Status / inspect** | Read-only view of a script's state. One operator-facing line per matched script: ``<path>: <active|paused|stopped>, <N> rule(s), interval=<I>s, next=<T>s``. Per-rule detail is intentionally not included here — operators investigating a specific rule can read `mob_spawner.log`, `@scripts <path>` for the full attribute dump, or `ms_spawn_report` for the population census. | n/a — read-only. |
| **Spawn report** | Read-only population census per matched script: per-rule current-vs-target living counts. The detail `ms_status` omits. | n/a — read-only. |

The operator escalation ladder from lightest to heaviest intervention: **Status** (diagnose) → **Restart** (kick the ticker, keep everything) → **Load** (fresh YAML + restart) → **Stop** (intentional pause) → **Delete** (clean slate).

All six accept a scope query in the same form as world-builder's commands (`all`, `shard=X`, `shard=X zone=Y`, …). Scope resolution uses the Reader → Definitions → Finder pipeline when a query is non-empty; an `all` query bypasses the manifest and operates on every ``MobSpawnerScript`` instance in the DB (which catches orphan scripts whose source files have been removed from the manifest — see "Edge cases" below).

### Load protocol

Race protection between `ms_load` and an in-flight tick. For each rule-set file in scope:

1. **Validate** the YAML. Running script untouched on validation failure.
2. **Snapshot state** from the existing script's `db` (bookkeeping dicts).
3. **Graceful stop** — `script.stop_when_safe(timeout=60s)`. The script's tick loop checks a stop flag at safe points (between rule iterations within a tick) and acknowledges once in a consistent stopped state.
4. **Force stop on timeout** — if no ack within the timeout, `script.force_stop()`. Internal-only; not an operator command. State already snapshotted in step 2.
5. **Swap** — replace `db.spawn_table` with the new rules. Purge bookkeeping entries for removed rules. Restore the snapshot for rules that still exist.
6. **Resume** — `script.unpause()` if the script was running before the drain (the drain pauses it). A script that was already paused / stopped at deploy time is NOT resumed — the operator's explicit stop isn't second-guessed.

`ms_load` runs async (`run_async` / `deferToThread`, matching `wb_build`) so the reactor stays responsive while workers wait for stop acks. Each script transitions independently.

### at_server_start integration

**Status: pending implementation.** Operators currently run `ms_load <scope>` interactively after server start. The convenience helper described below is the planned shape.

When shipped, the library will expose a helper (name [TBD]) the consumer calls from `server/conf/at_server_startstop.py`. The helper takes a scope query and performs the upsert (same logic as `ms_load`). On cold start it creates missing scripts; on warm restart it finds existing scripts (whose state survived via Evennia's script persistence) and updates rules in place.

**Consumer-driven, not library-driven.** Each shard's bootstrap knows which rule-set files belong to it; the library doesn't need to know about shards or routers.

### Edge cases

- **Rule removed from YAML between deployments.** Load purges its `last_spawn_times` / `last_death_times` entries. Mobs from the removed rule keep living; nothing replaces them when they die.
- **Rule-set file removed from manifest entirely.** Existing script persists in the DB. **Cleanup is operator-driven** (`ms_stop` / `ms_delete`), not automatic at server start — silent vanishing is too easy to trigger by accident. Inspect surfaces it as a warning.

## Library / consumer ownership

| Concept | Library | Consumer |
|---|---|---|
| Rule schema (typeclass, target, cooldown, max_per_room, hooks) | ✓ | declares each rule in YAML |
| Persistent Script per rule-set file | ✓ | |
| Tag category for area discovery | configurable | sets the category name (default `"mob_area"`) |
| Room selection (3-tier fallback) | ✓ | |
| Tick loop, cooldown bookkeeping, death detection | ✓ | |
| Re-tag spawned mob with rule's `area_tag` | ✓ | |
| Additional per-spawn tagging (loot categories, etc.) | | ✓ via typeclass `at_object_creation` or `ms_at_post_spawn` |
| `ms_at_post_spawn()` invocation | invokes if present (duck-typed) | writes the method on the typeclass (optional) |
| Typeclass behaviour (AI, combat, loot, death) | | ✓ |
| `at_server_start` wiring | provides helper *[name TBD]* | calls helper with shard's scope |
| Tags ON the rooms | | placed by whatever puts them there (FCM uses `evennia-world-builder`) |

## Agreed decisions

1. **Area-tag category is a library-level setting, not per-rule.** Set once at the consuming game's level (likely `MOB_SPAWNER_AREA_TAG_CATEGORY` or similar). Vocabulary choice, not a per-rule choice.
2. **Library re-tags the spawned mob with the rule's `area_tag`** in the configured category. Symmetric with the room query, useful by default (consumer AI / wander logic can read the same tag).
3. **Tick interval is a library-level setting** (likely `MOB_SPAWNER_TICK_SECONDS`). Single global value; tuning knob for the whole system. Per-rule and per-script overrides rejected — per-rule-set-file scripts make the per-rule case unmotivated.
4. **One persistent Script per rule-set YAML file.** Multiple scripts in the running game; each owns its own subset of rules. Plays naturally with sharding (each shard loads its own files; router loads none).
5. **Library observes deaths via tick-time count delta, not via callback.** No behaviour-coupling breadcrumbs on spawned mobs (the typeclass is not asked to phone home on death), no `on_death()` API. The library's behavioural surface to the typeclass after spawn is zero. The detection formula is `deaths = (last_observed_count + spawned_last_tick) - current_count`. Note that the library DOES stamp identity tags (`mob_spawner_rule`, `mob_spawner_file`) for population accounting — those are passive identifiers the library queries, not protocol the typeclass interacts with.
6. **Pipeline shape mirrors world-builder up to the terminal stage.** Reader → Definitions → Finder → Loader → Validator → Upsert. The terminal stage is upsert-with-state-preservation rather than tag-sweep-and-rebuild — the load-bearing distinction from world-builder.
7. **Same operator command pattern as world-builder.** Scope-aware admin commands (`ms_load all` / `ms_load shard=X` / etc.), auto-installed into `AccountCmdSet` via the library's AppConfig.ready(), `cmd:superuser()` locked, `ms_` prefix. Operations: `ms_load` (upsert), `ms_restart` (kick the ticker without YAML reload), `ms_stop` (graceful), `ms_delete` (clean removal), `ms_status` (read-only inspect), `ms_spawn_report` (read-only per-rule population census).
8. **`at_server_start` is consumer-driven.** Library provides the helper; consumer's gamedir wires it into its `at_server_startstop.py`. Library does not assume anything about the consumer's lifecycle hooks.
9. **Population identity is keyed on (file, rule_id), not on (typeclass, area_tag).** Each rule maintains a distinct population; the library counts living mobs by querying for the two identity tags (`mob_spawner_rule` = `str(rule_id)`, `mob_spawner_file` = script's `db_key`) stamped at spawn. Validator enforces unique `rule_id` per file; file paths are unique by definition — so `(file, rule_id)` is a structurally guaranteed unique discriminator. Enables the "indistinguishable variant" pattern (same `key`, optionally same `typeclass`, optionally same `area_tag`, different rule_id, different `attrs`) where population ratios produce emergent loot variation deterministically — compliance-relevant because deterministic supply isn't gambling. Earlier framing keyed counting on (typeclass, area_tag) and required distinct typeclass per variant; this was relaxed once identity tags landed.
10. **Rule identity is an author-supplied `rule_id` integer, unique within file.** Bookkeeping keyed on `rule_id` (file implicit per-script); global identity `(rule_file, rule_id)` for operator surfaces. Stable across YAML reordering / field edits; changing the ID is the explicit signal that the rule is now a different rule.
11. **First-tick initial values are `last_observed_count = 0` and `spawned_last_tick = 0`.** Death-detection formula naturally produces non-positive results when pre-existing mobs are in the world. No special-case first-tick branch.
12. **Rule schema v0 = today's FCM JSON fields + `rule_id`.** See [Rule schema (v0)](#rule-schema-v0). Iterate as concrete needs arise.
13. **`ms_load` race protocol: validate → snapshot → drain (60s timeout) → force-stop if needed → swap → resume.** Async (`run_async`). State preserved by snapshotting before the stop signal; safe under both graceful and forced stops. `force_stop` is internal — not exposed to operators.
14. **Spawn-time errors are caught and logged, not raised.** Each `create_object` call (and its surrounding hook invocation) runs inside `try / except`. On failure (unresolvable typeclass after consumer code changed, attribute-application error, etc.) the library logs the error with rule context and the tick continues. One bad rule doesn't take down the script.
15. **Empty `area_tag` queries are detected and logged.** Before the room-selection step, if zero rooms match the rule's `area_tag` under the configured category, the library logs a warning (with rule context). Skip the spawn. Repeat detection per tick — operators may be in the middle of deploying world content; the rule starts working as soon as tagged rooms appear.
16. **`den_room_tag` uses the same tag category as `area_tag`.** No second category. Consumers wanting to distinguish "group of rooms" from "single room" can comment their YAML to make the intent clear. Keep the library surface minimal.
17. **Library logs via Evennia's `evennia.utils.logger.log_file()`** to a dedicated filename (`mob_spawner.log`) inside `settings.LOG_DIR` — colocated with `server.log` / `portal.log`, distinct file. Thread-safe out of the box; zero consumer-side plumbing beyond what Evennia already configures. No Python `logging` module wiring, no custom `FileHandler`, no settings to add. Errors (decision #14), warnings (decision #15), and lifecycle events all route here. Nothing reaches `server.log` from this library.
18. **`ms_restart` is a first-class operator command.** Kicks the ticker on an existing script without re-reading YAML; preserves state. Recovery action for stuck / stopped scripts; works when YAML is unavailable (Reader fetch failed). Sits between `ms_status` and `ms_load` on the escalation ladder. Composition of `script.stop_when_safe()` + `script.start()` — both primitives already needed for `ms_stop` and `ms_load`.
19. **Validation gating mirrors world-builder.** Three predicate tiers (shape / per-file uniqueness / engine-resolvability). No cross-rule-references tier — mob-spawner has no cross-rule references. (A non-refusing Tier 4 *diagnostics* layer was added later — see decision #24.) `repo-ci-pre-validation` flag in `definitions.yaml` (same name as world-builder) lets a consumer skip whole-repo Tier 1+2 in `ms_load` when CI has already gated the YAML. `ms_load --force-validate` overrides per-invocation. Standalone **`ms-validate`** CLI runs Tier 1+2 without Evennia for local iteration / pre-commit / CI. See [Validation tiering and gating](#validation-tiering-and-gating).
20. **Loader uses world-builder's `file_metadata` pattern.** Each leaf rule-set file is a top-level mapping with one canonical list-bearing key (`rules:`); any other top-level keys are bagged into `LoadResult.file_metadata[path]` as `{key: value}` for downstream stages to look up. The library doesn't curate which keys exist — none are recognised today, the slot exists so per-file directives can land later without a schema break. Mirrors `evennia-world-builder`'s `entities:` + `incoming_exits:` / `links:` shape so authors see the same file-shape conventions across both libraries; a file appears in `file_metadata` only if it declared at least one non-`rules:` key (clean-by-default).
21. **Mandatory-vs-optional follows the consumer baseline.** The schema's required-field set is calibrated against the existing FCM `world/spawns/*.json` corpus (87 rules across 10 zones). Fields that 100% of authored rules set are mandatory in the library (`typeclass`, `key`, `area_tag`, `target`, `max_per_room`, plus exactly one of the cooldown pair). Fields that the consumer uses optionally are optional here (`desc`, `attrs`, `spawn_with_typeclass`, `den_room_tag`). Cooldown pair is mandatory **as a pair** but mutually exclusive — empirically every consumer rule declares exactly one, never both, never neither, so the contract resolves the earlier "one or the other" ambiguity to **exactly one of**. Two schema departures from the consumer baseline rather than direct ports: `rule_id` is library-mandatory but doesn't appear in the consumer baseline (the consumer used `f"{typeclass}:{area_tag}"` as bookkeeping handle; decision #10 replaced that with author-supplied integer IDs); and the consumer's `post_spawn_hook: <dotted_path>` YAML field is NOT carried into the library — replaced by the `ms_at_post_spawn()` method-on-typeclass protocol (decision #23).
22. **Room-selection patterns layer within a single rule; not mutually exclusive.** `spawn_with_typeclass`, `den_room_tag`, and the implicit random-area default can all coexist in one rule. The room-selection algorithm walks them in fixed order (pack → den → random) and uses the first that yields an eligible room. Mixing patterns is the explicit way to author meaningful choreography (e.g. "spawn next to the boss, but fall back to the den if the boss is dead, then to random within the area"). The validator does NOT refuse rules that set multiple patterns — the algorithm's natural fallthrough is the intended semantic. The architecture text's earlier "three patterns, in order of specificity" phrasing was clarified here to make the layering explicit.
23. **Per-spawn behaviour is a typeclass method, not a YAML field.** The library invokes `mob.ms_at_post_spawn()` if the spawned typeclass defines that method; absent, nothing happens. Duck-typed protocol: one optional method name, no inheritance demands, no required base class. This is a deliberate departure from the consumer baseline's `post_spawn_hook: <dotted_path>` YAML field — the existing FCM pattern wasn't designed with loose coupling in mind, and "scattered hook functions across shared modules" becomes hard to track as authoring scales. Per-rule customization of a shared typeclass is now expressed via subclassing (different behaviour → different typeclass), the OO-correct pattern. **The `ms_` prefix marks library provenance; the `at_` element follows Evennia's `at_object_creation`/`at_post_move`/etc. convention.** Tier 3 validates: if the resolved typeclass declares `ms_at_post_spawn`, it must be callable AND callable as `mob.ms_at_post_spawn()` (zero required args after `self` — catches `def ms_at_post_spawn(self, foo):` typos at validation time, not runtime). This is a measured exception to decision #3's "declares no protocol" stance: the protocol is optional, narrow (one method name), and the locality benefit (post-spawn behaviour lives with the typeclass code) is real and outweighs the tiny protocol-surface cost.
24. **Tag-existence is a Tier 4 deploy-time diagnostic, not a validation refusal.** Validator passes rules whose `area_tag` / `den_room_tag` reference tags with zero matching rooms — the operator may be deploying world content in parallel; refusing would break that workflow. A separate `EVENNIA_DIAGNOSTICS` tuple on the Validator holds side-effecting `_diagnostic_*` functions with signature `(LoadedRule) -> None`. They run only with `evennia_runtime=True`, only on rules that survived Tier 1, and emit a single WARN log per missing-tag rule (so the operator sees the issue once at deploy time instead of 240+ times/hour at tick time). Tick-time logging (decision #15) remains as the runtime fallback for tags that go missing post-deploy. Tier 4's contract is deliberately distinct from predicates: side effects allowed, no return value to inspect, no findings collected — naming convention `_diagnostic_*` (vs predicates' `_check_*`) marks the difference at every call site. The tag-category queried is `MOB_SPAWNER_AREA_TAG_CATEGORY` (defaulting to `"mob_area"` per decision #1).
25. **`MobSpawnerScript` inherits from `settings.BASE_SCRIPT_TYPECLASS`, not `evennia.DefaultScript` directly.** Defined via `class_from_module(getattr(settings, "BASE_SCRIPT_TYPECLASS", "evennia.DefaultScript"))` at module-import time — same pattern as sibling library `evennia-shards` uses for `WEBSOCKET_PROTOCOL_CLASS`. Consumer customisations on their base script (logging, telemetry, dashboard registration, etc.) compose into the library's scripts automatically; the consumer never has to choose between library benefits and their own customisations. The library doesn't strictly need this — `DefaultScript` primitives are sufficient for the tick loop's operation — but the consumer composability benefit is real and the cost (one `class_from_module` call, fallback to `DefaultScript` if the setting is missing) is small. Implicit contract: the consumer's base must behave like a `DefaultScript` subclass (e.g. if they override `at_repeat`, they must call `super().at_repeat()` so the library's tick loop runs). Documented as a known integration concern.

## The seam with world-builder

The coupling between this library and `evennia-world-builder` is a **single string convention**: the tag category name. World-builder doesn't know `mob_area` (or whatever the consumer chose) means anything — it just places the tags the consumer's YAML declares. Mob-spawner queries that category. The two libraries never import each other; they meet at the Evennia tag table.

For FCM this means: rooms in `fcm-world` carry `mob_area:<area_key>` tags placed by `wb_build`; spawn rules in the mob-spawner content repo reference the same `area_key`s; nothing extra is required for the two systems to compose.

Both libraries share `evennia-yaml-reader` as a runtime dependency for fetching YAML.

## Open questions

`[TBD — needs discussion]`:

- **`at_server_start` helper name.** Settle during implementation.
