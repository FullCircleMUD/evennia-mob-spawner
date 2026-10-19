# SPDX-License-Identifier: BSD-3-Clause
"""The running spawner: the in-memory rule table, the tick, and spawning.

``start_mob_spawner()`` is called from the game's ``at_server_start()``. It
reads every rule row into memory and registers the tick with
``evennia-calendar``, whose clock then fires it once a second. Each second the
tick runs the rules whose primary key falls in that second's slot —
``pk % N == second % N``, where ``N`` is ``MOB_SPAWNER_TICK_SECONDS`` — so a
large rule set spreads across the cycle instead of running in one burst.

Rules are read from memory, never from the database, between loads. The two
timers are written to the row and to memory together; the observed counts the
death formula needs live in memory only.

A rule's pass:
  1. Observe — count living mobs produced by this rule, keyed on the
     (file, rule_id) identity tags stamped at spawn time.
  2. Detect deaths — ``deaths = (last_observed + spawned_last) - current``
     (decision #5: observation, not callback). Stamp ``last_death_time`` if
     positive.
  3. Cooldown — gate on ``last_spawn_time`` (``respawn_seconds``) or
     ``last_death_time`` (``death_cooldown_seconds``).
  4. Population — skip if at ``target``.
  5. Condition — skip if the rule's ``condition`` answers no.
  6. Room — the pack's room, else the area pool. Every candidate skips a
     room at ``max_per_room`` or holding anything with a session.
  7. Spawn — create, tag, apply ``desc`` / ``attrs``, call
     ``ms_at_post_spawn()`` if the typeclass has one; stamp
     ``last_spawn_time``.
"""
import copy
import inspect
import random
import time
from dataclasses import dataclass

from django.dispatch import Signal

from .conditions import get_condition
from .config import (
    _ENABLED_CONFIG_KEY,
    _FILE_TAG_CATEGORY,
    _MS_AT_POST_SPAWN_ATTR,
    _PACK_TAG_CATEGORY,
    _RULE_TAG_CATEGORY,
    _TICK_SIGNAL_NAME,
    RuleField,
    get_area_tag_category,
    get_tick_seconds,
)
from .log import ms_log
from .validator import _normalise_tag

#: Sent by evennia-calendar's clock once a second, once registered.
spawn_tick = Signal()


@dataclass
class RuleState:
    """One rule as the tick holds it.

    ``pk``, ``file``, ``rule`` and the two timers mirror the row.
    ``last_observed`` and ``spawned_last`` are the death formula's two terms
    and exist only in memory.
    """

    pk: int
    file: str
    rule: dict
    last_spawn_time: float | None = None
    last_death_time: float | None = None
    last_observed: int = 0
    spawned_last: int = 0


#: ``pk -> RuleState``. Replaced wholesale by ``load_rules()``, so a tick
#: reads either the whole old table or the whole new one.
_table = {}

#: ``(file, str(rule_id)) -> RuleState`` — the same states, keyed the way a
#: mob's identity tags name them. Rebuilt alongside ``_table``.
_by_identity = {}

#: Whether the tick runs. Mirrors ``ServerConfig``; the tick reads it every
#: second, so it is served from memory.
_enabled = True


# ---------------------------------------------------------------------------
# The table and the flag
# ---------------------------------------------------------------------------


def load_rules() -> None:
    """Rebuild the in-memory table and the enabled flag from the database.

    Run on the reactor thread, where the tick runs, so the two never overlap.
    A rule that survives the reload keeps its in-memory death-formula terms:
    a kill between the last pass and the reload is still seen as a kill.
    """
    global _table, _by_identity, _enabled
    # The enabled flag persists in Evennia's own key/value store.
    from evennia.server.models import ServerConfig

    from .models import SpawnRule

    previous = _table
    table = {}
    for row in SpawnRule.objects.order_by("pk"):
        state = RuleState(
            pk=row.pk,
            file=row.file,
            rule=row.rule,
            last_spawn_time=row.last_spawn_time,
            last_death_time=row.last_death_time,
        )
        carried = previous.get(row.pk)
        if carried is not None:
            state.last_observed = carried.last_observed
            state.spawned_last = carried.spawned_last
        table[row.pk] = state

    _table = table
    _by_identity = {
        (state.file, str(state.rule[RuleField.RULE_ID.value])): state
        for state in table.values()
    }
    _enabled = bool(ServerConfig.objects.conf(_ENABLED_CONFIG_KEY, default=True))


