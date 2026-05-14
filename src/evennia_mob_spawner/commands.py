# SPDX-License-Identifier: BSD-3-Clause
"""Operator command surface.

Currently holds only the pre-validation gating helper. The full
``ms_load`` / ``ms_restart`` / ``ms_stop`` / ``ms_delete`` / ``ms_status``
admin command classes land here when the script lifecycle work begins
(architecture.md decision #7).

World-builder's equivalent module hosts the gating decision inline
inside the admin command's ``func()`` body. Mob-spawner pulls it out
into a named helper so the policy lives in one place — testable
without an Evennia engine, reachable from any future caller (admin
commands, scripts, scheduled jobs) without re-implementation.
"""
from .definitions import Definitions


FORCE_VALIDATE_FLAG = "force-validate"


def should_pre_validate(definitions: Definitions, flags: set) -> bool:
    """Decide whether ``ms_load`` should pre-validate the whole repo.

    The ``repo-ci-pre-validation`` setting in ``definitions.yaml`` is
    the consumer's persistent claim ("CI gates my YAML at merge time;
    runtime can trust it"). The ``--force-validate`` operator flag is
    an ad-hoc per-invocation override. Pre-validation runs whenever
    EITHER the setting is False (default safe) OR the flag is present.

    See architecture.md decision #19.

    Args:
        definitions: parsed ``Definitions`` for the content repo.
        flags:       set of operator flags parsed from the command
                     line (e.g. ``{"force-validate"}``).
    """
    force_validate = FORCE_VALIDATE_FLAG in flags
    return (not definitions.repo_ci_pre_validation) or force_validate
