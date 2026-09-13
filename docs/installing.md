# Installing

Everything a consumer game does to get `evennia-mob-spawner` running: install the package, register
the app, point it at a rule-set source, tag the rooms mobs may occupy, write the rules, and deploy
them. Work down the steps in order — each one assumes the ones above it.

## 1. Install the package

```
pip install evennia-mob-spawner
```

Two of its dependencies are **not on PyPI** and install from a checkout:

```
pip install -e path/to/evennia-yaml-reader
pip install -e path/to/evennia-logging-extension
```

`evennia-yaml-reader` is how the library reads YAML — it does no file I/O of its own, which is what
lets the same pipeline run against a local checkout and a remote repo. `evennia-logging-extension` is
where its log lines go; see [logging.md](logging.md).

## 2. Add the app

In your gamedir's `server/conf/settings.py`:

```python
INSTALLED_APPS = list(INSTALLED_APPS) + [
    "evennia_mob_spawner",
]
```

This is what installs the six admin commands. `AppConfig.ready()` patches them into `AccountCmdSet`,
so they work both OOC and while puppeting.

**The library import goes below `from evennia.settings_default import *`.** That line sets `LOG_DIR`,
which the logging extension resolves at first import. Above it, the library is refused at boot.

## 3. Point it at your rule-set source

Rules live in a content repo of their own, not in the gamedir. The default reader fetches from
GitHub:

```python
MOB_SPAWNER_READER = "evennia_yaml_reader.github.GitHubReader"
MOB_SPAWNER_READER_KWARGS = {
    "repo": "YourOrg/your-mob-rules",
    "ref": "main",
    "pat": MOB_SPAWNER_GITHUB_PAT,     # from secret_settings.py, not here
}
```

For local iteration, point it at a checkout instead:

```python
MOB_SPAWNER_READER = "evennia_yaml_reader.local.LocalReader"
MOB_SPAWNER_READER_KWARGS = {"root": "/path/to/your-mob-rules"}
```

The library resolves the dotted path and passes the kwargs through without inspecting them — they
belong to the reader, not to this library, so a reader of your own works the same way.

## 4. Tag the rooms mobs may occupy

A rule names an `area_tag`, and the library spawns into rooms carrying that tag under the configured
category — `mob_area` unless you override it:

```python
room.tags.add("millholm_wilds", category="mob_area")
```

Rooms built by `evennia-world-builder` can carry the tag from their own YAML. A rule whose `area_tag`
matches no rooms deploys without refusing and spawns nothing — see *What is not checked for you*.

## 5. Write `definitions.yaml` and the rule files

At the root of the rule-set repo, `definitions.yaml` declares the levels operator queries navigate:

```yaml
levels: [shard, zone]
repo-ci-pre-validation: false
```

Each folder carries an `index.yaml` manifest naming its entries as `folder` or `file`. A rule-set
file is a mapping with a `rules:` list:

```yaml
rules:
  - rule_id: 1
    key: "a forest kobold"
    typeclass: "typeclasses.mobs.Kobold"
    area_tag: "millholm_wilds"
    target: 6
    max_per_room: 2
    respawn_seconds: 300
```

`rule_id`, `key`, `typeclass`, `area_tag`, `target` and `max_per_room` are required, and exactly one
of `respawn_seconds` or `death_cooldown_seconds` must be declared. Optional: `den_room_tag`,
`spawn_with_typeclass`, `tags`, `desc`, `attrs`. The full field table is in
[architecture.md](architecture.md).

**`rule_id` is the bookkeeping handle.** Cooldown and observation state key on it, so it is stable
across reordering; changing it says "this is a different rule".

## 6. Validate locally, then deploy

`ms-validate` runs the engine-free tiers from the command line, so authoring mistakes surface before
a deploy — useful in a pre-commit hook or CI:

```
ms-validate --reader local --root /path/to/your-mob-rules
```

In game, deploy with a scoped query:

```
ms_load all
ms_load shard=shard0
ms_load shard=shard0 zone=millholm
```

