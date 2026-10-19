# SPDX-License-Identifier: BSD-3-Clause
"""Deployer — terminal stage of the pipeline.

For each rule-set YAML file in the validated ``LoadResult``, in one
transaction:
1. Create a row for each rule seen for the first time.
2. Update a surviving rule's row in place — its ``rule`` only. The row keeps
   its primary key, so its tick slot does not move, and keeps its timers, so
   reloading rules because a description changed does not reset a boss's
   death cooldown.
3. Delete the rows of rules gone from the file.

Files outside the ``LoadResult`` are untouched. Writing rows is all the
Deployer does: ``ms_load`` swaps the in-memory table the tick reads once the
load has completed, on the reactor thread.

This is the load-bearing distinction from ``evennia-world-builder``
(architecture decision #6): upsert-with-state-preservation, not
clean-and-rebuild.
"""
from .config import RuleField
from .definitions import Definitions
from .loader import LoadResult
from .log import ms_log


class Deployer:
    """Upserts validated rules into the rule table.

    Construction:
        definitions: parsed Definitions. Reserved for level vocabulary
                     usage in future operator-facing surfaces; not
                     consumed by the upsert logic today.
    """

    def __init__(self, definitions: Definitions):
        self.definitions = definitions

    def deploy(self, load_result: LoadResult) -> None:
        """Upsert every rule-set file in ``load_result``.

        ``load_result.file_metadata`` is reserved for future per-file
        directives; no file-level keys are recognised at v0.
        """
        for path, rules in load_result.rule_sets.items():
            self._upsert_one(path, rules)

    def _upsert_one(self, path: str, rules: list) -> None:
        """Create, update and delete ``path``'s rows to match ``rules``."""
        # Late-imported: models need the app registry, and this module is
        # imported by the command module before Evennia is fully up.
        from django.db import router, transaction

        from .models import SpawnRule

        rule_id = RuleField.RULE_ID.value
        with transaction.atomic(using=router.db_for_write(SpawnRule)):
            existing = {
                row.rule_id: row.pk for row in SpawnRule.objects.filter(file=path)
            }
            new_rule_ids = {rule[rule_id] for rule in rules}

            for rule in rules:
                pk = existing.get(rule[rule_id])
                if pk is None:
                    SpawnRule.objects.create(
                        file=path, rule_id=rule[rule_id], rule=rule,
                    )
                else:
                    SpawnRule.objects.filter(pk=pk).update(rule=rule)

            # SpawnRule has no lifecycle hooks, so a queryset delete is safe.
            purged = sorted(set(existing) - new_rule_ids)
            SpawnRule.objects.filter(file=path, rule_id__in=purged).delete()

        preserved = len(set(existing) & new_rule_ids)
        ms_log(
            f"deployed {path}: {len(rules)} rule(s) "
            f"({preserved} timers preserved, {len(purged)} purged)"
        )
