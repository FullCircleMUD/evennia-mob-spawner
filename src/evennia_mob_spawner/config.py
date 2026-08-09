# SPDX-License-Identifier: BSD-3-Clause
"""Settings dispatch for evennia-mob-spawner."""
import importlib

from django.conf import settings


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
    reader_class = get_reader_class()
    kwargs = getattr(settings, "MOB_SPAWNER_READER_KWARGS", {}) or {}
    return reader_class(**kwargs)


SHARD_LEVEL = "shard"
"""The level name that carries the shard id when co-installed with shards.

Level names are otherwise consumer-chosen. This is the one naming rule the
shards pairing imposes, and it is what lets ``ms_load`` tell which shard a
rule set belongs to. See docs/interoperability.md.
"""


def active_shard_id() -> str | None:
    """Return this process's shard id, or ``None`` if not sharded.

    "Sharded" means the shards library is installed **and** the role is not
    ``monolith`` — a successful import is not the test, because monolith is
    a non-sharded install where no shard context is ever set. Returning
    ``None`` is the signal for callers to skip every shard check and behave
    exactly as they do standalone.
    """
    try:
        from evennia_shards import ROLE_MONOLITH, get_role, get_shard_id
    except ImportError:
        return None

    if get_role() == ROLE_MONOLITH:
        return None
    return get_shard_id()


def is_shard_process() -> bool:
    """True only when this process is a shard.

    Distinct from :func:`active_shard_id`, which answers "is sharding in
    play at all". This answers "is this process's ORM narrowed to one
    shard's rows" — the condition that makes a cluster-wide question
    unanswerable here.

    False for the router (runs unscoped), for monolith (a non-sharded
    install — no shard context is ever set), and when shards isn't
    installed (no filter exists). See docs/interoperability.md.
    """
    try:
        from evennia_shards import ROLE_SHARD, get_role
    except ImportError:
        return False

    return get_role() == ROLE_SHARD
