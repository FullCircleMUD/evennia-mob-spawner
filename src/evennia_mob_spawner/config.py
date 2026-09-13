# SPDX-License-Identifier: BSD-3-Clause
"""Settings dispatch for evennia-mob-spawner."""
import importlib


DEFAULT_READER = "evennia_yaml_reader.github.GitHubReader"


def get_reader_class():
    """Resolve the MOB_SPAWNER_READER setting (dotted path) to a class.

    The setting value is a Python dotted path (e.g.
    ``"evennia_yaml_reader.GitHubReader"`` or
    ``"my_consumer.readers.MyReader"``). Defaults to
    ``evennia_yaml_reader.github.GitHubReader``.

    Construction is the consumer's responsibility — this function
    returns the class only. Reader kwargs are reader-specific and
    supplied by the consumer at construction time.

    Raises:
        ImportError: if the module path cannot be imported.
        AttributeError: if the named attribute does not exist on the module.
    """
    from django.conf import settings

    dotted = getattr(settings, "MOB_SPAWNER_READER", DEFAULT_READER)
    module_path, _, attr_name = dotted.rpartition(".")
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


DEFAULT_AREA_TAG_CATEGORY = "mob_area"
DEFAULT_TICK_SECONDS = 15


def get_tick_seconds() -> int:
    """Return the tick interval (seconds) for ``MobSpawnerScript`` instances.

    Architecture decision #3: tick interval is a library-level setting,
    single value for the whole consuming game. Override via
    ``settings.MOB_SPAWNER_TICK_SECONDS``; default is 15 seconds —
    matches FCM's existing convention.

    The setting feeds ``script.interval`` at ``at_script_creation``
    time. Changing the setting only affects newly created scripts;
    existing scripts keep their stored ``interval`` value until they
    are deleted and recreated.
    """
    from django.conf import settings

    return getattr(settings, "MOB_SPAWNER_TICK_SECONDS", DEFAULT_TICK_SECONDS)


def get_area_tag_category() -> str:
    """Return the tag category used for ``area_tag`` / ``den_room_tag`` queries.

    Architecture decision #1: tag category is a library-level setting,
    not a per-rule field. Override via ``settings.MOB_SPAWNER_AREA_TAG_CATEGORY``
    if your game uses a different category name; default is ``"mob_area"``,
    matching FCM's existing convention.

    Used by the Validator's Tier 4 diagnostic predicates (decision #24)
    and by the Deployer's room-selection / re-tag machinery once it
    lands.
    """
    from django.conf import settings

    return getattr(settings, "MOB_SPAWNER_AREA_TAG_CATEGORY", DEFAULT_AREA_TAG_CATEGORY)


def get_configured_reader():
    """Resolve the reader class and instantiate it with MOB_SPAWNER_READER_KWARGS.

    The consumer supplies reader-specific kwargs as a single dict-shaped
    setting (``MOB_SPAWNER_READER_KWARGS``). The library forwards them
    to the resolved reader class without inspecting their contents —
    kwargs are reader-specific and the library does not dictate their
    shape.

    Raises:
        Whatever the resolved reader class's __init__ raises if kwargs
        don't match. Unset / empty kwargs setting is treated as ``{}``.
    """
    from django.conf import settings

    reader_class = get_reader_class()
    kwargs = getattr(settings, "MOB_SPAWNER_READER_KWARGS", {}) or {}
    return reader_class(**kwargs)


SHARD_LEVEL = "shard"
"""The level name that carries the instance id when co-installed with scaling.

Level names are otherwise consumer-chosen. This is the one naming rule the
scaling pairing imposes, and it is what lets ``ms_load`` tell which instance a
rule set belongs to. The word matches scaling's own vocabulary — its roles are
``router`` and ``shard`` — so a consumer's ``definitions.yaml`` says what
scaling's settings say. See docs/interoperability.md.
"""


def active_shard_id() -> str | None:
    """Return this instance's id, or ``None`` when running standalone.

    Installed is the whole test: scaling has no single-instance mode, and it
    refuses to boot an instance whose ``SCALING_ROLE`` is neither role, so an
    import that succeeds on a running server means a multi-instance
    deployment. ``None`` is the signal for callers to skip every instance
    check and behave exactly as they do standalone.

    The id is multiplex's. Scaling mints none of its own and reads that one
    everywhere, so a second source for the same fact would only give the two
    somewhere to disagree.
    """
    try:
        import evennia_scaling  # noqa: F401 — presence is the test
        from evennia_portal_multiplex.config import get_instance_id
    except ImportError:
        return None

    return get_instance_id()


def is_router_process() -> bool:
    """True only when this instance is the router.

    The router holds no game world, so no rule set can deploy there. It is
    refused by role rather than by an id that never matches: under scaling
    ``SCALING_ROUTER_ID`` is an arbitrary name, so a rule-set folder can be
    named after it and would otherwise pass the id check.

    False when scaling isn't installed — a standalone game has no router.
    """
    try:
        from evennia_scaling.config import ROLE_ROUTER, get_role
    except ImportError:
        return False

    return get_role() == ROLE_ROUTER


# ---------------------------------------------------------------------------
# Constants
#
# Every module-level constant the library declares lives here, and every other
# module imports it from here — so a session about to declare one finds the
# existing name rather than minting a second for the same value.
# ---------------------------------------------------------------------------

# --- The shape of a rule-set source ---------------------------------------

_DEFINITIONS_PATH = "definitions.yaml"
_INDEX_FILENAME = "index.yaml"
_RULES_KEY = "rules"

# An index entry is one of these two kinds. Read by both the Finder, which
# walks the index to resolve a query, and the Loader, which reads it again to
# decide whether to descend or parse.
_KIND_FOLDER = "folder"
_KIND_FILE = "file"
_VALID_KINDS = (_KIND_FOLDER, _KIND_FILE)

# --- The command surface ---------------------------------------------------

_ALL_TOKEN = "all"
FORCE_VALIDATE_FLAG = "force-validate"

_STATE_COLOURS = {
    "active": "|g",   # ticking
    "paused": "|y",   # not running, and someone meant it
    "stopped": "|y",
    "stalled": "|r",  # not running, and nobody meant it
}

# --- The typeclass protocol, and what the library stamps -------------------

# The one optional protocol surface: a typeclass defining this method has it
# invoked after spawn, and one that doesn't is unaffected.
_MS_AT_POST_SPAWN_ATTR = "ms_at_post_spawn"

# Tag categories stamped on every spawned mob. Together with ``area_tag``
# they form the queryable identity breadcrumb on each mob: which rule
# produced it, and which rule-set file that rule lives in.
_RULE_TAG_CATEGORY = "mob_spawner_rule"
_FILE_TAG_CATEGORY = "mob_spawner_file"

# Library-reserved tag categories that authors cannot stamp from YAML.
# Anything under this prefix is owned by the library — the two identity
# categories above are under it — and allowing authors to spoof these would
# corrupt the (file, rule_id) population discriminator.
_RESERVED_TAG_CATEGORY_PREFIX = "mob_spawner_"

# Tag entries accept the same shapes Evennia's TagHandler.add() does:
# bare string (untyped tag), dict with `key` only (untyped tag), or
# dict with both `key` and `category` (categorised tag). ``data=`` is
# not exposed in YAML (matches world-builder's choice).
_TAG_DICT_ALLOWED_KEYS = frozenset({"key", "category"})
