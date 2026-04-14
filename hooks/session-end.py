"""
POC: SessionEnd hook — verify transcript extraction + background claude -p spawn.

Covers POC2 (auto-memory write) and POC4 (claude -p subprocess).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
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
    format="%(asctime)s %(levelname)s [session-end] %(message)s",
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


def encode_project_path(path: str) -> str:
    """Encode project path for Claude auto-memory directory.

    Mirrors Claude Code's internal encoding: D:\\Mercury\\Foo → D--Mercury-Foo
    """
    return path.replace(":", "-").replace("\\", "-").replace("/", "-").lstrip("-")


def write_checkpoint(session_id: str, cwd: str, context: str, turn_count: int) -> bool:
    """POC2: Write session checkpoint to auto-memory directory.

    Returns True if write succeeded, False otherwise.
    """
    if not cwd:
        logging.warning("[POC2] No cwd available, skipping checkpoint write")
        return False

    encoded = encode_project_path(cwd)
    memory_dir = Path.home() / ".claude" / "projects" / encoded / "memory"
    if not memory_dir.exists():
        logging.info("[POC2] Auto-memory dir does not exist: %s", memory_dir)
        return False

    checkpoint = memory_dir / "session-checkpoint.md"
    now = datetime.now(timezone.utc).astimezone()
    content = (
        f"# Session Checkpoint — {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"**Session**: {session_id}\n"
        f"**Project**: {cwd}\n"
        f"**Trigger**: SessionEnd (claude-handoff plugin)\n"
        f"**Turns**: {turn_count}\n\n"
        f"## Context Summary\n\n"
        f"{context[:3000]}\n"
    )
    checkpoint.write_text(content, encoding="utf-8")
    logging.info("[POC2] SUCCESS: Wrote checkpoint to %s (%d bytes)", checkpoint, len(content))
    return True


def spawn_flush(context: str, session_id: str) -> bool:
    """POC4: Spawn claude -p subprocess to do LLM extraction.

    Returns True if spawn succeeded, False otherwise.
    """
    # Find claude executable
    claude_exe = None
    execpath = os.environ.get("CLAUDE_CODE_EXECPATH")
    if execpath and Path(execpath).is_file():
        claude_exe = Path(execpath)
    else:
        for name in ("claude.exe", "claude"):
            candidate = Path.home() / ".local" / "bin" / name
            if candidate.is_file():
                claude_exe = candidate
                break
    if not claude_exe:
        found = shutil.which("claude")
        if found:
            claude_exe = Path(found)

    if not claude_exe:
        logging.error("[POC4] claude executable not found")
        return False

    # Write context to temp file
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    context_file = PLUGIN_DATA / f"flush-context-{session_id[:8]}-{timestamp}.md"
    context_file.write_text(context, encoding="utf-8")

    # Spawn flush script as background process
    flush_script = Path(__file__).resolve().parent.parent / "scripts" / "flush.py"
    if not flush_script.exists():
        logging.error("[POC4] flush.py not found at %s", flush_script)
        return False

    cmd = [sys.executable, str(flush_script), str(context_file), session_id]

    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        logging.info("[POC4] SUCCESS: Spawned flush.py (claude=%s, context=%d chars)", claude_exe, len(context))
        return True
    except Exception as e:
        logging.error("[POC4] Failed to spawn flush.py: %s", e)
        return False


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
    transcript_path_str = hook_input.get("transcript_path", "")
    cwd = hook_input.get("cwd", "")

    logging.info("SessionEnd fired: session=%s", session_id)
    logging.info("[POC] Hook input keys: %s", sorted(hook_input.keys()))

    if not transcript_path_str or not isinstance(transcript_path_str, str):
        logging.info("SKIP: no transcript path")
        return

    transcript_path = Path(transcript_path_str)
    if not transcript_path.exists():
        logging.info("SKIP: transcript missing: %s", transcript_path_str)
        return

    try:
        context, turn_count = extract_conversation_context(transcript_path)
    except Exception as e:
        logging.error("Context extraction failed: %s", e)
        return

    if not context.strip() or turn_count < MIN_TURNS_TO_FLUSH:
        logging.info("SKIP: %d turns (min %d), %d chars", turn_count, MIN_TURNS_TO_FLUSH, len(context))
        return

    logging.info("Extracted %d turns, %d chars", turn_count, len(context))

    # POC2: Write checkpoint to auto-memory
    write_checkpoint(session_id, cwd, context, turn_count)

    # POC4: Spawn claude -p background flush
    spawn_flush(context, session_id)


if __name__ == "__main__":
    main()
