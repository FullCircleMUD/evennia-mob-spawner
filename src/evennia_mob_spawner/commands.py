# SPDX-License-Identifier: BSD-3-Clause
"""Library-shipped admin commands.

Conventions for any command shipping from mob-spawner:

- Key is prefixed ``ms_`` so it namespaces cleanly and a stray short
  command name (e.g. ``load``) cannot accidentally invoke library work.
- Locked to ``cmd:superuser()`` — only the actual superuser may invoke.
- Auto-installed by mob-spawner's AppConfig (see apps.py) into
  AccountCmdSet, so the command works both OOC and IC. The consumer
  game does not need to import or wire these manually.

Currently shipped (architecture decision #7's full set):

- ``ms_load``    — exercises the full pipeline (Reader → Definitions →
                   Finder → Loader → Validator → Deployer). Spawn tick
                   loop is a no-op stub at this stage; spawning behaviour
                   lands in a follow-up implementation pass.
- ``ms_stop``    — pause the ticker on matching scripts; state preserved.
- ``ms_restart`` — kick the ticker on matching scripts without re-reading
                   YAML; state preserved (decision #18).
- ``ms_delete`` — remove matching scripts entirely from the DB. No state
                   preserved; full clean slate.
- ``ms_status`` — read-only inspect of matching scripts.

All five share the same scope-query syntax (``all | <level>=<value>``)
and the same async dispatch pattern; the operations differ only in
what they do to the resolved scripts. Scope resolution walks the
manifest via Finder; an empty query (``all``) bypasses the manifest
and operates on every ``MobSpawnerScript`` instance in the DB (which
catches orphan scripts whose source files were removed from the
manifest — see architecture.md "Edge cases").
"""
from evennia.commands.command import Command as BaseCommand
from evennia.utils.utils import run_async

from evennia_yaml_reader import ReaderError

from .config import get_configured_reader
from .definitions import Definitions
from .deployer import Deployer
from .errors import (
    DefinitionsError,
    DeployerError,
    FinderManifestError,
    FinderQueryError,
    LoaderError,
    LoaderInvalidShapeError,
    LoaderMissingEntryError,
    LoaderMissingIndexError,
    ValidatorError,
)
from .finder import Finder
from .loader import Loader
from .log import ms_log
from .validator import Validator


_ALL_TOKEN = "all"
FORCE_VALIDATE_FLAG = "force-validate"


def should_pre_validate(definitions: Definitions, flags: set) -> bool:
    """Decide whether ``ms_load`` should pre-validate the whole repo.

    The ``repo-ci-pre-validation`` setting in ``definitions.yaml`` is
    the consumer's persistent claim ("CI gates my YAML at merge time;
    runtime can trust it"). The ``--force-validate`` operator flag is
    an ad-hoc per-invocation override. Pre-validation runs whenever
    EITHER the setting is False (default safe) OR the flag is present.

    See architecture.md decision #19.

    Args:
        definitions: parsed ``Definitions`` for the content repo.
        flags:       set of operator flags parsed from the command
                     line (e.g. ``{"force-validate"}``).
    """
    force_validate = FORCE_VALIDATE_FLAG in flags
    return (not definitions.repo_ci_pre_validation) or force_validate


def _parse_args(args_str: str) -> tuple[dict, set]:
    """Parse ``all | level=value... [--flag...]`` into ``(query, flags)``.

    Returns:
        query: dict of level → value. Empty dict means the literal
               ``all`` token was supplied (load-everything scope).
        flags: set of flag names (with the leading ``--`` stripped).

    Raises:
        ValueError: if no scope token is present, if a non-flag token
                    is malformed, or if either side of an ``=`` is empty.
    """
    pairs: dict = {}
    flags: set = set()
    positional: list = []

    for token in args_str.split():
        if token.startswith("--"):
            flags.add(token[2:])
        else:
            positional.append(token)

    if not positional:
        raise ValueError(
            "no scope specified — use 'all' or one or more level=value pairs"
        )

    if positional == [_ALL_TOKEN]:
        return pairs, flags

    for token in positional:
        if "=" not in token:
            raise ValueError(f"Argument {token!r} is not of the form key=value")
        key, _, value = token.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(
                f"Argument {token!r}: both key and value must be non-empty"
            )
        pairs[key] = value
    return pairs, flags


