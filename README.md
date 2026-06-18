# evennia-mob-spawner

A declarative YAML-driven mob spawn system for [Evennia](https://www.evennia.com/).

Spawn rules — what typeclass spawns where, how many, how often — are expressed as data; a persistent script maintains the population against the running game's database. The library is the spawn equivalent of [evennia-world-builder](https://github.com/FullCircleMUD/evennia-world-builder): world-builder owns static content (rooms, exits, fixtures, immortal NPCs); this library owns refreshing populations (mobs that die, respawn, and self-heal).

## Status

**Pre-foundation.** Repository scaffold is in place; library code is not yet written. The next milestone is the rule schema and the persistent spawn-script lifecycle. See [docs/progress.md](docs/progress.md) for the running milestone log.

## Is this for me?

This library is useful if you are building an Evennia game that:

- Wants to express mob population rules as data (typeclass + area tag + cooldown + cap) rather than as Python builders.
- Wants population maintenance to self-heal across server restarts and content rebuilds without losing cooldown state.
- Wants rule files to live in a separate content repo (e.g. alongside your declarative world content), iterated locally and deployed from GitHub.

If your game's mob spawning is a few hand-coded scripts that rarely change, you do not need this library.

## Install

The package is not on PyPI yet. Install directly from git:

```
pip install git+https://github.com/FullCircleMUD/evennia-mob-spawner.git@main
```

## Compatibility with `evennia-shards`

The library is **shards-compatible but does not require shards**. If [`evennia-shards`](https://github.com/FullCircleMUD/evennia-shards) is installed alongside, the `ms_load` / `ms_validate` commands automatically carry the active multi-tenant context across their `run_async` worker-thread dispatch — the Script rows created in the worker get stamped with the running process's `shard_id` and become correctly scoped under the auto-filter. If shards isn't installed, the library falls back to an identity passthrough at import time and behaves identically to a non-sharded deployment. No configuration needed either way; the integration is a try-import in `commands.py` using shards' `preserve_tenant_context` helper. See [docs/shards-compatibility.md](docs/shards-compatibility.md).

## Learn more

- **[CLAUDE.md](CLAUDE.md)** — load-bearing principles and orientation for working in the repository.
- **[docs/INDEX.md](docs/INDEX.md)** — index of design documents.
- **[docs/architecture.md](docs/architecture.md)** — the spawn system's mechanisms and the library / consumer ownership boundary; the first architectural pass.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
