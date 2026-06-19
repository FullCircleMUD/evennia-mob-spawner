# DESIGN Index

Map of all design documents in this directory, organised by category. Add new documents here when they land — un-indexed documents are invisible.

## Process and discipline

- **[progress.md](progress.md)** — running log of milestones with links to evidence.

## Architecture and design

- **[architecture.md](architecture.md)** — primary entry point for architectural questions. Library / consumer ownership boundary, pipeline shape, agreed decisions, open `[TBD]` items.
- **[logging.md](logging.md)** — dedicated `mob_spawner.log` co-located with Evennia logs via `evennia.utils.logger.log_file()`. `ms_log` shim with ISO-timestamp + level format; silent no-op outside Evennia.
- **[shards-compatibility.md](shards-compatibility.md)** — how `ms_load` / `ms_validate` play nice with `evennia-shards` (the `MobSpawnerScript` rows they create get correctly stamped under multi-tenant deployments) without making shards a hard dependency. Optional import + `preserve_tenant_context` wrap around the `run_async` dispatch.

*(Focused docs may follow as work demands them — for example, the rule schema, script lifecycle, or the cross-validator between rule files and tagged rooms. Add them here when they exist.)*

## Archive

Historical context, not authoritative. Material in `archive/` is preserved per the "don't delete; supersede" principle.

*(The archive is currently empty. If substrate material later emerges — e.g. design notes carried over from the FCM-side spawn system the library is replacing, or brainstorming captured before a decision crystallised — it lands here.)*
