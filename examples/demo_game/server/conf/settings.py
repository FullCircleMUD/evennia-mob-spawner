r"""
Evennia settings file for the mob-spawner demo gamedir.

This gamedir is the integration point for ``evennia-mob-spawner`` and
``evennia-world-builder``. The two libraries are siblings — neither
depends on the other — and this gamedir installs and configures
whichever ones are currently in the venv.

See README.md in this directory for setup steps.
"""

import os

# Use the defaults from Evennia unless explicitly overridden.
from evennia.settings_default import *  # noqa: F401, F403

######################################################################
# Server identity
######################################################################

SERVERNAME = "mob_spawner_demo"


######################################################################
# Library INSTALLED_APPS registration — each library's AppConfig.ready()
# fires and auto-installs its admin commands into AccountCmdSet.
######################################################################
INSTALLED_APPS = list(INSTALLED_APPS) + [  # noqa: F405
    "evennia_world_builder",
    "evennia_mob_spawner",
]


######################################################################
# world-builder content source — the mob-spawner test-world fixture repo
######################################################################
# Defaults can be overridden via environment variables or
# secret_settings.py. The PAT defaults to empty; wb_build refuses to
# proceed unless secret_settings.py (or the env var) supplies a real one.
WORLDBUILDER_GITHUB_PAT = os.environ.get("WORLDBUILDER_GITHUB_PAT", "")
WORLDBUILDER_GITHUB_REPO = os.environ.get(
    "WORLDBUILDER_GITHUB_REPO",
    "FullCircleMUD/evennia-mob-spawner-test-world",
)
WORLDBUILDER_GITHUB_REF = os.environ.get(
    "WORLDBUILDER_GITHUB_REF",
    "main",
)


######################################################################
# mob-spawner content source — the rule-set fixture repo
######################################################################
# Pre-wired now even though the library's settings dispatch hasn't
# landed yet (architecture doc has it as [TBD]); these settings are
# inert until ms_load is implemented and starts reading them. Same
# pattern as the world-builder block above.
MOB_SPAWNER_GITHUB_PAT = os.environ.get("MOB_SPAWNER_GITHUB_PAT", "")
MOB_SPAWNER_GITHUB_REPO = os.environ.get(
    "MOB_SPAWNER_GITHUB_REPO",
    "FullCircleMUD/evennia-mob-spawner-test-yaml",
)
MOB_SPAWNER_GITHUB_REF = os.environ.get(
    "MOB_SPAWNER_GITHUB_REF",
    "main",
)


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *  # noqa: F401, F403
except ImportError:
    print("secret_settings.py file not found or failed to import.")


######################################################################
# Reader kwargs — built AFTER secret_settings import so PAT/repo/ref
# overrides there are reflected. Both libraries default to GitHubReader,
# so (repo, ref, pat) is the right kwargs shape for each.
######################################################################
WORLDBUILDER_READER_KWARGS = {
    "repo": WORLDBUILDER_GITHUB_REPO,
    "ref": WORLDBUILDER_GITHUB_REF,
    "pat": WORLDBUILDER_GITHUB_PAT,
}

MOB_SPAWNER_READER_KWARGS = {
    "repo": MOB_SPAWNER_GITHUB_REPO,
    "ref": MOB_SPAWNER_GITHUB_REF,
    "pat": MOB_SPAWNER_GITHUB_PAT,
}