One persistent script is created per rule-set file. Re-running `ms_load` swaps the rule table in
place and **preserves runtime state** — reloading because a description changed does not reset a boss
cooldown.

The other five commands: `ms_status` (inspect), `ms_stop` (graceful halt), `ms_restart` (kick the
ticker without re-reading YAML), `ms_delete` (remove), `ms_spawn_report` (per-rule population
census).

## Required settings

**None.** Every setting the library reads has a default, so there is nothing it refuses to start
without and no boot-time settings check.

That said, the default reader is `GitHubReader`, which cannot do anything useful without
`MOB_SPAWNER_READER_KWARGS` naming a repo. Leaving it unset is not refused at boot — it fails when a
command first tries to read, naming the reader.

## Optional settings

| Setting | Default | What it does |
|---|---|---|
| `MOB_SPAWNER_READER` | `evennia_yaml_reader.github.GitHubReader` | Dotted path to the reader class. GitHub is the default because rule sets are expected to live in a content repo; point it at `LocalReader` for local iteration. |
| `MOB_SPAWNER_READER_KWARGS` | `{}` | Passed to the reader's constructor untouched. Reader-specific — the library does not dictate the shape. |
| `MOB_SPAWNER_TICK_SECONDS` | `15` | Tick interval for every spawner script. Read at script creation, so changing it affects only scripts created afterwards — existing ones keep their stored interval until deleted and recreated. |
| `MOB_SPAWNER_AREA_TAG_CATEGORY` | `"mob_area"` | The tag category `area_tag` and `den_room_tag` are looked up under. Override if your game already uses a different category name. |

## Where it logs

Its own `mob_spawner.log`, under `settings.LOG_DIR`, beside Evennia's logs. Nothing to configure.
Spawn-time errors, validator refusals, lifecycle events, drain timeouts and empty-`area_tag` warnings
all land there rather than in `server.log`. See [logging.md](logging.md).

## What is not checked for you

- **Leaving the library out of `INSTALLED_APPS`.** `AppConfig.ready()` never runs, so the commands
  are never installed and nothing reports it. The symptom is `ms_load` returning "command not found"
  to a superuser.
- **A settings-module import above the Evennia defaults import.** `LOG_DIR` is unset at that point
  and the logging extension refuses at boot, naming the setting.
- **An `area_tag` matching no rooms.** Validation passes it deliberately — you may be deploying world
  content in parallel, and refusing would break that order. A Tier 4 diagnostic logs one `WARN` per
  affected rule at deploy time, and the rule spawns nothing until the rooms are tagged.
- **Whether your typeclasses behave.** Tier 3 checks that `typeclass` imports at `ms_load` time. It
  cannot check that the class works, that its `at_object_creation` succeeds, or that a
  `ms_at_post_spawn()` hook does something sensible.
- **`attrs:` naming something your typeclass does not declare.** The value is set, a `WARN` is
  logged, and it silently fails to persist unless the typeclass declares a matching
  `AttributeProperty`. The library cannot make it persist for you.
- **A consumer base script that swallows the tick.** `MobSpawnerScript` subclasses
  `settings.BASE_SCRIPT_TYPECLASS`. If your base overrides `at_repeat` without calling
  `super().at_repeat()`, the tick loop never runs and the population silently stops being maintained.
- **`rule_id` reuse across a rename.** Reusing an id for a different mob inherits the old one's
  cooldown and observation state. Nothing detects it, because it is indistinguishable from an edit.

## Co-installed with `evennia-scaling`

One rule the pairing imposes: the first declared level must be named `shard`, and its value is the
instance id — the `MULTIPLEX_INSTANCE_ID` of the instance those rules run on. Five of the six commands
are then confined to the instance they act on, and `ms_spawn_report` is unavailable, because no
instance can count a world it cannot see. Both are enforced rather than merely documented — see
[interoperability.md](interoperability.md).

Nothing to configure. The library detects scaling by importing it, and skips every check when it is
absent.
