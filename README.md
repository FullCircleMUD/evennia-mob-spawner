# evennia-mob-spawner

A declarative YAML-driven mob spawn system for [Evennia](https://www.evennia.com/).

Spawn rules — what typeclass spawns where, how many, how often — are expressed as data; a persistent script maintains the population against the running game's database. The library is the spawn equivalent of [evennia-world-builder](https://github.com/FullCircleMUD/evennia-world-builder): world-builder owns static content (rooms, exits, fixtures, immortal NPCs); this library owns refreshing populations (mobs that die, respawn, and self-heal).

## Status

**Working.** The full pipeline — Reader → Loader → Validator → Deployer — is implemented. One persistent script per rule-set file maintains populations across restarts and content rebuilds without losing cooldown state. Six admin commands (`ms_load`, `ms_status`, `ms_stop`, `ms_restart`, `ms_delete`, `ms_spawn_report`), each gated to the process that can correctly answer for it. The suite is green and the library is live-verified on a two-process deployment against a real consumer game. See [docs/progress.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/progress.md) for the running milestone log.

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

Then follow **[docs/installing.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/installing.md)** — the numbered steps, the settings and their defaults, and what is not checked for you.

Editable install for development against a checkout:

```
git clone https://github.com/FullCircleMUD/evennia-mob-spawner.git
cd evennia-mob-spawner
python -m venv venv
# Activate the venv (platform-specific)
pip install -e .
python runtests.py
```

## Compatibility with `evennia-scaling`

The library works with [`evennia-scaling`](https://github.com/FullCircleMUD/evennia-scaling) but **does not require it**. Scaling runs each Evennia instance on its own database, so a rule set belongs to one instance and the commands that act on it only reach the instance running them. Co-installed, the first declared level must be named `shard` and carries the instance id, and the five operating commands are confined to the instance they act on — the whole-world scope is refused, and so is the router, which holds no game world.

`ms_spawn_report` raises `NotImplementedError`: its census counted across a shared database, and no single instance can answer for the whole world. What it should become is open.

If scaling isn't installed, every check is skipped and the library behaves as a standalone single-instance game. No configuration either way; the integration is a try-import. See [docs/interoperability.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/interoperability.md).

## Learn more

- **[CLAUDE.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/CLAUDE.md)** — load-bearing principles and orientation for working in the repository.
- **[docs/INDEX.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/INDEX.md)** — index of design documents.
- **[docs/architecture.md](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/docs/architecture.md)** — the spawn system's mechanisms and the library / consumer ownership boundary; the first architectural pass.

## License

BSD 3-Clause. See [LICENSE](https://github.com/FullCircleMUD/evennia-mob-spawner/blob/main/LICENSE).
