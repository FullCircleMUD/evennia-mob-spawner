# SPDX-License-Identifier: BSD-3-Clause
"""MobSpawnerScript — one persistent Evennia script per rule-set YAML file.

Architecture:
    Each rule-set YAML file deployed via ``ms_load`` gets exactly one
    persistent ``MobSpawnerScript`` (decision #4). The script owns the
    rule table for its file, the bookkeeping dicts for cooldown / death
    observation, and the tick loop that maintains population against
    those rules.

Inheritance:
    Subclasses whichever class ``settings.BASE_SCRIPT_TYPECLASS`` points
    at (decision #25). Falls back to ``evennia.DefaultScript`` if the
    setting is missing. This lets consumer customisations on their base
    script (logging, telemetry, dashboard registration, etc.) compose
    into the library's scripts automatically — without forcing the
    consumer to choose between library benefits and their own
    customisation.

    Consumer base-class contract: must behave like a ``DefaultScript``
    subclass. If you override ``at_repeat`` in your base, always call
    ``super().at_repeat()`` so the library's tick loop runs.

Persistent state (architecture.md "The tick loop"):
    - ``db.spawn_table``           — list of rule dicts (the YAML rules).
    - ``db.last_spawn_times``      — ``{rule_id: timestamp_of_last_spawn}``.
    - ``db.last_death_times``      — ``{rule_id: timestamp_of_last_observed_death}``.
    - ``db.last_observed_counts``  — ``{rule_id: count_at_end_of_last_tick}``.
    - ``db.spawned_last_tick``     — ``{rule_id: count_spawned_in_prior_tick}``.

The tick loop:
    For each rule, on each tick:
      1. Observe — count living mobs matching typeclass + area_tag.
      2. Detect deaths — `deaths = (last_observed + spawned_last_tick) - current`
         (decision #5: observation, not callback). Stamp `last_death_time`
         if positive.
      3. Cooldown check — gate against `last_spawn_time` (respawn_seconds)
         or `last_death_time` (death_cooldown_seconds). Validator enforces
         exactly one of the pair is declared (decision #21).
      4. Population check — skip if at `target`.
      5. Room selection — three-tier fallback (decision #22):
            pack (`spawn_with_typeclass`) → den (`den_room_tag`) → random.
         All respect `max_per_room`.
      6. Spawn — `create_object` + re-tag with area_tag + apply `desc` /
         `attrs` overrides + invoke `mob.ms_at_post_spawn()` if present.
      7. Save state — update `last_observed_count`, `spawned_last_tick`,
         `last_spawn_time` (if spawned).
"""
import random
import time

from django.conf import settings
from evennia.utils.utils import class_from_module

from .config import get_area_tag_category, get_tick_seconds
from .log import ms_log


# Resolve the base script class at module-import time. Falls back to
# evennia.DefaultScript if BASE_SCRIPT_TYPECLASS is missing or unset
# (defensive — the setting is typically present in any standard
# Evennia configuration).
_BASE_SCRIPT = class_from_module(
    getattr(settings, "BASE_SCRIPT_TYPECLASS", "evennia.DefaultScript")
)


_MS_AT_POST_SPAWN_ATTR = "ms_at_post_spawn"


