# SPDX-License-Identifier: BSD-3-Clause
"""Definitions — parsed contents of a rule-set repo's ``definitions.yaml``.

Read once per command invocation; handed to Finder, Loader, Validator,
and Deployer as the manifest-vocabulary anchor. Fields land here as
concrete decisions get made; this scaffold holds only what the pipeline
needs to flow end-to-end.
"""
from dataclasses import dataclass

from evennia_yaml_reader import Reader

from .errors import DefinitionsError


_DEFINITIONS_PATH = "definitions.yaml"


@dataclass(frozen=True)
class Definitions:
    """Parsed contents of definitions.yaml.

    Attributes:
        levels: Hierarchical level names declared by the consumer, in order.
                Empty tuple means a flat manifest.
        repo_ci_pre_validation:
                Consumer's assertion that CI gates the rule-set repo with
                ``ms-validate`` before merge. When True, ``ms_load`` skips
                whole-repo Tier 1+2 validation and trusts the gate. The
                library does not verify this assertion.
    """

    levels: tuple = ()
    repo_ci_pre_validation: bool = False

    @classmethod
    def from_reader(cls, reader: Reader, path: str = _DEFINITIONS_PATH) -> "Definitions":
        result = reader.read(path)
        return cls.from_dict(result.parsed, source_path=path)

    @classmethod
    def from_dict(cls, data, *, source_path: str = "<dict>") -> "Definitions":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise DefinitionsError(
                f"{source_path}: expected a mapping, got {type(data).__name__}"
            )

        levels = data.get("levels", [])
        if not isinstance(levels, list) or not all(isinstance(x, str) for x in levels):
            raise DefinitionsError(
                f"{source_path}: 'levels' must be a list of strings"
            )

        gating = data.get("repo-ci-pre-validation", False)
        if not isinstance(gating, bool):
            raise DefinitionsError(
                f"{source_path}: 'repo-ci-pre-validation' must be a boolean"
            )

        return cls(levels=tuple(levels), repo_ci_pre_validation=gating)
