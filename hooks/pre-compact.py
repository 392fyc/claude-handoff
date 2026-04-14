"""
POC: PreCompact hook — verify that plugin hooks can access transcript_path
and parse JSONL conversation data.

Reads hook input from stdin, extracts conversation context from the
transcript file, and logs results to CLAUDE_PLUGIN_DATA/poc.log.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Recursion guard
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

# Resolve data directory — ${CLAUDE_PLUGIN_DATA} for installed plugins,
# fallback to plugin root for local dev
PLUGIN_DATA = Path(os.environ.get("CLAUDE_PLUGIN_DATA", Path(__file__).resolve().parent.parent / "data"))
PLUGIN_DATA.mkdir(parents=True, exist_ok=True)
LOG_FILE = PLUGIN_DATA / "poc.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [pre-compact] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

MAX_TURNS = 30
MAX_CONTEXT_CHARS = 15_000
MIN_TURNS_TO_FLUSH = 3


def extract_conversation_context(transcript_path: Path) -> tuple[str, int]:
    """Read JSONL transcript and extract last ~N conversation turns as markdown."""
    turns: list[str] = []

    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = entry.get("message", {})
            if isinstance(msg, dict):
                role = msg.get("role", "")
                content = msg.get("content", "")
            else:
                role = entry.get("role", "")
                content = entry.get("content", "")

            if role not in ("user", "assistant"):
                continue

            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)

            if isinstance(content, str) and content.strip():
                label = "User" if role == "user" else "Assistant"
                turns.append(f"**{label}:** {content.strip()}\n")

    recent = turns[-MAX_TURNS:]
    context = "\n".join(recent)

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[-MAX_CONTEXT_CHARS:]
        boundary = context.find("\n**")
        if boundary > 0:
            context = context[boundary + 1:]

    return context, len(recent)


def main() -> None:
    # Read hook input from stdin
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
    transcript_path_str = hook_input.get("transcript_path", "")

    logging.info("PreCompact fired: session=%s", session_id)

    # POC: Log all available hook input keys for discovery
    logging.info("[POC] Hook input keys: %s", sorted(hook_input.keys()))
    for k, v in sorted(hook_input.items()):
        val_str = str(v)[:200] if v else "(empty)"
        logging.info("[POC] %s = %s", k, val_str)

    # POC: Log environment variables for discovery
    plugin_vars = {k: v for k, v in os.environ.items()
                   if k.startswith(("CLAUDE_PLUGIN", "CLAUDE_CODE", "CLAUDE_PROJECT"))}
    logging.info("[POC] Plugin env vars: %s", sorted(plugin_vars.keys()))
    for k, v in sorted(plugin_vars.items()):
        logging.info("[POC] env %s = %s", k, v[:200])

    if not transcript_path_str or not isinstance(transcript_path_str, str):
        logging.info("SKIP: no transcript path")
        return

    transcript_path = Path(transcript_path_str)
    if not transcript_path.exists():
        logging.info("SKIP: transcript missing: %s", transcript_path_str)
        return

    # POC: verify we can read and parse the transcript
    try:
        context, turn_count = extract_conversation_context(transcript_path)
    except Exception as e:
        logging.error("Context extraction failed: %s", e)
        return

    if not context.strip():
        logging.info("SKIP: empty context")
        return

    logging.info("[POC] SUCCESS: extracted %d turns, %d chars from transcript", turn_count, len(context))
    logging.info("[POC] First 200 chars: %s", context[:200].replace("\n", " "))

    # Write extracted context to a file for inspection
    checkpoint_file = PLUGIN_DATA / "last-precompact-context.md"
    checkpoint_file.write_text(
        f"# PreCompact Context — {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"**Session**: {session_id}\n"
        f"**Turns**: {turn_count}\n"
        f"**Chars**: {len(context)}\n\n"
        f"{context}\n",
        encoding="utf-8",
    )
    logging.info("[POC] Wrote context to %s", checkpoint_file)


if __name__ == "__main__":
    main()