class MobSpawnerScript(_BASE_SCRIPT):
    """One persistent script per rule-set YAML file.

    See module docstring for inheritance rationale (decision #25),
    persistent state shape, and the tick-loop algorithm.
    """

    def at_script_creation(self):
        """Initialise persistent state on the script's first creation.

        Called once per script lifetime, when ``create_script`` first
        persists the script to the database. Subsequent server restarts
        skip this hook — the persistent state is loaded from the DB.

        Always calls ``super()`` so any consumer customisation on
        ``BASE_SCRIPT_TYPECLASS.at_script_creation`` runs (e.g.
        registering the script with a monitoring system, applying
        org-wide tag conventions).
        """
        super().at_script_creation()

        # Tick interval (decision #3) — library-level setting,
        # overridable via MOB_SPAWNER_TICK_SECONDS. Stored on the
        # script at creation; changing the setting later doesn't
        # affect existing scripts.
        self.interval = get_tick_seconds()
        self.persistent = True
        self.start_delay = True  # Don't fire at_repeat immediately on creation.

        # Bookkeeping initialised empty. The Deployer populates
        # ``spawn_table`` after creation; ``last_*`` dicts accumulate
        # state across ticks.
        self.db.spawn_table = []
        self.db.last_spawn_times = {}
        self.db.last_death_times = {}
        self.db.last_observed_counts = {}
        self.db.spawned_last_tick = {}

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    def at_repeat(self):
        """Tick — run one pass over every rule in ``db.spawn_table``.

        Algorithm per architecture.md "The tick loop". State is read
        from / written to the script's ``db`` namespace.

        Per-rule errors are caught and logged (decision #14); one bad
        rule never takes down the whole tick.

        ``super().at_repeat()`` is always invoked so consumer base-class
        customisations (logging, telemetry, …) compose.
        """
        super().at_repeat()

        spawn_table = self.db.spawn_table or []
        if not spawn_table:
            return

        now = time.time()
        prior_observed = dict(self.db.last_observed_counts or {})
        prior_spawned = dict(self.db.spawned_last_tick or {})
        last_spawn_times = dict(self.db.last_spawn_times or {})
        last_death_times = dict(self.db.last_death_times or {})

        new_observed: dict[int, int] = {}
        new_spawned: dict[int, int] = {}

        for rule in spawn_table:
            try:
                self._tick_one_rule(
                    rule, now, prior_observed, prior_spawned,
                    last_spawn_times, last_death_times,
                    new_observed, new_spawned,
                )
            except Exception as e:
                # Decision #14: one bad rule doesn't take down the tick.
                ms_log(
                    f"tick error for rule_id={rule.get('rule_id')!r} "
                    f"in {self.db_key!r}: {e}",
                    level="ERROR",
                )

        self.db.last_observed_counts = new_observed
        self.db.spawned_last_tick = new_spawned
        self.db.last_spawn_times = last_spawn_times
        self.db.last_death_times = last_death_times

    def _tick_one_rule(
        self,
        rule: dict,
        now: float,
        prior_observed: dict,
        prior_spawned: dict,
        last_spawn_times: dict,
        last_death_times: dict,
        new_observed: dict,
        new_spawned: dict,
    ) -> None:
        """Apply all 7 tick steps to a single rule.

        Mutates the four state dicts in place (``last_spawn_times``,
        ``last_death_times``, ``new_observed``, ``new_spawned``).
        Pure-read on ``prior_observed`` / ``prior_spawned``.
        """
        rule_id = rule["rule_id"]

        # 1. Observe
        current = self._count_living(rule)

        # 2. Detect deaths via count delta
        # (decision #5: observation, not callback)
        prior = prior_observed.get(rule_id, 0)
        produced = prior_spawned.get(rule_id, 0)
        deaths = (prior + produced) - current
        if deaths > 0:
            last_death_times[rule_id] = now

        # 3. Cooldown gate (exclusivity enforced by Validator,
        #    decision #21 + cooldown-exclusivity predicate)
        if not self._cooldown_elapsed(rule, rule_id, now,
                                       last_spawn_times, last_death_times):
            new_observed[rule_id] = current
            new_spawned[rule_id] = 0
            return

        # 4. Population gate
        target = rule["target"]
        if current >= target:
            new_observed[rule_id] = current
            new_spawned[rule_id] = 0
            return

        # 5. Room selection (3-tier fallback, decision #22)
        room = self._pick_room(rule)
        if room is None:
            # Tier 4 diagnostic at deploy time would have WARNed about
            # 0 tagged rooms; tick-time also surfaces the skip per
            # decision #15.
            ms_log(
                f"{self.db_key}: rule_id={rule_id} skipped — "
                f"no eligible room (area_tag={rule['area_tag']!r})",
                level="WARN",
            )
            new_observed[rule_id] = current
            new_spawned[rule_id] = 0
            return

        # 6. Spawn (decision #14: catch errors per-rule)
        try:
            self._spawn_one(rule, room)
            spawned_count = 1
        except Exception as e:
            ms_log(
                f"{self.db_key}: rule_id={rule_id} spawn failed: {e}",
                level="ERROR",
            )
            spawned_count = 0

        # 7. Save state
        new_observed[rule_id] = current + spawned_count
        new_spawned[rule_id] = spawned_count
        if spawned_count:
            last_spawn_times[rule_id] = now

    # ------------------------------------------------------------------
    # Tick helpers — observation, cooldown, room selection, spawn
    # ------------------------------------------------------------------

    @staticmethod
    def _count_living(rule: dict) -> int:
        """Count living mobs matching ``typeclass`` + ``area_tag``.

        Exact typeclass match per decision #9 — enables the
        indistinguishable-variant pattern. Mobs with ``db_location is
        None`` (orphan, mid-delete) are excluded.
        """
        from evennia.objects.models import ObjectDB

        return ObjectDB.objects.filter(
            db_typeclass_path=rule["typeclass"],
            db_tags__db_key=rule["area_tag"],
            db_tags__db_category=get_area_tag_category(),
        ).exclude(db_location__isnull=True).count()

    @staticmethod
    def _cooldown_elapsed(
        rule: dict, rule_id: int, now: float,
        last_spawn_times: dict, last_death_times: dict,
    ) -> bool:
        """True if the rule's cooldown has elapsed.

        Validator enforces exactly one of the two fields is present
        (cooldown_exclusivity predicate); we trust that here. The
        fallback `True` covers the defensive case where the validator
        was bypassed somehow.
        """
        if "respawn_seconds" in rule:
            last_spawn = last_spawn_times.get(rule_id, 0)
            return (now - last_spawn) >= rule["respawn_seconds"]
        if "death_cooldown_seconds" in rule:
            last_death = last_death_times.get(rule_id, 0)
            return (now - last_death) >= rule["death_cooldown_seconds"]
        return True

    def _pick_room(self, rule: dict):
        """Pick an eligible room for ``rule``.

        Three-tier fallback (decision #22):
        1. Pack: ``spawn_with_typeclass`` — find a living leader in the
           area, use the room it's in.
        2. Den: ``den_room_tag`` — the single room tagged with the den key.
        3. Random: any room in the area, uniformly chosen.

        Each step is skipped if its prerequisite is missing or the
        candidate room is full (``max_per_room``). Returns the first
        eligible room, or None if no room has space.
        """
        from evennia.objects.models import ObjectDB

        area_category = get_area_tag_category()

        # Step 1: pack-spawn alongside a living leader.
        leader_typeclass = rule.get("spawn_with_typeclass")
        if leader_typeclass:
            leader = ObjectDB.objects.filter(
                db_typeclass_path=leader_typeclass,
                db_tags__db_key=rule["area_tag"],
                db_tags__db_category=area_category,
            ).exclude(db_location__isnull=True).first()
            if leader and leader.location and self._room_has_space(leader.location, rule):
                return leader.location

        # Step 2: designated den room (rooms are objects with no parent
        # location — top of the location hierarchy).
        den_tag = rule.get("den_room_tag")
        if den_tag:
            den_room = ObjectDB.objects.filter(
                db_tags__db_key=den_tag,
                db_tags__db_category=area_category,
                db_location__isnull=True,
            ).first()
            if den_room and self._room_has_space(den_room, rule):
                return den_room

        # Step 3: random within the area pool.
        rooms = list(
            ObjectDB.objects.filter(
                db_tags__db_key=rule["area_tag"],
                db_tags__db_category=area_category,
                db_location__isnull=True,
            )
        )
        if not rooms:
            return None

        random.shuffle(rooms)
        for room in rooms:
            if self._room_has_space(room, rule):
                return room
        return None

    @staticmethod
    def _room_has_space(room, rule: dict) -> bool:
        """True if ``room`` can hold one more mob of this rule's typeclass.

        Counts existing mobs of the rule's exact typeclass in this room
        with the rule's area_tag, and compares against ``max_per_room``
        (validator-enforced positive integer).
        """
        from evennia.objects.models import ObjectDB

        mob_count = ObjectDB.objects.filter(
            db_typeclass_path=rule["typeclass"],
            db_location=room,
            db_tags__db_key=rule["area_tag"],
            db_tags__db_category=get_area_tag_category(),
        ).count()
        return mob_count < rule["max_per_room"]

    @staticmethod
    def _spawn_one(rule: dict, room):
        """Create one mob from ``rule`` in ``room``.

        Sequence:
        - ``create_object`` with the rule's typeclass and key.
        - Re-tag with the rule's ``area_tag`` (decision #2).
        - Apply ``desc`` override if present.
        - Apply ``attrs`` overrides via ``setattr`` (compatible with
          Evennia's ``AttributeProperty`` descriptors).
        - Invoke ``mob.ms_at_post_spawn()`` if the typeclass defines it
          (decision #23) — exceptions inside the hook are caught and
          logged; the mob still exists.
        """
        from evennia.utils.create import create_object

        mob = create_object(
            rule["typeclass"],
            key=rule["key"],
            location=room,
        )
        # Re-tag with area_tag under the configured category.
        mob.tags.add(rule["area_tag"], category=get_area_tag_category())

        # Apply rule-level description override.
        if "desc" in rule:
            mob.db.desc = rule["desc"]

        # Apply rule-level attribute overrides. setattr works with
        # AttributeProperty descriptors on modern Evennia typeclasses
        # (consumer pattern; see src/game CLAUDE.md note on
        # AttributeProperty access).
        for attr_name, attr_val in (rule.get("attrs") or {}).items():
            setattr(mob, attr_name, attr_val)

        # Optional ms_at_post_spawn() hook on the typeclass (decision #23).
        hook = getattr(mob, _MS_AT_POST_SPAWN_ATTR, None)
        if callable(hook):
            try:
                hook()
            except Exception as e:
                ms_log(
                    f"{type(mob).__name__}.{_MS_AT_POST_SPAWN_ATTR}() "
                    f"raised on rule_id={rule['rule_id']}: {e}",
                    level="ERROR",
                )

        return mob
