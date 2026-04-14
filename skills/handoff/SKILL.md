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

Collect the current state of the project by running these in parallel:

1. `git status` — uncommitted changes
2. `git log --oneline -5` — recent commits
3. `git branch --show-current` — current branch
4. `gh issue list --state open --limit 5 2>/dev/null` — open issues (skip if gh unavailable)

Also read the session checkpoint if one exists from a previous handoff:
```
~/.claude/projects/<encoded_cwd>/memory/session-checkpoint.md
```
Where `<encoded_cwd>` is the current working directory with `:` `\` `/` replaced by `-`, leading `-` stripped.

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
Include repo paths, branch names, file locations, and specific next steps.>

## Current State

- **Repo**: <repo path>
- **Branch**: <branch name>
- **Last commit**: <hash + message>
- **Working tree**: <clean / summary of changes>

## Task State

- **Completed**: <what was accomplished this session>
- **In Progress**: <incomplete work with specific details>
- **Pending**: <what comes next>

## Key Context

<Non-obvious facts the next session needs. Things NOT derivable from
code or git history — decisions, gotchas, environment quirks.>
```

## Step 3: Output & Save

Do **both** of these — never skip either:

1. **Output the Starting Prompt section directly in chat** — this is the PRIMARY artifact. The user pastes it verbatim as the first message of a new session.

2. **Save the full handoff document** to:
   ```
   ~/.claude/projects/<encoded_cwd>/memory/session-handoff.md
   ```
   This file is automatically loaded by Claude's auto-memory system on next session start.

## Step 4: Optional Auto-Continuation

Ask the user: "Launch a new session to continue?"

If yes:
```bash
# Windows (new terminal tab)
wt -w 0 nt --title "Handoff" -- claude

# macOS/Linux
claude &
```

## Rules

- The Starting Prompt must be **self-contained** — zero context assumed
- Include specific file paths, line numbers, and commands
- Never include secrets, API keys, or credentials
- The chat-output prompt is the PRIMARY deliverable — do not skip it
- Do NOT add automatic hooks for SessionEnd or PreCompact — handoff is explicit only
