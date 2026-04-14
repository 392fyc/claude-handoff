---
name: handoff
description: Generate a structured handoff document for session continuation. Creates a ready-to-paste starting prompt, saves a checkpoint for the next session, and optionally launches a new terminal.
user-invocable: true
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
  - Grep
---

# /handoff — Session Handoff & Continuation

You are executing the handoff skill. This is the **only** entry point for handoff — nothing triggers automatically. Follow these steps precisely.

## Step 1: Gather Context

Build a comprehensive picture of the current session by layering these sources. Each layer is **optional** — use whatever is available.

### Layer 1: Conversation Context (always available)
You already have the full conversation in your context window. Synthesize:
- What the user was working on
- Decisions made and their rationale
- Problems encountered and solutions found
- Incomplete work and known next steps

### Layer 2: Memory Search (agentic)
Search the project's auto-memory directory for related context:
```
~/.claude/projects/<encoded_cwd>/memory/
```
Where `<encoded_cwd>` is the cwd with `:` `\` `/` replaced by `-`, leading `-` stripped.

Glob for `*.md` files there. Read any that seem relevant — previous handoffs, checkpoints, project memories. Cross-reference with what you know from the conversation.

### Layer 3: Project Documentation (if present)
Check for project-level docs that provide architectural context:
- `CLAUDE.md` in the project root or parent directories
- `AGENTS.md`, `README.md`, or similar

Only skim — extract what's relevant to the handoff, not the full content.

### Layer 4: Version Control (optional, if available)
If the project uses git, gather supplementary state. **Do not fail if git is unavailable.**
```bash
git status --short 2>/dev/null
git log --oneline -5 2>/dev/null
git branch --show-current 2>/dev/null
```

## Step 2: Generate Handoff Document

Create a structured handoff document:

```markdown
---
name: session_handoff
description: "<one-line summary of what was done and what's next>"
type: project
---
# Session Handoff — <YYYY-MM-DD>

## Starting Prompt

<A ready-to-paste instruction block for the next session. Must be
COMPLETELY self-contained — the next session has NO prior context.
Include working directory, file locations, and specific next steps.>

## Current State

<Project state snapshot. Adapt to what's relevant:>
- **Working directory**: <path>
- **Branch / version**: <if applicable>
- **Uncommitted work**: <files changed, or "clean">

## Task State

- **Completed**: <what was accomplished this session>
- **In Progress**: <incomplete work with specific details>
- **Pending**: <what comes next>

## Key Context

<Non-obvious facts the next session needs. Things NOT derivable from
reading the code — decisions, gotchas, environment quirks, relationship
between components.>
```

## Step 3: Output & Save

Do **both** of these — never skip either:

1. **Output the Starting Prompt section directly in chat** — this is the PRIMARY artifact. The user pastes it verbatim as the first message of a new session.

2. **Save the full handoff document** to:
   ```
   ~/.claude/projects/<encoded_cwd>/memory/session-handoff.md
   ```
   This file is automatically loaded by Claude's auto-memory system on next session start.

## Step 4: Pre-Termination Checklist

Before handing off, the old session MUST confirm that all of its in-flight work has finished. A handoff is a terminal event — anything the old session was doing will NOT automatically continue in the new one.

Verify each of these before launching the new session:

- **No pending tool calls.** All Bash commands, file writes, and tool operations have returned.
- **No background processes.** Any `run_in_background` tasks, long-running builds, or subprocesses the session spawned have completed (or the user has explicitly acknowledged they are still running).
- **No unsaved state.** Any edits, commits, or writes the user expected are actually on disk.
- **No pending user questions.** If the old session owes the user a reply or a choice, answer it first.

If any of the above is incomplete, finish or explicitly defer it before proceeding. Surface the status to the user: "All pending work done — ready to hand off?"

## Step 5: Hand Off to a New Session

The purpose of handoff is **to terminate the current session and have a fresh session take over**. Once Step 4 is clean, the old session has no further output to produce after the handoff document is written — its job is done.

After saving the handoff document, offer to launch a new session. The launch command passes the Starting Prompt as the first user message so the new session begins immediately — no paste required.

```bash
# Windows (new terminal tab; old session should /exit afterwards)
wt -w 0 nt --title "Handoff" -- claude "Read the session handoff document at ~/.claude/projects/<encoded_cwd>/memory/session-handoff.md and continue the task described in it. Acknowledge the handoff and begin."

# macOS/Linux (background new session, old terminal ready to exit)
claude "Read the session handoff document and continue." &
```

The SessionStart hook will inject the full handoff document as additionalContext, so the new session has everything it needs.

Ask the user to confirm before launching ("Launch continuation session? The current session will end after handoff.").

After the user confirms the new session is running, the old session should cleanly terminate (user types `/exit` or closes the terminal). Do not continue producing output in the old session — context has been transferred.

## Rules

- The Starting Prompt must be **self-contained** — zero context assumed
- Include specific file paths, line numbers, and commands
- Never include secrets, API keys, or credentials
- The chat-output prompt is the PRIMARY deliverable — do not skip it
- Do NOT add automatic hooks for SessionEnd or PreCompact — handoff is explicit only
- Handoff is a **terminal** event for the old session — no further output is needed after the new session is launched
- Before terminating, verify all pending work (tool calls, background processes, unsaved state, unanswered questions) has completed — nothing carries over automatically to the new session
