"""Smoke tests for the session_chain SQLite scaffold.

Covers schema creation, upsert semantics, read API happy path,
concurrent write safety, session-start integration flow,
and v1→v2 schema migration with worktree_path round-trip.
"""

from __future__ import annotations

import sqlite3
import threading
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


class ConcurrentRecordHandoffTest(unittest.TestCase):
    """test_concurrent_record_handoff: 10 threads insert distinct edges; all must survive."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "chain.db"
        self.db = SessionChainDB(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_concurrent_record_handoff(self) -> None:
        """10 threads each record a distinct parent→child edge; all 10 must exist after."""
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                self.db.record_handoff(
                    chain_id="chain-concurrent",
                    parent_session_id=f"parent-{i}",
                    child_session_id=f"child-{i}",
                    project_dir="/tmp/proj",
                    task_ref=f"#t{i}",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Worker errors: {errors}")
        # Verify all 10 edges are present
        for i in range(10):
            entry = self.db.find_parent(f"child-{i}")
            self.assertIsNotNone(entry, f"Missing edge for child-{i}")
            assert entry is not None
            self.assertEqual(entry.parent_session_id, f"parent-{i}")


class ConcurrentBindChildTest(unittest.TestCase):
    """test_concurrent_bind_child: multiple threads race to bind same pending row;
    exactly one succeeds (rowcount=1), the rest get rowcount=0."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "chain.db"
        self.db = SessionChainDB(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_concurrent_bind_child(self) -> None:
        """Race 5 threads to bind the same pending row; exactly one must win."""
        self.db.record_handoff(
            chain_id="chain-race",
            parent_session_id="parent-race",
            child_session_id=None,
            project_dir="/tmp/race",
        )

        results: list[bool] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(child_id: str) -> None:
            try:
                bound = self.db.bind_child(
                    parent_session_id="parent-race",
                    child_session_id=child_id,
                )
                with lock:
                    results.append(bound)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(f"child-race-{i}",))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Worker errors: {errors}")
        successes = [r for r in results if r]
        self.assertEqual(
            len(successes), 1,
            f"Expected exactly 1 successful bind; got {len(successes)}. Results: {results}",
        )


