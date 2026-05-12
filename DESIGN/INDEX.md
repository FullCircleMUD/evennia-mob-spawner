# DESIGN Index

Map of all design documents in this directory, organised by category. Add new documents here when they land — un-indexed documents are invisible.

## Process and discipline

- **[documentation-structure.md](documentation-structure.md)** — what goes in CLAUDE.md vs README.md vs DESIGN/, conventions for new design documents.
- **[progress.md](progress.md)** — running log of milestones with links to evidence.

## Architecture and design

*(No architecture documents have landed yet. Candidates as work begins: `rule-schema.md` (the YAML rule shape and field semantics), `script-lifecycle.md` (how the persistent script comes up, ticks, persists across restarts, reloads rules without losing history), `death-protocol.md` (the breadcrumb attributes the library stamps and the `on_death(rule_id)` callback the consumer invokes), `cross-validator.md` (rules ↔ tagged rooms coherence check).)*

## Archive

Historical context, not authoritative. Material in `archive/` is preserved per the "don't delete; supersede" principle.

*(The archive is currently empty. If substrate material later emerges — e.g. design notes carried over from the FCM-side spawn system the library is replacing, or brainstorming captured before a decision crystallised — it lands here.)*
