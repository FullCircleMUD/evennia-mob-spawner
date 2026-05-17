# SPDX-License-Identifier: BSD-3-Clause
"""Django AppConfig for evennia-mob-spawner.

When the consumer adds ``evennia_mob_spawner`` to ``INSTALLED_APPS``,
this config's ``ready()`` runs and auto-installs the library's permanent
admin commands into ``AccountCmdSet``. AccountCmdSet's commands are
available OOC, and merge with ``CharacterCmdSet`` when an account
puppets a character — so a single patch makes the commands work in
both contexts.

Pattern mirrored from evennia-world-builder / evennia-shards: at
``ready()`` time, Evennia's lazy attribute exports (``evennia.Command``
etc.) are still ``None`` because ``evennia._init()`` hasn't run yet.
Real-runtime entry points (server.py, portal.py, runtests.py) call
``_init()`` AFTER ``django.setup()`` triggers our ``ready()``. We wrap
``_init()`` so the patching happens once the lazy exports are populated.
"""
from django.apps import AppConfig


class EvenniaMobSpawnerConfig(AppConfig):
    name = "evennia_mob_spawner"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        import evennia

        if getattr(evennia, "_evennia_mob_spawner_init_wrapped", False):
            return

        _original_init = evennia._init

        def _ems_wrapped_init(*args, **kwargs):
            _original_init(*args, **kwargs)
            _install_commands()

        evennia._init = _ems_wrapped_init
        evennia._evennia_mob_spawner_init_wrapped = True


def _install_commands():
    """Patch AccountCmdSet.at_cmdset_creation to add library commands.

    AccountCmdSet is the right home: its commands are available OOC,
    and merge with CharacterCmdSet on puppet, so the same command
    works in both contexts.

    Idempotent — the patch flag prevents double-installation if
    multiple libraries (or repeated ``_init()`` calls) reach this code.
    """
    from evennia.commands.default.cmdset_account import AccountCmdSet

    if getattr(AccountCmdSet, "_evennia_mob_spawner_cmdset_patched", False):
        return

    _original_at_cmdset_creation = AccountCmdSet.at_cmdset_creation

    def _ems_patched_at_cmdset_creation(self):
        _original_at_cmdset_creation(self)
        from .commands import (
            CmdMsDelete,
            CmdMsLoad,
            CmdMsRestart,
            CmdMsSpawnReport,
            CmdMsStatus,
            CmdMsStop,
        )

        self.add(CmdMsLoad())
        self.add(CmdMsStop())
        self.add(CmdMsRestart())
        self.add(CmdMsDelete())
        self.add(CmdMsStatus())
        self.add(CmdMsSpawnReport())

    AccountCmdSet.at_cmdset_creation = _ems_patched_at_cmdset_creation
    AccountCmdSet._evennia_mob_spawner_cmdset_patched = True
