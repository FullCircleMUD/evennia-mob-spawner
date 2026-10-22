# SPDX-License-Identifier: BSD-3-Clause
"""The rule table: one row per deployed rule, carrying the rule and its timers.

Rules live in the game's default database. They are a copy of the YAML, and
``ms_load`` rebuilds them, so nothing here needs a database of its own.
"""
from django.db import models
from evennia.utils.picklefield import PickledObjectField


class SpawnRule(models.Model):
    """One deployed rule.

    ``(file, rule_id)`` is the rule's identity. The primary key is what places
    the rule in a tick slot, so ``ms_load`` updates a surviving rule's row in
    place rather than replacing it.

    The two timers are the only runtime state that outlives the process.

    ``rule`` is pickled, not JSON, so it comes back exactly as the loader built
    it from the YAML. JSON turns an integer dict key into a string and a tuple
    into a list — see MODEL-03.
    """

    file = models.CharField(max_length=255)
    rule_id = models.PositiveIntegerField()
    rule = PickledObjectField()
    last_spawn_time = models.FloatField(null=True, blank=True)
    last_death_time = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["file", "rule_id"], name="mob_spawner_unique_file_rule",
            ),
        ]

    def __str__(self):
        return f"{self.file}:{self.rule_id}"
