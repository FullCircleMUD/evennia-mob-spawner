# SPDX-License-Identifier: BSD-3-Clause
"""Finder — walks the manifest tree following an operator query.

Returns a FoundLocation that the Loader uses as its entry point. Manifest
walking logic (level-by-level descent through ``index.yaml`` files) lands
when concrete needs surface; this scaffold returns the root location for
any query.
"""
from dataclasses import dataclass, field

from evennia_yaml_reader import Reader

from .definitions import Definitions


@dataclass(frozen=True)
class FoundLocation:
    """Entry point for the Loader — where the query lands in the manifest.

    Attributes:
        path:     Path within the source. "" at root, "<folder>" for a folder
                  scope, "<folder>/<file>.yaml" for a single-file scope.
        kind:     "root", "folder", or "file".
        location: Accumulated {level_name: value} pairs at this point.
    """

    path: str = ""
    kind: str = "root"
    location: dict = field(default_factory=dict)


class Finder:
    """Walks the manifest tree following an operator query."""

    def __init__(self, reader: Reader, definitions: Definitions):
        self.reader = reader
        self.definitions = definitions

    def find(self, query: dict | None = None) -> FoundLocation:
        """Resolve a query to a FoundLocation in the manifest tree.

        Scaffold: returns the root location regardless of query. Real
        walking logic lands when needed.
        """
        return FoundLocation(path="", kind="root", location=dict(query or {}))
