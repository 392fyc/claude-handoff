---
name: handoff
description: Generate a structured handoff document for session continuation. Creates a ready-to-paste starting prompt and optionally launches a new terminal session.
user-invocable: true
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
  - Grep
---

# /handoff — Session Handoff & Continuation

You are executing the handoff skill. Follow these steps precisely.

## Step 1: Gather Context

Read the session checkpoint if it exists:
```
~/.claude/projects/<encoded_cwd>/memory/session-checkpoint.md
```

Where `<encoded_cwd>` is the current working directory with `:` `\` `/` replaced by `-`, leading `-` stripped.

Also check for any in-progress work:
- Run `git status` to see uncommitted changes
- Run `git log --oneline -5` to see recent commits
- Check for open issues: `gh issue list --state open --limit 5` (if gh is available)

## Step 2: Generate Handoff Document

Create a structured handoff document with this format:

```markdown
# Session Handoff — <date>

## Starting Prompt

<A ready-to-paste instruction block for the next session. Must be self-contained
and actionable — the next session has NO context from this one.>

## Current State

- **Branch**: <current branch>
- **Last commit**: <hash + message>
- **Uncommitted changes**: <summary or "clean">
- **Open issues**: <relevant issues>

## Task State

- **Completed**: <what was done this session>
- **In Progress**: <incomplete work>
- **Pending**: <what comes next>

## Key Context

<Non-obvious facts the next session needs to know. Things that aren't
derivable from code or git history.>
```

## Step 3: Output & Save

1. **Output the Starting Prompt directly in chat** — this is the primary artifact the user will paste into the next session
2. **Save the full handoff document** to: `~/.claude/projects/<encoded_cwd>/memory/session-handoff.md`

## Step 4: Optional Auto-Continuation

Ask the user if they want to launch a new session automatically.

If yes, run:
```bash
# Windows
wt -w 0 nt --title "Handoff Session" -- claude --resume

# macOS/Linux  
claude --resume &
```

## Important Rules

- The Starting Prompt must be **self-contained** — assume the next session has zero context
- Include specific file paths, line numbers, and command examples
- Never include secrets, API keys, or credentials
- The chat-output prompt is the PRIMARY deliverable — do not skip it
