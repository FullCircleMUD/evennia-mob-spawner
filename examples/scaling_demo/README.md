# The scaling demo — three instances, two rule sets

`evennia-mob-spawner` running under `evennia-scaling`: a router and two shards, each shard on its own
Evennia database, each with its own rule set and its own mob population.

The single-instance demo is [../demo_game/](../demo_game/). This one exists for what that one cannot
show — that a rule set deploys only on the instance it names, and that an instance's scripts and mobs
are invisible to every other.

## Layout

One source tree. `router/` holds all the code and all five settings files; `shard0/` and `shard1/`
symlink back to it and own only their own `server/` directory. Identity is config, never a second
checkout.

```
scaling_demo/
├── requirements.txt        one venv, every instance runs from it
├── make_superuser.py       Account #1 without the launcher's prompt
├── rules/                  the YAML rule repo, read by all three
│   ├── definitions.yaml    levels: [shard, zone]
│   ├── index.yaml          shard0, shard1
│   ├── shard0/millholm.yaml     3 rules
│   └── shard1/wilderness.yaml   2 rules
├── router/                 the whole source tree
│   ├── typeclasses/        accounts, characters, rooms (scaling mixins), demo_mobs
│   ├── world/demo_rooms.py rooms carrying a uuid and a mob_area tag
│   └── server/conf/        settings.py, settings_common.py, settings_{router,shard0,shard1}.py
├── shard0/                 symlinks back to router/, own server/
└── shard1/                 symlinks back to router/, own server/
```

Each instance owns its `evennia.db3` and its `logs/`. `archive.db3` and `messagebus.db3` are the
router's, symlinked from each shard — on one machine that stands in for the shared Postgres a real
deployment would use.

| | router | shard0 | shard1 |
|---|---|---|---|
| telnet | 4010 | 4020 | 4030 |
| webserver | 4011/4012 | 4021/4022 | 4031/4032 |
| websocket | 4013 | 4023 | 4033 |

Only the router listens. The shards' ports are set and never bound, so starting one fully by accident
fails on something obvious instead of three instances fighting over one port.

## Build it

```bash
cd examples/scaling_demo
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# The venv's bin must be on PATH — the launcher shells out to `twistd`.
export PATH="$PWD/venv/bin:$PATH"

for i in router shard0 shard1; do
  (cd $i && ../venv/bin/evennia cascade_migrate --settings settings_$i.py)
  (cd $i && ../venv/bin/python ../make_superuser.py settings_$i)
done
```

`cascade_migrate`, not `migrate` — it covers the archive and message-bus aliases as well as the game
database. The flag goes **after** the verb; before it, the launcher reads `--settings` as the command
name.

## Run it

The router first, because a shard's `server_start` needs a live Portal at the address it dials:

```bash
(cd router && ../venv/bin/evennia start --settings settings_router.py)
(cd shard0 && ../venv/bin/evennia server_start --settings settings_shard0.py)
(cd shard1 && ../venv/bin/evennia server_start --settings settings_shard1.py)
```

**A shard's first start reports failure and has not failed.** It runs Evennia's initial setup, then
restarts itself once, and the launcher reads that restart as the Server not coming up. Run
`server_start` again and it comes up.

`AMP_PORT` is the launcher's control channel as well as the dial target, so `stop` and `reload` run
from a shard's directory reach the *router's* Portal. `server_start` is the only launcher verb safe
to use from a shard.

## Get onto a shard

Only the router listens, so every connection starts there and reaches a shard by going in character.

Two accounts, because they do different jobs:

| | | |
|---|---|---|
| `root` / `p` | Account #1 | Never transfers. `is_instance_root` keeps it where it is — archiving the account an instance requires would delete it. Use it for router-side work |
| `tester` / `spawnerdemo1` | Account #2, superuser | Transfers, and passes the `cmd:superuser()` lock on the `ms_*` commands. Use it for everything on a shard |

`tester` is not created by the build steps above. Make it once, from `root` on the router:

```
py from evennia.utils.create import create_account; from django.conf import settings; create_account('tester','tester@example.com','spawnerdemo1',typeclass=settings.BASE_ACCOUNT_TYPECLASS,is_superuser=True,permissions=['Developer'])
```

The password is long enough to pass Django's validators, which `p` is not — `make_superuser.py`
bypasses them and `create_account` does not.

Then, as `tester`: `charcreate Scout`, then `ic Scout`. That lands on shard0, which is
`SCALING_START_LOCATION_SHARD`.

**To reach shard1**, point the character at it from out of character and go back in, in the same
session:

```
ooc
py from evennia.objects.models import ObjectDB; s=[o for o in ObjectDB.objects.all() if o.key=='Scout'][0]; s.current_shard='shard1'; s.current_room_uuid='b1000000-0000-4000-8000-000000000001'
ic Scout
```

In the same session matters. A fresh login resolves the character's location pair again, and where it
lands is not necessarily where the last session left it.

## Build the rooms

Once per shard, in character on that shard:

```
py from world.demo_rooms import build; self.msg(build())
```

There is no world source here, so [world/demo_rooms.py](router/world/demo_rooms.py) stands in for one.
It gives each room a fixed `scaling_room_uuid` and a `mob_area` tag — two things for two libraries,
neither of which knows the other set the other one. Safe to run more than once.

shard0 gets four `millholm` rooms (one also `millholm_deep`) and one untagged. shard1 gets two
`wilds` rooms and one untagged. The untagged rooms are the control: no rule can reach them.

## What to look at

```
ms_load shard=shard0        # on shard0 — deploys
ms_load shard=shard1        # on shard0 — refused, it names another instance
ms_load all                 # anywhere — refused, the manifest spans every instance
ms_load shard=shard0        # on the router — refused by role
ms_status shard=shard0      # state, rule count, seconds to next tick
ms_stop / ms_restart / ms_delete
```

`ms_spawn_report` raises `NotImplementedError`, deliberately — a whole-world census has no instance
that can answer it. The operator gets a traceback rather than a sentence, which is what raising from
a command's `func()` does.

The thing worth seeing is what is *not* there. On shard1,
`py from evennia_mob_spawner.script import MobSpawnerScript; self.msg(repr([s.key for s in MobSpawnerScript.objects.all()]))`
returns `[]` while shard0's script is deployed and ticking. Each instance has its own `ScriptDB`, so
a script exists only where it was deployed and ticks only there. Nothing declares that and nothing
enforces it.

## Notes

- **macOS needs `sqlean.py`, and it is load-bearing.** The `darwin` block at the top of
  `settings_common.py` swaps in a non-Apple SQLite. Without it a daemonized start deadlocks on the
  child's first query — silently, nothing in any log. A demo that hangs after
  "Portal starting server ..." is this.
- **The tick is 5 seconds**, not the 15-second default, so a population fills while you are watching.
- The rule repo is read through `LocalReader` pointed at `../rules`, resolved from each instance's own
  `GAME_DIR` — one manifest, three readers, same files.
