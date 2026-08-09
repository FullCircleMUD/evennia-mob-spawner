# Compatibility with `evennia-shards`

The library is **compatible with [`evennia-shards`](https://github.com/FullCircleMUD/evennia-shards) but does not require it**. The integration is a single optional import in [`commands.py`](../src/evennia_mob_spawner/commands.py) and a one-line wrap around each `run_async` dispatch.

This document covers the tenant-context wrap only. The full picture of the pairing — the `shard` level mandate, the scope checks `ms_load` enforces, and the unsolved script-confinement problem — is in [interoperability.md](interoperability.md).

## What the integration does

`ms_load` defers its pipeline to a Twisted worker thread via `evennia.utils.utils.run_async`. Under a multi-tenant `shards` deployment, the worker thread spawns with a fresh `threading.local` — the active tenant set on the reactor thread does not carry across. Without intervention, any `Script` row created inside the worker (a persistent `MobSpawnerScript` instance — `ms_load`'s main work product) would land `shard_id=NULL`: the shards library's auto-stamp condition requires a tenant to be set, and the worker thread has none.

`ms_load` wraps its pipeline callable with `preserve_tenant_context` from the shards library:

```python
try:
    from evennia_shards import preserve_tenant_context
except ImportError:
    def preserve_tenant_context(fn):
        return fn

# ...

run_async(
    preserve_tenant_context(self._run_pipeline), query, flags,
    at_return=self._on_async_return,
    at_err=self._on_async_err,
)
```

`preserve_tenant_context` captures the reactor thread's active tenant at wrap time and re-applies it inside the worker on entry — `MobSpawnerScript` rows created by `ms_load` get correctly stamped with whichever shard the process is running as. Mob population then scopes to the right shard via the auto-filter.

## What happens without shards

If `evennia-shards` is not installed, the top-of-module `try` import raises `ImportError` and the fallback identity function takes its place. `preserve_tenant_context(fn)` then returns `fn` unchanged. The commands run identically to a non-sharded deployment.

No configuration to set, no settings flag to flip. The integration is structural — the optional import does its thing at module load time, and the wrap is a no-op in the non-sharded case.

## What's guaranteed

- **shards installed + configured** (e.g. `SHARDS_ROLE=shard, SHARD_ID=shard0`): `MobSpawnerScript` rows created by `ms_load` land `shard_id="shard0"`. Population maintenance scopes correctly to this shard's rooms.
- **shards installed + monolith mode**: shards' `apps.py` returns early in monolith, so no tenant context is ever set. `preserve_tenant_context` captures `None`, the wrapped callable runs unscoped (same as if no shards were installed). No-op effectively.
- **shards not installed**: the optional-import fallback applies. Mob-spawner behaves exactly as the standalone library — no DB-level partitioning, no shard stamping.

## Mob-spawning ticks (separate concern)

This compatibility note covers `ms_load`'s **build-time** writes. (The standalone `ms-validate` CLI never touches the DB — it runs the pipeline read-only and needs no shards integration.)

The persistent `MobSpawnerScript`'s **runtime** behaviour needs no thread-context integration. Periodic spawn ticks run on the reactor thread directly via Evennia's `at_repeat` / script scheduling — no thread spawn involved — so mob rows auto-stamp to the local shard via the normal `_tenant_aware_save` path.

That holds provided the script ticks on the shard that owns its rules. `ScriptDB` carries no `shard_id`, so the row is visible to every process and each attaches its own `LoopingCall`; nothing yet confines a rule set to its owning shard. Tracked as the open item in [interoperability.md](interoperability.md).

## Pattern source

Same shape as [`evennia-world-builder`'s shards compatibility](https://github.com/FullCircleMUD/evennia-world-builder/blob/main/docs/shards-compatibility.md). The pattern is documented as the canonical integration point in [`evennia-shards/docs/tenancy.md`](https://github.com/FullCircleMUD/evennia-shards/blob/main/docs/tenancy.md) under "Cross-thread tenant propagation."
