# Architecture

High-level mapping of the spawn system's mechanisms and the library / consumer boundary for each. This is the architectural pass — captures decisions agreed in conversation before any code lands. As specific mechanisms are implemented, focused docs (`rule-schema.md`, `script-lifecycle.md`, …) will drill into them and supersede the relevant rows below.

## Guiding principle

**Settings belong in YAML. Behaviour belongs in typeclasses.** The library reads settings from YAML (what spawns, where, how often, max count) and runs the loop that observes the world and creates objects. Everything that happens *to* a spawned mob — its AI, its combat, its death — is the typeclass's. The library and the typeclass meet at exactly one moment: spawn time. After that the mob is the typeclass's concern entirely.

This means: **the library has no callbacks into the typeclass, no protocol the typeclass must implement, no breadcrumbs on the mob pointing back at the library.** It observes the world (count queries by tag and typeclass) and acts on what it observes.

## Pipeline shape

The same pipeline as [`evennia-world-builder`](https://github.com/FullCircleMUD/evennia-world-builder), up to but not including the terminal stage:

```
Reader → Definitions → Finder → Loader → Validator → Upsert
```

- **Reader** — fetches YAML from a configured source ([`evennia-yaml-reader`](https://github.com/FullCircleMUD/evennia-yaml-reader)).
- **Definitions** — parses a `definitions.yaml` at the root of the rule-set content repo. Declares levels (e.g. `levels: [shard, zone]` for FCM) so scoped commands can navigate the manifest tree.
- **Finder** — walks the per-folder `index.yaml` manifest following an operator query.
- **Loader** — reads matching rule-set files into the rule data structure.
- **Validator** — predicate-tier checks (rule shape, typeclass resolvability, …) before any DB mutation.
- **Upsert** — terminal stage. **Different from world-builder.** For each rule-set file in scope, find the existing persistent Script (or create one), replace its in-memory rule table with the YAML's current rules, **preserve runtime state** (cooldown / observation history). The script's tick continues uninterrupted.

The Reader / Definitions / Finder / Loader / Validator stages are conceptually identical to world-builder's. They are duplicated, not shared, until a third consumer makes extraction worthwhile.

## Validation tiering and gating

Same model as world-builder; predicates split by whether they need a running Evennia engine:

| Tier | Checks | Needs Evennia? | Where it runs |
|---|---|---|---|
| 1 — Shape | Required fields, types, well-formedness, field-pair exclusivity (`respawn_seconds` xor `death_cooldown_seconds`) | No | `ms-validate` CLI, `ms_load` |
| 2 — Per-file uniqueness | `rule_id` unique within file | No | `ms-validate` CLI, `ms_load` |
| 3 — Engine resolvability | `typeclass` actually importable, `post_spawn_hook` dotted path resolves | Yes | `ms_load` only |
| 4 — Cross-refs | *(omitted; mob-spawner has no cross-rule references)* | n/a | n/a |

### Gating

A flag in the consumer's `definitions.yaml` — same name as world-builder's — controls whether `ms_load` pre-validates the whole repo on every invocation:

```yaml
repo-ci-pre-validation: true   # default: false
```

- **`false`** (default): `ms_load` walks the whole repo and runs Tier 1+2 before any in-scope work. Safe; expensive at scale.
- **`true`**: `ms_load` trusts the consumer's CI gate. Runs only Tier 3 on the in-scope files. Whole-repo Tier 1+2 is skipped on the assumption that CI has already enforced it on the YAML at merge time.

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

Inherits today's FCM JSON shape, plus `rule_id`. Fields settle as work lands; iterate when concrete needs surface.

| Field | Required | Notes |
|---|---|---|
| `rule_id` | ✓ | Integer, unique within file. |
| `typeclass` | ✓ | Dotted path. Exact match for counts (see [Decision 9](#agreed-decisions)). |
| `key` | ✓ | Spawned mob's display name. Same key across typeclasses is permitted — enables the "indistinguishable variant" pattern. |
| `area_tag` | ✓ | Tag key under the configured category. |
| `target` | ✓ | Population cap. |
| `respawn_seconds` / `death_cooldown_seconds` | one or the other | Mutually exclusive. |
| `max_per_room` | optional | Default `[TBD]` during implementation. |
| `desc` | optional | Description override; falls back to typeclass default if absent. |
| `attrs` | optional | Dict of per-rule attribute overrides applied to the spawned mob. |
| `post_spawn_hook` | optional | Dotted path; called with the new mob after creation. |
| `spawn_with_typeclass` | optional | Pack-spawn trigger — room must contain a living instance of this typeclass. |
| `den_room_tag` | optional | Single-room lair. Tag-category question deferred — see [Open questions](#open-questions). |

## The tick loop

Each script ticks every `MOB_SPAWNER_TICK_SECONDS` (library-level setting, single value for the whole consuming game). On each tick, for each rule the script holds:

1. **Observe** — count living mobs matching this rule's typeclass (**exact match**, not subclass-inclusive) AND tagged with the rule's `area_tag`.
2. **Detect deaths** — `deaths = (last_observed_count + spawned_last_tick) - current_count`. If positive, stamp `last_death_time = now`. (`spawned_last_tick` accounts for the script's own spawns inflating the population between observations.)
3. **Cooldown check** — if the rule uses `respawn_seconds` or `death_cooldown_seconds`, compare against `last_spawn_time` or `last_death_time` respectively. Skip if the cooldown hasn't elapsed.
4. **Population check** — skip if the rule is already at `target`.
5. **Room selection** — pick an eligible room (see [Room selection](#room-selection-three-tier-fallback)).
6. **Spawn** — `create_object(typeclass=..., location=..., ...)`. Apply rule-declared tags. Re-tag with the rule's `area_tag`. Invoke `post_spawn_hook` if declared.
7. **Save state** — `last_observed_count = current_count`, `spawned_last_tick = spawned_this_tick`, `last_spawn_time = now` (if spawned).

Step 1 happens *before* the spawn decision, so each tick's observation reflects the world as left by previous ticks — without double-counting this tick's own spawn.

**Initial values on first tick of a fresh script:** `last_observed_count = 0`, `spawned_last_tick = 0`. The death-detection formula naturally handles this — when pre-existing mobs are in the world, `deaths` comes out non-positive (no event). No special-case branch needed.

## Mechanisms

### Targeting — rooms eligible to spawn into

Rooms carry an Evennia tag whose **category is consumer-configured** (default `"mob_area"`) and whose **key is the rule's `area_tag`**. The library finds spawn-eligible rooms via a tag query against that category + key. The library has no opinion on how the tags get onto the rooms — in the FCM stack they're authored in YAML and placed by `evennia-world-builder`, but mob-spawner reads the tag table regardless of who wrote it.

### Room-selection (three-tier fallback)

Three patterns, in order of specificity:

1. **Pack spawning** — `spawn_with_typeclass: <dotted_path>` means "spawn me in a room that already contains a living instance of that typeclass" (e.g. a chieftain spawns where its pack already is).
2. **Den / lair** — `den_room_tag: <key>` means "always spawn in this one specific tagged room" (single-room boss lair).
3. **Random within area** — default; uniform pick from all rooms in the rule's `area_tag` that haven't hit `max_per_room`.

All three respect `max_per_room`. All three are tag-or-typeclass queries; no room dbrefs ever travel through rule data.

### Population maintenance

- `target: N` — how many of this rule's mobs should be alive at once.
- `respawn_seconds` — cooldown from the last spawn attempt for this rule. Counted from `last_spawn_time`. No callback required from the consumer.
- `death_cooldown_seconds` — cooldown from the kill time. Counted from `last_death_time` (set by the library's observation-based death detection). Still no callback required.

A rule sets one or the other, not both.

### Tags applied at spawn time

On every spawn the library stamps the new mob with:

- The same `area_tag` it queried (library re-tags the mob with the rule's `area_tag` in the configured category). Useful by default: the consumer's AI / wander logic can read the same tag to constrain movement.
- Any additional tags the rule declares — the library applies them generically without interpreting their meaning. Consumer-defined loot tags (e.g. FCM's `spawn_resources`, `spawn_gold`, `spawn_scrolls`, `spawn_recipes`) ride this generic mechanism.

**No breadcrumb attributes pointing back at the library.** The library doesn't need them; it observes the world rather than receiving notifications.

### Death detection (observation, not notification)

The library observes deaths by comparing observed population to expected population per tick (see [The tick loop](#the-tick-loop) step 2). No callback from the typeclass is required or accepted.

**What this gives up:** sub-tick precision on death timing. Death is detected at the next tick after it happens (±tick_seconds). For cooldown timescales (typically minutes to hours), this is irrelevant.

**What this preserves:** the typeclass's complete ownership of its own death pipeline. The mob's `die()` does whatever the consumer wants (corpse, loot, XP, events) without calling anything on the library.

**Edge case — mobs leaving the area for non-death reasons** (teleport, charm-and-follow): observed count drops; library interprets as a death. False positive. Effect: cooldown clock restarts unnecessarily; one extra delay before the next spawn. Harmless and rare.

### post_spawn_hook

Optional `post_spawn_hook: <dotted_path>` in the rule. After the mob is created (and after `at_object_creation` has fired in the typeclass), the library resolves the dotted path and calls it with the new mob. Used for special-case state init that wouldn't be set correctly by typeclass defaults alone.

The library owns the invocation machinery; the consumer writes the hook function. Generic, narrow, opt-in.

## Script lifecycle operations

Mirroring world-builder's command pattern: scope-aware admin commands auto-installed into `AccountCmdSet`, `cmd:superuser()` locked, `ms_` prefix.

| Operation | Purpose | State preserved? |
|---|---|---|
| **Load** (upsert) | For each rule-set file in scope: validate → drain → swap → resume (see [Load protocol](#load-protocol)). Find or create its Script; replace `db.spawn_table` with current YAML; purge stale entries for removed rules. | Yes — `last_spawn_times`, `last_death_times`, `last_observed_counts` survive (snapshot before stop, restored after swap). |
| **Restart** | Drain → start the existing script's ticker without re-reading YAML. Recovery action for a script that appears stuck or stopped; works when YAML is currently unavailable (Reader fetch failed). | Yes — state preserved; rules unchanged. |
| **Stop** | Stop the tick on a script; keep the persistent script + state. Resumable via Restart or Load. | Yes — state preserved. |
| **Delete** | Remove the script entirely from the DB. | No — full clean slate. |
| **Status / inspect** | Read-only view of a script's rules, cooldowns, population counts, last tick. Surfaces "this script's backing file is missing from the manifest" warnings. | n/a — read-only. |

The operator escalation ladder from lightest to heaviest intervention: **Status** (diagnose) → **Restart** (kick the ticker, keep everything) → **Load** (fresh YAML + restart) → **Stop** (intentional pause) → **Delete** (clean slate).

All four accept a scope query in the same form as world-builder's commands (`all`, `shard=X`, `shard=X zone=Y`, …). Scope resolution uses the same Reader / Definitions / Finder pipeline as load.

### Load protocol

Race protection between `ms_load` and an in-flight tick. For each rule-set file in scope:

1. **Validate** the YAML. Running script untouched on validation failure.
2. **Snapshot state** from the existing script's `db` (bookkeeping dicts).
3. **Graceful stop** — `script.stop_when_safe(timeout=60s)`. The script's tick loop checks a stop flag at safe points (between rule iterations within a tick) and acknowledges once in a consistent stopped state.
4. **Force stop on timeout** — if no ack within the timeout, `script.force_stop()`. Internal-only; not an operator command. State already snapshotted in step 2.
5. **Swap** — replace `db.spawn_table` with the new rules. Purge bookkeeping entries for removed rules. Restore the snapshot for rules that still exist.
6. **Resume** — `script.start()`.

`ms_load` runs async (`run_async` / `deferToThread`, matching `wb_build`) so the reactor stays responsive while workers wait for stop acks. Each script transitions independently.

### at_server_start integration

The library exposes a helper (name [TBD]) the consumer calls from `server/conf/at_server_startstop.py`. The helper takes a scope query and performs the upsert (same logic as `ms_load`). On cold start it creates missing scripts; on warm restart it finds existing scripts (whose state survived via Evennia's script persistence) and updates rules in place.

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
| Apply additional rule-declared tags | applies generically | declares which tags in YAML |
| post_spawn_hook invocation | invokes | writes the function |
| Typeclass behaviour (AI, combat, loot, death) | | ✓ |
| `at_server_start` wiring | provides helper | calls helper with shard's scope |
| Tags ON the rooms | | placed by whatever puts them there (FCM uses `evennia-world-builder`) |

## Agreed decisions

1. **Area-tag category is a library-level setting, not per-rule.** Set once at the consuming game's level (likely `MOB_SPAWNER_AREA_TAG_CATEGORY` or similar). Vocabulary choice, not a per-rule choice.
2. **Library re-tags the spawned mob with the rule's `area_tag`** in the configured category. Symmetric with the room query, useful by default (consumer AI / wander logic can read the same tag).
3. **Tick interval is a library-level setting** (likely `MOB_SPAWNER_TICK_SECONDS`). Single global value; tuning knob for the whole system. Per-rule and per-script overrides rejected — per-rule-set-file scripts make the per-rule case unmotivated.
4. **One persistent Script per rule-set YAML file.** Multiple scripts in the running game; each owns its own subset of rules. Plays naturally with sharding (each shard loads its own files; router loads none).
5. **Library observes deaths via tick-time count delta, not via callback.** No breadcrumbs on spawned mobs, no `on_death()` API. The library's surface to the typeclass after spawn is zero. The detection formula is `deaths = (last_observed_count + spawned_last_tick) - current_count`.
6. **Pipeline shape mirrors world-builder up to the terminal stage.** Reader → Definitions → Finder → Loader → Validator → Upsert. The terminal stage is upsert-with-state-preservation rather than tag-sweep-and-rebuild — the load-bearing distinction from world-builder.
7. **Same operator command pattern as world-builder.** Scope-aware admin commands (`ms_load all` / `ms_load shard=X` / etc.), auto-installed into `AccountCmdSet` via the library's AppConfig.ready(), `cmd:superuser()` locked, `ms_` prefix. Operations: `ms_load` (upsert), `ms_restart` (kick the ticker without YAML reload), `ms_stop` (graceful), `ms_delete` (clean removal), `ms_status` (read-only inspect).
8. **`at_server_start` is consumer-driven.** Library provides the helper; consumer's gamedir wires it into its `at_server_startstop.py`. Library does not assume anything about the consumer's lifecycle hooks.
9. **Typeclass count matching is exact, not subclass-inclusive.** Each rule maintains a distinct population; subclasses are managed by their own rules. Enables the "indistinguishable variant" pattern (same `key`, different typeclass, different loot) where population ratios produce emergent loot variation deterministically — compliance-relevant because deterministic supply isn't gambling.
10. **Rule identity is an author-supplied `rule_id` integer, unique within file.** Bookkeeping keyed on `rule_id` (file implicit per-script); global identity `(rule_file, rule_id)` for operator surfaces. Stable across YAML reordering / field edits; changing the ID is the explicit signal that the rule is now a different rule.
11. **First-tick initial values are `last_observed_count = 0` and `spawned_last_tick = 0`.** Death-detection formula naturally produces non-positive results when pre-existing mobs are in the world. No special-case first-tick branch.
12. **Rule schema v0 = today's FCM JSON fields + `rule_id`.** See [Rule schema (v0)](#rule-schema-v0). Iterate as concrete needs arise.
13. **`ms_load` race protocol: validate → snapshot → drain (60s timeout) → force-stop if needed → swap → resume.** Async (`run_async`). State preserved by snapshotting before the stop signal; safe under both graceful and forced stops. `force_stop` is internal — not exposed to operators.
14. **Spawn-time errors are caught and logged, not raised.** Each `create_object` call (and its surrounding hook invocation) runs inside `try / except`. On failure (unresolvable typeclass after consumer code changed, attribute-application error, etc.) the library logs the error with rule context and the tick continues. One bad rule doesn't take down the script.
15. **Empty `area_tag` queries are detected and logged.** Before the room-selection step, if zero rooms match the rule's `area_tag` under the configured category, the library logs a warning (with rule context). Skip the spawn. Repeat detection per tick — operators may be in the middle of deploying world content; the rule starts working as soon as tagged rooms appear.
16. **`den_room_tag` uses the same tag category as `area_tag`.** No second category. Consumers wanting to distinguish "group of rooms" from "single room" can comment their YAML to make the intent clear. Keep the library surface minimal.
17. **Library logs via Evennia's `evennia.utils.logger.log_file()`** to a dedicated filename (`mob_spawner.log`) inside `settings.LOG_DIR` — colocated with `server.log` / `portal.log`, distinct file. Thread-safe out of the box; zero consumer-side plumbing beyond what Evennia already configures. No Python `logging` module wiring, no custom `FileHandler`, no settings to add. Errors (decision #14), warnings (decision #15), and lifecycle events all route here. Nothing reaches `server.log` from this library.
18. **`ms_restart` is a first-class operator command.** Kicks the ticker on an existing script without re-reading YAML; preserves state. Recovery action for stuck / stopped scripts; works when YAML is unavailable (Reader fetch failed). Sits between `ms_status` and `ms_load` on the escalation ladder. Composition of `script.stop_when_safe()` + `script.start()` — both primitives already needed for `ms_stop` and `ms_load`.
19. **Validation gating mirrors world-builder.** Three tiers (shape / per-file uniqueness / engine-resolvability); no Tier 4 (no cross-rule references). `repo-ci-pre-validation` flag in `definitions.yaml` (same name as world-builder) lets a consumer skip whole-repo Tier 1+2 in `ms_load` when CI has already gated the YAML. `ms_load --force-validate` overrides per-invocation. Standalone **`ms-validate`** CLI runs Tier 1+2 without Evennia for local iteration / pre-commit / CI. See [Validation tiering and gating](#validation-tiering-and-gating).

## The seam with world-builder

The coupling between this library and `evennia-world-builder` is a **single string convention**: the tag category name. World-builder doesn't know `mob_area` (or whatever the consumer chose) means anything — it just places the tags the consumer's YAML declares. Mob-spawner queries that category. The two libraries never import each other; they meet at the Evennia tag table.

For FCM this means: rooms in `fcm-world` carry `mob_area:<area_key>` tags placed by `wb_build`; spawn rules in the mob-spawner content repo reference the same `area_key`s; nothing extra is required for the two systems to compose.

Both libraries share `evennia-yaml-reader` as a runtime dependency for fetching YAML.

## Open questions

`[TBD — needs discussion]`:

- **Default value for `max_per_room`** when unspecified. Probably 1; confirm during implementation.
- **Default tick interval value.** Today's FCM script uses 15s — reasonable starting point.
- **`at_server_start` helper name.** Settle during implementation.
- **`ms_status` output shape.** What information it displays and how it formats. Deferred — better scoped once the library has been exercised in practice.
