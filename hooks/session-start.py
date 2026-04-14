"""
SessionStart hook — inject handoff document into new session context.

When a previous session wrote a handoff document via /handoff, this hook
reads it and outputs it as additionalContext so the new session can
immediately continue the work without any user intervention.
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


def encode_project_path(path: str) -> str:
    return path.replace(":", "-").replace("\\", "-").replace("/", "-").lstrip("-")


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

    # Look for handoff document in auto-memory
    encoded = encode_project_path(cwd)
    memory_dir = Path.home() / ".claude" / "projects" / encoded / "memory"

    # Try handoff doc first, then checkpoint
    handoff = memory_dir / "session-handoff.md"
    if not handoff.exists():
        logging.info("No handoff document at %s", handoff)
        return

    content = handoff.read_text(encoding="utf-8").strip()
    if not content:
        logging.info("Handoff document is empty")
        return

    logging.info("Found handoff document (%d chars), injecting into session", len(content))

    # Output as additionalContext — Claude sees this at session start
    # Must use hookSpecificOutput wrapper for Claude to parse it
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "<session-handoff>\n"
                "A previous session created this handoff document for you. "
                "Read it carefully and continue the work described.\n\n"
                f"{content}\n"
                "</session-handoff>"
            ),
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
