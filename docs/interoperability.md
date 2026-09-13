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

**No coupling.** Neither imports the other. Deployment is per-process and deliberately so: `ms_load`
refuses to run anywhere but the shard that owns the rules, and the scripts it creates are confined to
that shard. Nothing here needs to tell another instance anything.

## evennia-mob-spawner

This library.

## evennia-portal-multiplex

**No coupling.** Neither imports the other. This library touches no session, no portal service and no
connection — it creates objects on a timer inside one server, and a player being redirected between
servers is invisible to it.

## evennia-scaling

**No coupling.** Neither imports the other, and nothing in this library names scaling or reads
anything it sets. Co-installed today, the commands behave exactly as they do standalone.

That is a statement about the code as it stands, not a clearance for the pairing. Every gate this
library carries is written against `evennia-shards` — the `shard` level name, the shard-scoped
refusals, the `owning_shard` stamp — and scaling is a different model: independent instances on
separate databases rather than one database with a tenancy filter.

`[TBD — needs discussion: converting this library from shards-compatible to scaling-compatible is a
piece of work in its own right, not a section to fill in, and it has not been scheduled. What it has
to settle: the gate rule (the shards form — deploy only where the rule set names the instance you are
standing on — is expected to transfer with the instance id substituted), what becomes of the
`owning_shard` stamp and script confinement once `ScriptDB` is no longer shared, and `ms_spawn_report`,
whose whole-world census has no unscoped process to run on when each instance holds its own database.]`

## evennia-shards

> **`evennia-shards` is deprecated.** `evennia-scaling` is the standard, and shards is retired from
> service once the rebuild runs on scaling. Everything below describes an integration that works
> today; it is not a model to extend or to copy into new work. Converting this library to scaling is
> the piece of work described under [evennia-scaling](#evennia-scaling) above.

**Optional integration.** `commands.py` imports `preserve_tenant_context` behind a `try`, falling back
to an identity function when shards is absent, so both co-installed and standalone deployments are
supported. See [shards-compatibility.md](shards-compatibility.md) for the mechanism.

**The first declared level must be named `shard`.** When co-installed with shards, the first entry in
`definitions.yaml`'s `levels:` list must be `shard`, and its value is the `SHARD_ID` of the shard the
rules under it are intended to run on. Level names are otherwise consumer-chosen, so this is the one
naming rule the pairing imposes — it is what lets `ms_load` tell which shard a rule set belongs to.

The mandate is enforced, not merely documented: once `definitions.yaml` is parsed, `ms_load` refuses
outright if `shard` is not the first declared level. Nothing else catches that — a consumer who
co-installs shards but keeps their own level names has queries that validate cleanly against their own
declarations.

**Monolith counts as a non-sharded install.** The gate is *shards installed **and** role is not
`monolith`* — not merely that the import succeeded. Under `monolith` there is no shard context and
`get_shard_id()` returns `None`, so every check below is skipped and the library behaves exactly as it
does standalone.

### Commands are gated to the process they can act on

A command's effect usually stops at the process running it, either because it writes rows this process
can see, or because it reaches for a script's live `ndb._task`, which exists only where the script
runs. Five commands therefore carry the same scope gate: the whole-world scope is refused, the query
must start with `shard=`, and it must name this shard.

| Command | Gate | Why |
|---|---|---|
| `ms_load` | shard-scoped | deploys into this process only |
| `ms_stop` | shard-scoped | `pause()` reads the local `ndb._task` |
| `ms_restart` | shard-scoped | `unpause()` / `start()` attach locally |
| `ms_delete` | shard-scoped | stops only the local task, then removes the shared row |
| `ms_status` | shard-scoped | reads the local `ndb._task` to tell a stalled script from a ticking one |
| `ms_spawn_report` | cluster-wide only | counts under the auto-filter; correct only unscoped |

The failure modes the gates prevent differ, and are worth naming because they are all quiet. `ms_stop`
from a foreign process writes nothing at all and still reports success. `ms_delete` removes the shared
row while the owning shard keeps ticking a script that no longer exists. A whole-world `ms_stop` halts
one shard's share and reports a clean sweep of every shard's.

`ms_spawn_report` is gated the other way. Its census counts through `ObjectDB`, so on a shard the
auto-filter narrows it — a shard reporting on another shard's script counts zero of a live population.
The router runs unscoped and is the only process that can answer for the whole world, including rows
left unstamped, so the command is refused only on a shard. The check collapses to that single case
because the router, monolith and standalone all see everything already.

`ms_status` is gated even though it changes nothing. Two of the three states it reports — paused and
stopped — come from shared row fields and read the same anywhere. The third, *stalled* (architecture.md
decision #26), is a script marked active with no `LoopingCall` attached, and that task is a live object
in one process's memory rather than a column. Off-process the task is absent rather than gone, so an
ungated `ms_status` would report every foreign script as stalled. Seeing a stall at a glance is worth
more than answering for every shard from one place, so the gate applies and the command is run per
shard.

### Scripts are confined to the shard that owns them

`ScriptDB` is not tenant-scoped, so a `MobSpawnerScript` row is visible to every process and each
attaches its own `LoopingCall`. Left alone, a rule set ticks on processes that do not own it — and on
the unscoped router `create_object` inserts with `shard_id=NULL`, leaving mobs invisible to the shard
whose rooms they occupy. This is what produced 41 unstamped mobs on a live deployment.

The Deployer stamps the shards library's `owning_shard` Attribute on every script it creates or
upserts, and shards confines the ticks. The stamp is safe to infer because `ms_load` already refuses to
run anywhere but the owning shard: whichever shard the deploy happens on *is* the owner. Nothing is
stamped off a sharded deployment, and an unstamped script is unconfined — correct, because the only way
to be unstamped is to have been created where sharding wasn't in play.

The confinement mechanism, and why it survives any boot order, is documented in
[shards' `script-confinement.md`](../../evennia-shards/docs/script-confinement.md).

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
