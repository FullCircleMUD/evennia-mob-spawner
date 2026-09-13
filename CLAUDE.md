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
4. [docs/test-plan.md](docs/test-plan.md) — every case the library covers and the test function covering it. **Any behavioural change starts here**, not in the code.
5. [docs/installing.md](docs/installing.md) — what a consumer actually does, and what is not checked for them. Read it before changing anything a consumer configures.

## Load-bearing architectural principles

These are the principles every implementation decision must respect. Getting them wrong is expensive to undo.

1. **The library does not own game concepts.** Mob typeclasses, AI behaviour, combat, loot, death pipelines, faction systems, alignment — all belong to the consumer game. The library provides infrastructure: YAML rule parsing, rule validation, script lifecycle (tick / count / cooldown / pick room / instantiate), and a tiny death-notification surface. When tempted to add a game concept, ask whether it's actually game-specific and should stay in the consumer.
2. **No FCM-specific assumptions.** This library was created in service of FullCircleMUD (FCM). Anything FCM-specific creeping into the library is a code smell. Loot tag categories (`spawn_resources`, `spawn_gold`, …), FCM AI handlers, FCM combat mixins, FCM mob typeclasses — all stay in FCM. Default to "consumer concern" when uncertain.
3. **Typeclass-agnostic; minimal protocol.** The library spawns objects via `create_object(typeclass=<rule.typeclass>)` and stamps a pair of passive identity *tags* (`mob_spawner_rule`, `mob_spawner_file`) on the new object — the library queries them to count populations; the typeclass neither reads nor writes them. It does not declare a base class the consumer must inherit. Death is detected by observation (tick-time count delta), not by a callback — there is no `on_death` API and the typeclass is never asked to phone home. The one optional protocol surface is a duck-typed `ms_at_post_spawn(self)` method: if the typeclass defines it, the library invokes it after spawn; if not, nothing happens. Adding a new mob typeclass to the consumer game must not require changes to the library.
4. **Pre-flight rule validation, no broken ticks.** All rule validation runs before the script starts ticking. If any rule is malformed (missing required field, unresolvable typeclass, invalid cooldown), the script refuses to come up against that rule-set and reports every finding. The operator gets either a clean script or a complete refusal — never half-loaded rules that tick partially and fail silently.
5. **Preserve runtime state across rule reloads.** Hot reload of YAML rules updates the script's rule table *in place* and preserves all runtime history — `last_spawn_times`, cooldown clocks, in-flight populations. Reloading rules because a description changed must not reset boss cooldowns. This is the sharp distinction from `evennia-world-builder`'s "clean + rebuild" lifecycle: world-builder rebuilds objects; mob-spawner reloads configuration.
6. **Test-first.** A case lands in [docs/test-plan.md](docs/test-plan.md), then the test, then the
   code. The plan is where behaviour is agreed, and the **Test function** column is a coverage claim
   checked both ways — a name that does not exist and a test no case claims are both errors. See
   [test-first-process.md](../../design/test-first-process.md).
7. **Synthetic content first.** Build the library against synthetic test fixtures the library owns (a fake `TestMob` typeclass, synthetic tagged rooms, synthetic YAML rules), exhaustively, before any consumer-game integration. Real consumer content surfaces edge cases synthetic fixtures didn't reach; when it does, pause integration, capture the case as a new synthetic fixture, fix against it, resume. Fixtures stay forever as regression coverage.

## Out of scope

Scope boundaries are decided as concrete questions arise, by applying the principles above. The library's surface area will be drawn deliberately as actual design needs surface, with each scope decision captured in docs/ when it is made.

Areas where scope questions are likely to need explicit decisions (TBD when they arrive):

