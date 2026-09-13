"""The room typeclass, carrying scaling's mixin.

**Not checked at boot**, unlike the account and character. A room without
this carries no `scaling_room_uuid`, and a character who leaves from one
cannot be returned to it — silently, with every character appearing at
`DEFAULT_HOME` instead.

The uuid is assigned, never minted: the mixin adds the attribute and leaves
it `None` for the world source to fill in. Here that source is
`world/demo_rooms.py`. A room that minted its own would mint a fresh one on
every rebuild, which is the moment the identity has to hold still.
"""

from evennia.objects.objects import DefaultRoom
from evennia_scaling.mixins import ScalingRoomMixin


class Room(ScalingRoomMixin, DefaultRoom):
    pass
