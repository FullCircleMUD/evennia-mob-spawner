# SPDX-License-Identifier: BSD-3-Clause
"""evennia-mob-spawner — declarative YAML-driven mob spawn system for Evennia.

Top-level package exports the symbols a consumer is expected to import
directly. Scope grows as pipeline stages move from scaffold to real
implementation — current contents are the implemented surface only;
scaffold-stage stages (Finder / Loader / Validator / Deployer) and
their error types are reachable via their submodules but not promoted
here until they have real behaviour.
"""

from evennia_yaml_reader import (
    GitHubReader,
    LocalReader,
    Reader,
    ReaderAuthError,
    ReaderError,
    ReaderNetworkError,
    ReaderNotFoundError,
    ReaderParseError,
    ReaderResult,
)

from .config import get_configured_reader, get_reader_class
from .definitions import Definitions
from .errors import DefinitionsError

__version__ = "0.1.2"

__all__ = [
    "__version__",
    # yaml-reader passthroughs
    "Reader",
    "ReaderResult",
    "GitHubReader",
    "LocalReader",
    "ReaderError",
    "ReaderAuthError",
    "ReaderNotFoundError",
    "ReaderNetworkError",
    "ReaderParseError",
    # settings dispatch
    "get_reader_class",
    "get_configured_reader",
    # definitions
    "Definitions",
    "DefinitionsError",
]
