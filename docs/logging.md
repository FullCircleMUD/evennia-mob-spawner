# Logging

How the library emits durable log output, separate from operator-facing messaging and from Evennia's generic `server.log`.

The library writes its own log file, `mob_spawner.log`, co-located with Evennia's standard logs (the `LOG_DIR` configured by the consumer's gamedir). All library code that needs to record a durable event routes through a single helper, `ms_log`, which wraps Evennia's built-in `evennia.utils.logger.log_file()`. Outside an Evennia engine (tests, any future CLI), the helper is a silent no-op.

## Why a dedicated log file

Two distinct output channels that should not be confused:

1. **Operator-facing messages.** `ms_load` and other admin commands collect strings on a Twisted worker thread and flush them via `caller.msg()` on the reactor thread when the async pipeline returns. Ephemeral, addressed to the human triggering the command — they belong on the operator's terminal, not in a log file.
2. **Durable forensic records.** Unexpected exceptions, lifecycle events (script start / stop / restart / delete), validator refusals, drain timeouts, spawn-time errors, empty `area_tag` warnings. These need to survive the operator session: read later, to answer "what happened during yesterday's reload" or "why is this zone unspawned."

Without a dedicated file, the only durable record is whatever Twisted dumps into `server.log` when an exception escapes — a thin slice of what's worth recording, mixed with everything else Evennia is logging. A dedicated `mob_spawner.log` lets an operator tail one file and grep its history without sifting Evennia noise.

## Why `evennia.utils.logger.log_file`

Evennia's `log_file(msg, filename="mob_spawner.log")` already handles every concern a custom logger would have to solve:

- Writes into `settings.LOG_DIR` — same directory as `server.log` and `portal.log` — without the library hard-coding a path.
- Thread-safe via Evennia's interruptable thread pool, so worker threads (e.g. `ms_load`) can call it without locking concerns.
- No dependency on Python's `logging` module hierarchy, so it can't be silently rerouted by a consumer's logging config.
- Already a documented Evennia surface; consumers reading `mob_spawner.log` find it next to logs they already know.

The library does not implement its own file rotation, level filtering, or destination dispatch. If those become real needs later, Evennia's logging surface is the place to extend, not this library.

## Filename

Hardcoded to `mob_spawner.log`. Not configurable.

**Why hardcoded.** A configurable filename is a footgun for very little gain: two operators tailing different files because one consumer renamed it, scripts and runbooks bit-rotting when the name drifts, and the library having to validate the consumer's choice. The library is one of many things logging into `LOG_DIR`; owning a fixed name in that namespace is a smaller surface than exposing yet another setting. If a consumer has a genuine conflict on that filename, they can rename their own file — this library got there first.

## Line format

Every line emitted by `ms_log` has the shape:

```
<ISO-8601 timestamp> [<LEVEL>] <message>
```

Example: `2026-05-13T14:22:01 [INFO] ms_load: starting rule reload for shard0/millholm.yaml`.

**Why a timestamp.** Evennia's `log_file` does not prepend one, and a forensic log without per-line time context is hard to correlate with other logs or with operator memory of when something happened. ISO-8601 sorts lexically and parses unambiguously.

**Why a level prefix.** Severity becomes filterable with plain `grep`, without committing the library to Python's `logging` module. Levels are deliberately small: `INFO`, `WARN`, `ERROR`. No `DEBUG` (the library has no chatty inner loops worth logging at that volume) and no `CRITICAL` (failure to spawn or load is not a process-ending event for the consumer — the operator gets a refusal and tries again).

## Non-Evennia behaviour

When the library is imported outside a running Evennia engine — tests, future CLI tooling — `evennia.utils.logger` may not be importable. `ms_log` detects this and becomes a silent no-op.

**Why silent and not a fallback file.** Tests and CLI paths don't want stray log files in CWD or CI workspaces, and any caller in that context already has its own output channel (stdout/stderr, test runner). Silent no-op is the smallest, least-surprising behaviour. Detection is by `ImportError` on `evennia.utils.logger`, evaluated lazily on first call — the import is never attempted in a non-Evennia context.

## What the library logs

The call sites below are wired. New ones are added deliberately, and land in [progress.md](progress.md) as they ship.

- **Spawn-time errors** (decision #14) — when `create_object` or a typeclass's `ms_at_post_spawn()` raises, the surrounding `try / except` logs the error with rule context. The tick continues; one bad rule doesn't take down the script.
- **Empty `area_tag` queries** (decision #15) — pre-spawn check finds zero rooms matching the rule's `area_tag`. Logged once per occurrence with rule context. Useful for operator-side "why isn't this zone spawning?" debugging.
- **Non-persisting `attrs:` overrides** — a rule's `attrs:` entry sets a value the typeclass has no matching `AttributeProperty` for. Logged once per entry per spawn, naming the rule, the attribute, and the typeclass. `setattr()` still runs for backward compatibility, but the value will not survive past the current object — this is the operator-visible signal for "why isn't this rule's loot/state actually landing?"
- **Script lifecycle events** — load, restart, stop, delete. Includes drain timeouts (graceful stop didn't ack within 60s → force-stop fired) and any state-snapshot / state-restore events around the swap.
- **Validator refusals** — every finding emitted when `ms_load` refuses to apply a malformed rule-set file. An authoring-mistake audit trail.
- **Unexpected exceptions** from async pipelines — full traceback to `mob_spawner.log` instead of letting Twisted's `Failure` dump it into `server.log`.

These are wired in deliberately, not en masse — the library does not log every internal function call. The discipline is "log what an operator would want to read later," not "log everything." Tick-level events (every observe / every spawn) stay out unless an error fires.

## Consumer impact

None beyond what Evennia already requires. The consumer's gamedir already has `LOG_DIR` configured (it has to, for `server.log` to work). No new setting, no new install step, no settings.py edit. `mob_spawner.log` appears on first emission alongside the existing logs and grows from there.

## Out of scope

- **Log rotation.** Deferred to Evennia / the operator's deployment infrastructure (logrotate, journald, etc.). The library does not own retention.
- **Structured / JSON logging.** The line format is human-readable. If a consumer wants machine-readable events later, that's an additive layer on top, not a replacement.
- **Per-call-site level configuration.** No `MOB_SPAWNER_LOG_LEVEL` setting, no filtering at emit time. Every `ms_log` call lands in the file. If volume becomes a real problem, that's a signal to log less, not to filter at runtime.
- **Routing to Python's `logging` module.** The library does not register loggers under its package namespace. Consumers that want to route library events into their own logging hierarchy would need a new bridge; deliberately deferred until someone asks.