class SessionStartIntegrationTest(unittest.TestCase):
    """test_session_start_integration: full flow — pending → find → bind → verify."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "chain.db"
        self.db = SessionChainDB(self.db_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_session_start_integration(self) -> None:
        """Simulate what session-start.py does:
        1. Previous session records a pending handoff.
        2. New session calls find_pending_by_project.
        3. New session calls bind_child.
        4. Verify chain is linked (child_session_id is set).
        """
        project_dir = "/tmp/myproject"
        parent_id = "sess-parent-integration"
        child_id = "sess-child-integration"
        chain_id = "chain-integration"

        # Step 1: previous session records pending handoff
        self.db.record_handoff(
            chain_id=chain_id,
            parent_session_id=parent_id,
            child_session_id=None,
            project_dir=project_dir,
            task_ref="#246",
        )

        # Step 2: new session finds the pending row
        pending = self.db.find_pending_by_project(project_dir)
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual(pending.parent_session_id, parent_id)
        self.assertIsNone(pending.child_session_id)
        self.assertEqual(pending.chain_id, chain_id)
        self.assertEqual(pending.task_ref, "#246")

        # Step 3: new session binds itself
        bound = self.db.bind_child(
            parent_session_id=pending.parent_session_id,
            child_session_id=child_id,
        )
        self.assertTrue(bound)

        # Step 4: verify chain is linked
        linked = self.db.find_parent(child_id)
        self.assertIsNotNone(linked)
        assert linked is not None
        self.assertEqual(linked.parent_session_id, parent_id)
        self.assertEqual(linked.child_session_id, child_id)
        self.assertEqual(linked.chain_id, chain_id)

        # Verify no more pending rows for this project
        still_pending = self.db.find_pending_by_project(project_dir)
        self.assertIsNone(still_pending)

    def test_find_pending_returns_none_when_empty(self) -> None:
        """find_pending_by_project returns None when no pending rows exist."""
        result = self.db.find_pending_by_project("/no/such/project")
        self.assertIsNone(result)

    def test_find_pending_ignores_bound_rows(self) -> None:
        """find_pending_by_project returns None when all rows are already bound."""
        self.db.record_handoff(
            chain_id="chain-bound",
            parent_session_id="p-bound",
            child_session_id="c-bound",
            project_dir="/tmp/boundproject",
        )
        result = self.db.find_pending_by_project("/tmp/boundproject")
        self.assertIsNone(result)


class SchemaV2MigrationTest(unittest.TestCase):
    """Tests for v1→v2 schema migration and worktree_path functionality."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "chain_v1.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _create_v1_db(self) -> None:
        """Manually create a bare v1 DB (no worktree_path column)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript("""
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
            CREATE INDEX IF NOT EXISTS idx_chain_id       ON session_chains (chain_id);
            CREATE INDEX IF NOT EXISTS idx_parent_session ON session_chains (parent_session_id);
            CREATE INDEX IF NOT EXISTS idx_child_session  ON session_chains (child_session_id);
            CREATE TABLE IF NOT EXISTS schema_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('version', '1');
        """)
        # Insert a v1 row without worktree_path
        conn.execute(
            """
            INSERT INTO session_chains
                (chain_id, parent_session_id, child_session_id, handoff_ts, project_dir, task_ref)
            VALUES ('chain-v1', 'parent-v1', 'child-v1', '2026-04-18T00:00:00+00:00',
                    '/tmp/v1proj', '#v1')
            """
        )
        conn.commit()
        conn.close()

    def test_schema_v1_to_v2_migration(self) -> None:
        """Create a bare v1 DB, open with v2 code; verify worktree_path column
        was added and schema_meta.version was bumped to 2."""
        self._create_v1_db()

        # Opening with v2 code should auto-migrate
        db = SessionChainDB(self.db_path)

        # Verify column exists via PRAGMA
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(session_chains)").fetchall()]
        conn.close()
        self.assertIn("worktree_path", cols, "worktree_path column should exist after migration")

        # Verify schema_meta.version is now 2
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["value"], "2", "schema_meta.version should be '2' after migration")

        # Verify existing v1 row is readable with worktree_path = None
        entry = db.find_parent("child-v1")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.parent_session_id, "parent-v1")
        self.assertIsNone(entry.worktree_path)

    def test_schema_v2_migration_idempotent(self) -> None:
        """Opening a v2 DB a second time must not error (migration is idempotent)."""
        # First open: migrates to v2
        db1 = SessionChainDB(self.db_path)
        # Second open: already v2 — must succeed without error
        db2 = SessionChainDB(self.db_path)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        conn.close()
        self.assertEqual(row["value"], "2")

    def test_worktree_path_round_trip(self) -> None:
        """record_handoff with worktree_path; find_parent returns entry with
        the same worktree_path populated."""
        db = SessionChainDB(self.db_path)
        db.record_handoff(
            chain_id="chain-wt",
            parent_session_id="parent-wt",
            child_session_id="child-wt",
            project_dir="/tmp/proj",
            task_ref="#7",
            worktree_path="/tmp/proj/.git/worktrees/feat-7",
        )
        entry = db.find_parent("child-wt")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.worktree_path, "/tmp/proj/.git/worktrees/feat-7")

    def test_worktree_path_coalesce(self) -> None:
        """Second upsert with worktree_path=None must preserve the original value
        (COALESCE guard mirrors project_dir / task_ref behavior)."""
        db = SessionChainDB(self.db_path)
        db.record_handoff(
            chain_id="chain-coalesce",
            parent_session_id="parent-coalesce",
            child_session_id="child-coalesce",
            project_dir="/tmp/proj",
            worktree_path="/tmp/proj/.git/worktrees/feat-coalesce",
        )
        # Second upsert omits worktree_path (None)
        db.record_handoff(
            chain_id="chain-coalesce",
            parent_session_id="parent-coalesce",
            child_session_id="child-coalesce",
            project_dir=None,
            worktree_path=None,
        )
        entry = db.find_parent("child-coalesce")
        assert entry is not None
        self.assertEqual(
            entry.worktree_path,
            "/tmp/proj/.git/worktrees/feat-coalesce",
            "worktree_path must survive None upsert (COALESCE guard)",
        )


if __name__ == "__main__":
    unittest.main()
