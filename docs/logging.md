# Logging

How the library emits durable log output, separate from operator-facing messaging and from Evennia's
generic `server.log`.

The library writes its own file, `mob_spawner.log`, under the consumer's `LOG_DIR` alongside Evennia's
standard logs. Every durable event routes through one helper, `ms_log`, bound in
[`log.py`](../src/evennia_mob_spawner/log.py). The mechanism belongs to `evennia-logging-extension`;
this library names its file and nothing else.

## Why a dedicated log file

Two output channels that should not be confused:

1. **Operator-facing messages.** `ms_load` and the other admin commands collect strings on a Twisted
   worker thread and flush them via `caller.msg()` when the async pipeline returns. Ephemeral, and
   addressed to the human who typed the command.
2. **Durable forensic records.** Unexpected exceptions, lifecycle events, validator refusals, drain
   timeouts, spawn-time errors, empty `area_tag` warnings. These have to survive the session — read
   later, to answer "what happened during yesterday's reload".

Without a dedicated file the only durable record is whatever Twisted dumps into `server.log` when an
exception escapes: a thin slice of what matters, mixed with everything else Evennia emits. A file of
our own is one an operator can tail and grep.

## Why the extension rather than `log_file` directly

Evennia's `logger.log_file()` hands every write to `deferToThread`. With a reactor running that is
correct — disk I/O stays off the reactor thread. Before one exists the deferred is created, never
runs, and dies with the process, leaving a zero-byte file.

`evennia-logging-extension` branches on whether a reactor is running and writes synchronously when
there is not, in the same format to the same file. So the library may log from anywhere, including
`AppConfig.ready()`, a management command, and the `ms-validate` CLI.

The library implements no rotation, level filtering or destination dispatch of its own. Those belong
to the extension or to the operator's deployment.

## Filename

Hardcoded to `mob_spawner.log`. Not configurable.

A configurable filename buys little and costs a setting to validate, plus two operators tailing
different files because one consumer renamed it. The library is one of many things writing into
`LOG_DIR`; owning a fixed name there is the smaller surface. A consumer with a genuine collision
renames their own.

## Line format

```
26-09-12 23:11:08 [-] [INFO] ms_load: starting rule reload for shard0/millholm.yaml
```

Evennia's timestamp and `[-]` marker, then the level tag and the message. The extension composes both
layers, so a line reads identically whether a reactor was up when it was written.

Levels are `INFO`, `WARN` and `ERROR`; anything else coerces to `INFO`. No `DEBUG` — the library has
no inner loop worth logging at that volume — and no `CRITICAL`, because a failed spawn or load is not
a process-ending event. `INFO` means the library working as intended and worth looking up later;
`WARN` and `ERROR` mean something is wrong or may be.

Passing `trace=True` from inside an `except` block appends the active traceback. A log call never
raises into its caller.

## What the library logs

Call sites are wired deliberately, not en masse. The discipline is "log what an operator would want to
read later", not "log everything" — tick-level events stay out unless an error fires. New call sites
land in [progress.md](progress.md) as they ship.

- **Spawn-time errors** (decision #14) — `create_object` or a typeclass's `ms_at_post_spawn()` raises.
  Logged with rule context; the tick continues, so one bad rule does not take down the script.
- **Empty `area_tag` queries** (decision #15) — the pre-spawn check finds zero rooms matching the
  rule's `area_tag`. Answers "why isn't this zone spawning?".
- **Non-persisting `attrs:` overrides** — a rule sets a value the typeclass has no matching
  `AttributeProperty` for. Named by rule, attribute and typeclass. `setattr()` still runs, but the
  value will not survive the current object.
- **Script lifecycle events** — load, restart, stop, delete, including drain timeouts and the
  state-snapshot / restore around a swap.
- **Validator refusals** — every finding emitted when `ms_load` refuses a malformed rule-set file.
- **Unexpected exceptions** from the async pipelines, with the traceback, rather than letting
  Twisted's `Failure` dump it into `server.log`.

## Consumer impact

`evennia-logging-extension` is a hard dependency and installs with the library. There is no new
setting and no `settings.py` edit; `mob_spawner.log` appears on first emission.

One ordering rule applies, and it is the extension's: a library import in the consumer's settings
module goes **below** `from evennia.settings_default import *`, which is what sets `LOG_DIR`. Where a
game overrides `LOG_DIR`, the override goes directly under that import — the directory resolves once,
at the first library import, and an override below it is read too late with nothing reporting the
mismatch.

## Out of scope

- **Log rotation.** The operator's deployment infrastructure owns retention.
- **Structured / JSON logging.** The line format is human-readable. A machine-readable layer would be
  additive.
- **Per-call-site level configuration.** No `MOB_SPAWNER_LOG_LEVEL`, no emit-time filtering. Volume
  becoming a problem is a signal to log less, not to filter at runtime.
- **Routing into Python's `logging` module.** The library registers no loggers under its package
  namespace.
