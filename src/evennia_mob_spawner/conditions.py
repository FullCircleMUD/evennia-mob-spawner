# SPDX-License-Identifier: BSD-3-Clause
"""The condition registry.

A game registers each condition by name at startup, and a rule's
``condition`` field names one. Before the rule spawns, the tick calls it with
the rule; a falsy answer skips the spawn. The conditions themselves are the
game's — the library only stores and looks them up.

The registry is process state. It empties when the Server process restarts,
so a game registers from ``at_server_start()``.
"""

#: ``name -> callable``. Lower case because it is live state rather than a
#: constant: it fills as the game registers.
_conditions = {}


def register_condition(name: str, fn) -> None:
    """Register ``fn`` as the condition called ``name``.

    ``fn`` takes the rule mapping and answers whether the rule may spawn now.

    **A name already in use is refused, never replaced.** Two subsystems can
    reach for the same name, and the second silently replacing the first
    would change when the first one's rules spawn with nothing to say why.
    """
    if name in _conditions:
        raise ValueError(
            f"condition {name!r} is already registered. Choose another name — "
            f"a second registration would silently replace the first."
        )
    _conditions[name] = fn


def get_condition(name: str):
    """Return the condition registered as ``name``, or ``None``."""
    return _conditions.get(name)
