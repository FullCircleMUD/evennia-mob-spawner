"""The router: the Portal, the login screen, and the out-of-character game.

Holds no game world and runs no spawn rules. `ms_load` is refused here by
role — see evennia-mob-spawner's docs/interoperability.md.
"""

import os

from server.conf.settings_common import *  # noqa: F401, F403

SERVERNAME = "mob-spawner scaling demo (router)"

SCALING_ROLE = "router"

# It is the router, so its own id is the shared literal rather than a
# second spelling of it.
MULTIPLEX_INSTANCE_ID = MULTIPLEX_DEFAULT_INSTANCE  # noqa: F405
MESSAGEBUS_INSTANCE_ID = MULTIPLEX_INSTANCE_ID

# Off here, because going in character on the router is a transfer. Left on,
# an account with one character is auto-puppeted the instant it logs in,
# which sends the session straight to a shard — so the character-select
# menu is unreachable and coming back out of character bounces straight in.
AUTO_PUPPET_ON_LOGIN = False

# This instance listens; the shards dial it.
AMP_PORT = MULTIPLEX_AMP_PORT  # noqa: F405
TELNET_PORTS = [4010]
WEBSERVER_PORTS = [(4011, 4012)]
WEBSOCKET_CLIENT_PORT = 4013

# Last, and never inside settings_common.py. configure() resolves
# INSTALLED_APPS to packages, which imports Evennia's logger, which reads a
# setting — and that read makes Django rebuild the settings from the
# top-level module. Called from a shared module mid-import that namespace is
# still empty and the boot dies on an AttributeError for a setting that is
# in fact set.
from evennia_database_cascade import configure  # noqa: E402

DATABASES, DATABASE_ROUTERS = configure(  # noqa: F405
    DATABASES, INSTALLED_APPS, GAME_DIR, os.environ,  # noqa: F405
)
