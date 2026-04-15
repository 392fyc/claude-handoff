# session_chain — Handoff to Next Session

> **Status when this document was written**: 2026-04-16, by Mercury S54.
> **Scaffold landed**: scaffold PR on branch `feat/session-chain-db-scaffold`.
> **What remains**: wiring the scaffold into actual runtime (hook + skill), adding the CLAUDE.md injection consumer, alpha.2 release.
>
> This document is the *single source of truth* for continuing Phase 4-2.a
> implementation. Read it first; do **not** re-derive decisions from chat
> logs.

---

## What has been shipped (alpha.1-experimental scaffold)

| File | Purpose |
|------|---------|
| `session_chain/__init__.py` | Package export (`SessionChainDB`, `ChainEntry`, `DEFAULT_DB_PATH`). Version `0.1.0-experimental`. |
| `session_chain/db.py` | SQLite schema + read/write API. Upsert uses `ON CONFLICT DO UPDATE` with `COALESCE` to avoid wiping omitted columns (INSERT OR REPLACE is banned by KB concept `sqlite-upsert-semantics`). |
| `session_chain/tests/test_db_smoke.py` | Smoke tests: schema creation, upsert preservation, pending-child bind, chain ordering. |

### Current API surface

```python
from session_chain import SessionChainDB, ChainEntry, DEFAULT_DB_PATH

db = SessionChainDB()                       # defaults to ~/.claude/handoff/session_chain.db
db.record_handoff(                          # idempotent upsert on (parent, child) edge
    chain_id="chain-abc",
    parent_session_id="sess-A",
    child_session_id="sess-B",               # may be None for pending handoff
    project_dir="/path/to/proj",
    task_ref="#246",
)
db.bind_child(                              # promote pending row once new session id is known
    parent_session_id="sess-A",
    child_session_id="sess-B",
)
db.find_parent("sess-B")        -> ChainEntry | None
db.find_children("sess-A")      -> list[ChainEntry]
db.list_chain("chain-abc")      -> list[ChainEntry]  # ordered by handoff_ts ASC
```

### Schema freeze

The schema at `schema_meta.version = '1'` is the baseline. Any schema change
MUST:

1. Add a migration step in `db.py._init_schema` (use `ALTER TABLE ... ADD COLUMN` where possible).
2. Bump `schema_meta.version`.
3. Be backward-compatible — old DBs should be auto-migrated by opening them with the new code.

Indices on `chain_id`, `parent_session_id`, `child_session_id` are in place.

---

## What is NOT done (work for next session)

### W1. `session-start.py` integration

**Goal**: when a session starts, resolve whether it is a child of a prior
handoff and inject the parent's context into `additionalContext`.

**Steps**:

1. In `hooks/session-start.py`, at top, import:
   ```python
   from session_chain import SessionChainDB
   ```
   (Adjust `sys.path` to include the plugin root if needed. Confirm plugin
   runtime sets `CLAUDE_PLUGIN_ROOT` — do NOT hardcode paths.)
2. Read `CLAUDE_SESSION_ID` from the environment (if not set, skip — no chain
   to record).
3. Read the `claude-handoff` pending handoff marker — **figure out where the
   current handoff skill stores the pending parent_session_id**. This is the
   integration seam. Options:
   - A state file under `~/.claude/handoff/pending/<session-id>.json`
     containing `parent_session_id` + `chain_id` + `task_ref`.
   - An env var `CLAUDE_HANDOFF_PARENT_SESSION` injected by the handoff skill
     when the user pastes the starting prompt.
   - Parse the starting prompt for a marker (fragile — avoid).

   **Decision needed**: pick a marker protocol and document it here BEFORE
   coding. Read `skills/handoff/SKILL.md` to see what artifacts the current
   skill already produces.
4. If a pending marker is found, call `db.bind_child(parent_session_id=...,
   child_session_id=current_session_id)`.
5. Call `db.find_parent(current_session_id)` and, if present, prepend a
   short "Parent session: <id> | Task: <task_ref>" line to
   `additionalContext`.

**Testing**: integration test that simulates two sessions in a temp DB dir.
Do NOT hit the real `~/.claude/handoff/session_chain.db`.

### W2. `/handoff` skill integration (write side)

**Goal**: when the user runs `/handoff`, the skill calls
`db.record_handoff(...)` with `child_session_id=None` (pending) so that the
new session can later bind itself.

**Steps**:

1. Read `skills/handoff/SKILL.md` to understand the current output contract
   (starting prompt + persisted document per user terminology rule in
   Mercury CLAUDE.md).
2. Before emitting the starting prompt, derive `chain_id`:
   - If the current session already has a parent in the DB, reuse that parent's
     `chain_id`.
   - Otherwise generate a new `chain_id = current_session_id` (chain root).
3. Call `db.record_handoff(chain_id=..., parent_session_id=current_session_id,
   child_session_id=None, project_dir=cwd, task_ref=<from skill args>)`.
4. Write the pending marker artifact (per W1 step 3 decision).
5. Emit the starting prompt as today.

### W3. Dry-run CLI

Add `python -m session_chain --chain <id>` and `--parent <id>` for operator
inspection. Low priority but useful for debugging session chains in-field.

### W4. alpha.2 release

- Update `plugin.json` version to `1.0.1-alpha.2`.
- Update `marketplace.json` metadata.
- Tag `v1.0.1-alpha.2`.
- GitHub release with changelog referencing Mercury issue #246.

---

## Non-goals (explicitly out of scope for 4-2.a)

- Worktree-per-task creation (that is Mercury issue #247, separate repo).
- Context size budgeting for injected parent-session summary (defer to a
  later issue).
- Multi-project chain merging.
- UI/viewer.

---

## Information needed from the next session operator

Before resuming, the next session should have:

1. **Current `claude-handoff` skill behaviour audit** — run `/handoff` once in
   a toy project and record (a) what artifacts land on disk, (b) where, (c)
   what env vars the starting prompt relies on. That audit determines the
   pending-marker protocol (W1 step 3).
2. **Plugin runtime path resolution** — confirm whether plugin Python code
   can `import session_chain` when loaded under the Claude Code plugin
   runtime, or whether an explicit `sys.path.insert(0, plugin_root / 'lib')`
   shim is required. Check Claude Code plugin docs, do not guess.
3. **Mercury issue #246 acceptance criteria** — re-read the issue body;
   tests must cover each checkbox.

## References

- Mercury issue #246 (this work)
- Mercury issue #247 (adjacent: worktree-per-task, separate repo — do not
  cross-pollute)
- Mercury issue #248 (adjacent: Phase 3 Karpathy-pattern improvements)
- Mercury research: `Mercury/.mercury/docs/research/phase4-2-worktree-mount-eval.md` §4-2.a
- KB concept: `sqlite-upsert-semantics` (never use `INSERT OR REPLACE`)
- KB concept: `claude-handoff-plugin-v1-alpha` (plugin layout + marketplace schema)
- Plugin root: `github.com/392fyc/claude-handoff`

## Known UNVERIFIED facts

- **Plugin runtime Python path behaviour**: not verified by WebSearch. Claude
  Code plugin documentation needs to be consulted before W1 step 1.
- **Handoff skill output contract**: file locations for the "persisted
  handoff document" are set by the skill's current runtime behaviour, not a
  public spec. Re-audit on session resume.
