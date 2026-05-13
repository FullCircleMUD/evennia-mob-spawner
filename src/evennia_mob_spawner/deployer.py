# SPDX-License-Identifier: BSD-3-Clause
"""Deployer — terminal stage of the pipeline.

For each rule-set file in the LoadResult, finds or creates the persistent
Script, replaces its rule table with the new YAML, and preserves runtime
state (cooldowns, observation counts). The drain-swap-resume protocol
(architecture.md decision #13) lands here when implementation begins.

Scaffold: no-op. The pipeline can flow end-to-end with this in place;
real script management lands when concrete needs surface.
"""
from .definitions import Definitions
from .loader import LoadResult


class Deployer:
    """Applies a LoadResult to the running game's scripts.

    The full lifecycle (validate → snapshot → drain → swap → resume) is
    documented in architecture.md. This scaffold accepts the LoadResult
    and does nothing with it — the pipeline plumbing is exercised without
    any DB or Script mutation.
    """

    def __init__(self, definitions: Definitions):
        self.definitions = definitions

    def deploy(self, load_result: LoadResult) -> None:
        """Apply the LoadResult to running scripts.

        Scaffold: no-op. Wires in when the Script lifecycle is implemented.
        """
        return None