def rule_states() -> list:
    """Return the in-memory table as a list of ``RuleState``, by primary key."""
    table = _table
    return [table[pk] for pk in sorted(table)]


def spawn_rule_for(mob) -> dict | None:
    """Return a copy of the rule ``mob`` was spawned from, or ``None``.

    Read from the in-memory table by the mob's identity tags — its file and
    ``rule_id`` — so it costs no query against the rule table and sees an
    ``ms_load`` the moment the table is swapped. ``None`` for anything no
    rule produced, and for a mob whose rule has since been removed.

    A deep copy, so a caller changing the result cannot rewrite the rule the
    tick runs.
    """
    files = mob.tags.get(category=_FILE_TAG_CATEGORY, return_list=True)
    rule_ids = mob.tags.get(category=_RULE_TAG_CATEGORY, return_list=True)
    if len(files) != 1 or len(rule_ids) != 1:
        return None

    state = _by_identity.get((files[0], rule_ids[0]))
    return copy.deepcopy(state.rule) if state is not None else None


def is_enabled() -> bool:
    """True if the spawner is enabled."""
    return _enabled


def set_enabled(enabled: bool) -> None:
    """Turn the spawner on or off, in ``ServerConfig`` and in memory."""
    global _enabled
    # The enabled flag persists in Evennia's own key/value store.
    from evennia.server.models import ServerConfig

    ServerConfig.objects.conf(_ENABLED_CONFIG_KEY, value=bool(enabled))
    _enabled = bool(enabled)


# ---------------------------------------------------------------------------
# Boot and the tick
# ---------------------------------------------------------------------------


def _second_key(date) -> int:
    """The calendar key: changes every second, so the signal fires every second."""
    return int(date.elapsed_real_seconds)


def start_mob_spawner() -> None:
    """Load the rules and register the tick. Call from ``at_server_start()``.

    The game starts evennia-calendar's clock itself. Calendar registrations
    are process state, so this runs on every boot and reload; calling it
    again in the same process replaces the registration rather than adding a
    second one.
    """
    from evennia_calendar.service import register_signal, unregister_signal

    load_rules()
    spawn_tick.connect(tick, weak=False, dispatch_uid=_TICK_SIGNAL_NAME)
    unregister_signal(_TICK_SIGNAL_NAME)
    register_signal(spawn_tick, key=_second_key, name=_TICK_SIGNAL_NAME)
    ms_log(
        f"spawner started: {len(_table)} rule(s), "
        f"{'enabled' if _enabled else 'disabled'}"
    )


def tick(sender=None, current=None, **kwargs) -> None:
    """The calendar signal's receiver: run the rules in this second's slot.

    Per-rule errors are caught and logged (decision #14); one bad rule never
    stops the others in its slot.
    """
    if not _enabled:
        return

    slots = get_tick_seconds()
    slot = int(current.elapsed_real_seconds) % slots
    now = time.time()
    for state in rule_states():
        if state.pk % slots != slot:
            continue
        try:
            run_rule(state, now)
        except Exception as e:
            ms_log(
                f"{state.file}: rule_id={state.rule.get(RuleField.RULE_ID.value)} "
                f"pass failed: {e}",
                level="ERROR",
            )


