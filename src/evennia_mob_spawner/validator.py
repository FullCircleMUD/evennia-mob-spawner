# SPDX-License-Identifier: BSD-3-Clause
"""Validator — checks a LoadResult before the Deployer is invoked.

Mirrors evennia-world-builder's predicate-tiered structure:

- **Tier 1 — stateless per-rule predicates** (``PER_RULE_PREDICATES``):
  pure ``(LoadedRule) -> finding | None``. Always run.
- **Tier 2 — stateful per-file checks**: methods that read / update
  ``self.seen_ids`` during the pass. Always run, but only on rules
  that passed Tier 1 (so garbage data doesn't pollute the state).
- **Tier 3 — Evennia-runtime predicates** (``EVENNIA_ONLY_PREDICATES``):
  stateless predicates that need the consumer's typeclasses + hook
  module paths to be importable. Run only when the caller passes
  ``evennia_runtime=True``. ``ms_load`` passes True; ``ms-validate``
  (CLI) leaves it False.
- **Tier 4 — diagnostic checks** (``EVENNIA_DIAGNOSTICS``):
  ``(LoadedRule) -> None`` functions with side effects (typically
  ``ms_log`` calls). Distinct contract from predicates: they NEVER
  refuse deployment, they only emit deploy-time warnings. Runs only
  with ``evennia_runtime=True``, only on rules that passed Tier 1.
  Naming convention ``_diagnostic_*`` (vs. predicates' ``_check_*``)
  marks the difference at every call site. See decision #24.

Plus a **file-level shape pass** (``_check_file_metadata_shape``) that
runs once per file, agnostic of the per-rule loop.

All findings flow through ``_record_finding`` and accumulate; the pass
completes before any error is raised. Per CLAUDE.md principle 4:
gather every finding, then refuse — never partial, never halt on the
first error. Operators see the complete list in one run.

Adding a stateless check: write a predicate function and append it to
``PER_RULE_PREDICATES`` (Tier 1) or ``EVENNIA_ONLY_PREDICATES`` (Tier 3).
Adding a stateful check: write a method on Validator and call it from
the per-rule loop.

Predicate tuples are deliberately empty at this stage — structure
lands first; concrete predicates land in subsequent passes.
"""
import importlib
import inspect
from dataclasses import dataclass

from .definitions import Definitions
from .errors import ValidatorError
from .loader import LoadResult
from .log import ms_log


_MS_AT_POST_SPAWN_ATTR = "ms_at_post_spawn"


@dataclass(frozen=True)
class LoadedRule:
    """A single rule paired with the file path it came from.

    The path is carried so predicate findings can name the source file
    for the operator. Predicates do not need (and should not depend on)
    the rule's index within the file; the rule's own ``rule_id`` is the
    author-facing handle.

    Attributes:
        path: Repo-relative path of the YAML file the rule was loaded from.
        rule: The raw rule mapping as parsed from YAML.
    """

    path: str
    rule: dict


# ---------------------------------------------------------------------------
# Tier 1 predicates — pure (LoadedRule) -> str | None.
#
# Each predicate does its own defer-cleanly guard on the rule's shape so
# predicates are independent of one another and the dispatch order in
# PER_RULE_PREDICATES doesn't matter. A non-mapping rule is caught
# explicitly by _check_rule_is_mapping (one clean finding); the other
# predicates short-circuit to None on non-mappings so they don't
# cascade redundant findings.
# ---------------------------------------------------------------------------


def _rule_or_none(loaded: LoadedRule) -> dict | None:
    """Return the rule mapping if it is a dict, else None.

    All field predicates use this to skip cleanly on non-mapping rules
    (which _check_rule_is_mapping catches and reports separately). The
    one cascade-style finding avoids drowning the operator in
    "missing required field" lines for a rule that isn't even a dict.
    """
    return loaded.rule if isinstance(loaded.rule, dict) else None


def _check_rule_is_mapping(loaded: LoadedRule) -> str | None:
    """Every rule must be a mapping at the top level.

    Catches `rules: [-> "string", 7, null, ...]` shapes that the Loader
    accepts (the list-of-rules check is structural; individual rule
    shape is the Validator's concern).
    """
    if isinstance(loaded.rule, dict):
        return None
    return (
        f"{loaded.path}: rule entries must be mappings, "
        f"got {type(loaded.rule).__name__}"
    )


