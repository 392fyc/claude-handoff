"""session_chain — cross-session continuity tracking for claude-handoff.

STATUS: v0.2.0 — DB API complete and wired into session-start.py.
        find_pending_by_project + concurrent write safety verified.

Provides a SQLite-backed `session_chains` table that records parent↔child
session handoff links. The claude-handoff SessionStart hook consumes this API
to auto-detect pending parent sessions and inject chain context.
"""

from .db import (
    SessionChainDB,
    ChainEntry,
    DEFAULT_DB_PATH,
)

__all__ = ["SessionChainDB", "ChainEntry", "DEFAULT_DB_PATH"]
__version__ = "0.2.0"