def _run_validator(
    messages: list, definitions, load_result, refusal_label: str,
) -> bool:
    """Run a Validator pass over ``load_result`` with ``evennia_runtime=True``.

    Returns True on a clean pass, False if the validator refused.
    Callers should return early on False. The command runs inside
    Evennia, so Tier 3 predicates (typeclass importability,
    ms_at_post_spawn callability + signature) and Tier 4 diagnostics
    (tag-existence WARN logs) both fire.
    """
    validator = Validator(definitions, evennia_runtime=True)
    try:
        validator.validate(load_result)
    except ValidatorError as e:
        messages.extend(validator.messages)
        messages.append(f"ms_load: refusing to deploy — {refusal_label}: {e}")
        ms_log(
            f"ms_load: validation refused — {refusal_label}: {e}",
            level="ERROR",
        )
        for finding in validator.messages:
            ms_log(f"  validator: {finding}", level="INFO")
        return False
    return True


class CmdMsLoad(BaseCommand):
    """Load mob spawn rules from the configured manifest source.

    Usage:
        ms_load all [--force-validate]
        ms_load <level>=<value> [<level>=<value> ...] [--force-validate]

    A bare ``ms_load`` with no scope does nothing — the explicit
    ``all`` keyword is required to deploy the entire rule set. This is
    a deliberate guard rail against an accidental full reload.

    Validation gating: ``definitions.yaml`` carries a
    ``repo-ci-pre-validation`` flag (default ``false``). When false,
    ms_load pre-validates the whole repo before every load — safe but
    expensive at scale. When the consumer has set up CI to gate PRs
    (GitHub branch protection + required ms-validate check), they can
    flip the flag to true and ms_load will skip the whole-repo walk and
    trust the gate. ``--force-validate`` overrides the flag for one
    invocation: pre-validate this run regardless.

    Examples (assuming ``levels: [shard, zone]``):

        ms_load all
            Load every rule-set file in the manifest.

        ms_load shard=shard0
            Load every rule-set file under shard=shard0.

        ms_load shard=shard0 zone=millholm
            Load the single rule-set file.

        ms_load shard=shard0 zone=millholm --force-validate
            Same, but pre-validate the whole repo first regardless of
            the gating setting.
    """

    key = "ms_load"
    locks = "cmd:superuser()"
    help_category = "Mob Spawner"

    def func(self):
        args = (self.args or "").strip()

        if not args:
            self.caller.msg(
                "ms_load: no scope specified. Use `ms_load all` to load "
                "every rule-set file in the manifest, or specify a query "
                "like `ms_load shard=shard0 zone=millholm`."
            )
            return

        try:
            query, flags = _parse_args(args)
        except ValueError as e:
            self.caller.msg(f"ms_load: {e}")
            return

        # Hand the pipeline off to a worker thread. caller.msg() can't
        # be called from the worker safely; the at_return / at_err
        # callbacks fire back on the reactor and flush the collected
        # message list there.
        self.caller.msg("ms_load: running async (gameplay continues)…")
        run_async(
            self._run_pipeline, query, flags,
            at_return=self._on_async_return,
            at_err=self._on_async_err,
        )

    def _run_pipeline(self, query: dict, flags: set) -> list:
        """Worker-thread entrypoint: runs the full pipeline.

        Collects every operator-facing line into a list of messages and
        returns it. ``at_return`` flushes the list via ``caller.msg`` on
        the reactor thread. Errors with operator-meaningful context
        (validation refusal, deploy failure) get appended as messages
        and the function returns normally; only unexpected exceptions
        bubble out for ``at_err`` to handle.
        """
        messages: list = []

        scope_desc = "all" if not query else " ".join(
            f"{k}={v}" for k, v in query.items()
        )
        flag_desc = " ".join(f"--{f}" for f in sorted(flags)) if flags else ""
        ms_log(
            f"ms_load started: scope={scope_desc}"
            + (f" {flag_desc}" if flag_desc else "")
        )

        try:
            reader = get_configured_reader()
        except Exception as e:
            msg = (
                f"ms_load: could not construct reader "
                f"(check MOB_SPAWNER_READER and MOB_SPAWNER_READER_KWARGS): {e}"
            )
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages

        try:
            definitions = Definitions.from_reader(reader)
        except ReaderError as e:
            msg = f"ms_load: could not load definitions.yaml: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages
        except DefinitionsError as e:
            msg = f"ms_load: definitions.yaml is malformed: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages

        try:
            definitions.validate_query(query)
        except DefinitionsError as e:
            msg = f"ms_load: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages

        finder = Finder(reader, definitions)
        loader = Loader(reader, definitions)

        # Decide whether to pre-validate the whole repo. The setting is
        # the consumer's persistent claim ("I have CI gating"); the
        # flag is an ad-hoc per-invocation override. Pre-validation runs
        # whenever EITHER the setting is False (default safe) OR the
        # flag is present. See architecture.md decision #19.
        pre_validate = should_pre_validate(definitions, flags)

        messages.append("ms_load: starting validation")
        ms_log(
            "ms_load: validation started "
            f"({'pre-validate whole repo' if pre_validate else 'scope-only'})"
        )

        if pre_validate:
            try:
                load_result = loader.load(finder.find())
            except FinderManifestError as e:
                msg = f"ms_load: manifest error during pre-validation: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages
            except (LoaderMissingIndexError, LoaderMissingEntryError,
                    LoaderInvalidShapeError) as e:
                msg = f"ms_load: pre-validation load failed: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages
            except ReaderError as e:
                msg = f"ms_load: read error during pre-validation: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages

            if not _run_validator(
                messages, definitions, load_result,
                "pre-validation failed",
            ):
                return messages

            # Re-load with the requested scope (whole-repo pre-validate
            # succeeded; now load the subset to deploy).
            try:
                load_result = loader.load(finder.find(query))
            except (FinderManifestError, FinderQueryError) as e:
                msg = f"ms_load: scope find error after pre-validation: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages
        else:
            try:
                found = finder.find(query)
            except FinderQueryError as e:
                msg = f"ms_load: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages
            except FinderManifestError as e:
                msg = f"ms_load: manifest error: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages

            try:
                load_result = loader.load(found)
            except (LoaderMissingIndexError, LoaderMissingEntryError,
                    LoaderInvalidShapeError) as e:
                msg = f"ms_load: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages
            except ReaderError as e:
                msg = f"ms_load: read error during loading: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages

            if not _run_validator(
                messages, definitions, load_result, "validation failed",
            ):
                return messages

        n_files = len(load_result.rule_sets)
        n_rules = sum(len(rules) for rules in load_result.rule_sets.values())
        messages.append(
            f"ms_load: validation complete "
            f"({n_files} file{'' if n_files == 1 else 's'}, "
            f"{n_rules} rule{'' if n_rules == 1 else 's'})"
        )

        # Deploy stage: race-safe upsert. The Deployer drains any
        # in-flight tick (decision #13's stop_when_safe + force_stop
        # protocol), swaps db.spawn_table, and resumes the ticker —
        # all per-file, preserving cooldown/observation state for
        # rules that survive the swap.
        messages.append("ms_load: starting deployment")
        ms_log(f"ms_load: validation complete ({n_files} files, {n_rules} rules)")

        deployer = Deployer(definitions)
        try:
            deployer.deploy(load_result)
        except DeployerError as e:
            msg = f"ms_load: deploy failed: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages

        messages.append(
            f"ms_load: deployment complete "
            f"({n_files} script{'' if n_files == 1 else 's'} upserted, "
            f"{n_rules} rule{'' if n_rules == 1 else 's'} active)"
        )
        ms_log(
            f"ms_load: deploy complete "
            f"({n_files} scripts upserted, {n_rules} rules total)"
        )
        return messages

    def _on_async_return(self, messages: list) -> None:
        """Reactor-thread callback: flush every message to the operator."""
        for msg in messages or []:
            self.caller.msg(msg)

    def _on_async_err(self, failure) -> None:
        """Reactor-thread callback for unexpected pipeline exceptions.

        Any error the pipeline catches itself goes to ``messages`` and
        comes back through ``_on_async_return``. This handler only fires
        for exceptions that escape the pipeline (e.g. a Loader bug, a
        programming error). The Twisted ``Failure`` carries the
        traceback; the full text goes to ``mob_spawner.log`` so a later
        operator can read it, the operator at the prompt gets a single
        line.
        """
        ms_log(
            f"ms_load: unexpected pipeline error: {failure.getTraceback()}",
            level="ERROR",
        )
        self.caller.msg(
            "ms_load: pipeline crashed with an unexpected error; "
            "see mob_spawner.log for the traceback."
        )


