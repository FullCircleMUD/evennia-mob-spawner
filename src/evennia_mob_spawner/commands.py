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
                   Finder → Loader → Validator → Deployer) and upserts the
                   persistent ``MobSpawnerScript`` whose tick loop maintains
                   the population (observe → cooldown → spawn).
- ``ms_stop``    — pause the ticker on matching scripts; state preserved.
- ``ms_restart`` — kick the ticker on matching scripts without re-reading
                   YAML; state preserved (decision #18).
- ``ms_delete`` — remove matching scripts entirely from the DB. No state
                   preserved; full clean slate.
- ``ms_status`` — read-only inspect of matching scripts.
- ``ms_spawn_report`` — population census: per-rule current vs target,
                   grouped by ``area_tag`` within each script. Separate
                   from ``ms_status`` by design — ``ms_status`` answers
                   "what is the spawner doing?" (script state, ticks,
                   cooldowns); this command answers "what mobs exist?".

All six share the same scope-query syntax (``all | <level>=<value>``)
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

# Optional integration with evennia-shards. When the shards library is
# installed and configured, ``preserve_tenant_context`` captures the
# active tenant at wrap time and re-applies it inside the deferred
# worker thread — without this, ObjectDB rows (the per-room
# MobSpawnerScript script-host objects) created in the worker would
# land ``shard_id=NULL`` because multitenant's threading.local tenant
# doesn't propagate across the ``run_async`` thread spawn. When the
# shards library isn't installed, the import fails and we fall back
# to an identity passthrough that's a no-op. See
# ``evennia-shards/docs/tenancy.md`` for the helper's contract.
try:
    from evennia_shards import preserve_tenant_context
except ImportError:
    def preserve_tenant_context(fn):
        return fn

from .config import (
    SHARD_LEVEL,
    active_shard_id,
    get_configured_reader,
    is_shard_process,
)
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


def check_shard_scope(query: dict, command_key: str = "ms_load") -> str | None:
    """Return an operator-facing refusal, or ``None`` if the scope is fine.

    Three refusals, all no-ops off a sharded deployment:

    - the whole-world scope, which spans every shard's rule sets and so can
      only ever be partly correct on one process;
    - a query that doesn't name the shard level at all;
    - a query naming a shard this process doesn't own. The router's
      ``SHARD_ID`` is mandated to be ``"router"``, so this rejects the
      command there without needing a role check of its own.

    Applies to any command whose effect is confined to the process running
    it. Deploying is one such: ``ms_load`` only reaches this process's
    scripts. So is anything touching a script's live ``ndb._task``, which
    exists only where the script runs — a broad scope there would stop or
    remove one shard's share while reporting a clean sweep of all of them.
    """
    shard_id = active_shard_id()
    if shard_id is None:
        return None

    if not query:
        return (
            f"{command_key}: `{_ALL_TOKEN}` is not available on a sharded "
            f"deployment. Run this one shard at a time."
        )

    if next(iter(query)) != SHARD_LEVEL:
        return f"{command_key}: the query must start with '{SHARD_LEVEL}='."

    if query[SHARD_LEVEL] != shard_id:
        return f"{command_key}: you can only run this from the shard it acts on."

    return None


def check_cluster_wide_scope(command_key: str) -> str | None:
    """Refuse a whole-world command on a process that can only see part of it.

    Some commands answer a question about the entire world rather than about
    one shard's slice. Under the tenancy auto-filter a shard process sees
    only its own rows, so the answer it produces is not a partial answer
    flagged as such — it is a confident, wrongly-scoped one. A shard
    reporting on another shard's script counts zero of a live population.

    **Why this collapses to "refuse only on a shard".** There are three
    roles, and only one of them narrows the ORM:

    - *router* — runs unscoped, so it sees every shard's rows plus any
      orphaned ones. The only process that can answer for the whole world.
    - *monolith* — a non-sharded install by definition; no shard context is
      ever set, and the single process is the whole world.
    - *shards not installed* — no auto-filter exists at all.

    So rather than enumerating "router or monolith or standalone", the check
    is the single negative case: this process is a shard. Anything else can
    see the whole world, whether because it is unscoped or because there is
    only one world to see.
    """
    if not is_shard_process():
        return None

    return (
        f"{command_key}: reports across the whole world, so it can only be "
        f"run from the router."
    )


def check_shard_levels(definitions) -> str | None:
    """Return a refusal if the shard level was never adopted, else ``None``.

    Checked once ``definitions.yaml`` is parsed, because that is the first
    point the declared levels are known. Nothing else catches this: a
    consumer who co-installs shards but keeps their own level names has a
    query that validates perfectly against their own declarations, so the
    breach would otherwise surface only as a confusing "not a declared
    level" error. No-op off a sharded deployment.
    """
    if active_shard_id() is None:
        return None

    levels = definitions.levels
    if not levels or levels[0] != SHARD_LEVEL:
        return f"ms_load: the first level in definitions.yaml must be '{SHARD_LEVEL}'."
    return None


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

    On a sharded deployment (the shards library installed and the role
    not ``monolith``), the first declared level must be ``shard`` and its
    value must match the shard this process is running as — a rule set
    can only be deployed from the process that owns it. ``ms_load all``
    is refused there; deploy one shard at a time.
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

        # Refuse before dispatch on a sharded deployment: a rule set may
        # only be deployed from the process that owns it. Synchronous so
        # the refusal reaches the operator directly rather than through
        # the async callbacks. No-op when not sharded.
        refusal = check_shard_scope(query)
        if refusal:
            self.caller.msg(refusal)
            return

        # Hand the pipeline off to a worker thread. caller.msg() can't
        # be called from the worker safely; the at_return / at_err
        # callbacks fire back on the reactor and flush the collected
        # message list there.
        #
        # The pipeline callable is wrapped with preserve_tenant_context
        # so any shards-tenant active on the reactor thread carries
        # into the worker — without it, every Script row created in
        # the worker would land unstamped. No-op when shards isn't
        # installed (see top-of-file import).
        self.caller.msg(f"ms_load {args} : running async (gameplay continues)…")
        run_async(
            preserve_tenant_context(self._run_pipeline), query, flags,
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

        # The shard level can only be checked once definitions.yaml is
        # parsed. Ahead of validate_query so a consumer who never adopted
        # the mandate gets told that, rather than a generic "not a
        # declared level" for the shard key they were required to pass.
        refusal = check_shard_levels(definitions)
        if refusal:
            messages.append(refusal)
            ms_log(refusal, level="ERROR")
            return messages

        try:
            definitions.validate_query(query)
        except DefinitionsError as e:
            msg = f"ms_load: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages

        # Ahead of reading a single rule-set file: a stalled script in the
        # scope makes the whole load moot, so a stall costs a manifest walk
        # rather than a full load-and-validate of the repo.
        try:
            refusal = check_scope_not_stalled(query, reader, definitions)
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
        if refusal:
            messages.append(refusal)
            ms_log(" ".join(refusal.split()), level="WARN")
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


def _is_stalled(script) -> bool:
    """True if ``script`` is marked active but carries no live tick.

    A script reaches this state when its ``ndb._task`` is lost without the
    pause marker being written — ``_pause_task`` only records ``_paused_time``
    when a task exists, so a task that disappears any other way leaves
    ``is_active`` True with nothing scheduled. ``_script_state`` reports that
    as "active", and Evennia's own recovery path cannot escape it:
    ``_unpause_task`` is gated on ``_paused_time``, so the boot walk, ``pause()``
    and ``unpause()`` are all no-ops. Only ``start()`` attaches a fresh task.

    Reads ``ndb._task``, which exists only in the process running the script,
    so this is meaningful **only** on a ``shard_scoped`` command. Anywhere
    else it reports every foreign script as stalled.
    """
    return (
        bool(script.is_active)
        and not script.db._paused_time
        and not script.ndb._task
    )


_STATE_COLOURS = {
    "active": "|g",   # ticking
    "paused": "|y",   # not running, and someone meant it
    "stopped": "|y",
    "stalled": "|r",  # not running, and nobody meant it
}


def _colour_state(state: str) -> str:
    """Wrap a state word in an Evennia colour code.

    Scanning a list of scripts for the one that is wrong is the job the
    operator actually has, so the state carries the colour rather than the
    whole line. Unknown states pass through uncoloured.
    """
    colour = _STATE_COLOURS.get(state)
    return f"{colour}{state}|n" if colour else state


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


def check_scope_not_stalled(query: dict, reader, definitions) -> str | None:
    """Return an operator-facing refusal if any script in scope is stalled.

    ``ms_load``'s job is rule content; getting a dead ticker going again is
    ``ms_restart``'s. Deploying onto a stalled script swaps its rules and
    reports a clean deploy while the script stays dead — so an operator
    reaching for ``ms_load`` to troubleshoot a stall is told which command
    they actually want, rather than being handed a success message.

    Refuses the whole scope rather than skipping the stalled files. A
    partial deploy reported as complete is the failure this guards against,
    one level down. Naming every stalled script means one ``ms_restart``
    over the same scope clears them all before the load is re-run.

    Reads ``ndb._task`` via :func:`_is_stalled`, which is only meaningful in
    the process running the script — sound here because ``ms_load`` already
    refuses to run anywhere but the shard owning the scope.

    Raises:
        FinderQueryError / FinderManifestError: if the manifest can't be
            walked — callers surface these as operator messages.
    """
    scripts, _scope = _resolve_scope_to_scripts(query, reader, definitions)
    stalled = sorted(s.db_key for s in scripts if _is_stalled(s))
    if not stalled:
        return None

    scope_args = " ".join(f"{k}={v}" for k, v in query.items()) or _ALL_TOKEN
    listing = "\n".join(f"  {key}" for key in stalled)
    return (
        f"ms_load: refusing — {len(stalled)} "
        f"script{'' if len(stalled) == 1 else 's'} stalled "
        f"(active but not ticking):\n{listing}\n"
        f"Run `ms_restart {scope_args}` to get them ticking, "
        f"then re-run ms_load to deploy."
    )


class _MsOperateBase(BaseCommand):
    """Shared scaffold for stop / restart / delete / status commands.

    Subclasses define ``key``, ``op_label`` (for log + messages),
    ``op_help`` (the docstring shown in-game), and override
    ``apply(script, messages)`` to do the per-script work.
    """

    locks = "cmd:superuser()"
    help_category = "Mob Spawner"
    op_label = "op"

    cluster_wide_only = False
    """Set on subclasses whose answer covers the whole world, not one shard.

    Such a command is refused on a shard process — see
    :func:`check_cluster_wide_scope`.
    """

    shard_scoped = False
    """Set on subclasses whose effect stops at the process running them.

    Evennia keeps a script's ``LoopingCall`` in ``ndb._task``, which exists
    only in the process running it, so an operation reaching for that task
    can only act on this shard's share:

    - ``pause()`` reads ``ndb._task`` and, finding none, writes nothing at
      all — run from elsewhere it silently does nothing while reporting
      success.
    - ``delete()`` stops only the local task before removing the shared
      row, leaving the owning shard ticking a script that no longer exists.

    So these carry the same scope gate as ``ms_load``: the whole-world
    scope is refused, and the query must name this shard. Stopping "all"
    from one shard would halt its own scripts while every other shard kept
    ticking — a partial action reported as a complete one.
    """

    def func(self):
        args = (self.args or "").strip()
        if not args:
            self.caller.msg(
                f"{self.key}: no scope specified. Use `{self.key} all` for "
                f"every script, or a query like `{self.key} shard=shard0`."
            )
            return

        if self.cluster_wide_only:
            refusal = check_cluster_wide_scope(self.key)
            if refusal:
                self.caller.msg(refusal)
                return

        try:
            query, flags = _parse_args(args)
        except ValueError as e:
            self.caller.msg(f"{self.key}: {e}")
            return

        if self.shard_scoped:
            refusal = check_shard_scope(query, self.key)
            if refusal:
                self.caller.msg(refusal)
                return

        # Reader is needed only when the query is non-empty (manifest
        # walk). For `all`, we go straight to the DB.
        #
        # preserve_tenant_context wrap mirrors the ms_load site above —
        # any shards-tenant on the reactor thread carries into the
        # worker. No-op when shards isn't installed.
        self.caller.msg(f"{self.key} {args} : running async (gameplay continues)…")
        run_async(
            preserve_tenant_context(self._run), query, flags,
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

    # pause() acts on ndb._task, which exists only where the script runs.
    shard_scoped = True

    def apply(self, script, messages: list) -> None:
        state = _script_state(script)
        if state != "active":
            messages.append(f"  {script.db_key}: already {state}")
            return
        if _is_stalled(script):
            # pause() would be a no-op here (see _is_stalled) and reporting
            # "stopped" off the back of it puts a false line in the log. Say
            # what is actually true and point at the one command that escapes.
            messages.append(
                f"  {script.db_key}: not ticking — nothing to stop "
                f"(run ms_restart to recover)"
            )
            ms_log(
                f"ms_stop: {script.db_key} found stalled (active, no task)",
                level="WARN",
            )
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

    # unpause() and start() both attach ndb._task on the calling process.
    shard_scoped = True

    def apply(self, script, messages: list) -> None:
        state = _script_state(script)
        # Ahead of the "active" check, because a stalled script reads as
        # active — and unpause() cannot reach it, having no pause marker
        # to work from. start() attaches a task unconditionally, so it is
        # the only route out (see _is_stalled).
        if _is_stalled(script):
            script.start()
            messages.append(
                f"  {script.db_key}: restarted "
                f"(was stalled — active but not ticking)"
            )
            ms_log(
                f"ms_restart: {script.db_key} restarted from stalled "
                f"(active, no task)",
                level="WARN",
            )
            self._confirm_ticking(script, messages)
            return
        if state == "active":
            messages.append(f"  {script.db_key}: already active")
            return
        if state == "paused":
            script.unpause()
            messages.append(f"  {script.db_key}: restarted (was paused)")
            ms_log(f"ms_restart: {script.db_key} unpaused")
            self._confirm_ticking(script, messages)
            return
        # stopped — needs start(), not unpause(). Decision #18 says
        # ms_restart kicks the ticker on an existing script regardless
        # of how it got stopped.
        script.start()
        messages.append(f"  {script.db_key}: restarted (was stopped)")
        ms_log(f"ms_restart: {script.db_key} started")
        self._confirm_ticking(script, messages)

    @staticmethod
    def _confirm_ticking(script, messages: list) -> None:
        """Report a restart that didn't take, rather than claiming success.

        Every route into a stalled script is a silent no-op — that is what
        makes the state expensive to diagnose. A recovery command that
        reports success without one is the same trap one step further on,
        so the claim is checked before it is made.
        """
        if script.ndb._task:
            return
        messages.append(
            f"  {script.db_key}: ...but no tick attached — "
            f"see mob_spawner.log"
        )
        ms_log(
            f"ms_restart: {script.db_key} restarted but no task attached",
            level="ERROR",
        )


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

    # delete() stops only the local task before removing the shared row.
    shard_scoped = True

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

    Reports one line per script: path, state, rule count, tick
    interval, and a next-tick estimate for a script that is ticking.
    """

    key = "ms_status"
    op_label = "inspect"

    # Read-only, but still gated: telling a stalled script from a healthy
    # one means reading ndb._task, which exists only in the process running
    # the script. From anywhere else the task is absent rather than gone,
    # and the command would report every foreign script as stalled. Seeing
    # a stall at a glance is worth more than answering for every shard from
    # one place — see architecture.md decision #26.
    shard_scoped = True

    def apply(self, script, messages: list) -> None:
        state = "stalled" if _is_stalled(script) else _script_state(script)
        rule_count = len(script.db.spawn_table or [])
        rule_suffix = "" if rule_count == 1 else "s"
        line = (
            f"  {script.db_key}: {_colour_state(state)}, "
            f"{rule_count} rule{rule_suffix}, interval={script.interval}s"
        )
        # ``next=`` only makes sense when the ticker is actively scheduled.
        # Paused / stopped / stalled scripts have no next-tick time.
        if state == "active":
            try:
                next_in = script.time_until_next_repeat()
                if next_in is not None:
                    line += f", next={int(next_in)}s"
            except Exception:
                pass
        messages.append(line)


class CmdMsSpawnReport(_MsOperateBase):
    """Population census of mobs spawned by matching scripts.

    Usage:
        ms_spawn_report all
        ms_spawn_report <level>=<value> [<level>=<value> ...]

    For each script in scope, walks the rule table and emits per-rule
    current-vs-target counts, grouped by ``area_tag``. Per-area
    subtotals and a per-script total are appended. Under-target rules
    are marked with a trailing ``*`` for scanability.

    Counts are live: each rule does a tag-indexed ``COUNT`` query
    (``_count_living``) at report time. State of the script
    (active / paused / stopped) is shown as context but does NOT filter
    which scripts are reported — a paused script with ``current=0`` is
    exactly the kind of thing the report is meant to surface.

    The report deliberately answers only "what mobs exist?". For
    "why is this rule under target?" (cooldown state, etc.) use
    ``ms_status``. Splitting the two keeps each output focused.
    """

    key = "ms_spawn_report"
    op_label = "report"

    # A census of the whole world, not of one shard's slice. On a shard the
    # auto-filter would silently narrow the counts — a shard reporting on
    # another shard's script counts zero of a live population. The router
    # sees every row, including any left unstamped.
    cluster_wide_only = True

    def apply(self, script, messages: list) -> None:
        state = _script_state(script)
        rules = script.db.spawn_table or []

        if not rules:
            messages.append(f"  {script.db_key}: {state}, no rules")
            return

        messages.append(f"  {script.db_key}: {state}")

        # Group rules by area_tag, preserving rule order within each
        # group. Python dicts preserve insertion order, which we want
        # here so author-controlled rule order in YAML carries through.
        by_area: dict = {}
        for rule in rules:
            by_area.setdefault(rule["area_tag"], []).append(rule)

        script_target = 0
        script_current = 0
        for area_tag, area_rules in by_area.items():
            messages.append(f"    {area_tag}:")
            area_target = 0
            area_current = 0
            for rule in area_rules:
                target = rule["target"]
                try:
                    current = script._count_living(rule)
                except Exception as e:
                    # Decision #14 sibling: per-rule errors don't kill
                    # the whole report; log and surface the failure.
                    msg = (
                        f"      rule_id={rule.get('rule_id')} "
                        f"count failed: {e}"
                    )
                    messages.append(msg)
                    ms_log(
                        f"ms_spawn_report: {script.db_key} "
                        f"rule_id={rule.get('rule_id')}: {e}",
                        level="ERROR",
                    )
                    continue

                marker = "  *" if current < target else ""
                messages.append(
                    f"      rule_id={rule['rule_id']} "
                    f"key={rule['key']!r} "
                    f"target={target} current={current}{marker}"
                )
                area_target += target
                area_current += current

            messages.append(
                f"      subtotal: {area_current}/{area_target}"
            )
            script_target += area_target
            script_current += area_current

        messages.append(f"    totals: {script_current}/{script_target}")
