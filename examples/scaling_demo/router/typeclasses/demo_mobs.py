"""Mobs for the demo's spawn rules to place.

Deliberately plain. mob-spawner is typeclass-agnostic — it calls
`create_object(typeclass=<rule.typeclass>)` and stamps its own identity
tags, and the typeclass neither reads nor writes them. Nothing here
inherits from anything the library provides, because the library provides
no base class to inherit.

`Rat` carries the one optional protocol surface, `ms_at_post_spawn`, so the
demo can show it firing. `Wolf` does not, so the demo also shows that a
typeclass without it spawns perfectly well.
"""

from evennia.objects.objects import DefaultCharacter


class Wolf(DefaultCharacter):
    """No post-spawn hook. The library asks and gets nothing, which is fine."""


class Rat(DefaultCharacter):
    """Carries the duck-typed hook the library calls after a spawn."""

    def ms_at_post_spawn(self):
        # Visible proof the hook fired, readable from `examine` in game.
        self.db.post_spawn_ran = True


class Boss(DefaultCharacter):
    """A single-target mob, for watching one rule's population separately."""
