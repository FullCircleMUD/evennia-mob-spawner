# Interoperability

This library against every sibling library in `libraries/`. What it does that can constrain, or be
constrained by, a sibling: it reads rule-set YAML through a pluggable reader, creates one **persistent
`MobSpawnerScript` per rule-set file**, dispatches its deploy pipeline **off the reactor thread**, and
calls `create_object` on every tick to place mobs into rooms it locates **by tag**.

## evennia-ai-memory

**No coupling.** Neither imports the other. Memory and lore retrieval hang off NPCs that already
exist; this library creates objects and counts them, and never reads or writes what an NPC
remembers. The one thing worth knowing is a consequence of the tick rather than an interaction:
a mob this library culls or respawns is a new object, so anything keyed to the old object's identity
is keyed to something that no longer exists. Whether that matters is ai-memory's question to answer,
not ours to prevent.

## evennia-archive

**No coupling.** Neither imports the other. A spawned mob is deliberately disposable — the tick loop
culls and replaces them, and a rule's identity lives in YAML rather than in any object — so there is
nothing here that has to survive a rebuild of the game database.

The open question is whether anything a *character* holds can point at a spawned mob, which would
make that reference archive's to dispose of. That constraint belongs to archive's data model, and is
tracked in [its own interoperability entry](../../evennia-archive/docs/interoperability.md) rather
than restated here.

## evennia-calendar

**No coupling.** Neither imports the other. Cooldowns here are real seconds from `time.time()`, not
game-world dates, so a calendar that redefines what a day means does not move a respawn timer.
Time-of-day spawn gating is deliberately out of scope — see `CLAUDE.md` § Out of scope.

## evennia-database-cascade

**No coupling.** This library owns no tables and declares no alias: its state lives on
`MobSpawnerScript` as Evennia Attributes, in the game database, and its content lives in a YAML repo
read through `evennia-yaml-reader`. There is nothing here for the cascade to resolve, so no
`db_spec.py` and no dependency.

## evennia-equipment

**No coupling.** Neither imports the other. A rule's `attrs:` and `tags:` are applied generically to
whatever typeclass the rule names, so a consumer spawning equipped mobs does it in their own
`ms_at_post_spawn()` hook — the library neither knows nor needs to know that a wearslot exists.

## evennia-llm-service

**No coupling.** Neither imports the other. This library makes no LLM calls and holds no prompts.

## evennia-logging-extension

**Hard dependency.** Imported unconditionally — `make_logger` in
[`log.py`](../src/evennia_mob_spawner/log.py), which binds `ms_log` to this library's own
`mob_spawner.log`. It is the only path by which this library writes a log line.

No constraint travels either way beyond the extension's own install rule: a library import in the
consumer's settings module goes below `from evennia.settings_default import *`, because that is what
sets `LOG_DIR`. See [logging.md](logging.md).

## evennia-message-bus

**No coupling.** Neither imports the other. Deployment is per-instance and deliberately so: `ms_load`
refuses to run anywhere but the instance the rules name, and the scripts it creates live in that
instance's database. Nothing here needs to tell another instance anything.

