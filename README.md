# evennia-mob-spawner

A declarative YAML-driven mob spawn system for [Evennia](https://www.evennia.com/).

Spawn rules — what typeclass spawns where, how many, how often — are expressed as data; a persistent script maintains the population against the running game's database. The library is the spawn equivalent of [evennia-world-builder](https://github.com/FullCircleMUD/evennia-world-builder): world-builder owns static content (rooms, exits, fixtures, immortal NPCs); this library owns refreshing populations (mobs that die, respawn, and self-heal).

## Status

**Working.** The full pipeline — Reader → Loader → Validator → Deployer — is implemented. One persistent script per rule-set file maintains populations across restarts and content rebuilds without losing cooldown state. Six admin commands (`ms_load`, `ms_status`, `ms_stop`, `ms_restart`, `ms_delete`, `ms_spawn_report`), each gated to the process that can correctly answer for it. 274 tests green, live-verified on a two-process sharded deployment against a real consumer game. See [docs/progress.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/progress.md) for the running milestone log.

## Is this for me?

This library is useful if you are building an Evennia game that:

- Wants to express mob population rules as data (typeclass + area tag + cooldown + cap) rather than as Python builders.
- Wants population maintenance to self-heal across server restarts and content rebuilds without losing cooldown state.
- Wants rule files to live in a separate content repo (e.g. alongside your declarative world content), iterated locally and deployed from GitHub.

If your game's mob spawning is a few hand-coded scripts that rarely change, you do not need this library.

## Install

```
pip install evennia-mob-spawner
```

Editable install for development against a checkout:

```
git clone https://github.com/FullCircleMUD/evennia-mob-spawner.git
cd evennia-mob-spawner
python -m venv venv
# Activate the venv (platform-specific)
pip install -e .
python runtests.py
```

## Compatibility with `evennia-shards`

The library is **shards-compatible but does not require shards**. If [`evennia-shards`](https://github.com/FullCircleMUD/evennia-shards) is installed alongside, the `ms_load` / `ms_validate` commands automatically carry the active multi-tenant context across their `run_async` worker-thread dispatch — the Script rows created in the worker get stamped with the running process's `shard_id` and become correctly scoped under the auto-filter. If shards isn't installed, the library falls back to an identity passthrough at import time and behaves identically to a non-sharded deployment. No configuration needed either way; the integration is a try-import in `commands.py` using shards' `preserve_tenant_context` helper. See [docs/shards-compatibility.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/shards-compatibility.md).

Co-installed, the pairing also requires `shard` as the first declared level and confines `ms_load` to the shard it is running as. See [docs/interoperability.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/interoperability.md).

## Learn more

- **[CLAUDE.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/CLAUDE.md)** — load-bearing principles and orientation for working in the repository.
- **[docs/INDEX.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/INDEX.md)** — index of design documents.
- **[docs/architecture.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/architecture.md)** — the spawn system's mechanisms and the library / consumer ownership boundary; the first architectural pass.

## License

BSD 3-Clause. See [LICENSE](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/LICENSE).
