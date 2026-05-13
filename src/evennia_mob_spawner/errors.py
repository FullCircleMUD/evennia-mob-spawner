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


class LoaderError(Exception):
    """Base class for Loader failures."""


class ValidatorError(Exception):
    """Base class for Validator failures."""


class DeployerError(Exception):
    """Base class for terminal-stage (deployer / upsert) failures."""
