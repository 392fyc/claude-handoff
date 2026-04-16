"""Smoke tests for the session_chain SQLite scaffold.

Only covers schema creation, upsert semantics, and read API happy path.
Not exhaustive — full integration tests land with session-start.py wiring
(see docs/SESSION_CHAIN_HANDOFF.md).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from session_chain import SessionChainDB


class SessionChainSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "chain.db"
        self.db = SessionChainDB(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_schema_created(self) -> None:
        self.assertTrue(self.db_path.exists())
        entries = self.db.list_chain("nonexistent")
        self.assertEqual(entries, [])

    def test_record_and_find(self) -> None:
        self.db.record_handoff(
            chain_id="chain-1",
            parent_session_id="sess-A",
            child_session_id="sess-B",
            project_dir="/tmp/proj",
            task_ref="#123",
        )
        parent = self.db.find_parent("sess-B")
        self.assertIsNotNone(parent)
        assert parent is not None
        self.assertEqual(parent.parent_session_id, "sess-A")
        self.assertEqual(parent.task_ref, "#123")

    def test_upsert_preserves_fields(self) -> None:
        """Re-recording the same edge must NOT wipe project_dir / task_ref
        when the update omits them. Guards against INSERT OR REPLACE regression.
        """
        self.db.record_handoff(
            chain_id="chain-1",
            parent_session_id="sess-A",
            child_session_id="sess-B",
            project_dir="/tmp/proj",
            task_ref="#123",
        )
        self.db.record_handoff(
            chain_id="chain-1",
            parent_session_id="sess-A",
            child_session_id="sess-B",
            project_dir=None,
            task_ref=None,
        )
        entry = self.db.find_parent("sess-B")
        assert entry is not None
        self.assertEqual(entry.project_dir, "/tmp/proj")
        self.assertEqual(entry.task_ref, "#123")

    def test_bind_pending_child(self) -> None:
        self.db.record_handoff(
            chain_id="chain-2",
            parent_session_id="sess-X",
            child_session_id=None,
            task_ref="#456",
        )
        changed = self.db.bind_child(
            parent_session_id="sess-X",
            child_session_id="sess-Y",
        )
        self.assertTrue(changed)
        parent = self.db.find_parent("sess-Y")
        assert parent is not None
        self.assertEqual(parent.parent_session_id, "sess-X")

    def test_chain_ordering(self) -> None:
        self.db.record_handoff(
            chain_id="chain-3",
            parent_session_id="s1",
            child_session_id="s2",
            handoff_ts="2026-04-16T10:00:00+00:00",
        )
        self.db.record_handoff(
            chain_id="chain-3",
            parent_session_id="s2",
            child_session_id="s3",
            handoff_ts="2026-04-16T11:00:00+00:00",
        )
        chain = self.db.list_chain("chain-3")
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0].parent_session_id, "s1")
        self.assertEqual(chain[1].parent_session_id, "s2")


if __name__ == "__main__":
    unittest.main()
