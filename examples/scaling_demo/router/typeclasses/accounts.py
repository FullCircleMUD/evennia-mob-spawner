"""The account typeclass, carrying scaling's mixin.

`ScalingAccountMixin` already carries `evennia-archive`'s
`ArchivableAccountMixin` — listing both is wrong, and listing archive's
first cannot import at all, because Python refuses a base class that
precedes its own subclass.
"""

from evennia.accounts.accounts import DefaultAccount, DefaultGuest  # noqa: F401
from evennia_scaling.mixins import ScalingAccountMixin


class Account(ScalingAccountMixin, DefaultAccount):
    pass


class Guest(DefaultGuest):
    """Deliberately without the mixin — a guest carries nothing worth moving."""
