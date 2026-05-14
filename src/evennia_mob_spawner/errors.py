# SPDX-License-Identifier: BSD-3-Clause
"""Exception types raised by evennia-mob-spawner.

Reader exceptions live in ``evennia-yaml-reader``; this module declares
only the library's own pipeline-stage error types. Subtypes will be added
under each base as concrete failure modes appear.
"""


class DefinitionsError(Exception):
    """definitions.yaml is malformed or violates the schema."""


class FinderError(Exception):
    """Base class for Finder failures."""


class FinderManifestError(FinderError):
    """An index.yaml is malformed or missing where the Finder expected it."""


class FinderQueryError(FinderError):
    """Operator query is invalid: value not found at level in the manifest."""


class LoaderError(Exception):
    """Base class for Loader failures."""


class LoaderMissingEntryError(LoaderError):
    """An index pointed at a file the Reader could not fetch."""


class LoaderMissingIndexError(LoaderError):
    """A folder is missing its index.yaml, or the index is malformed."""


class LoaderInvalidShapeError(LoaderError):
    """A leaf rule-set file does not match the required top-level shape."""


class ValidatorError(Exception):
    """Base class for Validator failures."""


class DeployerError(Exception):
    """Base class for terminal-stage (deployer / upsert) failures."""
