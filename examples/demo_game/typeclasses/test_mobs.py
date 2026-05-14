# SPDX-License-Identifier: BSD-3-Clause
"""Synthetic test mob typeclasses for mob-spawner integration smokes.

These exist to be referenced by the fixture rules in
``evennia-mob-spawner-test-yaml`` so a live ``ms_load`` against this
gamedir can exercise spawn end-to-end. They are deliberately bare —
inherit from ``DefaultObject``, add nothing, override nothing. The
library's exact-match typeclass counting (architecture decision #9)
treats each subclass as a distinct population, so the variant pattern
(``Wolf`` vs ``WolfFat``) demonstrates per-typeclass population
management without any behaviour the test mobs would actually need.

These are *not* production mobs and have no AI, combat, loot, or other
game behaviour. They exist only to be created by the library and
counted by its tick loop.
"""
from evennia import DefaultObject


class Wolf(DefaultObject):
    """Base test wolf."""


class WolfFat(Wolf):
    """Wolf variant — same surface as Wolf, distinct typeclass.

    Demonstrates the indistinguishable-variant pattern: same ``key`` /
    ``desc`` as ``Wolf`` in the fixture rules, but a distinct typeclass
    so the spawn system maintains its population independently.
    """


class Rabbit(DefaultObject):
    """Base test rabbit."""


class Kobold(DefaultObject):
    """Test kobold — pack member; spawns alongside the chieftain."""


class KoboldChieftain(DefaultObject):
    """Test boss-style mob — single instance, den-room placement,
    death-cooldown semantics. Per-spawn state reset via
    ``ms_at_post_spawn`` (architecture decision #23): the library
    invokes this method after the mob is fully constructed and
    rule-level ``attrs`` overrides have been applied.
    """

    def ms_at_post_spawn(self):
        """Reset per-instance state that should not carry across spawns."""
        self.db.has_rallied = False
