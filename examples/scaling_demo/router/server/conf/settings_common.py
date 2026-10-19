"""
Common configuration shared by every instance in the demo.

One source tree, three settings files. `router/` holds all the code and all
four settings files; `shard0/` and `shard1/` symlink back to it and own only
their own `server/` directory. Identity is config, never a separate checkout.

Cascade:
    settings_router.py / settings_shard0.py / settings_shard1.py
        -> settings_common.py (this file)
            -> settings.py

Anything every instance needs goes here, so there is one place to change it
and no way for the three to drift. Anything that differs — a name, a role, a
set of ports — goes in the per-instance file and nowhere else.

What each instance owns vs shares:

    evennia.db3      per instance   its own world, its own Limbo, its own #1
    logs/            per instance   so a log line belongs to one instance
    archive.db3      shared         a character between instances lives here
    messagebus.db3   shared         how instances tell each other anything
    rules/           shared         the YAML rule repo, read by every instance
    everything else  shared         symlinks back to router/

This demo exists to exercise `evennia-mob-spawner` under `evennia-scaling`.
The scaling half is copied from that library's own `examples/`, which is the
executable reference for it; what is added here is the mob-spawner half —
the app, the reader pointed at `rules/`, and a mob typeclass to spawn.
"""

import os
import sys

# ── macOS only: use a bundled, non-Apple SQLite build ────────────────
#
# macOS ships /usr/lib/libsqlite3.dylib, which drives sqlite3_initialize()
# through libdispatch. libdispatch does not survive fork(), so once any
# SQLite connection has been opened, a daemonizing (forking) start deadlocks
# on the child's first SQLite call — silently, with no error or timeout.
# `evennia start` forks on Unix; `--nodaemon` and Windows do not, which is
# why this only bites daemonized starts on macOS.
if sys.platform == "darwin":
    try:
        import sqlean
        import sqlean.dbapi2

        class _DemoConnection(sqlean.dbapi2.Connection):
            def getlimit(self, category):
                # Django uses this only to size bulk_create batches.
                return 999

        _sqlean_connect = sqlean.dbapi2.connect

        def _connect(*args, **kwargs):
            kwargs.setdefault("factory", _DemoConnection)
            return _sqlean_connect(*args, **kwargs)

        sqlean.dbapi2.connect = _connect
        sqlean.connect = _connect
        sqlean.SQLITE_LIMIT_VARIABLE_NUMBER = 9
        sqlean.dbapi2.SQLITE_LIMIT_VARIABLE_NUMBER = 9

        sys.modules["sqlite3"] = sqlean
        sys.modules["sqlite3.dbapi2"] = sqlean.dbapi2
    except ImportError:
        pass

from server.conf.settings import *  # noqa: F401, F403, E402

######################################################################
# Apps
######################################################################
#
# `evennia_portal_multiplex` must come before `evennia_scaling`. Django runs
# each AppConfig.ready() in this order, and multiplex refuses an instance
# that has not declared MULTIPLEX_INSTANCE_ID — which scaling then reads
# without checking. Listed the other way round, a missing name is reported
# as multiplex's error raised from scaling, saying nothing about where to
# look. Alphabetical gives the right order, which is why it is easy to get
# right and easy to break by hand.
#
# `evennia_mob_spawner` has no ordering constraint against any of them: it
# reads scaling behind a try-import at command time, not at ready().
# `evennia_calendar` supplies the clock the spawner's tick runs on.
INSTALLED_APPS = list(INSTALLED_APPS) + [  # noqa: F405
    "evennia_archive",
    "evennia_calendar",
    "evennia_database_cascade",
    "evennia_message_bus",
    "evennia_mob_spawner",
    "evennia_portal_multiplex",
    "evennia_scaling",
]

######################################################################
# Who the router is
######################################################################
#
# The one shared literal in the whole deployment. Every other name either
# points at this or is an instance's own, declared in its own file.
MULTIPLEX_DEFAULT_INSTANCE = "router"

