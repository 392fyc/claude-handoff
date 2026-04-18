"""
SessionStart hook — inject handoff document into new session context,
and bind this session as the child of any pending chain handoff.

When a previous session wrote a handoff document via /handoff, this hook
reads it and outputs it as additionalContext so the new session can
immediately continue the work without any user intervention.

Additionally, if the session_chain DB has a pending (child_session_id=NULL)
row for the current project_dir, this hook binds the new session_id and
injects a short chain-context line into additionalContext.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

# Recursion guard
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

PLUGIN_DATA = Path(os.environ.get("CLAUDE_PLUGIN_DATA", Path(__file__).resolve().parent.parent / "data"))
PLUGIN_DATA.mkdir(parents=True, exist_ok=True)
LOG_FILE = PLUGIN_DATA / "poc.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [session-start] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Make session_chain importable from plugin root
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

try:
    from session_chain import SessionChainDB
    _CHAIN_DB_AVAILABLE = True
except ImportError as _e:
    logging.warning("session_chain import failed (%s) — chain linking disabled", _e)
    _CHAIN_DB_AVAILABLE = False


def encode_project_path(path: str) -> str:
    return path.replace(":", "-").replace("\\", "-").replace("/", "-").lstrip("-")


def _try_link_chain(session_id: str, cwd: str) -> str:
    """Attempt to find a pending chain row for cwd, bind this session, and
    return a short context string. Returns empty string on any failure.

    All exceptions are caught — DB failure must never block session start.
    """
    if not _CHAIN_DB_AVAILABLE:
        return ""
    if not session_id or session_id == "unknown":
        return ""
    try:
        db = SessionChainDB()
        pending = db.find_pending_by_project(cwd)
        if pending is None:
            logging.info("chain: no pending row for project_dir=%s", cwd)
            return ""
        bound = db.bind_child(
            parent_session_id=pending.parent_session_id,
            child_session_id=session_id,
        )
        if bound:
            linked = db.find_parent(session_id)
            if (
                linked is None
                or linked.chain_id != pending.chain_id
                or linked.project_dir != pending.project_dir
            ):
                logging.warning(
                    "chain: bind verification failed for child=%s "
                    "(expected chain=%s project=%s)",
                    session_id, pending.chain_id, pending.project_dir,
                )
                return ""
            task_part = f" | task: {linked.task_ref}" if linked.task_ref else ""
            info = (
                f"[session_chain] Parent session: {linked.parent_session_id}"
                f" | chain: {linked.chain_id}{task_part}"
            )
            if linked.worktree_path:
                info += (
                    f"\n⚠ You MUST cd to this worktree before any file operation:"
                    f" {linked.worktree_path}"
                )
                logging.info(
                    "chain: worktree_path=%s injected for child=%s",
                    linked.worktree_path, session_id,
                )
            logging.info(
                "chain: bound child=%s to parent=%s chain=%s",
                session_id, linked.parent_session_id, linked.chain_id,
            )
            return info
        else:
            logging.info(
                "chain: bind_child returned False for parent=%s (already bound?)",
                pending.parent_session_id,
            )
            return ""
    except Exception as exc:  # noqa: BLE001
        logging.warning("chain: error during chain linking: %s", exc)
        return ""


def main() -> None:
    try:
        raw_input = sys.stdin.read()
        try:
            hook_input: dict = json.loads(raw_input)
        except json.JSONDecodeError:
            fixed_input = re.sub(r'(?<!\\)\\(?!["\\])', r'\\\\', raw_input)
            hook_input = json.loads(fixed_input)
    except (json.JSONDecodeError, ValueError, EOFError) as e:
        logging.error("Failed to parse stdin: %s", e)
        return

    session_id = hook_input.get("session_id", "unknown")
    cwd = hook_input.get("cwd", "")

    logging.info("SessionStart fired: session=%s cwd=%s", session_id, cwd)

    if not cwd:
        logging.info("SKIP: no cwd")
        return

    # --- session_chain integration ---
    chain_context = _try_link_chain(session_id, cwd)

    # Look for handoff document in auto-memory
    encoded = encode_project_path(cwd)
    memory_dir = Path.home() / ".claude" / "projects" / encoded / "memory"

    # Try handoff doc first, then checkpoint
    handoff = memory_dir / "session-handoff.md"
    if not handoff.exists():
        logging.info("No handoff document at %s", handoff)
        # Still output chain context if available
        if chain_context:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": chain_context,
                }
            }
            print(json.dumps(output))
        return

    content = handoff.read_text(encoding="utf-8").strip()
    if not content:
        logging.info("Handoff document is empty")
        if chain_context:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": chain_context,
                }
            }
            print(json.dumps(output))
        return

    logging.info("Found handoff document (%d chars), injecting into session", len(content))

    # Build additionalContext: prepend chain info if available
    handoff_body = (
        "<session-handoff>\n"
        "A previous session created this handoff document for you. "
        "Read it carefully and continue the work described.\n\n"
        f"{content}\n"
        "</session-handoff>"
    )
    additional_context = (
        f"{chain_context}\n\n{handoff_body}" if chain_context else handoff_body
    )

    # Output as additionalContext — Claude sees this at session start
    # Must use hookSpecificOutput wrapper for Claude to parse it
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
