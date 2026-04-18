"""SQLite session_chains schema + read/write API.

STATUS: v0.3.0 — schema v2 adds worktree_path as first-class column.
        Includes auto-migration from v1 DBs via ALTER TABLE ADD COLUMN.

Schema invariants:
- `chain_id` is the logical chain root (typically = the first session's id in
  a continuity chain). Multiple rows share the same chain_id across handoffs.
- `(parent_session_id, child_session_id)` is the unique handoff edge.
- `child_session_id` may be NULL when a handoff is pending (old session has
  emitted the handoff but new session has not started yet).
- `worktree_path` is nullable; carries the git worktree path across handoffs
  so session-start.py can cd to the correct worktree automatically.
- Timestamps stored as ISO-8601 UTC strings for sqlite-cli readability.

Mercury issue: 392fyc/Mercury#246
claude-handoff issue: 392fyc/claude-handoff#7
KB concept: sqlite-upsert-semantics (never use INSERT OR REPLACE for partial
             updates — it deletes unspecified columns).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(
    os.environ.get("CLAUDE_HANDOFF_DB")
    or (Path.home() / ".claude" / "handoff" / "session_chain.db")
)


# Base schema (v1 shape) — CREATE TABLE IF NOT EXISTS so it is safe to run
# on an already-initialized DB.  The v1→v2 migration below adds worktree_path
# via ALTER TABLE after this executes.
SCHEMA_BASE_V1 = """
CREATE TABLE IF NOT EXISTS session_chains (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id            TEXT    NOT NULL,
    parent_session_id   TEXT    NOT NULL,
    child_session_id    TEXT,
    handoff_ts          TEXT    NOT NULL,
    project_dir         TEXT,
    task_ref            TEXT,
    UNIQUE (parent_session_id, child_session_id)
);

CREATE INDEX IF NOT EXISTS idx_chain_id         ON session_chains (chain_id);
CREATE INDEX IF NOT EXISTS idx_parent_session   ON session_chains (parent_session_id);
CREATE INDEX IF NOT EXISTS idx_child_session    ON session_chains (child_session_id);

