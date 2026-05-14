# SPDX-License-Identifier: BSD-3-Clause
"""Loader — reads rule-set YAML files from a FoundLocation.

Walks the FoundLocation (file or folder) returned by the Finder and
loads every leaf rule-set file underneath it. Each file is required to
be a top-level mapping with a ``rules:`` key whose value is a list of
rule mappings; any other top-level keys ride alongside as file-level
metadata.

File shape contract::

    rules:
      - { ... rule 1 ... }
      - { ... rule 2 ... }
    # (any number of file-level keys may appear alongside `rules:`)

The Loader does not curate which file-level keys exist — it just bags
every non-``rules:`` top-level key into ``LoadResult.file_metadata`` so
downstream stages can look up the keys they care about. A file appears
in ``file_metadata`` only if it declared at least one such key. No
file-level keys are recognised today; the slot exists so per-file
directives can land later without a schema break.

Content validation (required rule fields, types, uniqueness, …) is the
Validator's concern. The Loader's job is structural only: enforce the
top-level shape, surface the rules list under each file path, raise on
malformed / missing inputs.
"""
from dataclasses import dataclass, field

from evennia_yaml_reader import Reader, ReaderNotFoundError

from .definitions import Definitions
from .errors import (
    LoaderInvalidShapeError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
)
from .finder import FoundLocation


_INDEX_FILENAME = "index.yaml"
_KIND_FOLDER = "folder"
_KIND_FILE = "file"
_RULES_KEY = "rules"


@dataclass(frozen=True)
class LoadResult:
    """Output of the Loader stage.

    Attributes:
        rule_sets:     Mapping of file path -> list of raw rule dicts as
                       parsed from each file's ``rules:`` block. One
                       entry per loaded file; the file path is the
                       script's identity downstream.
        file_metadata: Mapping of file path -> ``{key: value, ...}`` for
                       any file-level keys declared alongside ``rules:``.
                       A file appears here only if it declared at least
                       one such key. Library doesn't curate the contents;
                       downstream stages look up the keys they care about.
    """

    rule_sets: dict = field(default_factory=dict)
    file_metadata: dict = field(default_factory=dict)


class Loader:
    """Reads rule-set files into a LoadResult.

    Construction:
        reader:      configured Reader (for fetching index.yaml and rule
                     files).
        definitions: parsed Definitions. Currently unused by the Loader
                     itself — the file path is sufficient identity for
                     each loaded file — but kept on the constructor for
                     pipeline symmetry with sibling libraries and to
                     leave the slot open for level-aware breadcrumbs in
                     future operator-facing surfaces.
    """

    def __init__(self, reader: Reader, definitions: Definitions):
        self.reader = reader
        self.definitions = definitions
        # Per-load accumulators, reset at the start of every load() call.
        self._rule_sets: dict = {}
        self._file_metadata: dict = {}

    def load(self, found: FoundLocation) -> LoadResult:
        """Walk ``found`` and load every rule-set file underneath."""
        self._rule_sets = {}
        self._file_metadata = {}
        self._walk(found)
        return LoadResult(
            rule_sets=dict(self._rule_sets),
            file_metadata=dict(self._file_metadata),
        )

    def _walk(self, found: FoundLocation) -> None:
        if found.kind == _KIND_FILE:
            self._load_file(found.path)
            return

        # folder — read its index.yaml and recurse over entries in order
        index_path = (
            f"{found.path}/{_INDEX_FILENAME}" if found.path else _INDEX_FILENAME
        )
        for entry in self._read_index_entries(index_path):
            name = entry["name"]
            kind = entry["kind"]
            if kind == _KIND_FILE:
                child_path = (
                    f"{found.path}/{name}.yaml" if found.path else f"{name}.yaml"
                )
                self._load_file(child_path)
            else:
                child_path = f"{found.path}/{name}" if found.path else name
                # Location dict not carried forward — the Loader is
                # file-identified, not level-identified.
                self._walk(
                    FoundLocation(path=child_path, kind=_KIND_FOLDER, location={})
                )

    def _load_file(self, path: str) -> None:
        try:
            read = self.reader.read(path)
        except ReaderNotFoundError as e:
            raise LoaderMissingEntryError(
                f"Index pointed at file {path!r} but it was not found at source"
            ) from e

        parsed = read.parsed
        if not isinstance(parsed, dict) or _RULES_KEY not in parsed:
            raise LoaderInvalidShapeError(
                f"{path!r}: file must be a top-level mapping with a "
                f"'rules:' key whose value is a list of rule mappings"
            )

        rule_list = parsed[_RULES_KEY]
        if not isinstance(rule_list, list):
            raise LoaderInvalidShapeError(
                f"{path!r}: 'rules:' must be a list, "
                f"got {type(rule_list).__name__}"
            )

        self._rule_sets[path] = rule_list

        file_meta = {k: v for k, v in parsed.items() if k != _RULES_KEY}
        if file_meta:
            self._file_metadata[path] = file_meta

    def _read_index_entries(self, path: str) -> list:
        try:
            read = self.reader.read(path)
        except ReaderNotFoundError as e:
            raise LoaderMissingIndexError(
                f"Folder index not found at {path!r}"
            ) from e

        data = read.parsed
        if not isinstance(data, dict) or "entries" not in data:
            raise LoaderMissingIndexError(f"{path}: missing 'entries' field")
        entries = data["entries"]
        if not isinstance(entries, list):
            raise LoaderMissingIndexError(f"{path}: 'entries' must be a list")
        return entries
