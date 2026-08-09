# SPDX-License-Identifier: BSD-3-Clause
"""Deployer — terminal stage of the pipeline.

For each rule-set YAML file in the validated ``LoadResult``:
1. Find existing ``MobSpawnerScript`` by ``db_key = file_path``, or
   create a new one if none exists.
2. Snapshot the script's runtime bookkeeping
   (``last_spawn_times`` / ``last_death_times`` / ``last_observed_counts``).
3. Drain any in-flight tick via ``script.stop_when_safe(60s)``; fall
   back to ``script.force_stop()`` on timeout (decision #13's race
   protocol). Only invoked when the script is currently running.
4. Replace ``db.spawn_table`` with the new rule list.
5. Purge bookkeeping entries for ``rule_id``s that vanished from YAML.
6. Restore bookkeeping for ``rule_id``s that survived the swap.
7. Resume the ticker (only if it was running before the drain).

This is the load-bearing distinction from ``evennia-world-builder``
(architecture decision #6): upsert-with-state-preservation, not
clean-and-rebuild. Cooldown clocks survive YAML edits — reloading
rules because a description changed must not reset a boss's
death-cooldown timer.

The Deployer requires Evennia at runtime — it imports ``script.py``
which dynamically inherits from ``settings.BASE_SCRIPT_TYPECLASS``,
and it calls ``create_script`` for new files. Therefore the Deployer
runs only from ``ms_load`` (Evennia admin command), never from
``ms-validate`` (CLI).
"""
from .definitions import Definitions
from .loader import LoadResult
from .log import ms_log


class Deployer:
    """Upserts validated rules into per-file persistent scripts.

    Construction:
        definitions: parsed Definitions. Reserved for level vocabulary
                     usage in future operator-facing surfaces; not
                     consumed by the upsert logic today.
    """

    def __init__(self, definitions: Definitions):
        self.definitions = definitions

    def deploy(self, load_result: LoadResult) -> None:
        """Upsert every rule-set file in ``load_result``.

        Iterates ``load_result.rule_sets.items()`` and applies the
        upsert protocol to each (file_path, rules) pair.
        ``load_result.file_metadata`` is reserved for future per-file
        directives; no file-level keys are recognised at v0.
        """
        for path, rules in load_result.rule_sets.items():
            self._upsert_one(path, rules)

    def _upsert_one(self, path: str, rules: list) -> None:
        """Find-or-create the script for ``path``; swap its rules and state."""
        script = self._find_or_create(path)

        # Snapshot bookkeeping before swapping the rule table. Any rule
        # whose rule_id survives the swap gets its state restored; any
        # rule whose rule_id vanished from YAML gets its entries purged.
        old_last_spawn_times = dict(script.db.last_spawn_times or {})
        old_last_death_times = dict(script.db.last_death_times or {})
        old_last_observed_counts = dict(script.db.last_observed_counts or {})
        old_spawned_last_tick = dict(script.db.spawned_last_tick or {})

        new_rule_ids = {rule["rule_id"] for rule in rules}
        preserved = set(old_last_spawn_times) & new_rule_ids
        purged = set(old_last_spawn_times) - new_rule_ids

        # Drain any in-flight tick before swapping (decision #13's
        # race protocol). Only meaningful for currently-running scripts
        # — paused or stopped scripts have no in-flight tick to drain.
        # ``was_running`` (not ``is_active``) is the discriminator
        # because Evennia's ``is_active`` is True for both running and
        # paused scripts; only the running case needs draining and
        # post-swap resumption.
        was_running = bool(script.is_active) and not script.db._paused_time
        if was_running:
            drained = script.stop_when_safe(timeout=60.0)
            if not drained:
                ms_log(
                    f"deployer: {path}: stop_when_safe timed out after 60s; "
                    f"forcing stop (in-flight tick will run to completion "
                    f"but new ticks are blocked during swap)",
                    level="WARN",
                )
                script.force_stop()

        try:
            # Swap the rule table.
            script.db.spawn_table = list(rules)

            # Preserve surviving bookkeeping; purge removed.
            script.db.last_spawn_times = {
                rid: ts for rid, ts in old_last_spawn_times.items()
                if rid in new_rule_ids
            }
            script.db.last_death_times = {
                rid: ts for rid, ts in old_last_death_times.items()
                if rid in new_rule_ids
            }
            script.db.last_observed_counts = {
                rid: c for rid, c in old_last_observed_counts.items()
                if rid in new_rule_ids
            }
            script.db.spawned_last_tick = {
                rid: c for rid, c in old_spawned_last_tick.items()
                if rid in new_rule_ids
            }

            ms_log(
                f"deployed {path}: {len(rules)} rule(s) "
                f"({len(preserved)} state preserved, {len(purged)} purged)"
            )
        finally:
            # Clear any lingering stop-request flag (force_stop sets it
            # and doesn't clear; stop_when_safe clears on success but
            # not on timeout). The swap is complete; the next tick
            # should proceed normally.
            script.ndb._stop_requested = False
            if was_running:
                script.unpause()

    def _find_or_create(self, path: str):
        """Return the ``MobSpawnerScript`` for ``path`` (creating if absent)."""
        # Late-imported so this module can be loaded outside an Evennia
        # context — script.py touches ``settings.BASE_SCRIPT_TYPECLASS``
        # at module-import time and may need the gamedir on sys.path.
        from evennia.utils.create import create_script

        from .script import MobSpawnerScript

        existing = MobSpawnerScript.objects.filter(db_key=path).first()
        if existing is not None:
            self._stamp_owning_shard(existing)
            return existing

        script = create_script(
            typeclass=MobSpawnerScript,
            key=path,
            persistent=True,
        )
        self._stamp_owning_shard(script)
        return script

    @staticmethod
    def _stamp_owning_shard(script) -> None:
        """Declare which shard owns `script`, when running sharded.

        ``ms_load`` only runs on the process that owns the scope, so the
        shard this deploy is happening on *is* the owning shard. The shards
        library reads this Attribute to confine the script's ticks to that
        process — without it, the row is visible to every process and each
        attaches its own ``LoopingCall``.

        No-op off a sharded deployment: nothing is stamped, and an unstamped
        script is unconfined, which is correct because the only way to be
        unstamped is to have been created where sharding wasn't in play.
        """
        from .config import active_shard_id

        shard_id = active_shard_id()
        if shard_id is None:
            return

        try:
            from evennia_shards import OWNING_SHARD_ATTR
        except ImportError:
            return

        script.attributes.add(OWNING_SHARD_ATTR, shard_id)