def _check_rule_id_well_formed(loaded: LoadedRule) -> str | None:
    """Required: ``rule_id`` is a non-negative integer.

    ``rule_id`` is the bookkeeping handle the Script's persistent state
    keys on. Stable across YAML reordering; changing it is the explicit
    signal that this is a different rule (architecture.md decision #10).
    """
    rule = _rule_or_none(loaded)
    if rule is None:
        return None

    if "rule_id" not in rule:
        return f"{loaded.path}: missing required field 'rule_id'"

    value = rule["rule_id"]
    # bool is a subclass of int in Python — exclude explicitly so that
    # `rule_id: true` doesn't slip through.
    if not isinstance(value, int) or isinstance(value, bool):
        return (
            f"{loaded.path}: 'rule_id' must be an integer, "
            f"got {type(value).__name__}"
        )
    if value < 0:
        return f"{loaded.path}: 'rule_id' must be non-negative, got {value}"
    return None


def _check_typeclass_well_formed(loaded: LoadedRule) -> str | None:
    """Required: ``typeclass`` is a non-empty string.

    Dotted-path validity / importability is Tier 3's concern; Tier 1
    only enforces the structural shape. Matches world-builder's split
    between shape (always run) and resolvability (engine-only).
    """
    rule = _rule_or_none(loaded)
    if rule is None:
        return None

    if "typeclass" not in rule:
        return f"{loaded.path}: missing required field 'typeclass'"

    value = rule["typeclass"]
    if not isinstance(value, str):
        return (
            f"{loaded.path}: 'typeclass' must be a string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        return f"{loaded.path}: 'typeclass' must be a non-empty string"
    return None


def _check_key_well_formed(loaded: LoadedRule) -> str | None:
    """Required: ``key`` is a non-empty string.

    The spawned mob's display name. Same ``key`` across rules is
    permitted — the "indistinguishable variant" pattern (architecture
    decision #9), where multiple rules produce mobs that look identical
    to the player but differ in loot, mechanics, or population targets.
    Population counting discriminates on (file, rule_id) via identity
    tags stamped at spawn, so rules can also share ``typeclass`` and
    ``area_tag`` without colliding. Uniqueness of ``key`` is not
    enforced.
    """
    rule = _rule_or_none(loaded)
    if rule is None:
        return None

    if "key" not in rule:
        return f"{loaded.path}: missing required field 'key'"

    value = rule["key"]
    if not isinstance(value, str):
        return (
            f"{loaded.path}: 'key' must be a string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        return f"{loaded.path}: 'key' must be a non-empty string"
    return None


def _check_area_tag_well_formed(loaded: LoadedRule) -> str | None:
    """Required: ``area_tag`` is a non-empty string.

    Used as the tag key under the configured ``mob_area``-equivalent
    category (architecture.md decision #1). Empty-tag queries against
    Evennia's tag table return everything, so empty is refused.
    """
    rule = _rule_or_none(loaded)
    if rule is None:
        return None

    if "area_tag" not in rule:
        return f"{loaded.path}: missing required field 'area_tag'"

    value = rule["area_tag"]
    if not isinstance(value, str):
        return (
            f"{loaded.path}: 'area_tag' must be a string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        return f"{loaded.path}: 'area_tag' must be a non-empty string"
    return None


def _check_target_well_formed(loaded: LoadedRule) -> str | None:
    """Required: ``target`` is a positive integer (>= 1).

    Population cap. ``target: 0`` is functionally equivalent to deleting
    the rule (never spawn); refused so authors don't accidentally
    silence a rule by typo.
    """
    rule = _rule_or_none(loaded)
    if rule is None:
        return None

    if "target" not in rule:
        return f"{loaded.path}: missing required field 'target'"

    value = rule["target"]
    if not isinstance(value, int) or isinstance(value, bool):
        return (
            f"{loaded.path}: 'target' must be an integer, "
            f"got {type(value).__name__}"
        )
    if value < 1:
        return f"{loaded.path}: 'target' must be at least 1, got {value}"
    return None


def _check_respawn_seconds_well_formed(loaded: LoadedRule) -> str | None:
    """Optional shape: ``respawn_seconds`` is a non-negative number.

    Cooldown counted from ``last_spawn_time``. 0 is allowed and means
    "no cooldown" (spawn every tick if under target). Whether
    ``respawn_seconds`` is permitted at all given the
    ``respawn_seconds`` xor ``death_cooldown_seconds`` rule is the
    exclusivity check's concern — separate predicate in a later pass.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "respawn_seconds" not in rule:
        return None

    value = rule["respawn_seconds"]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return (
            f"{loaded.path}: 'respawn_seconds' must be a number, "
            f"got {type(value).__name__}"
        )
    if value < 0:
        return f"{loaded.path}: 'respawn_seconds' must be non-negative, got {value}"
    return None


def _check_death_cooldown_seconds_well_formed(loaded: LoadedRule) -> str | None:
    """Optional shape: ``death_cooldown_seconds`` is a non-negative number.

    Cooldown counted from ``last_death_time`` (set by observation-based
    death detection). 0 is allowed for "no cooldown" semantics.
    Exclusivity with ``respawn_seconds`` is a separate predicate.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "death_cooldown_seconds" not in rule:
        return None

    value = rule["death_cooldown_seconds"]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return (
            f"{loaded.path}: 'death_cooldown_seconds' must be a number, "
            f"got {type(value).__name__}"
        )
    if value < 0:
        return (
            f"{loaded.path}: 'death_cooldown_seconds' must be non-negative, "
            f"got {value}"
        )
    return None


def _check_cooldown_exclusivity(loaded: LoadedRule) -> str | None:
    """Required: exactly one of ``respawn_seconds`` / ``death_cooldown_seconds``.

    Architecture decision #12 mandates a cooldown model per rule; the
    two fields are semantically distinct (clock starts at spawn time
    vs. clock starts at death time) and not interchangeable — see the
    "Population maintenance" section. Empirically, every one of the
    87 rules in the consumer baseline declares exactly one. Neither
    declared → the spawn loop has no cooldown gate (spawn every tick
    until at target), almost always a YAML typo. Both declared →
    ambiguous; no sensible AND / OR semantic exists.

    Presence is checked at the dict-key level, not value validity —
    ``respawn_seconds: null`` is "present" for exclusivity purposes;
    bad values are the per-field shape predicate's concern.
    """
    rule = _rule_or_none(loaded)
    if rule is None:
        return None

    has_respawn = "respawn_seconds" in rule
    has_death = "death_cooldown_seconds" in rule

    if has_respawn and has_death:
        return (
            f"{loaded.path}: 'respawn_seconds' and 'death_cooldown_seconds' "
            f"are mutually exclusive — declare exactly one"
        )
    if not has_respawn and not has_death:
        return (
            f"{loaded.path}: must declare exactly one of "
            f"'respawn_seconds' or 'death_cooldown_seconds'"
        )
    return None


def _check_max_per_room_well_formed(loaded: LoadedRule) -> str | None:
    """Required: ``max_per_room`` is a positive integer (>= 1).

    Per-room cap respected by all three room-selection patterns
    (architecture.md "Room-selection"). 0 would mean "never spawn into
    any room", which is rule-deletion by another name; refused. Absent
    is also refused — the consumer baseline sets this field on every
    one of its 87 production rules, and a missing value would mean
    either "unlimited per room" (no atmospheric ceiling) or "0 per
    room" (silenced rule), neither a good default.
    """
    rule = _rule_or_none(loaded)
    if rule is None:
        return None

    if "max_per_room" not in rule:
        return f"{loaded.path}: missing required field 'max_per_room'"

    value = rule["max_per_room"]
    if not isinstance(value, int) or isinstance(value, bool):
        return (
            f"{loaded.path}: 'max_per_room' must be an integer, "
            f"got {type(value).__name__}"
        )
    if value < 1:
        return (
            f"{loaded.path}: 'max_per_room' must be at least 1, got {value}"
        )
    return None


def _check_desc_well_formed(loaded: LoadedRule) -> str | None:
    """Optional shape: ``desc`` is a string.

    Description override; falls back to the typeclass default if
    absent. Empty string IS allowed — `desc: ""` is the explicit
    "override to empty" semantic (architecture.md Rule schema).
    """
    rule = _rule_or_none(loaded)
    if rule is None or "desc" not in rule:
        return None

    value = rule["desc"]
    if not isinstance(value, str):
        return (
            f"{loaded.path}: 'desc' must be a string, "
            f"got {type(value).__name__}"
        )
    return None


def _check_attrs_well_formed(loaded: LoadedRule) -> str | None:
    """Optional shape: ``attrs`` is a mapping.

    Per-rule attribute overrides applied to the spawned mob. The
    library applies them generically; semantics belong to the
    consumer's typeclass.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "attrs" not in rule:
        return None

    value = rule["attrs"]
    if not isinstance(value, dict):
        return (
            f"{loaded.path}: 'attrs' must be a mapping, "
            f"got {type(value).__name__}"
        )
    return None


def _check_spawn_with_typeclass_well_formed(loaded: LoadedRule) -> str | None:
    """Optional shape: ``spawn_with_typeclass`` is a non-empty string.

    Pack-spawn trigger — room must already contain a living instance
    of this typeclass. Importability is Tier 3.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "spawn_with_typeclass" not in rule:
        return None

    value = rule["spawn_with_typeclass"]
    if not isinstance(value, str):
        return (
            f"{loaded.path}: 'spawn_with_typeclass' must be a string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        return (
            f"{loaded.path}: 'spawn_with_typeclass' must be a non-empty string"
        )
    return None


def _check_den_room_tag_well_formed(loaded: LoadedRule) -> str | None:
    """Optional shape: ``den_room_tag`` is a non-empty string.

    Single-room lair target. Uses the same tag category as ``area_tag``
    (architecture.md decision #16).
    """
    rule = _rule_or_none(loaded)
    if rule is None or "den_room_tag" not in rule:
        return None

    value = rule["den_room_tag"]
    if not isinstance(value, str):
        return (
            f"{loaded.path}: 'den_room_tag' must be a string, "
            f"got {type(value).__name__}"
        )
    if not value.strip():
        return f"{loaded.path}: 'den_room_tag' must be a non-empty string"
    return None


# Tag entries accept the same shapes Evennia's TagHandler.add() does:
# bare string (untyped tag), dict with `key` only (untyped tag), or
# dict with both `key` and `category` (categorised tag). ``data=`` is
# not exposed in YAML (matches world-builder's choice).
_TAG_DICT_ALLOWED_KEYS = frozenset({"key", "category"})

# Library-reserved tag categories that authors cannot stamp from YAML.
# Anything under this prefix is owned by the library (identity tags
# stamped at spawn time); allowing authors to spoof these would
# corrupt the (file, rule_id) population discriminator.
_RESERVED_TAG_CATEGORY_PREFIX = "mob_spawner_"


def _normalise_tag(entry):
    """Return ``(key, category)`` from either accepted tag-entry shape.

    Caller is responsible for first running ``_check_tags_field_shape``
    against the parent rule — this helper assumes the entry has the
    correct shape (string, or dict with `key` + optional `category`).
    """
    if isinstance(entry, str):
        return entry, None
    return entry["key"], entry.get("category")


def _check_tags_field_shape(loaded: LoadedRule) -> str | None:
    """Optional shape: ``tags`` is a list of strings or `{key, category?}` dicts.

    Each entry is one of:
    - A non-empty string (untyped tag)
    - A mapping with ``key`` (non-empty string, required) and optionally
      ``category`` (non-empty string). No other keys allowed.

    Empty list is permitted (no-op). Field absent is permitted.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "tags" not in rule:
        return None

    value = rule["tags"]
    if not isinstance(value, list):
        return (
            f"{loaded.path}: 'tags' must be a list, "
            f"got {type(value).__name__}"
        )

    for i, entry in enumerate(value):
        prefix = f"{loaded.path}: 'tags[{i}]'"
        if isinstance(entry, str):
            if not entry.strip():
                return f"{prefix} must be a non-empty string"
            continue
        if not isinstance(entry, dict):
            return (
                f"{prefix} must be a string or mapping, "
                f"got {type(entry).__name__}"
            )

        extra = set(entry.keys()) - _TAG_DICT_ALLOWED_KEYS
        if extra:
            return (
                f"{prefix} has unsupported key(s) {sorted(extra)!r}; "
                f"allowed keys are {sorted(_TAG_DICT_ALLOWED_KEYS)!r}"
            )

        if "key" not in entry:
            return f"{prefix} missing required 'key'"
        key = entry["key"]
        if not isinstance(key, str):
            return (
                f"{prefix} 'key' must be a string, "
                f"got {type(key).__name__}"
            )
        if not key.strip():
            return f"{prefix} 'key' must be a non-empty string"

        if "category" in entry:
            category = entry["category"]
            if not isinstance(category, str):
                return (
                    f"{prefix} 'category' must be a string, "
                    f"got {type(category).__name__}"
                )
            if not category.strip():
                return (
                    f"{prefix} 'category' must be a non-empty string"
                )

    return None


def _check_tags_no_reserved_category(loaded: LoadedRule) -> str | None:
    """Optional shape: no tag may target a library-reserved category.

    Categories starting with ``mob_spawner_`` are owned by the library
    (identity tags stamped by ``_spawn_one`` at spawn time). Allowing
    YAML to stamp tags in those categories would let authors spoof the
    (file, rule_id) population discriminator. Refused.

    Defers cleanly if the field shape would already fail (sibling
    predicate ``_check_tags_field_shape`` reports those findings).
    """
    rule = _rule_or_none(loaded)
    if rule is None or "tags" not in rule:
        return None

    value = rule["tags"]
    if not isinstance(value, list):
        return None  # shape predicate owns this finding

    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            continue
        category = entry.get("category")
        if not isinstance(category, str):
            continue
        if category.startswith(_RESERVED_TAG_CATEGORY_PREFIX):
            return (
                f"{loaded.path}: 'tags[{i}]' uses reserved category "
                f"{category!r} (prefix {_RESERVED_TAG_CATEGORY_PREFIX!r} "
                f"is owned by the library)"
            )

    return None


# ---------------------------------------------------------------------------
# Tier 3 — engine-runtime predicates.
#
# These only run when the caller passes ``evennia_runtime=True`` to the
# Validator. ``ms_load`` does; ``ms-validate`` (CLI) doesn't, because the
# consumer's gamedir isn't on sys.path in CI environments.
# ---------------------------------------------------------------------------


def _resolve_dotted(path: str):
    """Resolve a dotted path to an object.

    Returns ``(resolved_object, None)`` on success, or
    ``(None, predicate_ready_reason)`` on failure — the reason is a
    short string suitable for embedding in a per-field finding (caller
    prefixes the rule path and field name).
    """
    module_path, _, attr_name = path.rpartition(".")
    if not module_path or not attr_name:
        return None, f"{path!r} is not a dotted path"
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        return None, f"module {module_path!r} could not be imported ({e})"
    if not hasattr(module, attr_name):
        return None, (
            f"module {module_path!r} loaded but {attr_name!r} not found"
        )
    return getattr(module, attr_name), None


def _check_typeclass_resolvable(loaded: LoadedRule) -> str | None:
    """Tier 3: ``typeclass`` resolves to a class.

    Defers cleanly if the field is absent or non-string (Tier 1 owns
    those findings; Tier 3 just adds the resolvability layer).
    """
    rule = _rule_or_none(loaded)
    if rule is None or "typeclass" not in rule:
        return None
    value = rule["typeclass"]
    if not isinstance(value, str) or not value.strip():
        return None

    resolved, error = _resolve_dotted(value)
    if error is not None:
        return f"{loaded.path}: 'typeclass' — {error}"
    if not isinstance(resolved, type):
        return (
            f"{loaded.path}: 'typeclass' {value!r} resolved but is not a "
            f"class (got {type(resolved).__name__})"
        )
    return None


def _check_spawn_with_typeclass_resolvable(loaded: LoadedRule) -> str | None:
    """Tier 3: ``spawn_with_typeclass`` (when present) resolves to a class."""
    rule = _rule_or_none(loaded)
    if rule is None or "spawn_with_typeclass" not in rule:
        return None
    value = rule["spawn_with_typeclass"]
    if not isinstance(value, str) or not value.strip():
        return None

    resolved, error = _resolve_dotted(value)
    if error is not None:
        return f"{loaded.path}: 'spawn_with_typeclass' — {error}"
    if not isinstance(resolved, type):
        return (
            f"{loaded.path}: 'spawn_with_typeclass' {value!r} resolved but is "
            f"not a class (got {type(resolved).__name__})"
        )
    return None


def _check_typeclass_ms_at_post_spawn_callable(loaded: LoadedRule) -> str | None:
    """Tier 3: if the resolved ``typeclass`` declares ``ms_at_post_spawn``, it is callable.

    The library's contract for per-spawn behaviour (architecture
    decision #23) is an optional method on the typeclass. If the
    typeclass declares the attribute with a non-callable value (e.g.
    `ms_at_post_spawn = "string"` by mistake), the runtime call would
    crash; flag it at validation.

    Defers cleanly if `typeclass` is absent / non-string / unresolvable
    (sibling predicates own those findings). Only adds a finding when
    the resolved class actually declares the attribute and it's not
    callable.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "typeclass" not in rule:
        return None
    value = rule["typeclass"]
    if not isinstance(value, str) or not value.strip():
        return None

    resolved, error = _resolve_dotted(value)
    if error is not None or not isinstance(resolved, type):
        return None

    if not hasattr(resolved, _MS_AT_POST_SPAWN_ATTR):
        return None
    hook = getattr(resolved, _MS_AT_POST_SPAWN_ATTR)
    if not callable(hook):
        return (
            f"{loaded.path}: typeclass {value!r} declares "
            f"{_MS_AT_POST_SPAWN_ATTR!r} but it is not callable "
            f"(got {type(hook).__name__})"
        )
    return None


def _check_typeclass_ms_at_post_spawn_signature(loaded: LoadedRule) -> str | None:
    """Tier 3: ``ms_at_post_spawn`` is callable as ``mob.ms_at_post_spawn()``.

    The library invokes the method with no arguments after binding
    ``self``. A signature like ``def ms_at_post_spawn(self, foo)`` would
    crash with ``TypeError`` at the first spawn — catch at validation.

    Accepts the canonical ``def ms_at_post_spawn(self)``, plus
    defaulted args (``self, foo=None``), variadic ``(*args, **kwargs)``,
    staticmethod / classmethod variants — anything callable as zero-arg
    after the implicit ``self`` (if any) is bound.

    Defers cleanly when sibling predicates would already report the
    issue (typeclass missing / unresolvable, hook absent / non-callable).
    Also defers cleanly when ``inspect.signature`` cannot introspect the
    callable (rare — some C-extension callables); runtime will surface
    those.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "typeclass" not in rule:
        return None
    value = rule["typeclass"]
    if not isinstance(value, str) or not value.strip():
        return None

    resolved, error = _resolve_dotted(value)
    if error is not None or not isinstance(resolved, type):
        return None

    if not hasattr(resolved, _MS_AT_POST_SPAWN_ATTR):
        return None
    hook = getattr(resolved, _MS_AT_POST_SPAWN_ATTR)
    if not callable(hook):
        return None  # sibling predicate (`_callable`) owns this finding

    try:
        sig = inspect.signature(hook)
    except (ValueError, TypeError):
        return None  # introspection unavailable; defer to runtime

    params = list(sig.parameters.values())
    # Convention: strip first param if named self/cls (instance / classmethod).
    # staticmethod / classmethod-bound-via-descriptor have no self/cls in the
    # signature already; this heuristic only removes it when present.
    if params and params[0].name in ("self", "cls"):
        params = params[1:]

    required = [
        p for p in params
        if p.default is inspect.Parameter.empty
        and p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]
    if required:
        names = ", ".join(p.name for p in required)
        return (
            f"{loaded.path}: typeclass {value!r}'s "
            f"{_MS_AT_POST_SPAWN_ATTR!r} requires additional arguments "
            f"({names}) — signature must be callable as "
            f"'mob.{_MS_AT_POST_SPAWN_ATTR}()'"
        )
    return None


# ---------------------------------------------------------------------------
# Tier 4 — diagnostic checks (engine-runtime only).
#
# DIFFERENT CONTRACT FROM PREDICATES. Diagnostic functions:
#   - Return None unconditionally.
#   - May have side effects (log via ms_log).
#   - Never refuse deployment.
#
# Naming convention `_diagnostic_*` (vs predicates' `_check_*`) makes the
# distinction visible at every call site. Tier 4 runs after Tier 1+2 pass
# on a rule, only when `evennia_runtime=True`. See decision #24.
# ---------------------------------------------------------------------------


def _diagnostic_area_tag_rooms_exist(loaded: LoadedRule) -> None:
    """Tier 4 diagnostic: WARN-log if ``area_tag`` references zero tagged rooms.

    Operator may be deploying world content in parallel; this isn't a
    validation failure. The log line surfaces the issue once per deploy
    so the tick-time skip log (decision #15) doesn't spam.

    Side effect: emits ms_log at WARN level. Returns None.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "area_tag" not in rule:
        return
    value = rule["area_tag"]
    if not isinstance(value, str) or not value.strip():
        return

    from evennia.objects.models import ObjectDB
    from .config import get_area_tag_category

    count = ObjectDB.objects.filter(
        db_tags__db_key=value,
        db_tags__db_category=get_area_tag_category(),
    ).count()

    if count == 0:
        ms_log(
            f"{loaded.path}: rule_id={rule.get('rule_id')} "
            f"area_tag={value!r} has 0 tagged rooms — spawns will be "
            f"skipped until rooms are tagged",
            level="WARN",
        )


def _diagnostic_den_room_tag_rooms_exist(loaded: LoadedRule) -> None:
    """Tier 4 diagnostic: WARN-log if ``den_room_tag`` references zero tagged rooms.

    Same one-log-per-deploy semantic as the area_tag diagnostic.
    Field is optional — if absent, no diagnostic.

    Side effect: emits ms_log at WARN level. Returns None.
    """
    rule = _rule_or_none(loaded)
    if rule is None or "den_room_tag" not in rule:
        return
    value = rule["den_room_tag"]
    if not isinstance(value, str) or not value.strip():
        return

    from evennia.objects.models import ObjectDB
    from .config import get_area_tag_category

    count = ObjectDB.objects.filter(
        db_tags__db_key=value,
        db_tags__db_category=get_area_tag_category(),
    ).count()

    if count == 0:
        ms_log(
            f"{loaded.path}: rule_id={rule.get('rule_id')} "
            f"den_room_tag={value!r} has 0 tagged rooms — pack/random "
            f"fallback will be used instead of the den",
            level="WARN",
        )


class Validator:
    """Validates a LoadResult via a predicate-list pipeline.

    Construction:
        definitions:     parsed Definitions (reserved for predicates that
                         depend on level vocabulary; unused today).
        evennia_runtime: when True, Tier 3 (Evennia-runtime) predicates
                         run alongside Tier 1. ``ms_load`` passes True;
                         ``ms-validate`` (CLI) leaves it False because
                         the consumer's gamedir isn't on sys.path in CI.

    Attributes (populated during validate()):
        messages: human-readable findings produced during the pass.
                  Callers print these — the Validator does no I/O.
        errors:   subset of messages corresponding to actual errors.
                  Empty after a clean run; non-empty implies validate()
                  raised ValidatorError.
        seen_ids: ``{file_path: set(rule_ids)}`` accumulated during the
                  per-rule loop. Used by Tier 2 for in-pass duplicate
                  detection of ``rule_id`` within each file.
    """

    # Tier 1 — pure (LoadedRule) -> str | None. Always active.
    PER_RULE_PREDICATES: tuple = (
        _check_rule_is_mapping,
        _check_rule_id_well_formed,
        _check_typeclass_well_formed,
        _check_key_well_formed,
        _check_area_tag_well_formed,
        _check_target_well_formed,
        _check_respawn_seconds_well_formed,
        _check_death_cooldown_seconds_well_formed,
        _check_cooldown_exclusivity,
        _check_max_per_room_well_formed,
        _check_desc_well_formed,
        _check_attrs_well_formed,
        _check_spawn_with_typeclass_well_formed,
        _check_den_room_tag_well_formed,
        _check_tags_field_shape,
        _check_tags_no_reserved_category,
    )

    # Tier 3 — same shape as Tier 1, but only active when
    # ``evennia_runtime=True``. Predicates here must be free to import
    # consumer modules; the assumption is the engine is up.
    EVENNIA_ONLY_PREDICATES: tuple = (
        _check_typeclass_resolvable,
        _check_spawn_with_typeclass_resolvable,
        _check_typeclass_ms_at_post_spawn_callable,
        _check_typeclass_ms_at_post_spawn_signature,
    )

    # Tier 4 — diagnostic checks. DIFFERENT CONTRACT from predicates:
    # signature ``(LoadedRule) -> None``, side effects allowed (ms_log),
    # no refusal. Active only with ``evennia_runtime=True``. Run after
    # Tier 1+2 pass on a rule (decision #24).
    EVENNIA_DIAGNOSTICS: tuple = (
        _diagnostic_area_tag_rooms_exist,
        _diagnostic_den_room_tag_rooms_exist,
    )

    def __init__(self, definitions: Definitions, *, evennia_runtime: bool = False):
        self.definitions = definitions
        self.evennia_runtime = evennia_runtime
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.seen_ids: dict[str, set[int]] = {}

    def validate(self, load_result: LoadResult) -> None:
        """Run all active checks against ``load_result``. Raise on findings.

        Iterates every (file_path, rule) pair, runs Tier 1 (and Tier 3
        if active) on each, runs Tier 2 only on rules that passed
        Tier 1, then runs the file-level shape pass once at the end.
        After the full pass, raises ``ValidatorError`` if any error-
        severity finding was collected. Callers should still read
        ``self.messages`` to surface the complete list to the operator.
        """
        for path, rules in load_result.rule_sets.items():
            for rule in rules:
                loaded = LoadedRule(path=path, rule=rule)
                stateless_clean = self._run_stateless_predicates(loaded)
                if not stateless_clean:
                    # Skip Tier 2 — it would record garbage in seen_ids
                    # (e.g. a non-integer rule_id, a None, etc.).
                    # Skip Tier 4 — diagnosing a broken rule is noise.
                    continue
                self._check_and_record_unique_rule_id(loaded)
                # Tier 4 diagnostics. Engine-only; side-effecting; no findings.
                # Runs even if Tier 2 added a duplicate-id finding — the
                # diagnostic still helps the operator.
                if self.evennia_runtime:
                    self._run_diagnostics_for_rule(loaded)

        self._check_file_metadata_shape(load_result.file_metadata)

        if self.errors:
            n = len(self.errors)
            raise ValidatorError(
                f"{n} validation error{'s' if n != 1 else ''} — see messages"
            )

    def _record_finding(self, finding: str) -> None:
        """All findings flow through here so accumulation stays uniform."""
        self.messages.append(finding)
        self.errors.append(finding)

    def _active_predicates(self) -> tuple:
        """Predicates appropriate for the current caller context.

        Tier 1 always runs; Tier 3 opts in via ``evennia_runtime=True``.
        """
        active = self.PER_RULE_PREDICATES
        if self.evennia_runtime:
            active = active + self.EVENNIA_ONLY_PREDICATES
        return active

    def _run_stateless_predicates(self, loaded: LoadedRule) -> bool:
        """Run every active predicate against ``loaded``. True iff all pass."""
        clean = True
        for predicate in self._active_predicates():
            finding = predicate(loaded)
            if finding is not None:
                self._record_finding(finding)
                clean = False
        return clean

    def _check_and_record_unique_rule_id(self, loaded: LoadedRule) -> None:
        """Tier 2 — record this rule's ``rule_id`` and refuse duplicates.

        ``rule_id`` must be unique within its file (architecture.md
        decision #10 — the persistent Script's bookkeeping is keyed on
        it). Uniqueness across files is not required and not checked
        here.

        Only runs on rules that passed Tier 1 (`_run_stateless_predicates`
        returned True), so the rule_id is guaranteed to be a valid
        non-negative integer — no defensive guards needed.
        """
        rule_id = loaded.rule["rule_id"]
        seen = self.seen_ids.setdefault(loaded.path, set())
        if rule_id in seen:
            self._record_finding(
                f"{loaded.path}: duplicate rule_id {rule_id} — "
                f"must be unique within file"
            )
            return
        seen.add(rule_id)

    def _check_file_metadata_shape(self, file_metadata: dict) -> None:
        """File-level pass — shape-check the per-file metadata dict.

        Currently enforces the minimum: each ``file_metadata[path]``
        value must be a mapping. The Loader's own output always
        satisfies this (it builds the value as a dict comprehension
        from the parsed YAML's top-level mapping), so the check fires
        only when ``LoadResult`` is constructed outside the Loader
        (tests, future direct construction).

        No specific file-level keys are recognised at v0 — when
        per-file directives are pinned, their per-key shape checks
        slot in here, mirroring world-builder's
        ``_check_incoming_exits_shape`` / ``_check_links_shape``.
        """
        for path, meta in file_metadata.items():
            if not isinstance(meta, dict):
                self._record_finding(
                    f"{path}: file metadata must be a mapping, "
                    f"got {type(meta).__name__}"
                )

    def _run_diagnostics_for_rule(self, loaded: LoadedRule) -> None:
        """Run every Tier 4 diagnostic against the rule. No return value.

        Diagnostics have side effects (ms_log) and never refuse —
        they're informational deploy-time warnings, distinct from
        predicate findings. Caller is responsible for gating on
        ``evennia_runtime``.
        """
        for diagnostic in self.EVENNIA_DIAGNOSTICS:
            diagnostic(loaded)
