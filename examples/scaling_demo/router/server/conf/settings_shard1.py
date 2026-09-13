"""shard1: a place a character is played, and where rule sets deploy.

Owns its own Evennia database, so the MobSpawnerScript rows deployed here
exist nowhere else and tick only here. Its rule sets are `rules/shard1/`.
"""

import os

from server.conf.settings_common import *  # noqa: F401, F403

SERVERNAME = "mob-spawner scaling demo (shard1)"

SCALING_ROLE = "shard"

# The only per-instance literal. It must appear in SCALING_SHARDS spelled
# exactly this way, and it is the folder name under `rules/` that holds
# this instance's rule sets.
MULTIPLEX_INSTANCE_ID = "shard1"
MESSAGEBUS_INSTANCE_ID = MULTIPLEX_INSTANCE_ID

# On here, because a session arrives already knowing which character it is
# playing. Auto-puppet is what turns that into actually playing.
AUTO_PUPPET_ON_LOGIN = True

# Dials the router's Portal rather than opening one of its own. The telnet
# and web ports are never listened on; they are distinct so that starting
# this instance fully by accident fails on something obvious instead of
# three instances quietly fighting over one port.
AMP_PORT = MULTIPLEX_AMP_PORT  # noqa: F405
TELNET_PORTS = [4030]
WEBSERVER_PORTS = [(4031, 4032)]
WEBSOCKET_CLIENT_PORT = 4033

# Last, and never inside settings_common.py — see settings_router.py.
from evennia_database_cascade import configure  # noqa: E402

DATABASES, DATABASE_ROUTERS = configure(  # noqa: F405
    DATABASES, INSTALLED_APPS, GAME_DIR, os.environ,  # noqa: F405
)
