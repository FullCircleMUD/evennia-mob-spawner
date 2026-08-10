# Interoperability

This library against every sibling library in `libraries/`. What it does that can constrain, or be
constrained by, a sibling: it reads rule-set YAML through a pluggable reader, creates one **persistent
`MobSpawnerScript` per rule-set file**, dispatches its deploy pipeline **off the reactor thread**, and
calls `create_object` on every tick to place mobs into rooms it locates **by tag**.

## evennia-mob-spawner

This library.

## evennia-shards

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
runs. Four commands therefore carry the same scope gate: the whole-world scope is refused, the query
must start with `shard=`, and it must name this shard.

| Command | Gate | Why |
|---|---|---|
| `ms_load` | shard-scoped | deploys into this process only |
| `ms_stop` | shard-scoped | `pause()` reads the local `ndb._task` |
| `ms_restart` | shard-scoped | `unpause()` / `start()` attach locally |
| `ms_delete` | shard-scoped | stops only the local task, then removes the shared row |
| `ms_spawn_report` | cluster-wide only | counts under the auto-filter; correct only unscoped |
| `ms_status` | none | reads shared row fields, so it is correct anywhere |

The failure modes the gates prevent differ, and are worth naming because they are all quiet. `ms_stop`
from a foreign process writes nothing at all and still reports success. `ms_delete` removes the shared
row while the owning shard keeps ticking a script that no longer exists. A whole-world `ms_stop` halts
one shard's share and reports a clean sweep of every shard's.

`ms_spawn_report` is gated the other way. Its census counts through `ObjectDB`, so on a shard the
auto-filter narrows it — a shard reporting on another shard's script counts zero of a live population.
The router runs unscoped and is the only process that can answer for the whole world, including rows
left unstamped, so the command is refused only on a shard. The check collapses to that single case
because the router, monolith and standalone all see everything already.

`ms_status` is deliberately ungated: it derives state from shared row fields alone, so it returns the
same answer everywhere and is useful from the router as a cluster-wide view. Its absence from the
gated set is a decision, not an oversight.

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
