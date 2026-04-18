"""session_chain — cross-session continuity tracking for claude-handoff.

STATUS: v0.3.0 — schema v2 adds worktree_path as first-class column.
        Auto-migration from v1 DBs via ALTER TABLE ADD COLUMN (idempotent).

Provides a SQLite-backed `session_chains` table that records parent↔child
session handoff links. The claude-handoff SessionStart hook consumes this API
to auto-detect pending parent sessions and inject chain context, including
an explicit worktree cd directive when worktree_path is set.
"""

from .db import (
    SessionChainDB,
    ChainEntry,
    DEFAULT_DB_PATH,
)

__all__ = ["SessionChainDB", "ChainEntry", "DEFAULT_DB_PATH"]
__version__ = "0.3.0"