CREATE TABLE IF NOT EXISTS schema_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('version', '1');
"""


@dataclass
class ChainEntry:
    chain_id: str
    parent_session_id: str
    child_session_id: Optional[str]
    handoff_ts: str
    project_dir: Optional[str] = None
    task_ref: Optional[str] = None
    worktree_path: Optional[str] = None
    id: Optional[int] = None


class SessionChainDB:
    """Thin wrapper around the session_chains SQLite table.

    Read/write API is intentionally minimal — higher-level semantics
    (chain root resolution, multi-hop traversal) belong to callers.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA_BASE_V1)
            # Detect current schema version
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
            current = int(row["value"]) if row else 1
            if current < 2:
                # Use PRAGMA table_info to make ADD COLUMN idempotent
                cols = [
                    r["name"]
                    for r in conn.execute(
                        "PRAGMA table_info(session_chains)"
                    ).fetchall()
                ]
                if "worktree_path" not in cols:
                    conn.execute(
                        "ALTER TABLE session_chains ADD COLUMN worktree_path TEXT"
                    )
                conn.execute(
                    "UPDATE schema_meta SET value='2' WHERE key='version'"
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def record_handoff(
        self,
        *,
        chain_id: str,
        parent_session_id: str,
        child_session_id: Optional[str] = None,
        project_dir: Optional[str] = None,
        task_ref: Optional[str] = None,
        worktree_path: Optional[str] = None,
        handoff_ts: Optional[str] = None,
    ) -> int:
        """Insert a handoff edge. Upsert-safe: if (parent, child) already exists,
        update the mutable fields (project_dir, task_ref, worktree_path,
        handoff_ts) via the ON CONFLICT DO UPDATE path — never via INSERT OR
        REPLACE, which would destroy columns that are omitted (see KB
        `sqlite-upsert-semantics`).

        COALESCE guards: project_dir, task_ref, and worktree_path are protected
        from None overrides — an upsert with a NULL value preserves the
        existing non-NULL column value.
        """
        ts = handoff_ts or self._now_iso()
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO session_chains
                    (chain_id, parent_session_id, child_session_id,
                     handoff_ts, project_dir, task_ref, worktree_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (parent_session_id, child_session_id)
                DO UPDATE SET
                    chain_id      = excluded.chain_id,
                    handoff_ts    = excluded.handoff_ts,
                    project_dir   = COALESCE(excluded.project_dir,   session_chains.project_dir),
                    task_ref      = COALESCE(excluded.task_ref,      session_chains.task_ref),
                    worktree_path = COALESCE(excluded.worktree_path, session_chains.worktree_path)
                """,
                (chain_id, parent_session_id, child_session_id,
                 ts, project_dir, task_ref, worktree_path),
            )
            return cursor.lastrowid or 0

    def bind_child(
        self,
        *,
        parent_session_id: str,
        child_session_id: str,
    ) -> bool:
        """Promote a pending handoff (child_session_id = NULL) to bound state.
        Returns True if a pending row was updated; False if no pending row
        exists (caller should decide whether to `record_handoff` instead).
        """
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                UPDATE session_chains
                   SET child_session_id = ?
                 WHERE parent_session_id = ?
                   AND child_session_id IS NULL
                """,
                (child_session_id, parent_session_id),
            )
            return cursor.rowcount > 0

    def find_parent(self, child_session_id: str) -> Optional[ChainEntry]:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                """
                SELECT id, chain_id, parent_session_id, child_session_id,
                       handoff_ts, project_dir, task_ref, worktree_path
                  FROM session_chains
                 WHERE child_session_id = ?
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (child_session_id,),
            ).fetchone()
            return _row_to_entry(row)

    def find_children(self, parent_session_id: str) -> list[ChainEntry]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                SELECT id, chain_id, parent_session_id, child_session_id,
                       handoff_ts, project_dir, task_ref, worktree_path
                  FROM session_chains
                 WHERE parent_session_id = ?
                 ORDER BY handoff_ts ASC
                """,
                (parent_session_id,),
            ).fetchall()
            return [e for e in (_row_to_entry(r) for r in rows) if e is not None]

    def find_pending_by_project(self, project_dir: str) -> Optional[ChainEntry]:
        """Find the most recent pending handoff (child_session_id IS NULL) for
        the given project_dir. Returns None if no pending row exists.

        Used by session-start.py to auto-detect a parent session when a new
        session starts inside the same project directory.
        """
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                """
                SELECT id, chain_id, parent_session_id, child_session_id,
                       handoff_ts, project_dir, task_ref, worktree_path
                  FROM session_chains
                 WHERE child_session_id IS NULL
                   AND project_dir = ?
                 ORDER BY handoff_ts DESC, id DESC
                 LIMIT 1
                """,
                (project_dir,),
            ).fetchone()
            return _row_to_entry(row)

    def list_chain(self, chain_id: str) -> list[ChainEntry]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                SELECT id, chain_id, parent_session_id, child_session_id,
                       handoff_ts, project_dir, task_ref, worktree_path
                  FROM session_chains
                 WHERE chain_id = ?
                 ORDER BY handoff_ts ASC
                """,
                (chain_id,),
            ).fetchall()
            return [e for e in (_row_to_entry(r) for r in rows) if e is not None]


def _row_to_entry(row: Optional[sqlite3.Row]) -> Optional[ChainEntry]:
    if row is None:
        return None
    return ChainEntry(
        id=row["id"],
        chain_id=row["chain_id"],
        parent_session_id=row["parent_session_id"],
        child_session_id=row["child_session_id"],
        handoff_ts=row["handoff_ts"],
        project_dir=row["project_dir"],
        task_ref=row["task_ref"],
        worktree_path=row["worktree_path"],
    )
