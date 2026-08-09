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

`ms_load` then requires the query to *start* with `shard=`, and compares its value against
`get_shard_id()`, refusing when they differ — so a rule set can only be deployed from the process that
owns it. The router's `SHARD_ID` is mandated to be `"router"`, so this rejects `ms_load` on the router
without needing a role check.

**`ms_load all` is refused when shards is installed.** The empty query means load-everything, which
spans every shard's files and so can only ever be partly correct on a single process. Rather than
silently narrowing the scope, the command refuses and tells the operator to deploy one shard at a
time.

**Monolith counts as a non-sharded install.** The gate is *shards installed **and** role is not
`monolith`* — not merely that the import succeeded. Under `monolith` there is no shard context and
`get_shard_id()` returns `None`, so both the shard-match check and the `ms_load all` refusal are
skipped and the library behaves exactly as it does standalone.

**`ScriptDB` is not tenant-scoped.** A `MobSpawnerScript` row is a single row visible to every process,
and each process attaches its own `LoopingCall` to it — so one rule set can tick on a process that does
not own it. On the router, which runs unscoped, `create_object` then inserts with `shard_id=NULL` and
the mobs are invisible to the shard whose rooms they occupy. `Script.stop()` is not a remedy: it writes
`db_is_active=False` to the shared row, stopping the script cluster-wide.

[TBD — needs discussion: how a spawner script is confined to the shard that owns it. The constraint is
shards' (`ScriptDB` carries no `shard_id`; see its `docs/interoperability.md`), but the confinement
mechanism is this library's, since it owns the script's lifecycle and knows its shard.]

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
