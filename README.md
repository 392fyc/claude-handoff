# claude-handoff

Session handoff & auto-continuation for [Claude Code](https://claude.com/claude-code).

When a working session approaches its context limit, `/handoff` generates a structured handoff document, launches a fresh session, and the new session picks up exactly where the old one left off — no copy-paste, no context loss.

## What It Does

1. You invoke `/handoff` in a session that is about to end (context full, long-running task needs continuation, end of day).
2. The skill synthesizes the conversation into a **self-contained Starting Prompt** and saves it as a handoff document in Claude's auto-memory.
3. Optionally launches a new terminal tab running `claude` with the Starting Prompt as its first message.
4. A SessionStart hook in the new session reads the handoff document and injects it as additional context — the new session begins working immediately.
5. The old session terminates; context has been transferred.

## Design Principles

- **Explicit only.** Handoff runs when you type `/handoff`. No automatic triggers on SessionEnd, PreCompact, or anywhere else.
- **Terminal event.** Handoff is for ending a session, not pausing it. The old session's job ends when the new one launches — but only after all its pending work (tool calls, background processes, unsaved state) has completed.
- **Layered context.** The skill gathers context from conversation → memory → project docs → VCS (optional), degrading gracefully when any layer is unavailable. No git-only assumptions.
- **Zero runtime dependencies.** Python stdlib only. If `python` runs, the plugin runs.

## Installation

### From GitHub (recommended)

```bash
# Add this repo as a marketplace
claude plugin marketplace add 392fyc/claude-handoff

# Install the plugin
claude plugin install claude-handoff@claude-handoff

# Restart Claude Code for hooks/skills to load
```

### From a local clone (development)

```bash
git clone https://github.com/392fyc/claude-handoff.git
claude plugin marketplace add /path/to/claude-handoff
claude plugin install claude-handoff@claude-handoff --scope local
```

### Update after pulling changes

```bash
claude plugin update claude-handoff@claude-handoff
# Then restart the session — plugin cache has version delay
```

## Usage

In any Claude Code session:

```
/handoff
```

The skill will:
1. Build a handoff document from conversation context, memory, and project docs
2. Write it to `~/.claude/projects/<encoded-cwd>/memory/session-handoff.md`
3. Print the Starting Prompt in the chat so you can paste it manually if preferred
4. Ask whether to launch a new terminal tab automatically

After the new session starts, exit the old one (`/exit` or close the terminal).

## Requirements

- [Claude Code](https://claude.com/claude-code)
- Python 3.8+ (stdlib only; no pip installs required)
- Windows Terminal (`wt`) for auto-launch on Windows, or any terminal that can background `claude` on macOS/Linux

## How It Works

### SessionStart hook (`hooks/session-start.py`)

On every new session, the hook checks `~/.claude/projects/<encoded-cwd>/memory/session-handoff.md`. If present and non-empty, it outputs the contents wrapped in a `<session-handoff>` tag via the `hookSpecificOutput.additionalContext` channel. Claude Code then injects that into the new session's first turn.

### `/handoff` skill

The skill prompt (see `skills/handoff/SKILL.md`) instructs Claude how to:
- Gather layered context (conversation, memory, docs, optional VCS)
- Compose a self-contained Starting Prompt
- Write the handoff document
- Output the prompt to chat AND launch a new session

### Data paths

- `$CLAUDE_PLUGIN_DATA` — hook logs (`poc.log`) live here (~/.claude/plugins/data/claude-handoff-\<marketplace\>/)
- `~/.claude/projects/<encoded-cwd>/memory/` — handoff documents (managed by Claude Code's auto-memory)

Path encoding: replace `:`, `\`, `/` in cwd with `-`, strip leading `-`. E.g. `D:\Work\Project` → `D--Work-Project`.

## File Layout

```
claude-handoff-plugin/
├── .claude-plugin/
│   ├── plugin.json          # plugin manifest
│   └── marketplace.json     # local marketplace descriptor
├── hooks/
│   ├── hooks.json           # registers SessionStart hook
│   └── session-start.py     # injects handoff doc as additionalContext
├── skills/
│   └── handoff/
│       └── SKILL.md         # /handoff command definition
├── LICENSE
└── README.md
```

## License

[MIT](./LICENSE)
