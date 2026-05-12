# Progress

Running log of milestones with links to evidence. Reverse chronological — newest first.

## 2026-05-12 (latest)

- **Repository bootstrapped.** LIBRARY_STANDARDS scaffold in place: `pyproject.toml`, `runtests.py`, `tests/test_settings.py` + `tests/urls.py`, `src/evennia_mob_spawner/__init__.py` (version 0.0.1), smoke tests, `CLAUDE.md`, `README.md`, `DESIGN/INDEX.md`, `DESIGN/progress.md`, `DESIGN/documentation-structure.md`, `DESIGN/archive/`.

  Tests use Django's test runner via `runtests.py` (standard LIBRARY_STANDARDS pattern — the library will depend on Evennia at runtime once code lands).

- **`evennia-yaml-reader` wired in as a dependency.** Declared in `pyproject.toml` so a consumer install gets it transitively. Smoke test `YamlReaderDependencyTest.test_can_import_reader_primitives` verifies the dependency resolves in the venv: `GitHubReader`, `LocalReader`, `Reader`, `ReaderError`, and `ReaderResult` all importable from `evennia_yaml_reader`. Confirms the library can lean on the shared Reader infrastructure rather than duplicating it.

  Context: the Reader was extracted from `evennia-world-builder` into `evennia-yaml-reader` so that mob-spawner (and future declarative-content libraries) can share it. Settings dispatch — choosing *which* Reader to instantiate at runtime, and with which kwargs — will be a mob-spawner-side concern, named under its own setting keys (e.g. `MOB_SPAWNER_READER`, `MOB_SPAWNER_READER_KWARGS`) when that machinery lands.