# ---------------------------------------------------------------------------
# Scope resolution for ms_stop / ms_restart / ms_delete / ms_status.
#
# Empty query → all MobSpawnerScript instances (catches orphans —
# scripts whose source files were removed from the manifest).
# Non-empty query → Finder walks the manifest; the scope resolves to
# either an exact file path (kind=file) or a folder path (kind=folder).
# Scripts are matched by ``db_key`` (which is the file path the
# Deployer used when creating them).
# ---------------------------------------------------------------------------


def _script_state(script) -> str:
    """Return ``"active"`` / ``"paused"`` / ``"stopped"`` for ``script``.

    Evennia's ``script.is_active`` is True for BOTH running and paused
    scripts — it flips to False only on ``stop()`` (or never-started).
    The actual paused state is tracked via ``db._paused_time`` (set by
    ``pause()``, cleared by ``unpause()`` / ``_stop_task()``). Without
    this discrimination, ``ms_status`` reports a paused script as
    "active" and ``ms_restart`` sees it as "already running" and
    doesn't unpause — the bug fixed alongside this helper.
    """
    if not script.is_active:
        return "stopped"
    if script.db._paused_time:
        return "paused"
    return "active"


def _resolve_scope_to_scripts(query: dict, reader, definitions):
    """Return ``(scripts, scope_description)`` for an operator query.

    ``scripts`` is a list of ``MobSpawnerScript`` instances matching
    the scope. ``scope_description`` is a short string for log lines
    and operator output (``"all"``, the file path, or
    ``"under <folder>"``).

    May raise ``FinderQueryError`` / ``FinderManifestError`` if the
    manifest can't be walked — callers should catch and surface as
    operator messages.
    """
    from .script import MobSpawnerScript

    if not query:
        return list(MobSpawnerScript.objects.all()), "all"

    finder = Finder(reader, definitions)
    found = finder.find(query)

    if found.kind == "file":
        scripts = list(MobSpawnerScript.objects.filter(db_key=found.path))
        return scripts, found.path

    # folder: match scripts whose db_key falls under the folder path.
    # Root folder (path == "") shouldn't happen here — a non-empty
    # query that resolves to root would be malformed — but if it does,
    # match everything for safety.
    if not found.path:
        scripts = list(MobSpawnerScript.objects.all())
        return scripts, "all"

    prefix = f"{found.path}/"
    scripts = list(MobSpawnerScript.objects.filter(db_key__startswith=prefix))
    return scripts, f"under {found.path}"