def run_rule(state: RuleState, now: float) -> None:
    """One pass over one rule: observe, detect deaths, gate, place, spawn."""
    rule = state.rule

    # 1-2. Observe and detect deaths. ``current`` is stored without this
    # pass's own spawn; the spawn goes in ``spawned_last`` and the two are
    # summed next pass. Folding it into both would read as a death.
    current = count_living(state)
    deaths = (state.last_observed + state.spawned_last) - current
    state.last_observed = current
    state.spawned_last = 0
    if deaths > 0:
        _write_timer(state, "last_death_time", now)

    # 3-5. The gates.
    if not _cooldown_elapsed(state, now):
        return
    if current >= rule[RuleField.TARGET.value]:
        return
    name = rule.get(RuleField.CONDITION.value)
    if name:
        condition = get_condition(name)
        if condition is None:
            raise LookupError(f"condition {name!r} is not registered")
        if not condition(rule):
            return

    # 6. Room. A missing area is a configuration problem; a full or
    # occupied one is the spawner working as intended.
    room = pick_room(state)
    if room is None:
        if not _area_has_rooms(rule):
            ms_log(
                f"{state.file}: rule_id={rule[RuleField.RULE_ID.value]} skipped — "
                f"no room carries area_tag={rule[RuleField.AREA_TAG.value]!r}",
                level="WARN",
            )
        return

    # 7. Spawn.
    spawn_one(state, room)
    state.spawned_last = 1
    _write_timer(state, "last_spawn_time", now)


def _cooldown_elapsed(state: RuleState, now: float) -> bool:
    """True if the rule's cooldown has run. The validator ensures exactly one."""
    rule = state.rule
    respawn = RuleField.RESPAWN_SECONDS.value
    death = RuleField.DEATH_COOLDOWN_SECONDS.value
    if respawn in rule:
        return (now - (state.last_spawn_time or 0)) >= rule[respawn]
    if death in rule:
        return (now - (state.last_death_time or 0)) >= rule[death]
    return True


def _write_timer(state: RuleState, field: str, value: float) -> None:
    """Write a timer to the row, then to memory."""
    from .models import SpawnRule

    SpawnRule.objects.filter(pk=state.pk).update(**{field: value})
    setattr(state, field, value)


# ---------------------------------------------------------------------------
# Population and room selection
#
# The model rather than the typeclass throughout: the queries count rows by
# tag, and tags live on ObjectDB through Evennia's own many-to-many tables.
# Two chained ``.filter`` calls give two JOINs against the tag table — a single
# ``.filter`` with both tag conditions would look for one row satisfying both.
# ---------------------------------------------------------------------------


def count_living(state: RuleState) -> int:
    """Count the living mobs this rule produced, by its identity tags.

    Mobs with no location (orphaned, mid-delete) are excluded.
    """
    # A census of game objects by tag; tags live on ObjectDB.
    from evennia.objects.models import ObjectDB

    return ObjectDB.objects.filter(
        db_tags__db_key=str(state.rule[RuleField.RULE_ID.value]),
        db_tags__db_category=_RULE_TAG_CATEGORY,
    ).filter(
        db_tags__db_key=state.file,
        db_tags__db_category=_FILE_TAG_CATEGORY,
    ).exclude(db_location__isnull=True).count()


def room_has_space(state: RuleState, room) -> bool:
    """True if ``room`` holds fewer of this rule's mobs than ``max_per_room``.

    Counts only this rule's mobs (decision #9), so two rules sharing a
    typeclass and area cap their populations independently.
    """
    # A census of game objects by tag and location; tags live on ObjectDB.
    from evennia.objects.models import ObjectDB

    count = ObjectDB.objects.filter(
        db_location=room,
        db_tags__db_key=str(state.rule[RuleField.RULE_ID.value]),
        db_tags__db_category=_RULE_TAG_CATEGORY,
    ).filter(
        db_tags__db_key=state.file,
        db_tags__db_category=_FILE_TAG_CATEGORY,
    ).count()
    return count < state.rule[RuleField.MAX_PER_ROOM.value]


def room_is_occupied(room) -> bool:
    """True if anything in ``room`` has a session.

    A player watching an empty room should not see a mob appear in it. A mob
    or a disconnected character has no session, and no one to see it.
    """
    return any(obj.has_account for obj in room.contents)


