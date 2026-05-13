# SPDX-License-Identifier: BSD-3-Clause
"""Loader — reads rule-set YAML files from a FoundLocation.

For each file in scope, parses YAML and produces a per-file rules list.
The output shape is intentionally thin: a dict keyed by file path with a
list of raw rule dicts as value. The Validator and Deployer consume this
shape downstream.
"""
from dataclasses import dataclass, field

from evennia_yaml_reader import Reader

from .definitions import Definitions
from .finder import FoundLocation


@dataclass(frozen=True)
class LoadResult:
    """Output of the Loader stage.

    Attributes:
        rule_sets: Mapping of file path -> list of raw rule dicts as they
                   were parsed from YAML. The Validator works against this
                   shape directly; the Deployer operates per-file.
    """

    rule_sets: dict = field(default_factory=dict)


class Loader:
    """Reads rule-set files into a LoadResult."""

    def __init__(self, reader: Reader, definitions: Definitions):
        self.reader = reader
        self.definitions = definitions

    def load(self, found: FoundLocation) -> LoadResult:
        """Walk the FoundLocation, read each rule-set file, return rules.

        Scaffold: returns an empty LoadResult. File-walking + parsing
        logic lands when needed.
        """
        return LoadResult()
