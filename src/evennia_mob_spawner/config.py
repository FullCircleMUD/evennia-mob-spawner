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
