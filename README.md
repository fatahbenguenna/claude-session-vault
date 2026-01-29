# Claude Session Vault 🗄️

Persist and search all your Claude Code sessions in a local SQLite database.

## Features

- **Automatic persistence** - All prompts, tool uses, and responses are saved
- **Full-text search** - Find anything from any session instantly
- **Session browser** - Navigate through your Claude Code history
- **Export** - Export sessions to Markdown, JSON, or text
- **Statistics** - See your usage patterns and top tools
- **Zero config** - Just install and go

## Installation

### Option 1: pip install (recommended)

```bash
pip install claude-session-vault
claude-vault-install
```

### Option 2: Install from source

```bash
git clone https://github.com/fatahbenguenna/claude-session-vault.git
cd claude-session-vault
pip install -e .
claude-vault-install
```

### Option 3: One-liner

```bash
pip install claude-session-vault && python -c "from claude_vault.installer import install_hooks; install_hooks()"
```

## Usage

### Search your history

```bash
# Full-text search across all sessions
claude-vault search "authentication bug"

# Search within a specific session
claude-vault search "login" --session abc123

# Search only tool uses
claude-vault search "Edit" --type PostToolUse

# Output as JSON
claude-vault search "database" --json
```

### Browse sessions

```bash
# List recent sessions
claude-vault sessions

# Filter by project
claude-vault sessions --project fps-api

# Show more sessions
claude-vault sessions -n 50
```

### View a session

```bash
# Show all events in a session
claude-vault show abc123

# Show only user prompts
claude-vault show abc123 --prompts-only

# Show only tool uses
claude-vault show abc123 --tools-only

# Output as JSON
claude-vault show abc123 --json
```

### Export sessions

```bash
# Export to Markdown
claude-vault export session.md --session abc123

# Export to JSON
claude-vault export session.json --session abc123 --format json

# Export to plain text
claude-vault export session.txt --session abc123 --format txt
```

### Statistics

```bash
# View vault statistics
claude-vault stats

# Output as JSON
claude-vault stats --json
```

### Other commands

```bash
# Show database path
claude-vault path

# Show installation instructions
claude-vault install

# Get help
claude-vault --help
```

## How it works

Claude Session Vault uses Claude Code's [hooks system](https://docs.anthropic.com/en/docs/claude-code/hooks) to intercept events:

1. **SessionStart** - Records when a new session begins
2. **UserPromptSubmit** - Captures every prompt you send
3. **PostToolUse** - Records tool executions and results
4. **SessionEnd** - Marks session as completed

All data is stored locally in `~/.claude/vault.db` (SQLite with FTS5 for fast search).

## Configuration

The installer adds these hooks to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{"type": "command", "command": "claude-vault-hook"}]
    }],
    "UserPromptSubmit": [{
      "hooks": [{"type": "command", "command": "claude-vault-hook"}]
    }],
    "PostToolUse": [{
      "matcher": "*",
      "hooks": [{"type": "command", "command": "claude-vault-hook"}]
    }],
    "SessionEnd": [{
      "hooks": [{"type": "command", "command": "claude-vault-hook"}]
    }]
  }
}
```

## Uninstall

```bash
# Remove hooks from Claude Code settings
python -c "from claude_vault.installer import uninstall_hooks; uninstall_hooks()"

# Uninstall package
pip uninstall claude-session-vault

# Optionally remove database
rm ~/.claude/vault.db
```

## Database Schema

```sql
-- Sessions table
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY,
    session_id TEXT UNIQUE,
    project_path TEXT,
    project_name TEXT,
    started_at TIMESTAMP,
    ended_at TIMESTAMP
);

-- Events table (with FTS5 for search)
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    event_type TEXT,
    tool_name TEXT,
    tool_input TEXT,
    tool_response TEXT,
    prompt TEXT,
    cwd TEXT,
    transcript_path TEXT,
    timestamp TIMESTAMP
);
```

## Share with friends

Just tell them to run:

```bash
pipx install git+https://github.com/fatahbenguenna/claude-session-vault.git && claude-vault-install
```

Or share this repository!

## Requirements

- Python 3.10+
- Claude Code CLI

## License

MIT
