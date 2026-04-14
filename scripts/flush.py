"""
POC: Background flush — LLM extraction via claude -p subprocess.

Spawned by session-end.py hook. Reads context from a temp file,
calls claude -p to extract important knowledge, writes result to
CLAUDE_PLUGIN_DATA/sessions/ and optionally to auto-memory checkpoint.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ["CLAUDE_INVOKED_BY"] = "handoff_plugin_flush"

PLUGIN_DATA = Path(os.environ.get("CLAUDE_PLUGIN_DATA", Path(__file__).resolve().parent.parent / "data"))
PLUGIN_DATA.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR = PLUGIN_DATA / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = PLUGIN_DATA / "poc.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [flush] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def find_claude_exe() -> Path | None:
    execpath = os.environ.get("CLAUDE_CODE_EXECPATH")
    if execpath and Path(execpath).is_file():
        return Path(execpath)
    for name in ("claude.exe", "claude"):
        candidate = Path.home() / ".local" / "bin" / name
        if candidate.is_file():
            return candidate
    found = shutil.which("claude")
    if found:
        return Path(found)
    return None


def run_flush(context: str) -> str:
    """Call claude -p to extract knowledge from conversation context."""
    prompt = f"""Review the conversation context below and respond with a concise summary
of important items that should be preserved for the next session.
Do NOT use any tools — just return plain text.

Format your response as:

**Context:** [One line about what the user was working on]

**Key Decisions:**
- [Any decisions with rationale]

**In Progress:**
- [Tasks that are incomplete and need continuation]

**Next Steps:**
- [What should happen next]

Only include sections that have actual content. If nothing is worth saving,
respond with exactly: FLUSH_OK

## Conversation Context

{context}"""

    claude_exe = find_claude_exe()
    if not claude_exe:
        logging.error("claude executable not found")
        return "FLUSH_ERROR: claude executable not found"

    logging.info("Using claude CLI: %s", claude_exe)

    # Clean environment
    env = {}
    for k, v in os.environ.items():
        if k == "CLAUDE_INVOKED_BY":
            env[k] = v
        elif k.startswith(("CLAUDE_", "MCP_")):
            continue
        else:
            env[k] = v

    cmd = [str(claude_exe), "-p"]
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            encoding="utf-8", timeout=120, env=env, **kwargs,
        )
    except subprocess.TimeoutExpired:
        return "FLUSH_ERROR: claude -p timed out"
    except Exception as e:
        return f"FLUSH_ERROR: {type(e).__name__}: {e}"

    if result.returncode != 0:
        stderr_text = (result.stderr or "").strip()
        logging.error("claude -p failed (rc=%d): %s", result.returncode, stderr_text[:500])
        return f"FLUSH_ERROR: exit code {result.returncode}: {stderr_text[:500]}"

    output = result.stdout.strip()
    if not output:
        return "FLUSH_ERROR: empty output"

    return output


def main():
    if len(sys.argv) < 3:
        logging.error("Usage: flush.py <context_file> <session_id>")
        sys.exit(1)

    context_file = Path(sys.argv[1])
    session_id = sys.argv[2]

    logging.info("flush.py started: session=%s context=%s", session_id, context_file)

    if not context_file.exists():
        logging.error("Context file not found: %s", context_file)
        return

    context = context_file.read_text(encoding="utf-8").strip()
    if not context:
        logging.info("Context file is empty, skipping")
        context_file.unlink(missing_ok=True)
        return

    logging.info("Flushing: %d chars", len(context))

    response = run_flush(context)

    # Save result
    now = datetime.now(timezone.utc).astimezone()
    result_file = SESSIONS_DIR / f"{now.strftime('%Y-%m-%d')}_{session_id[:8]}.md"

    if "FLUSH_OK" in response:
        logging.info("Result: FLUSH_OK")
        result_file.write_text(f"# Session {session_id[:8]} — FLUSH_OK\n\nNothing worth saving.\n", encoding="utf-8")
    elif "FLUSH_ERROR" in response:
        logging.error("Result: %s", response)
    else:
        logging.info("Result: saved (%d chars)", len(response))
        result_file.write_text(
            f"# Session {session_id[:8]} — {now.strftime('%Y-%m-%d %H:%M')}\n\n{response}\n",
            encoding="utf-8",
        )

    # POC3: Write to SQLite for persistence verification
    try:
        import sqlite3
        db_path = PLUGIN_DATA / "handoff.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""CREATE TABLE IF NOT EXISTS flush_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL,
            chars INTEGER DEFAULT 0
        )""")
        status = "ok" if "FLUSH_OK" in response else ("error" if "FLUSH_ERROR" in response else "saved")
        conn.execute(
            "INSERT INTO flush_log (session_id, timestamp, status, chars) VALUES (?, ?, ?, ?)",
            (session_id, now.isoformat(), status, len(response)),
        )
        conn.commit()
        conn.close()
        logging.info("[POC3] SUCCESS: SQLite record written to %s", db_path)
    except Exception as e:
        logging.error("[POC3] SQLite write failed: %s", e)

    # Cleanup
    context_file.unlink(missing_ok=True)
    logging.info("Flush complete for session %s", session_id)


if __name__ == "__main__":
    main()
