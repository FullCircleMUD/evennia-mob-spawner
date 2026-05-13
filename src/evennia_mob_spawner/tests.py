# SPDX-License-Identifier: BSD-3-Clause
"""Smoke tests for evennia-mob-spawner.

Proves the package installs, the test runner discovers tests correctly, and
the evennia-yaml-reader dependency is wired up. Real tests land alongside
the spawn-rule pipeline as it is built out.
"""

from django.test import TestCase

import evennia_mob_spawner
from evennia_mob_spawner.log import ms_log


class PackageSmokeTest(TestCase):
    def test_version_present(self):
        self.assertEqual(evennia_mob_spawner.__version__, "0.0.1")


class LogShimSmokeTest(TestCase):
    """ms_log is importable and callable without raising."""

    def test_ms_log_callable_at_default_level(self):
        # Must not raise even if Evennia logger isn't fully bootstrapped
        # in this test context (the shim swallows ImportError silently).
        ms_log("smoke test: default level")

    def test_ms_log_unknown_level_coerced(self):
        # Unknown levels degrade to INFO rather than rejecting — the shim
        # contract is "never raise into the caller."
        ms_log("smoke test: unknown level", level="NONSENSE")

    def test_ms_log_valid_levels(self):
        for level in ("INFO", "WARN", "ERROR"):
            ms_log(f"smoke test: level {level}", level=level)


class YamlReaderDependencyTest(TestCase):
    """Confirms the evennia-yaml-reader dependency resolves in this venv."""

    def test_can_import_reader_primitives(self):
        # The dependency contributes the Reader contract and the typed
        # exception hierarchy that mob-spawner will use for rule fetching.
        from evennia_yaml_reader import (
            GitHubReader,
            LocalReader,
            Reader,
            ReaderError,
            ReaderResult,
        )

        # Sanity: the imported classes are the expected types.
        self.assertTrue(issubclass(GitHubReader, Reader))
        self.assertTrue(issubclass(LocalReader, Reader))
        self.assertTrue(issubclass(ReaderError, Exception))
        # ReaderResult is a dataclass with the documented two fields.
        self.assertEqual(
            set(ReaderResult.__dataclass_fields__.keys()),
            {"raw_bytes", "parsed"},
        )