- Whether the cross-validator (spawn rules ↔ room tags in the consumer's static-content repo) lives in this library, in `evennia-world-builder`, or in the consumer.
- Whether to support conditional spawn gates (time-of-day, population prerequisites, event flags) at the library level, or leave them as consumer-defined `post_spawn_hook` extensions that gate themselves.
- Whether the library owns initial seeding semantics (populate-on-start vs gradual fill via the tick loop), or leaves that as a per-rule policy.

## Working conventions

- **Behavioural change starts in the test plan.** Add or amend the case in [docs/test-plan.md](docs/test-plan.md), fill the **Test function** column when the test exists, then implement. Never leave the column stale — it is a coverage claim, and the linter treats a dangling name as an error.
- **Editing design docs.** Update or add design documents whenever an architectural decision is made or refined. Capture the *why*, not just the *what*. Index new docs in [docs/INDEX.md](docs/INDEX.md).
- **Don't put implementation detail in this file or README.** Link out to docs/ instead. Keep CLAUDE.md and README.md stable; let docs/ churn.
- **License.** BSD 3-Clause. Source files carry an SPDX header on the first line (`# SPDX-License-Identifier: BSD-3-Clause`).

## Sanctioned divergences — five linter findings that are decisions

The `library-standards-linter` reports five warnings that are settled rather than outstanding. Each
is commented at its site, and they are the whole of its current output — a clean run means these five
and nothing else. **Do not "fix" them**; a session working down the linter output would otherwise
undo all five.

| Finding | Where | Why |
|---|---|---|
| `constant_outside_config`, `settings_read_outside_config`, `settings_import_at_module_scope` | `script.py` — `_BASE_SCRIPT` | It resolves `BASE_SCRIPT_TYPECLASS` into the class `MobSpawnerScript` inherits from, so it must evaluate when `script.py` is imported. Moving it to `config.py` was tried and raises `AppRegistryNotReady`: `config.py` is imported from `__init__.py`, which runs during `django.setup()` while the app registry is still populating. |
| `db_attribute_write` | `script.py` — `mob.db.desc` | Evennia's `DefaultObject` declares no `AttributeProperty` for `desc`; `get_display_desc` reads `self.db.desc`. A straight assignment would set a plain Python attribute that dies with the object, so descriptions would silently stop persisting. |
| `log_shim_function_name` | `log.py` — `ms_log` | The name is kept deliberately. `log.py` is internal and never re-exported, so every import is already qualified by the package; the linter's four-character stem floor is what objects, not the standard's "named for the library". |

The one thing that is *not* a divergence: `evennia-yaml-reader` and `evennia-logging-extension` are
hard dependencies and neither is on PyPI, so both install from a checkout. See
[docs/installing.md](docs/installing.md).

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
├── docs/                      # technical wiki (humans + LLMs)
│   ├── INDEX.md
│   ├── installing.md          # what a consumer does, start to finish
│   ├── architecture.md
│   ├── test-plan.md
│   ├── progress.md
│   ├── interoperability.md
│   ├── logging.md
│   └── archive/
├── src/
│   └── evennia_mob_spawner/   # library code (src layout)
│       ├── __init__.py        # the public surface + __version__
│       ├── config.py          # every constant, and the settings accessors
│       ├── apps.py            # AppConfig.ready() — installs the commands
│       ├── definitions.py     # definitions.yaml → declared levels
│       ├── finder.py          # walks index.yaml to resolve a query
│       ├── loader.py          # reads the matched rule-set files
│       ├── validator.py       # the four predicate tiers
│       ├── deployer.py        # upsert: drain, swap, preserve, resume
│       ├── script.py          # MobSpawnerScript and the tick loop
│       ├── commands.py        # the six ms_* admin commands
│       ├── cli.py             # ms-validate, the engine-free CLI
│       ├── errors.py          # the library's exception types
│       ├── log.py             # binds ms_log to mob_spawner.log via evennia-logging-extension
│       └── tests.py           # unit tests, run via runtests.py
├── tests/                     # standalone test infrastructure
│   ├── __init__.py
│   ├── test_settings.py
│   └── urls.py
└── examples/                  # demo gamedirs for integration testing
    ├── requirements.txt       # the demo venv: evennia, sqlean on macOS, the siblings editable
    └── demo_game/             # the gamedir; venv/ beside it, both gitignored
```

## Tools and environment

- Python 3.10+ (pinned via `pyproject.toml`).
- Evennia is a runtime dependency (`pip install evennia`).
- **Tests use Django's test runner via `runtests.py`, not pytest.** No consumer gamedir required, and no sibling library installed — the `evennia-scaling` integration is exercised by injecting stand-in modules into `sys.modules`, so the real import path has never run in tests.
- Structural compliance: `python .claude/skills/library-standards-linter/lint_library.py evennia-mob-spawner` from the umbrella root. It validates the test-plan coverage trail too.
- YAML parsing: PyYAML (`yaml.safe_load`). Schema validation: hand-written predicates rather than a schema library — same approach as `evennia-world-builder`. The tiering and what each tier refuses is in [docs/architecture.md](docs/architecture.md) § Validation tiering and gating.
- **Two venvs, both gitignored.** The split is load-bearing:
  - `evennia-mob-spawner/venv/` is the library test venv — `runtests.py` runs against this. Install: `evennia`, `evennia-yaml-reader` and `evennia-logging-extension` (both editable), and `evennia-mob-spawner` itself (`pip install -e .`). **Do not install `evennia-world-builder` here.** The library is independent of world-builder by architecture (siblings, not stacked); keeping world-builder out of the test venv is what enforces that — any accidental `import evennia_world_builder` in library code fails fast in tests instead of passing silently.
  - `evennia-mob-spawner/examples/venv/` is the demo gamedir venv, built from [examples/requirements.txt](examples/requirements.txt): `./venv/bin/pip install -r requirements.txt` from `examples/`. It adds `evennia-world-builder` (editable) — the demo wires both content libraries into `INSTALLED_APPS` to exercise the seam between them in vivo — and `sqlean.py` on macOS.
- **macOS: the demo gamedir needs `sqlean.py`, and it is load-bearing.** macOS's system SQLite initialises through libdispatch, which does not survive `fork()`, so a daemonized `evennia start` deadlocks on the child's first SQLite call — silently, with no error and no timeout. The `darwin` block at the top of `examples/demo_game/server/conf/settings.py` swaps in the bundled build, and it only works if `sqlean.py` is actually installed in the venv: the block is a `try`/`except ImportError` and goes quiet otherwise. A demo that hangs after "Portal starting server ..." with nothing in `server.log` after the reactor line is this.

## Sibling libraries to reference

When in doubt about a convention not covered here, look at how a sibling library does it:

- **[../evennia-world-builder/](../evennia-world-builder/)** — declarative YAML world authoring; partly implemented. Closest architectural cousin: same YAML/Reader/Validator pattern applied to static content.
- **[../evennia-scaling/](../evennia-scaling/)** — many Evennia instances, each on its own database. The optional integration this library's command gates are written against; see [docs/interoperability.md](docs/interoperability.md).