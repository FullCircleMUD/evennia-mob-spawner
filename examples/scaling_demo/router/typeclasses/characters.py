"""The character typeclass, carrying scaling's mixin.

Identity is minted at creation and never reissued, so a character created
without this can never be archived — which is why the boot refuses rather
than warns.
"""

from evennia.objects.objects import DefaultCharacter
from evennia_scaling.mixins import ScalingCharacterMixin


class Character(ScalingCharacterMixin, DefaultCharacter):
    pass
