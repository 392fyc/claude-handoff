"""Handoff orchestrator — starts a continuation Claude Code session.

Reads a handoff document and launches a new Claude Code session with the
handoff content injected as the opening prompt. Optionally links the
previous session to the new one in the ``session_chains`` table.

UPSTREAM: https://github.com/392fyc/claude-memory-compiler
SOURCE:   scripts/handoff-orchestrator.py
SHA:      7fd15d5446f16b3bdc9b8056c6dd000958411dc2
DATE:     2026-04-18
ISSUE:    claude-handoff#3 (AgentKB fork salvage; Mercury #252 follow-up)

Key divergences from the AgentKB original:

- DB path: the AgentKB orchestrator wrote to ``AgentKB/stats/skill-usage.db``
  with a flat ``session_chain`` (singular, two-column) table. This repo's
  ``session_chain/db.py`` owns the schema authoritatively — we call
  ``SessionChainDB.bind_child`` instead of raw SQL. DB location is driven
  by ``CLAUDE_HANDOFF_DB`` env (defaults to ``~/.claude/handoff/session_chain.db``).

- Log file: writes to ``~/.claude/handoff/orchestrator.log`` (AgentKB wrote
  to ``AgentKB/scripts/flush.log``). Override via ``CLAUDE_HANDOFF_LOG`` env.

- ``skill_stats.py`` coupling: dropped. The AgentKB orchestrator had no
  direct dependency on it; the stale ``DB_PATH = ROOT/stats/skill-usage.db``
  constant was a leftover from the shared-file pattern inside AgentKB.

Usage::

    uv run python orchestrator.py --handoff-doc <path> [--prev-session <id>] [--cwd <dir>] [--visible]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from session_chain.db import SessionChainDB  # noqa: E402

_DEFAULT_LOG = Path.home() / ".claude" / "handoff" / "orchestrator.log"
LOG_FILE = Path(os.environ.get("CLAUDE_HANDOFF_LOG", str(_DEFAULT_LOG)))
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Windows console defaults to GBK/CP936 which can't handle emoji from Claude.
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [orchestrator] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Mirror to stderr for interactive use.
console = logging.StreamHandler(sys.stderr)
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
logging.getLogger().addHandler(console)


def link_handoff_child(prev_session_id: str, next_session_id: str) -> None:
    """Bind the pending ``(prev, NULL)`` row to the new child session.

    If no pending row exists for ``prev_session_id`` (e.g. /handoff wrote the
    doc but skipped the DB write), we log and return — higher-level callers
    choose whether to retroactively ``record_handoff`` in that case.
    """
    try:
        db = SessionChainDB()
        if db.bind_child(parent_session_id=prev_session_id, child_session_id=next_session_id):
            logging.info(
                "session_chain linked: %s -> %s", prev_session_id, next_session_id
            )
        else:
            logging.warning(
                "session_chain link: no pending row for prev=%s (consider record_handoff)",
                prev_session_id,
            )
    except Exception as e:
        logging.warning("Failed to link session chain: %s", e)


async def start_continuation_session(
    handoff_doc: Path,
    cwd: str,
    prev_session_id: str | None = None,
) -> None:
    """Launch a new Claude Code session with the handoff content as prompt."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    handoff_path = handoff_doc.resolve()
    prompt = (
        "Continue from session handoff. "
        "The SessionStart hook injects the full document as additionalContext. "
        f"Fallback: read {handoff_path}. "
        "Acknowledge the handoff and begin with the first pending task."
    )

    logging.info("Starting continuation session (cwd=%s)", cwd)

    new_session_id: str | None = None
    known_session_ids: set[str] = set()

    # Snapshot existing sessions BEFORE launching so we can diff afterwards.
    if prev_session_id:
        try:
            from claude_agent_sdk import list_sessions

            for s in list_sessions(directory=cwd, limit=20) or []:
                sid = getattr(s, "session_id", None) or getattr(s, "tag", None)
                if sid:
                    known_session_ids.add(sid)
        except Exception:
            pass

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=cwd,
                permission_mode="default",
                max_turns=None,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text, end="", flush=True)
            elif isinstance(message, ResultMessage):
                logging.info(
                    "Continuation session ended: stop_reason=%s",
                    getattr(message, "stop_reason", "unknown"),
                )
    except Exception as e:
        logging.error("Continuation session error: %s", e)
        raise

    # Find the new session ID by diffing against pre-launch snapshot.
    if prev_session_id:
        try:
            from claude_agent_sdk import list_sessions

            for s in list_sessions(directory=cwd, limit=20) or []:
                sid = getattr(s, "session_id", None) or getattr(s, "tag", None)
                if sid and sid not in known_session_ids:
                    new_session_id = sid
                    break

            if new_session_id:
                link_handoff_child(prev_session_id, new_session_id)
            else:
                logging.info("Could not determine new session_id; chain not linked")
        except Exception as e:
            logging.warning("Failed to link session chain: %s", e)


