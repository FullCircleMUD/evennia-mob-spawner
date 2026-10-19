# SPDX-License-Identifier: BSD-3-Clause
"""Library-shipped admin commands.

Conventions for any command shipping from mob-spawner:

- Key is prefixed ``ms_`` so it namespaces cleanly and a stray short
  command name (e.g. ``load``) cannot accidentally invoke library work.
- Locked to ``cmd:superuser()`` — only the actual superuser may invoke.
- Auto-installed by mob-spawner's AppConfig (see apps.py) into
  AccountCmdSet, so the command works both OOC and IC. The consumer
  game does not need to import or wire these manually.

Shipped:

- ``ms_load``   — runs the full pipeline (Reader → Definitions → Finder →
                  Loader → Validator → Deployer), writes the rule rows, and
                  swaps the in-memory table the tick reads.
- ``ms_status`` — the spawner's state, then one row per rule with its live
                  count against its target.
- ``ms_start`` / ``ms_stop`` — turn the spawner on or off. The setting
                  persists across restarts.
- ``ms_delete`` — remove one rule's row by id. Its live mobs stay.
"""
# Both unavoidable for a module whose job is in-game commands. An Evennia
# command is one by inheritance — there is no duck-typed alternative the
# cmdhandler will accept.
from evennia.commands.command import Command as BaseCommand

# The deploy pipeline fetches YAML over the network, and doing that on the
# reactor thread would freeze every connected player for the duration.
from evennia.utils.utils import run_async

from evennia_yaml_reader import ReaderError