class _MsOperateBase(BaseCommand):
    """Shared scaffold for stop / restart / delete / status commands.

    Subclasses define ``key``, ``op_label`` (for log + messages),
    ``op_help`` (the docstring shown in-game), and override
    ``apply(script, messages)`` to do the per-script work.
    """

    locks = "cmd:superuser()"
    help_category = "Mob Spawner"
    op_label = "op"

    def func(self):
        args = (self.args or "").strip()
        if not args:
            self.caller.msg(
                f"{self.key}: no scope specified. Use `{self.key} all` for "
                f"every script, or a query like `{self.key} shard=shard0`."
            )
            return

        try:
            query, flags = _parse_args(args)
        except ValueError as e:
            self.caller.msg(f"{self.key}: {e}")
            return

        # Reader is needed only when the query is non-empty (manifest
        # walk). For `all`, we go straight to the DB.
        self.caller.msg(f"{self.key}: running async (gameplay continues)…")
        run_async(
            self._run, query, flags,
            at_return=self._on_async_return,
            at_err=self._on_async_err,
        )

    def _run(self, query: dict, flags: set) -> list:
        messages: list = []
        ms_log(f"{self.key} started: scope={self._describe_scope(query)}")

        try:
            scripts, scope_desc = self._resolve(query)
        except Exception as e:
            msg = f"{self.key}: scope resolution failed: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages

        if not scripts:
            messages.append(f"{self.key}: no matching scripts in scope {scope_desc!r}")
            ms_log(f"{self.key}: scope {scope_desc!r} matched 0 scripts")
            return messages

        messages.append(
            f"{self.key}: scope {scope_desc!r} matched "
            f"{len(scripts)} script{'' if len(scripts) == 1 else 's'}"
        )

        for script in scripts:
            try:
                self.apply(script, messages)
            except Exception as e:
                msg = f"  {script.db_key}: {self.op_label} failed: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")

        return messages

    def _resolve(self, query: dict):
        """Resolve scope query to scripts. Override to skip Reader for ``all``."""
        if not query:
            from .script import MobSpawnerScript
            return list(MobSpawnerScript.objects.all()), "all"

        try:
            reader = get_configured_reader()
            definitions = Definitions.from_reader(reader)
        except (ReaderError, DefinitionsError) as e:
            raise RuntimeError(f"manifest unavailable: {e}") from e

        return _resolve_scope_to_scripts(query, reader, definitions)

    @staticmethod
    def _describe_scope(query: dict) -> str:
        if not query:
            return "all"
        return " ".join(f"{k}={v}" for k, v in query.items())

    def apply(self, script, messages: list) -> None:
        """Per-script operation. Override in subclasses."""
        raise NotImplementedError

    def _on_async_return(self, messages: list) -> None:
        for msg in messages or []:
            self.caller.msg(msg)

    def _on_async_err(self, failure) -> None:
        ms_log(
            f"{self.key}: unexpected error: {failure.getTraceback()}",
            level="ERROR",
        )
        self.caller.msg(
            f"{self.key}: command crashed unexpectedly; "
            f"see mob_spawner.log for the traceback."
        )


