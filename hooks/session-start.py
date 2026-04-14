"""
POC: SessionStart hook — inject last checkpoint into session context.

Reads the most recent session-checkpoint.md from auto-memory and outputs
it as additionalContext so the new session has continuity.
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
    logging.info("[POC] Hook input keys: %s", sorted(hook_input.keys()))

    if not cwd:
        logging.info("SKIP: no cwd")
        return

    # Look for session checkpoint in auto-memory
    encoded = encode_project_path(cwd)
    memory_dir = Path.home() / ".claude" / "projects" / encoded / "memory"
    checkpoint = memory_dir / "session-checkpoint.md"

    if not checkpoint.exists():
        logging.info("No checkpoint found at %s", checkpoint)
        return

    content = checkpoint.read_text(encoding="utf-8").strip()
    if not content:
        logging.info("Checkpoint is empty")
        return

    logging.info("[POC] SUCCESS: Found checkpoint (%d chars), injecting into session", len(content))

    # Output additionalContext for Claude to see
    output = {
        "additionalContext": f"<session-restore>\n{content}\n</session-restore>"
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
