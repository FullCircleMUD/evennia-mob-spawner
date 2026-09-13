# SPDX-License-Identifier: BSD-3-Clause
"""The demo's world source: rooms carrying a uuid and an area tag.

Run it in game, as a superuser, on the instance you want rooms on::

    py from world.demo_rooms import build; build()

Safe to run more than once — a room whose uuid is already here is left
alone, so it is also how you repair the set after a rebuild.

There is no real world source in this demo, so this stands in for one. Each
room gets two things, for two different libraries:

    scaling_room_uuid   evennia-scaling   which room, across instances
    mob_area tag        evennia-mob-spawner   which rules may spawn here

The two are independent. A room can carry either, both or neither, and
neither library knows the other set the other one.

**The rooms differ per instance, and that is the point.** shard0 holds the
Millholm rooms and shard1 holds the Wilderness rooms, matching the rule-set
folders under `rules/`. A rule set deployed against rooms that were never
tagged finds no candidate rooms and spawns nothing, which is the failure
this arrangement lets you see rather than guess at.

**Limbo deliberately gets neither.** It is the room to walk into when you
want to see what happens somewhere the deployment cannot reproduce.
"""

#: Fixed uuids, per instance. `a…` on shard0, `b…` on shard1, so a glance at
#: a character's `current_room_uuid` says which shard it names.
#:
#: The first two on shard0 are what SCALING_DEFAULT_HOME_UUID and
#: SCALING_START_LOCATION_UUID point at, so changing them here means
#: changing settings_common.py too.
#:
#: The third element is the `mob_area` tag, or None for a room no rule can
#: reach. Millholm's dead-end carries a second tag as well, so the demo has
#: a room that two different rules can both select.
ROOMS = {
    "shard0": [
        ("a1000000-0000-4000-8000-000000000001", "Millholm Road", ["millholm"]),
        ("a1000000-0000-4000-8000-000000000002", "The Hollow Oak", ["millholm"]),
        ("a1000000-0000-4000-8000-000000000003", "Mill Pond", ["millholm"]),
        ("a1000000-0000-4000-8000-000000000004", "The Old Cellar", ["millholm", "millholm_deep"]),
        ("a1000000-0000-4000-8000-000000000005", "Quiet Chapel", None),
    ],
    "shard1": [
        ("b1000000-0000-4000-8000-000000000001", "Wilderness Track", ["wilds"]),
        ("b1000000-0000-4000-8000-000000000002", "Deep Thicket", ["wilds"]),
        ("b1000000-0000-4000-8000-000000000003", "Frozen Scree", None),
    ],
}

#: The category every area tag is stored under. mob-spawner's default, and
#: what `MOB_SPAWNER_AREA_TAG_CATEGORY` would override. A tag written under
#: any other category is invisible to the library — the rule finds no rooms
#: and the population silently stays at zero.
AREA_TAG_CATEGORY = "mob_area"

ROOM_TYPECLASS = "typeclasses.rooms.Room"
EXIT_TYPECLASS = "typeclasses.exits.Exit"


def build():
    """Create this instance's rooms, and return a summary to read.

    Walking between them matters as much as arriving: moving is what
    restamps a character's location pair, and it is how you watch a mob
    population from inside a room rather than from `ms_status`.
    """
    from evennia.utils.create import create_object
    from evennia_portal_multiplex.config import get_instance_id
    from evennia_scaling.mixins import find_room_by_uuid

    here = get_instance_id()
    wanted = ROOMS.get(here)
    if not wanted:
        return (
            f"{here} has no rooms in demo_rooms.ROOMS. It is the router, or "
            f"a name not in {tuple(ROOMS)}."
        )

    built, kept, rooms = [], [], []
    for room_uuid, key, area_tags in wanted:
        existing = find_room_by_uuid(room_uuid)
        if existing:
            room = existing
            kept.append(f"{room.key} {room.dbref}")
        else:
            room = create_object(ROOM_TYPECLASS, key=key)
            room.scaling_room_uuid = room_uuid
            built.append(f"{room.key} {room.dbref} {room_uuid}")

        # Re-stamped every run, not only on creation: this is the repair
        # path for a room whose tags were removed by hand, and adding a tag
        # that is already there is a no-op.
        for tag in area_tags or ():
            room.tags.add(tag, category=AREA_TAG_CATEGORY)

        rooms.append(room)

    lines = [f"{here}:"]
    lines += [f"  built  {line}" for line in built]
    lines += [f"  kept   {line}" for line in kept]
    lines += [f"  exits  {_link(rooms)}"]
    lines += ["  tagged " + _describe_tags(rooms)]
    return "\n".join(lines)


def _link(rooms):
    """Two-way exits along the chain, skipping any that already exist."""
    from evennia.utils.create import create_object

    made = 0
    for first, second in zip(rooms, rooms[1:]):
        for source, target, name in (
            (first, second, second.key.lower()),
            (second, first, first.key.lower()),
        ):
            if any(exit.key == name for exit in source.exits):
                continue
            create_object(
                EXIT_TYPECLASS, key=name, location=source, destination=target,
            )
            made += 1
    return f"{made} made"


def _describe_tags(rooms):
    """One line per area tag, with how many rooms carry it.

    The number is what a rule's `target` is checked against by hand: a
    target of 6 across two rooms with `max_per_room: 1` can only ever reach
    2, and that is a rule-authoring mistake nothing refuses.
    """
    counts = {}
    for room in rooms:
        for tag in room.tags.get(category=AREA_TAG_CATEGORY, return_list=True):
            counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return "none"
    return ", ".join(f"{tag}={n} room(s)" for tag, n in sorted(counts.items()))