class CmdMsStop(_MsOperateBase):
    """Stop the ticker on matching scripts. State preserved.

    Usage:
        ms_stop all
        ms_stop <level>=<value> [<level>=<value> ...]

    Resumable via ``ms_restart`` or ``ms_load``. The script and its
    bookkeeping (cooldown clocks, observed counts) remain in the DB.
    """

    key = "ms_stop"
    op_label = "stop"

    def apply(self, script, messages: list) -> None:
        state = _script_state(script)
        if state != "active":
            messages.append(f"  {script.db_key}: already {state}")
            return
        script.pause()
        messages.append(f"  {script.db_key}: stopped")
        ms_log(f"ms_stop: {script.db_key} paused")


class CmdMsRestart(_MsOperateBase):
    """Kick the ticker on matching scripts. YAML not re-read; state preserved.

    Usage:
        ms_restart all
        ms_restart <level>=<value> [<level>=<value> ...]

    Recovery action for stopped or stuck scripts (architecture
    decision #18). Sits between ``ms_status`` (diagnose) and
    ``ms_load`` (full rule refresh) on the escalation ladder.
    """

    key = "ms_restart"
    op_label = "restart"

    def apply(self, script, messages: list) -> None:
        state = _script_state(script)
        if state == "active":
            messages.append(f"  {script.db_key}: already active")
            return
        if state == "paused":
            script.unpause()
            messages.append(f"  {script.db_key}: restarted (was paused)")
            ms_log(f"ms_restart: {script.db_key} unpaused")
            return
        # stopped — needs start(), not unpause(). Decision #18 says
        # ms_restart kicks the ticker on an existing script regardless
        # of how it got stopped.
        script.start()
        messages.append(f"  {script.db_key}: restarted (was stopped)")
        ms_log(f"ms_restart: {script.db_key} started")


class CmdMsDelete(_MsOperateBase):
    """Delete matching scripts entirely from the DB. State lost.

    Usage:
        ms_delete all
        ms_delete <level>=<value> [<level>=<value> ...]

    Full clean slate. Cooldown clocks, observation history,
    everything goes. Use when ``ms_stop`` is not enough (e.g. when
    you need to reset a script's accumulated state, or when the
    rule-set file has been removed from the manifest and the orphan
    script must be cleaned up).
    """

    key = "ms_delete"
    op_label = "delete"

    def apply(self, script, messages: list) -> None:
        key = script.db_key
        script.delete()
        messages.append(f"  {key}: deleted")
        ms_log(f"ms_delete: {key} removed")


class CmdMsStatus(_MsOperateBase):
    """Read-only inspect of matching scripts.

    Usage:
        ms_status all
        ms_status <level>=<value> [<level>=<value> ...]

    Reports one line per script: path, active/paused state, rule
    count, tick interval, time-since-tick (or next-tick estimate).
    """

    key = "ms_status"
    op_label = "inspect"

    def apply(self, script, messages: list) -> None:
        state = _script_state(script)
        rule_count = len(script.db.spawn_table or [])
        rule_suffix = "" if rule_count == 1 else "s"
        line = (
            f"  {script.db_key}: {state}, "
            f"{rule_count} rule{rule_suffix}, interval={script.interval}s"
        )
        # ``next=`` only makes sense when the ticker is actively scheduled.
        # Paused / stopped scripts have no next-tick time.
        if state == "active":
            try:
                next_in = script.time_until_next_repeat()
                if next_in is not None:
                    line += f", next={int(next_in)}s"
            except Exception:
                pass
        messages.append(line)