def start_visible_session(handoff_doc: Path, cwd: str) -> None:
    """Launch claude CLI in a new visible terminal window for interactive use."""
    import subprocess as _sp

    prompt = (
        f"Read the handoff document at {handoff_doc.resolve()} "
        "and continue from where the previous session left off. "
        "Acknowledge the handoff and begin with the first pending task."
    )

    if sys.platform == "win32":
        import shutil

        doc_path = str(handoff_doc.resolve()).replace("/", "\\")
        short_prompt = (
            f"Read the handoff document at {doc_path} and continue. "
            "Acknowledge the handoff and begin."
        )

        if shutil.which("wt"):
            # -w 0 targets the most recently used Windows Terminal window
            # (opens a new tab, not a new window).
            _sp.Popen([
                "wt", "-w", "0", "new-tab",
                "--title", "Claude Continuation",
                "-d", cwd,
                "--", "cmd", "/k", "claude", "--", short_prompt,
            ])
        else:
            _sp.Popen([
                "cmd", "/c", "start", '""',
                "/d", cwd, "cmd", "/k", "claude", "--", short_prompt,
            ])
    else:
        launched = False
        for term_cmd in [
            ["gnome-terminal", "--", "claude", "--", prompt],
            ["xterm", "-e", "claude", "--", prompt],
            ["claude", "--", prompt],
        ]:
            try:
                _sp.Popen(term_cmd, cwd=cwd)
                launched = True
                break
            except FileNotFoundError:
                continue

        if not launched:
            logging.error(
                "Failed to launch visible session: no terminal available (cwd=%s)", cwd
            )
            return

    logging.info("Launched visible claude session (cwd=%s)", cwd)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start a continuation Claude Code session from a handoff document"
    )
    parser.add_argument(
        "--handoff-doc",
        required=True,
        help="Path to the handoff markdown document",
    )
    parser.add_argument(
        "--prev-session",
        default=None,
        help="Previous session ID (for chain linking)",
    )
    parser.add_argument(
        "--cwd",
        default=str(Path.cwd()),
        help="Working directory for the new session",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Launch in a new visible terminal (interactive) instead of headless SDK mode",
    )
    args = parser.parse_args()

    handoff_doc = Path(args.handoff_doc)
    if not handoff_doc.is_file():
        logging.error("Handoff document not found or not a file: %s", handoff_doc)
        sys.exit(1)

    logging.info(
        "Handoff orchestrator started: doc=%s prev=%s cwd=%s visible=%s",
        handoff_doc,
        args.prev_session,
        args.cwd,
        args.visible,
    )

    if args.visible:
        start_visible_session(handoff_doc, args.cwd)
    else:
        asyncio.run(
            start_continuation_session(
                handoff_doc=handoff_doc,
                cwd=args.cwd,
                prev_session_id=args.prev_session,
            )
        )


if __name__ == "__main__":
    main()