def _first_eligible(state: RuleState, rooms):
    """The first of ``rooms``, in random order, with space and no session."""
    rooms = list(rooms)
    random.shuffle(rooms)
    for room in rooms:
        if not room_is_occupied(room) and room_has_space(state, room):
            return room
    return None


def pick_room(state: RuleState):
    """Return a room to spawn into, or ``None``.

    A pack rule tries the rooms of its living pack members inside the area
    first, so a new member joins them. Otherwise, or when none of those
    rooms will take it, any room in the area.
    """
    # Pack members and area rooms are found by tag; tags live on ObjectDB.
    from evennia.objects.models import ObjectDB

    rule = state.rule
    area_tag = rule[RuleField.AREA_TAG.value]
    area_category = get_area_tag_category()

    pack = rule.get(RuleField.PACK.value)
    if pack:
        members = ObjectDB.objects.filter(
            db_tags__db_key=pack,
            db_tags__db_category=_PACK_TAG_CATEGORY,
        ).filter(
            db_tags__db_key=area_tag,
            db_tags__db_category=area_category,
        ).exclude(db_location__isnull=True)
        room = _first_eligible(state, {member.location for member in members})
        if room is not None:
            return room

    # Rooms are the objects with no location of their own.
    pool = ObjectDB.objects.filter(
        db_tags__db_key=area_tag,
        db_tags__db_category=area_category,
        db_location__isnull=True,
    )
    return _first_eligible(state, pool)


def _area_has_rooms(rule: dict) -> bool:
    """True if any room carries the rule's ``area_tag``."""
    # Rooms are found by tag; tags live on ObjectDB.
    from evennia.objects.models import ObjectDB

    return ObjectDB.objects.filter(
        db_tags__db_key=rule[RuleField.AREA_TAG.value],
        db_tags__db_category=get_area_tag_category(),
        db_location__isnull=True,
    ).exists()


# ---------------------------------------------------------------------------
# Spawning
# ---------------------------------------------------------------------------


