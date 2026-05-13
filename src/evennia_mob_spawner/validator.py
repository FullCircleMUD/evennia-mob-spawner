# SPDX-License-Identifier: BSD-3-Clause
"""Validator — checks the LoadResult before the Deployer is invoked.

Predicate tiering and the actual checks land as concrete decisions get
made (see architecture.md "Validation tiering and gating"). This scaffold
collects messages and errors, never raises, and produces a clean pass —
useful for end-to-end pipeline plumbing while the real predicates are
written.
"""
from .definitions import Definitions
from .errors import ValidatorError
from .loader import LoadResult


class Validator:
    """Runs predicate tiers over a LoadResult, collecting findings.

    Per CLAUDE.md principle 4: gather every finding before refusing.
    ``validate()`` raises ``ValidatorError`` if any error-severity findings
    were collected. Until predicates land, validation always passes.
    """

    def __init__(self, definitions: Definitions, *, evennia_runtime: bool = False):
        self.definitions = definitions
        self.evennia_runtime = evennia_runtime
        self.messages: list[str] = []
        self.errors: list[str] = []

    def validate(self, load_result: LoadResult) -> None:
        """Run all predicates over load_result. Append findings; raise on errors.

        Scaffold: no predicates registered yet. Always passes cleanly.
        """
        if self.errors:
            raise ValidatorError(
                f"validation refused: {len(self.errors)} error(s) collected"
            )
