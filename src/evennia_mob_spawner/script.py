# SPDX-License-Identifier: BSD-3-Clause
"""MobSpawnerScript — one persistent Evennia script per rule-set YAML file.

Architecture:
    Each rule-set YAML file deployed via ``ms_load`` gets exactly one
    persistent ``MobSpawnerScript`` (decision #4). The script owns the
    rule table for its file, the bookkeeping dicts for cooldown / death
    observation, and the tick loop that maintains population against
    those rules.

Inheritance:
    Subclasses whichever class ``settings.BASE_SCRIPT_TYPECLASS`` points
    at (decision #25). Falls back to ``evennia.DefaultScript`` if the
    setting is missing. This lets consumer customisations on their base
    script (logging, telemetry, dashboard registration, etc.) compose
    into the library's scripts automatically — without forcing the
    consumer to choose between library benefits and their own
    customisation.

    Consumer base-class contract: must behave like a ``DefaultScript``
    subclass. If you override ``at_repeat`` in your base, always call
    ``super().at_repeat()`` so the library's tick loop runs.

Persistent state (architecture.md "The tick loop"):
    - ``db.spawn_table``           — list of rule dicts (the YAML rules).
    - ``db.last_spawn_times``      — ``{rule_id: timestamp_of_last_spawn}``.
    - ``db.last_death_times``      — ``{rule_id: timestamp_of_last_observed_death}``.
    - ``db.last_observed_counts``  — ``{rule_id: count_at_end_of_last_tick}``.
    - ``db.spawned_last_tick``     — count of spawns produced in the prior tick
                                     (per-rule, ``{rule_id: count}``).

Tick logic stub at this stage:
    ``at_repeat`` is a no-op pass-through. The observation / death-
    detection / cooldown-check / room-pick / spawn sequence (architecture.md
    "The tick loop") lands in a follow-up pass. The script's lifecycle and
    state-management plumbing must work end-to-end first.
"""
from django.conf import settings
from evennia.utils.utils import class_from_module

from .config import get_tick_seconds


# Resolve the base script class at module-import time. Falls back to
# evennia.DefaultScript if BASE_SCRIPT_TYPECLASS is missing or unset
# (defensive — the setting is typically present in any standard
# Evennia configuration).
_BASE_SCRIPT = class_from_module(
    getattr(settings, "BASE_SCRIPT_TYPECLASS", "evennia.DefaultScript")
)


class MobSpawnerScript(_BASE_SCRIPT):
    """One persistent script per rule-set YAML file.

    See module docstring for inheritance rationale (decision #25),
    persistent state shape, and the tick-logic-stub note.
    """

    def at_script_creation(self):
        """Initialise persistent state on the script's first creation.

        Called once per script lifetime, when ``create_script`` first
        persists the script to the database. Subsequent server restarts
        skip this hook — the persistent state is loaded from the DB.

        Always calls ``super()`` so any consumer customisation on
        ``BASE_SCRIPT_TYPECLASS.at_script_creation`` runs (e.g.
        registering the script with a monitoring system, applying
        org-wide tag conventions).
        """
        super().at_script_creation()

        # Tick interval (decision #3) — library-level setting,
        # overridable via MOB_SPAWNER_TICK_SECONDS. Stored on the
        # script at creation; changing the setting later doesn't
        # affect existing scripts.
        self.interval = get_tick_seconds()
        self.persistent = True
        self.start_delay = True  # Don't fire at_repeat immediately on creation.

        # Bookkeeping initialised empty. The Deployer populates
        # ``spawn_table`` after creation; ``last_*`` dicts accumulate
        # state across ticks.
        self.db.spawn_table = []
        self.db.last_spawn_times = {}
        self.db.last_death_times = {}
        self.db.last_observed_counts = {}
        self.db.spawned_last_tick = {}

    def at_repeat(self):
        """Tick handler — no-op stub at this stage.

        The full tick loop (observe / detect deaths / cooldown check /
        population check / room selection / spawn / save state) lands in
        a follow-up implementation pass. ``super().at_repeat()`` is
        invoked so consumer-side overrides (logging, telemetry) still
        run.
        """
        super().at_repeat()
        # Tick logic intentionally absent at this stage.
