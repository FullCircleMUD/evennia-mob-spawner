# CLAUDE.md

> **Project-wide working rules and cross-repo context live in the FCM umbrella repo's `CLAUDE.md`**,
> loaded automatically when you work from the umbrella root. If you opened this repo directly instead
> of via the umbrella, relaunch from the umbrella root for the full context. This file holds only this
> repo's specific instructions.

Instructions for Claude (and other LLM agents) working in this repository.

## What this project is

`evennia-mob-spawner` is a library that adds declarative, YAML-driven mob spawn management to [Evennia](https://www.evennia.com/). Spawn rules (what typeclass spawns where, how many, how often) are expressed as data; a persistent script maintains the population against the running game's database via Evennia's tag system. Tagline: **"Declarative YAML-driven mob spawn system for Evennia."**

The library is Evennia-flavored and primarily intended for use on FullCircleMUD, but is FCM-agnostic by design: nothing in the library knows about FCM-specific zones, typeclasses, AI mixins, loot semantics, or game systems.

**Sibling library:** [evennia-world-builder](https://github.com/FullCircleMUD/evennia-world-builder) handles static world content (rooms, exits, fixtures, immortal NPCs). This library handles refreshing populations — mobs that die, respawn, and self-heal. The two libraries are siblings, not stacked: neither depends on the other. They share architectural style (Reader abstraction, Validator, YAML repo, admin command) but not code.

For the big-picture overview, read [README.md](README.md).
For the design wiki, read [docs/INDEX.md](docs/INDEX.md).

## Project status

For the current state of the project — milestones reached, what's pending — see [docs/progress.md](docs/progress.md), the running log of milestones with links to evidence.

## Where to read first

For any non-trivial task, start by reading in this order:

1. [README.md](README.md) — what the project is, status, quick start.
2. [docs/INDEX.md](docs/INDEX.md) — map of all design docs.
3. [docs/architecture.md](docs/architecture.md) — high-level mapping of the spawn system's mechanisms and the library / consumer ownership boundary. Read this before adding to the library's surface area; it captures decisions already pinned and flags the open questions explicitly.
## Load-bearing architectural principles

These are the principles every implementation decision must respect. Getting them wrong is expensive to undo.

1. **The library does not own game concepts.** Mob typeclasses, AI behaviour, combat, loot, death pipelines, faction systems, alignment — all belong to the consumer game. The library provides infrastructure: YAML rule parsing, rule validation, script lifecycle (tick / count / cooldown / pick room / instantiate), and a tiny death-notification surface. When tempted to add a game concept, ask whether it's actually game-specific and should stay in the consumer.
2. **No FCM-specific assumptions.** This library was created in service of FullCircleMUD (FCM). Anything FCM-specific creeping into the library is a code smell. Loot tag categories (`spawn_resources`, `spawn_gold`, …), FCM AI handlers, FCM combat mixins, FCM mob typeclasses — all stay in FCM. Default to "consumer concern" when uncertain.
3. **Typeclass-agnostic; minimal protocol.** The library spawns objects via `create_object(typeclass=<rule.typeclass>)` and stamps a pair of passive identity *tags* (`mob_spawner_rule`, `mob_spawner_file`) on the new object — the library queries them to count populations; the typeclass neither reads nor writes them. It does not declare a base class the consumer must inherit. Death is detected by observation (tick-time count delta), not by a callback — there is no `on_death` API and the typeclass is never asked to phone home. The one optional protocol surface is a duck-typed `ms_at_post_spawn(self)` method: if the typeclass defines it, the library invokes it after spawn; if not, nothing happens. Adding a new mob typeclass to the consumer game must not require changes to the library.
4. **Pre-flight rule validation, no broken ticks.** All rule validation runs before the script starts ticking. If any rule is malformed (missing required field, unresolvable typeclass, invalid cooldown), the script refuses to come up against that rule-set and reports every finding. The operator gets either a clean script or a complete refusal — never half-loaded rules that tick partially and fail silently.
5. **Preserve runtime state across rule reloads.** Hot reload of YAML rules updates the script's rule table *in place* and preserves all runtime history — `last_spawn_times`, cooldown clocks, in-flight populations. Reloading rules because a description changed must not reset boss cooldowns. This is the sharp distinction from `evennia-world-builder`'s "clean + rebuild" lifecycle: world-builder rebuilds objects; mob-spawner reloads configuration.
6. **Synthetic content first.** Build the library against synthetic test fixtures the library owns (a fake `TestMob` typeclass, synthetic tagged rooms, synthetic YAML rules), exhaustively, before any consumer-game integration. Real consumer content surfaces edge cases synthetic fixtures didn't reach; when it does, pause integration, capture the case as a new synthetic fixture, fix against it, resume. Fixtures stay forever as regression coverage.

## Out of scope

Scope boundaries are decided as concrete questions arise, by applying the principles above. The library's surface area will be drawn deliberately as actual design needs surface, with each scope decision captured in docs/ when it is made.

Areas where scope questions are likely to need explicit decisions (TBD when they arrive):

- Whether the library ships a `wb-validate`-style standalone CLI for local / pre-commit rule validation, in addition to an in-game admin command.
- Whether the cross-validator (spawn rules ↔ room tags in the consumer's static-content repo) lives in this library, in `evennia-world-builder`, or in the consumer.
- How the Reader is supplied: depend on `evennia-world-builder`'s Reader, duplicate a minimal Reader here, or let the consumer pass any callable that returns raw YAML.
- Whether to support conditional spawn gates (time-of-day, population prerequisites, event flags) at the library level, or leave them as consumer-defined `post_spawn_hook` extensions that gate themselves.
- Whether the library owns initial seeding semantics (populate-on-start vs gradual fill via the tick loop), or leaves that as a per-rule policy.

## Working conventions

- **Editing design docs.** Update or add design documents whenever an architectural decision is made or refined. Capture the *why*, not just the *what*. Index new docs in [docs/INDEX.md](docs/INDEX.md).
- **Don't put implementation detail in this file or README.** Link out to docs/ instead. Keep CLAUDE.md and README.md stable; let docs/ churn.
- **License.** BSD 3-Clause. Source files carry an SPDX header on the first line (`# SPDX-License-Identifier: BSD-3-Clause`).

## Documentation discipline (load-bearing)

Design documents in `docs/` must reflect decisions **actually discussed and agreed on with the project owner**. They are not a place to forward-design the system from first principles or extrapolate "reasonable defaults" from a starting point.

**Rules:**

1. **Only capture what was discussed and agreed.** If the conversation establishes a principle (e.g. "the library declares no protocol; the consumer reads breadcrumbs"), do not extrapolate it into specifics that were not raised (e.g. an exact breadcrumb attribute schema, an inheritance pattern, naming conventions for hooks).
2. **Flag open questions explicitly.** Where a topic has been raised but not resolved, write `[TBD — needs discussion: <what is open>]` in the doc. Future sessions then pick the topic up deliberately rather than inheriting unagreed assumptions.
3. **Distinguish archived material from in-conversation decisions.** Material in `docs/archive/` is preserved historical context, not authoritative. Restating archived content in new docs is acceptable when it provides necessary context, but mark it as such rather than presenting it as a decision freshly made or as canonical project intent.
4. **Smaller is better.** A doc that captures three discussed points faithfully is more useful than one that captures three discussed points plus seven invented ones. Resist the urge to fill out sections "for completeness."

If a session catches itself writing content that goes beyond what was discussed, stop and either remove the extrapolation or convert it to a `[TBD]` marker. Documentation that puts unagreed decisions in the project's mouth is worse than documentation that has gaps.

## Repository layout

```
evennia-mob-spawner/
├── CLAUDE.md                  # this file
├── README.md
├── LICENSE                    # BSD 3-Clause
├── pyproject.toml
├── runtests.py                # standalone test runner (no consumer gamedir needed)
├── .gitignore
├── docs/                    # technical wiki (humans + LLMs)
├── src/
│   └── evennia_mob_spawner/   # library code (src layout)
│       ├── __init__.py
│       └── tests.py           # unit tests, run via runtests.py
├── tests/                     # standalone test infrastructure
│   ├── __init__.py
│   ├── test_settings.py
│   └── urls.py
└── examples/                  # demo gamedirs for integration testing
```

## Tools and environment

- Python 3.10+ (pinned via `pyproject.toml`).
- Evennia is a runtime dependency (`pip install evennia`).
- **Tests use Django's test runner via `runtests.py`, not pytest.** No consumer gamedir required. Pattern mirrors `evennia-shards`.
- YAML parsing: PyYAML (`yaml.safe_load`). Schema validation: hand-written predicates rather than a schema library — same approach as `evennia-world-builder`; rationale to be captured in docs/ when the validator lands.
- **Two venvs, both gitignored.** The split is load-bearing:
  - `evennia-mob-spawner/venv/` is the library test venv — `runtests.py` runs against this. Install: `evennia`, `evennia-yaml-reader` (editable), and `evennia-mob-spawner` itself (`pip install -e .`). **Do not install `evennia-world-builder` here.** The library is independent of world-builder by architecture (siblings, not stacked); keeping world-builder out of the test venv is what enforces that — any accidental `import evennia_world_builder` in library code fails fast in tests instead of passing silently.
  - `evennia-mob-spawner/examples/venv/` is the demo gamedir venv. Install: the same three plus `evennia-world-builder` (editable). The demo gamedir wires both libraries into `INSTALLED_APPS` to exercise the seam between them in vivo; running `evennia start` from `examples/demo_game/` needs both.

## Sibling libraries to reference

When in doubt about a convention not covered here, look at how a sibling library does it:

- **[../evennia-world-builder/](../evennia-world-builder/)** — declarative YAML world authoring; partly implemented. Closest architectural cousin: same YAML/Reader/Validator pattern applied to static content.
- **[../evennia-shards/](../evennia-shards/)** — split-deployment / sharding library; working MVP. Reference for the test-runner pattern, src layout, pyproject.toml shape.