"""session_chain — cross-session continuity tracking for claude-handoff.

STATUS: scaffold (v0.1.0-experimental) — schema + read/write API is defined
        but NOT yet wired into session-start.py or the handoff skill.
        Implementation continuation: see docs/SESSION_CHAIN_HANDOFF.md.

Provides a SQLite-backed `session_chains` table that records parent↔child
session handoff links. Future Mercury Phase 4-2.a will consume this API from
inside the claude-handoff SessionStart hook and the /handoff skill.
"""

from .db import (
    SessionChainDB,
    ChainEntry,
    DEFAULT_DB_PATH,
)

__all__ = ["SessionChainDB", "ChainEntry", "DEFAULT_DB_PATH"]
__version__ = "0.1.0-experimental"