The one thing that might is `ms_spawn_report`, whose whole-world census no single instance can answer.
Collecting it over the bus is one of the candidates under [evennia-scaling](#evennia-scaling), and
is not decided.

## evennia-mob-spawner

This library.

## evennia-portal-multiplex

**No coupling.** Neither imports the other. This library touches no session, no portal service and no
connection — it creates objects on a timer inside one server, and a player being redirected between
servers is invisible to it.

## evennia-scaling

**Optional integration.** Neither library imports the other at module scope. This library detects
scaling behind a `try` import and, when it is absent, every check below is skipped and the commands
behave exactly as they do standalone. No setting to flip either way.

Scaling runs each instance on its own Evennia database, and no instance can see another's. That is
what the gates are for: a rule set belongs to one instance, and the commands that deploy or operate on
it only reach the instance running them.

`examples/scaling_demo/` is a working deployment of everything below — a router and two shards, a rule
set each. It is the executable reference for this section: the prose can go quietly stale, the demo
cannot, because it boots. It is also the only place the real imports in `config.py` run; the library's
own suite injects stand-ins.

**Detection is the import.** Scaling has no single-instance mode and refuses to boot an instance whose
`SCALING_ROLE` is neither `router` nor `shard`, so an import that succeeds on a running server means a
multi-instance deployment. The instance id is multiplex's `MULTIPLEX_INSTANCE_ID`, read through
`evennia_portal_multiplex.config.get_instance_id()` — scaling mints none of its own, and a second
source for the same fact would only give the two somewhere to disagree.

**The first declared level must be named `shard`.** When co-installed with scaling, the first entry in
`definitions.yaml`'s `levels:` list must be `shard`, and its value is the instance id the rules under
it are intended to run on. Level names are otherwise consumer-chosen, so this is the one naming rule
the pairing imposes — it is what lets `ms_load` tell which instance a rule set belongs to. The word
matches scaling's own vocabulary (`SCALING_SHARDS`, `SCALING_ROLE=shard`), so the manifest says what
the settings say.

The mandate is enforced, not merely documented: once `definitions.yaml` is parsed, `ms_load` refuses
outright if `shard` is not the first declared level. Nothing else catches that — a consumer who
co-installs scaling but keeps their own level names has queries that validate cleanly against their own
declarations.

### Commands are gated to the instance they can act on

A command's effect stops at the instance running it, either because it writes rows only that instance
holds, or because it reaches for a script's live `ndb._task`, which exists only where the script runs.
Five commands therefore carry the same scope gate.

| Command | Gate | Why |
|---|---|---|
| `ms_load` | shard-scoped | deploys into this instance only |
| `ms_stop` | shard-scoped | `pause()` reads the local `ndb._task` |
| `ms_restart` | shard-scoped | `unpause()` / `start()` attach locally |
| `ms_delete` | shard-scoped | stops the local task, then removes the row |
| `ms_status` | shard-scoped | reads the local `ndb._task` to tell a stalled script from a ticking one |
| `ms_spawn_report` | refuses outright | no instance can answer a whole-world census — see below |

The gate has four refusals:

- **the whole-world scope.** The YAML manifest holds every instance's rule sets, so `all` would deploy
  another instance's rules into this one — or stop this instance's share while reporting a clean sweep
  of every instance's.
- **the router.** It holds no game world, so no rule set deploys there. Refused by role rather than by
  an id that never matches: `SCALING_ROUTER_ID` is an arbitrary name, so a rule-set folder can be named
  after it and would otherwise pass the id check.
- **a query that doesn't start with `shard=`.**
- **a query naming an instance this one isn't.**

The failure modes are worth naming because they are all quiet. `ms_stop` from another instance writes
nothing at all and still reports success. `ms_delete` removes a row the owning instance is still
ticking a script against.

`ms_status` is gated even though it changes nothing. Two of the three states it reports — paused and
stopped — come from row fields and read the same anywhere. The third, *stalled* (architecture.md
decision #26), is a script marked active with no `LoopingCall` attached, and that task is a live object
in one process's memory rather than a column. Off-process the task is absent rather than gone, so an
ungated `ms_status` would report every foreign script as stalled.

### `ms_spawn_report` is not available

It raises `NotImplementedError`. The census counted through `ObjectDB`, which under a shared database
meant one unscoped process could answer for the whole world; with each instance holding its own
database there is nothing left to run a whole-world count on.

A per-instance census is not the same command wearing a different hat. Run on one shard it is a
complete, correct answer about that shard, and an operator who asked "is my world populated?" would
read it as an answer about the world.

`[TBD — needs discussion: what ms_spawn_report should become. Candidates raised but not decided — a
per-instance report that says so in its own output, an aggregate collected over evennia-message-bus,
or neither. The command's docstring carries what the old census produced, for whoever picks it up.]`

### Scripts are confined by the database they live in

Each instance has its own `ScriptDB`, so a `MobSpawnerScript` exists only where it was deployed and
ticks only there. Nothing declares ownership and nothing enforces it — confinement is structural.

## evennia-shards

**No coupling.** Neither imports the other, and nothing in this library names shards or reads anything
it sets. This library integrates with `evennia-scaling` instead; the two answer the same problem
differently and are not co-installed.

## evennia-survival

**No coupling.** Neither imports the other. Hunger and thirst meters tick on characters; this library
maintains mob populations. A consumer whose mobs carry survival meters gets them through the
typeclass the rule names, with no involvement from either library's API.

## evennia-targeting

**No coupling.** Neither library imports the other, and they share no data. Targeting resolves in-room
search terms against objects already in play; it does not create, place, or query by the tags this
library stamps.

## evennia-world-builder

**No coupling in code.** Neither library imports the other, though both consume `evennia-yaml-reader`
and both deploy content into the same world.

**Content-level dependency, in one direction.** A rule's `area_tag` resolves to rooms carrying that tag
under the configured area tag category — rooms typically built by world-builder from its own YAML. The
tags are ordinary Evennia tags, so the coupling runs through the game database rather than either API,
but a rule set deployed against rooms that were never tagged finds no candidate rooms and spawns
nothing. Deploy order matters: rooms before rules.

## evennia-yaml-reader

**Hard dependency.** Imported unconditionally — `Reader` in `definitions.py`, `finder.py` and
`loader.py`, `LocalReader` in `cli.py`, `ReaderError` in `commands.py`. The reader is the only path by
which this library touches YAML; it does no file I/O of its own, which is what lets the same pipeline
run against a local checkout and a remote repo.

## fcm-telemetry-spawn

**No coupling in code.** Neither imports the other, and the two "spawn" systems are unrelated: this
library maintains mob populations, and telemetry-spawn places NFTs and fungibles against economy
saturation. Nothing measured there reaches a rule — a rule's `target` and cooldowns come from YAML an
author wrote, and never from a rate calculation.

**One content-level interface, in one direction.** A rule can stamp `tags:` and `attrs:` on the mobs
it spawns, and a consumer uses that to declare what a given mob can hold. Telemetry-spawn reads those
markings when it decides where to place loot. That is the whole of the contact between them.

The library stays out of what the markings mean. It applies `tags:` and `attrs:` generically to
whatever typeclass a rule names; loot slot semantics and the tag categories that carry them are FCM's,
per `CLAUDE.md` principle 2. So the coupling runs through the game database and the consumer's
vocabulary rather than through either library's API, and neither has to change when the other does.

## fcm-xrpl

**No coupling.** Neither imports the other. This library spawns mobs; it mints nothing, owns no
wallet, and never touches the ledger. A mob carrying on-chain loot gets it from the consumer's
typeclass or its `ms_at_post_spawn()` hook, which is where a library-agnostic game concept belongs.