def spawn_one(state: RuleState, room):
    """Create one mob from the rule in ``room`` and return it.

    Sequence:
    - ``create_object`` with the rule's typeclass and key.
    - Stamp the ``area_tag`` (decision #2), the identity tags — the rule's
      ``rule_id`` and its file — and the pack tag for a pack rule.
    - Stamp YAML-declared ``tags``. Each entry is either a bare string
      (untyped) or a mapping with ``key`` + optional ``category``. Reserved
      categories (prefix ``mob_spawner_``) are refused at validation time.
    - Apply the ``desc`` override.
    - Apply ``attrs`` via ``setattr`` — only persists if the typeclass
      declares a matching ``AttributeProperty``; otherwise ``setattr`` still
      runs but a ``WARN`` is logged (``_persists_as_attribute``).
    - Invoke ``mob.ms_at_post_spawn()`` if the typeclass defines it (decision
      #23) — exceptions inside the hook are caught and logged; the mob still
      exists.
    """
    # Creating a typeclassed object is Evennia's to perform — it wires up the
    # typeclass, the database row and the hooks in one call.
    from evennia.utils.create import create_object

    rule = state.rule
    rule_id = rule[RuleField.RULE_ID.value]
    mob = create_object(
        rule[RuleField.TYPECLASS.value], key=rule[RuleField.KEY.value], location=room,
    )

    # Everything from here to the hook is rolled back on failure.
    #
    # create_object() has already committed the mob to the database, so an
    # exception in tagging or attribute application would otherwise leave a
    # real, located mob carrying none of its identity tags. The tick catches
    # per-rule and moves on (decision #14), so nothing else would clean it up
    # — and because population counts are taken by querying those very tags,
    # an untagged mob is invisible to them: never counted, never culled,
    # never replaced. It simply accumulates.
    #
    # delete() rather than a database transaction: at_object_creation() can
    # start tickers and scripts, and a transaction rollback would leave those
    # pointing at a row that no longer exists.
    try:
        mob.tags.add(rule[RuleField.AREA_TAG.value], category=get_area_tag_category())
        # rule_id is a non-negative int; tag keys are strings.
        mob.tags.add(str(rule_id), category=_RULE_TAG_CATEGORY)
        mob.tags.add(state.file, category=_FILE_TAG_CATEGORY)
        pack = rule.get(RuleField.PACK.value)
        if pack:
            mob.tags.add(pack, category=_PACK_TAG_CATEGORY)

        for tag_entry in rule.get(RuleField.TAGS.value) or []:
            key, category = _normalise_tag(tag_entry)
            mob.tags.add(key, category=category)

        # Written through ``.db`` deliberately, and this is the one place the
        # library does not use assignment. Evennia's DefaultObject declares
        # no AttributeProperty for ``desc``: ``get_display_desc`` reads
        # ``self.db.desc``, and Evennia's own ``@desc`` command writes
        # ``obj.db.desc``. So ``mob.desc = ...`` would set a plain Python
        # attribute that dies with the object — the description would
        # silently fail to persist and the mob would show the default
        # instead, with the rule looking like it applied.
        #
        # The linter's ``db_attribute_write`` finding on this line is
        # expected and is not to be "fixed".
        if RuleField.DESC.value in rule:
            mob.db.desc = rule[RuleField.DESC.value]

        # setattr only persists if the typeclass declares attr_name as an
        # AttributeProperty — otherwise it silently sets a plain,
        # non-persisted Python attribute that vanishes with the object. WARN
        # when that's about to happen so a missing declaration is caught
        # immediately rather than silently producing mobs that never carry
        # their configured loot/state.
        for attr_name, attr_val in (rule.get(RuleField.ATTRS.value) or {}).items():
            if not _persists_as_attribute(mob, attr_name):
                ms_log(
                    f"{state.file}: rule_id={rule_id} sets "
                    f"attrs.{attr_name} but {type(mob).__name__} has no "
                    f"matching AttributeProperty — value will not persist",
                    level="WARN",
                )
            setattr(mob, attr_name, attr_val)
    except Exception:
        # Report the rollback separately: a cleanup that itself fails leaves
        # exactly the orphan this block exists to prevent, and that needs to
        # be visible rather than masked by the re-raise.
        try:
            mob.delete()
            ms_log(
                f"{state.file}: rule_id={rule_id} spawn rolled "
                f"back — partially-built mob deleted, no orphan left",
                level="WARN",
            )
        except Exception as cleanup_err:
            ms_log(
                f"{state.file}: rule_id={rule_id} rollback "
                f"failed — untagged mob left in world: {cleanup_err}",
                level="ERROR",
            )
        raise

    hook = getattr(mob, _MS_AT_POST_SPAWN_ATTR, None)
    if callable(hook):
        try:
            hook()
        except Exception as e:
            ms_log(
                f"{type(mob).__name__}.{_MS_AT_POST_SPAWN_ATTR}() "
                f"raised on rule_id={rule_id}: {e}",
                level="ERROR",
            )

    return mob


def _persists_as_attribute(obj, attr_name):
    """Return True if ``setattr(obj, attr_name, ...)`` will persist.

    ``setattr()`` only survives past the current Python object if
    ``attr_name`` is backed by an ``AttributeProperty`` descriptor
    somewhere in the typeclass's MRO — otherwise it silently sets a
    plain instance attribute that vanishes with the object.

    Uses ``inspect.getattr_static`` rather than plain ``getattr`` —
    triggering the descriptor's own ``__get__`` at the class level
    (``instance=None``) raises internally rather than returning the
    descriptor, so a normal ``getattr(cls, name, None)`` would
    silently (and incorrectly) report "not declared" via its default.
    ``getattr_static`` inspects the MRO directly without invoking the
    descriptor protocol at all.
    """
    # The descriptor whose presence decides whether a setattr persists.
    from evennia.typeclasses.attributes import AttributeProperty

    descriptor = inspect.getattr_static(obj, attr_name, None)
    return isinstance(descriptor, AttributeProperty)
