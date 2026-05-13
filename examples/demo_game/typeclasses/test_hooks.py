# SPDX-License-Identifier: BSD-3-Clause
"""Synthetic post_spawn_hook functions for mob-spawner integration smokes.

Referenced from the fixture rules in ``evennia-mob-spawner-test-yaml``.
Hooks are intentionally trivial — the library's contract is just "call
this dotted path with the spawned mob"; the test fixtures don't need
real behaviour to exercise that contract.
"""


def reset_chieftain_state(mob):
    """post_spawn_hook for the test KoboldChieftain.

    Clears per-instance state that should not carry over from a previous
    incarnation. Synthetic; demonstrates the post_spawn_hook contract
    (decision-level reference in the library's architecture doc).
    """
    if hasattr(mob, "db"):
        mob.db.has_rallied = False
