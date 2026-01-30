# Claude Session Vault 🗄️

Persist, browse, and search all your Claude Code sessions in a local SQLite database.

## Quick Start

```bash
# Install
pipx install git+https://github.com/fatahbenguenna/claude-session-vault.git
claude-vault-install

# Sync existing sessions (one-time, optional)
claude-vault sync --all

# Launch the interactive browser
claude-vault
```

## The Interactive Browser

**`claude-vault` (or `claude-vault browse`)** is the heart of the application. It provides a TUI similar to `claude --resume` but with powerful search capabilities.

```
╭──────────────────────────────────────────────────────────────────────────────╮
│ Browse Sessions (71)                                                         │
├──────────────────────────────────────────────────────────────────────────────┤
│  🔍 Type to search...                                                        │
├──────────────────────────────────────────────────────────────────────────────┤
│  ▼ Sessions                                                                  │
│  ├── 📁 fps-api (12)                                                         │
│  │   ├── ▸ Fix authentication bug in login flow                              │
│  │   │     9 minutes ago · 45 msg                                            │
│  │   ├── ▸ Add Docker multi-env support                                      │
│  │   │     2 hours ago · 120 msg                                             │
│  ├── 📁 my-project (3)                                                       │
│  │   ├── ▸ Implement user dashboard                                          │
│  │   │     yesterday · 89 msg                                                │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### Key Features

| Feature | Description |
|---------|-------------|
| **Fuzzy Search** | Type to filter by project name or session title |
| **Content Search** | Type 3+ characters to search inside conversation content |
| **Full History** | Access all sessions even after Claude purges old ones |
| **Preview** | Press `Ctrl+V` to preview session content |
| **Open in Claude** | From preview, press `o` to resume session in Claude Code |
| **Export** | Press `Ctrl+E` to export to Markdown |
| **Rename** | Press `Ctrl+R` to give a session a custom name |

### Keyboard Shortcuts

**Browser:**
| Key | Action |
|-----|--------|
| `↑` `↓` | Navigate sessions |
| `←` `→` | Collapse/expand project groups |
| `Enter` | Select session |
| `Ctrl+V` | Open preview |
| `Ctrl+E` | Export to Markdown |
| `Ctrl+J` | Export to JSON |
| `Ctrl+R` | Rename session |
| `Esc` | Quit |

**Preview panel:**
| Key | Action |
|-----|--------|
| `e` | Export to Markdown file |
| `c` | Copy to clipboard |
| `o` / `Enter` | Open in Claude Code |
| `Esc` | Close preview |

## Why Claude Session Vault?

| `claude --resume` | `claude-vault` |
|-------------------|----------------|
| ✅ Resume sessions | ✅ Browse all history |
| ❌ Sessions can be purged | ✅ **Permanent archive** |
| ❌ Search by title only | ✅ **Full-text content search** |
| ❌ No export | ✅ **Export to Markdown/JSON** |
| ❌ No statistics | ✅ **Usage analytics** |
| ❌ Closed format | ✅ **Open SQLite database** |

## Installation

### Recommended: pipx

```bash
pipx install git+https://github.com/fatahbenguenna/claude-session-vault.git
claude-vault-install
```

### Alternative: pip

```bash
pip install git+https://github.com/fatahbenguenna/claude-session-vault.git
claude-vault-install
```

### From source

```bash
git clone https://github.com/fatahbenguenna/claude-session-vault.git
cd claude-session-vault
pip install -e .
claude-vault-install
```

## Syncing Existing Sessions

After installation, sync your existing Claude Code history:

```bash
# Sync all JSONL files (recommended first time)
claude-vault sync --all

# Check what was synced
claude-vault stats
```

This enables full-text search across all your past conversations.

## Other Commands

### Search

```bash
# Full-text search across all sessions
claude-vault search "authentication bug"

# Interactive mode - select and export from results
claude-vault search "docker" -i

# Search within a specific session
claude-vault search "login" --session abc123
```

### Sessions List

```bash
# List recent sessions (table view)
claude-vault sessions

# Filter by project
claude-vault sessions --project fps-api
```

### View Session

```bash
# Show all events in a session
claude-vault show abc123

# Show only user prompts
claude-vault show abc123 --prompts-only
```

### Export

```bash
# Export to Markdown (conversation format)
claude-vault export session.md --session abc123

# Export to JSON
claude-vault export session.json --session abc123 --format json
```

### Statistics

```bash
claude-vault stats
```

```
╭──────────────────────────────────────────────────────────────────────────────╮
│ Claude Session Vault Statistics                                              │
╰──────────────────────────────────────────────────────────────────────────────╯
  Metric                              Value
 ───────────────────────────────────────────
  Total Sessions                         39
  Total Events                         1011
  Transcript Entries                 149149
  Sessions with Full Transcript       15510
  Database Size                   795.01 MB

Top Projects: fps-api (31), luxCaRent (2), claude-session-vault (1)
Most Used Tools: Bash (417), Read (327), Edit (67)
```

### Other

```bash
claude-vault path      # Show database location
claude-vault version   # Show version
claude-vault update    # Update from GitHub
claude-vault --help    # All commands
```

## How It Works

Claude Session Vault uses Claude Code's [hooks system](https://docs.anthropic.com/en/docs/claude-code/hooks) to capture events in real-time:

1. **SessionStart** - Records when a new session begins
2. **UserPromptSubmit** - Captures every prompt you send
3. **PostToolUse** - Records tool executions and results
4. **SessionEnd** - Marks session as completed

Additionally, `sync` command parses Claude's JSONL transcript files to extract full conversation content for search.

All data is stored locally in `~/.claude/vault.db` (SQLite with FTS5).

## Database Schema

```sql
-- Sessions
CREATE TABLE sessions (
    session_id TEXT UNIQUE,
    project_name TEXT,
    custom_name TEXT,
    started_at TIMESTAMP
);

-- Hook events (with FTS5 search)
CREATE TABLE events (
    session_id TEXT,
    event_type TEXT,
    tool_name TEXT,
    prompt TEXT,
    timestamp TIMESTAMP
);

-- Full transcript content (with FTS5 search)
CREATE TABLE transcript_entries (
    session_id TEXT,
    role TEXT,
    content TEXT,
    timestamp TIMESTAMP
);
```

## Uninstall

```bash
# Complete uninstall (removes hooks and database)
claude-vault uninstall

# Or keep the database for later
claude-vault uninstall --keep-db

# Then remove the package
pipx uninstall claude-session-vault
```

## Requirements

- Python 3.10+
- Claude Code CLI

## License

MIT
