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
      1. Observe — count living mobs produced by this rule, keyed on
         the (file, rule_id) identity tags stamped at spawn time.
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
      6. Spawn — `create_object` + re-tag with area_tag + identity tags
         (rule_id, file) + any YAML-declared `tags` + apply `desc` /
         `attrs` overrides + invoke `mob.ms_at_post_spawn()` if present.
      7. Save state — update `last_observed_count`, `spawned_last_tick`,
         `last_spawn_time` (if spawned).
"""
import inspect
import random
import time

from django.conf import settings
from evennia.typeclasses.attributes import AttributeProperty
from evennia.utils.utils import class_from_module

from .config import get_area_tag_category, get_tick_seconds
from .log import ms_log
from .validator import _normalise_tag


# Resolve the base script class at module-import time. Falls back to
# evennia.DefaultScript if BASE_SCRIPT_TYPECLASS is missing or unset
# (defensive — the setting is typically present in any standard
# Evennia configuration).
_BASE_SCRIPT = class_from_module(
    getattr(settings, "BASE_SCRIPT_TYPECLASS", "evennia.DefaultScript")
)


_MS_AT_POST_SPAWN_ATTR = "ms_at_post_spawn"

# Tag categories stamped on every spawned mob. Together with ``area_tag``
# they form the queryable identity breadcrumb on each mob: which rule
# produced it, and which rule-set file that rule lives in.
_RULE_TAG_CATEGORY = "mob_spawner_rule"
_FILE_TAG_CATEGORY = "mob_spawner_file"


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
    descriptor = inspect.getattr_static(obj, attr_name, None)
    return isinstance(descriptor, AttributeProperty)


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

        # Race-protocol cooperation (decision #13): if a graceful stop
        # has been requested between ticks (e.g. by the Deployer about
        # to swap our rule table), don't start a new pass at all. The
        # ndb flag is cleared by ``stop_when_safe`` after the
        # ``_tick_in_progress`` signal is observed False, OR by the
        # Deployer once the swap is complete.
        if self.ndb._stop_requested:
            return

        # In-flight signal for ``stop_when_safe``: True while we're
        # iterating the rule list, False once we return.
        self.ndb._tick_in_progress = True
        try:
            now = time.time()
            prior_observed = dict(self.db.last_observed_counts or {})
            prior_spawned = dict(self.db.spawned_last_tick or {})
            last_spawn_times = dict(self.db.last_spawn_times or {})
            last_death_times = dict(self.db.last_death_times or {})

            new_observed: dict[int, int] = {}
            new_spawned: dict[int, int] = {}

            stopped_early = False
            for rule in spawn_table:
                # Check the stop flag at the safe point between rules
                # (decision #13: "checks a stop flag at safe points
                # within a tick"). Mid-rule interruption isn't safe —
                # half-applied state, partial spawn, etc. — but
                # between-rule exits leave a clean partial state.
                if self.ndb._stop_requested:
                    stopped_early = True
                    break

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

            if stopped_early:
                ms_log(
                    f"{self.db_key}: tick exited mid-loop on stop request"
                )

            # Partial state is fine: rules we processed got their
            # observation / cooldown / spawn updates; rules we didn't
            # are simply absent from new_observed / new_spawned (their
            # old values aren't carried forward, which means they'll be
            # treated as "first tick" on next pass — that's harmless
            # under the death-detection formula since deaths come out
            # non-positive when the prior count is missing).
            self.db.last_observed_counts = new_observed
            self.db.spawned_last_tick = new_spawned
            self.db.last_spawn_times = last_spawn_times
            self.db.last_death_times = last_death_times
        finally:
            self.ndb._tick_in_progress = False

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

    def _count_living(self, rule: dict) -> int:
        """Count living mobs produced by this rule.

        Population identity is keyed on (file, rule_id) — the two identity
        tags stamped on every spawn by ``_spawn_one``. Discriminating on
        rule_id rather than typeclass means two rules sharing the same
        typeclass (e.g. loot variants of the same mob) are counted as
        independent populations.

        Two chained ``.filter`` calls produce two separate JOINs against
        the tag table — needed because a single ``.filter`` with both
        tag conditions would look for one row satisfying both, which the
        many-to-many tag table cannot provide.

        Mobs with ``db_location is None`` (orphan, mid-delete) are excluded.
        """
        from evennia.objects.models import ObjectDB

        return ObjectDB.objects.filter(
            db_tags__db_key=str(rule["rule_id"]),
            db_tags__db_category=_RULE_TAG_CATEGORY,
        ).filter(
            db_tags__db_key=self.db_key,
            db_tags__db_category=_FILE_TAG_CATEGORY,
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

    def _room_has_space(self, room, rule: dict) -> bool:
        """True if ``room`` can hold one more mob from this rule.

        Counts mobs produced by THIS rule in this room — keyed on the
        (file, rule_id) identity tags stamped at spawn (decision #9),
        not on typeclass. Two rules sharing typeclass + area_tag enforce
        their ``max_per_room`` caps independently.

        Two chained ``.filter`` calls produce two separate JOINs against
        the tag table — needed because a single ``.filter`` with both
        tag conditions would look for one row satisfying both, which the
        many-to-many tag table cannot provide.
        """
        from evennia.objects.models import ObjectDB

        mob_count = ObjectDB.objects.filter(
            db_location=room,
            db_tags__db_key=str(rule["rule_id"]),
            db_tags__db_category=_RULE_TAG_CATEGORY,
        ).filter(
            db_tags__db_key=self.db_key,
            db_tags__db_category=_FILE_TAG_CATEGORY,
        ).count()
        return mob_count < rule["max_per_room"]

    def _spawn_one(self, rule: dict, room):
        """Create one mob from ``rule`` in ``room``.

        Sequence:
        - ``create_object`` with the rule's typeclass and key.
        - Stamp identity tags: ``area_tag`` (decision #2), the rule's
          ``rule_id``, and the source file path. Together these are the
          mob's breadcrumb back to the rule that produced it.
        - Stamp YAML-declared ``tags`` if the rule provides them. Each
          entry is either a bare string (untyped) or a mapping with
          ``key`` + optional ``category``. Reserved categories (prefix
          ``mob_spawner_``) are refused at validation time.
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
        mob.tags.add(rule["area_tag"], category=get_area_tag_category())
        # rule_id is a non-negative int; tag keys are strings.
        # self.db_key is the script's persistent key, set by the Deployer
        # to the rule-set file's repo path.
        mob.tags.add(str(rule["rule_id"]), category=_RULE_TAG_CATEGORY)
        mob.tags.add(self.db_key, category=_FILE_TAG_CATEGORY)

        # YAML-declared tags (optional). Shape pre-validated by
        # ``_check_tags_field_shape``; ``_normalise_tag`` collapses
        # string vs dict entries to a uniform ``(key, category)`` pair.
        for tag_entry in rule.get("tags") or []:
            key, category = _normalise_tag(tag_entry)
            mob.tags.add(key, category=category)

        # Apply rule-level description override.
        if "desc" in rule:
            mob.db.desc = rule["desc"]

        # Apply rule-level attribute overrides. setattr only persists if
        # the typeclass declares attr_name as an AttributeProperty —
        # otherwise it silently sets a plain, non-persisted Python
        # attribute that vanishes with the object (consumer pattern;
        # see src/game CLAUDE.md note on AttributeProperty access).
        # WARN when that's about to happen so a missing declaration is
        # caught immediately rather than silently producing mobs that
        # never carry their configured loot/state.
        for attr_name, attr_val in (rule.get("attrs") or {}).items():
            if not _persists_as_attribute(mob, attr_name):
                ms_log(
                    f"{self.db_key}: rule_id={rule['rule_id']} sets "
                    f"attrs.{attr_name} but {type(mob).__name__} has no "
                    f"matching AttributeProperty — value will not persist",
                    level="WARN",
                )
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

    # ------------------------------------------------------------------
    # Race protocol primitives (decision #13)
    # ------------------------------------------------------------------

    def stop_when_safe(self, timeout: float = 60.0) -> bool:
        """Request a graceful stop. Wait up to ``timeout`` seconds for drain.

        The ``Deployer`` calls this before swapping ``db.spawn_table``
        so an in-flight tick doesn't see a half-mutated rule list. Sets
        ``ndb._stop_requested`` which the tick loop checks at the safe
        point between rules. Polls ``ndb._tick_in_progress`` until the
        tick exits, then pauses the script so no NEW ticks fire.

        Returns:
            True  — clean stop: any in-flight tick has drained and the
                    script is now paused.
            False — timeout: the in-flight tick did not acknowledge
                    within ``timeout`` seconds. Caller should fall
                    back to ``force_stop``.

        No-op semantics when the script isn't actively ticking:
        already-paused / already-stopped / never-started scripts
        return True immediately without setting any flags.
        """
        # If the script isn't ticking, nothing to drain. Don't touch
        # the stop flag (so a paused script's next un-pause resumes
        # cleanly).
        if not self.is_active or self.db._paused_time:
            return True

        self.ndb._stop_requested = True

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.ndb._tick_in_progress:
                # Either no tick was running, or the in-flight tick
                # observed the flag and exited. Pause now to prevent
                # any further ticks from firing during the swap.
                if not self.db._paused_time:
                    self.pause()
                # Clear the flag — by the time the script next ticks
                # (after the Deployer unpauses), the swap is complete
                # and we want normal operation.
                self.ndb._stop_requested = False
                return True
            time.sleep(0.1)

        # Drain timed out. Caller should force_stop. Don't clear the
        # flag — force_stop relies on it being set so any tick that's
        # still running will exit at its next between-rules check.
        return False

    def force_stop(self) -> None:
        """Hard-stop: pause regardless of in-flight tick state.

        Internal-only — not exposed as an operator command (decision
        #13). Called by the ``Deployer`` when ``stop_when_safe`` times
        out, on the theory that proceeding with the swap is preferable
        to indefinitely blocking the Deployer.

        Note that CPython has no clean way to interrupt a running
        Python function from another thread. ``force_stop`` cannot
        actually halt a wedged tick — it sets the stop flag (so the
        tick will exit at its next between-rules check, IF it gets
        there) and pauses the script (so NEW ticks don't fire). A
        truly stuck tick (e.g. blocked on a DB query) will run to
        whatever conclusion it reaches; the swap proceeds in parallel,
        and the next scheduled tick (after the Deployer un-pauses)
        sees the swapped state.
        """
        self.ndb._stop_requested = True
        if self.is_active and not self.db._paused_time:
            self.pause()