from .config import (
    FORCE_VALIDATE_FLAG,
    SHARD_LEVEL,
    RuleField,
    _ALL_TOKEN,
    active_shard_id,
    get_configured_reader,
    get_tick_seconds,
    is_router_process,
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


def check_shard_scope(query: dict, command_key: str = "ms_load") -> str | None:
    """Return an operator-facing refusal, or ``None`` if the scope is fine.

    Four refusals, all no-ops off a scaled deployment:

    - the whole-world scope. The YAML manifest holds every instance's rule
      sets, so ``all`` here would deploy another instance's rules into this
      one;
    - the router, which holds no game world. Refused by role rather than by
      an id that never matches: ``SCALING_ROUTER_ID`` is an arbitrary name,
      so a rule-set folder can be named after it and would otherwise pass
      the id check below;
    - a query that doesn't name the shard level at all;
    - a query naming an instance this one isn't.

    Deploying is confined to the instance running it: ``ms_load`` writes
    only this instance's rule table.
    """
    shard_id = active_shard_id()
    if shard_id is None:
        return None

    if not query:
        return (
            f"{command_key}: `{_ALL_TOKEN}` is not available on a scaled "
            f"deployment. Run this one shard at a time."
        )

    if is_router_process():
        return (
            f"{command_key}: the router holds no game world, so no rule set "
            f"deploys there. Run this from the shard it acts on."
        )

    if next(iter(query)) != SHARD_LEVEL:
        return f"{command_key}: the query must start with '{SHARD_LEVEL}='."

    if query[SHARD_LEVEL] != shard_id:
        return f"{command_key}: you can only run this from the shard it acts on."

    return None


def check_shard_levels(definitions) -> str | None:
    """Return a refusal if the shard level was never adopted, else ``None``.

    Checked once ``definitions.yaml`` is parsed, because that is the first
    point the declared levels are known. Nothing else catches this: a
    consumer who co-installs scaling but keeps their own level names has a
    query that validates perfectly against their own declarations, so the
    breach would otherwise surface only as a confusing "not a declared
    level" error. No-op off a scaled deployment.
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
    ms_at_post_spawn callability + signature, registered conditions) and
    Tier 4 diagnostics (tag-existence WARN logs) both fire.
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
        self.caller.msg(f"ms_load {args} : running async (gameplay continues)…")
        run_async(
            self._run_pipeline, query, flags,
            at_return=self._on_async_return,
            at_err=self._on_async_err,
        )

    def _run_pipeline(self, query: dict, flags: set) -> tuple[list, bool]:
        """Worker-thread entrypoint: runs the full pipeline.

        Returns ``(messages, deployed)``. ``messages`` is every
        operator-facing line; ``deployed`` is True only when rows were
        written, and tells ``at_return`` whether to swap the in-memory
        table. Errors with operator-meaningful context (validation refusal,
        deploy failure) get appended as messages and the function returns
        normally; only unexpected exceptions bubble out for ``at_err``.
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
            return messages, False

        try:
            definitions = Definitions.from_reader(reader)
        except ReaderError as e:
            msg = f"ms_load: could not load definitions.yaml: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages, False
        except DefinitionsError as e:
            msg = f"ms_load: definitions.yaml is malformed: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages, False

        # The shard level can only be checked once definitions.yaml is
        # parsed. Ahead of validate_query so a consumer who never adopted
        # the mandate gets told that, rather than a generic "not a
        # declared level" for the shard key they were required to pass.
        refusal = check_shard_levels(definitions)
        if refusal:
            messages.append(refusal)
            ms_log(refusal, level="ERROR")
            return messages, False

        try:
            definitions.validate_query(query)
        except DefinitionsError as e:
            msg = f"ms_load: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages, False

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
                return messages, False
            except (LoaderMissingIndexError, LoaderMissingEntryError,
                    LoaderInvalidShapeError) as e:
                msg = f"ms_load: pre-validation load failed: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages, False
            except ReaderError as e:
                msg = f"ms_load: read error during pre-validation: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages, False

            if not _run_validator(
                messages, definitions, load_result,
                "pre-validation failed",
            ):
                return messages, False

            # Re-load with the requested scope (whole-repo pre-validate
            # succeeded; now load the subset to deploy).
            try:
                load_result = loader.load(finder.find(query))
            except (FinderManifestError, FinderQueryError) as e:
                msg = f"ms_load: scope find error after pre-validation: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages, False
        else:
            try:
                found = finder.find(query)
            except FinderQueryError as e:
                msg = f"ms_load: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages, False
            except FinderManifestError as e:
                msg = f"ms_load: manifest error: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages, False

            try:
                load_result = loader.load(found)
            except (LoaderMissingIndexError, LoaderMissingEntryError,
                    LoaderInvalidShapeError) as e:
                msg = f"ms_load: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages, False
            except ReaderError as e:
                msg = f"ms_load: read error during loading: {e}"
                messages.append(msg)
                ms_log(msg, level="ERROR")
                return messages, False

            if not _run_validator(
                messages, definitions, load_result, "validation failed",
            ):
                return messages, False

        n_files = len(load_result.rule_sets)
        n_rules = sum(len(rules) for rules in load_result.rule_sets.values())
        messages.append(
            f"ms_load: validation complete "
            f"({n_files} file{'' if n_files == 1 else 's'}, "
            f"{n_rules} rule{'' if n_rules == 1 else 's'})"
        )

        # Deploy stage: write the rows, preserving timers for rules that
        # survive. The in-memory table is swapped on the reactor afterwards.
        messages.append("ms_load: starting deployment")
        ms_log(f"ms_load: validation complete ({n_files} files, {n_rules} rules)")

        deployer = Deployer(definitions)
        try:
            deployer.deploy(load_result)
        except DeployerError as e:
            msg = f"ms_load: deploy failed: {e}"
            messages.append(msg)
            ms_log(msg, level="ERROR")
            return messages, False

        messages.append(
            f"ms_load: deployment complete "
            f"({n_files} file{'' if n_files == 1 else 's'}, "
            f"{n_rules} rule{'' if n_rules == 1 else 's'} written)"
        )
        ms_log(
            f"ms_load: deploy complete "
            f"({n_files} files, {n_rules} rules written)"
        )
        return messages, True

    def _on_async_return(self, result) -> None:
        """Reactor-thread callback: swap the table if rows were written, then report.

        The swap happens here rather than in the worker because the tick
        runs on the reactor too, so the two can never overlap.
        """
        from .runtime import load_rules

        messages, deployed = result
        if deployed:
            load_rules()
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
# The operating commands
# ---------------------------------------------------------------------------


def status_lines() -> list:
    """Return ``ms_status``'s output: the spawner's state, then one row per rule.

    The live count is the tick's own population count, so the two never
    disagree about what a rule has spawned.
    """
    from .runtime import count_living, is_enabled, rule_states

    states = rule_states()
    lines = [
        f"spawner: {'enabled' if is_enabled() else 'disabled'}, "
        f"N={get_tick_seconds()}, "
        f"{len(states)} rule{'' if len(states) == 1 else 's'}",
        "id  file  rule  key  area  live/max",
    ]
    for state in states:
        rule = state.rule
        lines.append(
            f"{state.pk}  {state.file}  {rule[RuleField.RULE_ID.value]}  "
            f"{rule[RuleField.KEY.value]}  {rule[RuleField.AREA_TAG.value]}  "
            f"{count_living(state)}/{rule[RuleField.TARGET.value]}"
        )
    return lines


def delete_rule(arg: str) -> str:
    """Delete the rule whose row id is ``arg``; return the operator message.

    The rule's live mobs stay in the world; nothing replaces them when they
    die. A rule still in its YAML comes back on that file's next
    ``ms_load``.
    """
    from .models import SpawnRule
    from .runtime import load_rules

    arg = (arg or "").strip()
    if not arg.isdigit():
        return f"ms_delete: {arg!r} is not a rule id — `ms_status` lists them."

    row = SpawnRule.objects.filter(pk=int(arg)).first()
    if row is None:
        return f"ms_delete: no rule has id {arg} — `ms_status` lists them."

    label = f"{row.file} rule {row.rule_id}"
    row.delete()
    load_rules()
    ms_log(f"ms_delete: removed {label} (id {arg})")
    return f"ms_delete: removed {label} (id {arg}). Its live mobs stay until they die."


class CmdMsStatus(BaseCommand):
    """Show the spawner's state and every rule's population.

    Usage:
        ms_status

    One row per rule: id, file, rule_id, key, area and the live count
    against the rule's target. The id is what ``ms_delete`` takes.
    """

    key = "ms_status"
    locks = "cmd:superuser()"
    help_category = "Mob Spawner"

    def func(self):
        # One population count per rule — off the reactor.
        run_async(
            status_lines,
            at_return=self._on_async_return,
            at_err=self._on_async_err,
        )

    def _on_async_return(self, lines: list) -> None:
        self.caller.msg("\n".join(lines))

    def _on_async_err(self, failure) -> None:
        ms_log(
            f"ms_status: unexpected error: {failure.getTraceback()}",
            level="ERROR",
        )
        self.caller.msg(
            "ms_status: command crashed unexpectedly; "
            "see mob_spawner.log for the traceback."
        )


class CmdMsStart(BaseCommand):
    """Turn the spawner on.

    Usage:
        ms_start

    The setting persists across restarts.
    """

    key = "ms_start"
    locks = "cmd:superuser()"
    help_category = "Mob Spawner"

    def func(self):
        from .runtime import set_enabled

        set_enabled(True)
        ms_log("ms_start: spawner enabled")
        self.caller.msg("ms_start: spawner enabled.")


class CmdMsStop(BaseCommand):
    """Turn the spawner off.

    Usage:
        ms_stop

    Nothing spawns until ``ms_start``. Live mobs are untouched, and the
    setting persists across restarts.
    """

    key = "ms_stop"
    locks = "cmd:superuser()"
    help_category = "Mob Spawner"

    def func(self):
        from .runtime import set_enabled

        set_enabled(False)
        ms_log("ms_stop: spawner disabled")
        self.caller.msg("ms_stop: spawner disabled.")


class CmdMsDelete(BaseCommand):
    """Delete one rule by id.

    Usage:
        ms_delete <id>

    ``ms_status`` lists the ids. The rule's live mobs stay until they die.
    To remove a rule for good, take it out of its YAML and ``ms_load`` the
    file — a rule still in its YAML comes back on that file's next load.
    """

    key = "ms_delete"
    locks = "cmd:superuser()"
    help_category = "Mob Spawner"

    def func(self):
        self.caller.msg(delete_rule(self.args))