# Same instance, named for the library that asks. Assignment rather than a
# second literal, so the two cannot drift.
SCALING_ROUTER_ID = MULTIPLEX_DEFAULT_INSTANCE

######################################################################
# Every shard in the deployment
######################################################################
#
# **This must be an exact duplicate of every shard's MULTIPLEX_INSTANCE_ID,
# spelled identically.** Nothing can check that: no instance can read
# another's settings.
#
# These two names are also the first-level folder names in `rules/`, because
# mob-spawner's pairing with scaling mandates that the first declared level
# is `shard` and its value is the instance id. A folder here that no
# instance answers to is a rule set nothing can ever deploy.
SCALING_SHARDS = ("shard0", "shard1")

######################################################################
# Where the world's anchor rooms are
######################################################################
#
# Evennia's START_LOCATION and DEFAULT_HOME name two rooms by primary key,
# which names nothing across instances — every instance has its own #5.
# These supply the missing half. Both anchors are on shard0 in this demo.
SCALING_START_LOCATION_SHARD = "shard0"
SCALING_DEFAULT_HOME_SHARD = "shard0"

# The Hollow Oak and the Millholm Road, both on shard0. The uuids are fixed
# in `world/demo_rooms.py`, which stands in for the world source this demo
# does not have — changing one means changing the other.
SCALING_START_LOCATION_UUID = "a1000000-0000-4000-8000-000000000002"
SCALING_DEFAULT_HOME_UUID = "a1000000-0000-4000-8000-000000000001"

######################################################################
# Locks
######################################################################
#
# `list(...)` rather than `+=`: Evennia declares this as a tuple, and `+=`
# with a list raises TypeError before the server starts.
#
# The archive's mixin writes `owns_character()` into a character's puppet,
# edit and delete locks. Without this the clause cannot resolve, evaluates
# false, and an account is refused its own character with nothing logged.
LOCK_FUNC_MODULES = list(LOCK_FUNC_MODULES) + [  # noqa: F405
    "evennia_archive.lockfuncs",
]

######################################################################
# The launcher verb that starts a Server without a Portal
######################################################################
#
# Declared for all three even though only the shards use it. Without the
# setting the verb does not resolve, and it fails silently — it falls
# through to Django and is reported as an unknown command.
EXTRA_LAUNCHER_COMMANDS = {
    "server_start": "evennia_portal_multiplex.launcher.server_start",
}

######################################################################
# One Portal, on the router
######################################################################
#
# The AMP port the router's Portal listens on, and therefore the port the
# shards dial. One constant used three times, because a mismatch means a
# Server that never attaches and a registry that never hears of it.
MULTIPLEX_AMP_PORT = 4016

######################################################################
# Typeclasses
######################################################################
#
# The account and character carry scaling's mixins, or the instance refuses
# to start. The room's is not checked at boot and is the one worth checking
# by hand — a room with no uuid costs a character their location, silently.
BASE_ACCOUNT_TYPECLASS = "typeclasses.accounts.Account"
BASE_CHARACTER_TYPECLASS = "typeclasses.characters.Character"
BASE_ROOM_TYPECLASS = "typeclasses.rooms.Room"

######################################################################
# Mob spawner
######################################################################
#
# The rule repo is a directory in this demo rather than a GitHub checkout,
# so the reader is the local one. `rules/` sits beside the three gamedirs
# and is read identically by all of them — one manifest describing every
# instance's rule sets, which is exactly the arrangement that makes the
# `all` scope refusable: `all` here would deploy shard1's rules onto
# shard0.
#
# GAME_DIR is this instance's own directory, so `../rules` resolves to the
# same place from all three.
MOB_SPAWNER_READER = "evennia_yaml_reader.LocalReader"
MOB_SPAWNER_READER_KWARGS = {
    "root": os.path.join(os.path.dirname(GAME_DIR), "rules"),  # noqa: F405
}

# Faster than the 15-second default, so a population fills in while you are
# watching it rather than after you have moved on.
MOB_SPAWNER_TICK_SECONDS = 5
