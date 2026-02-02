# Claude Session Vault

## Project Overview

**Claude Session Vault** is a CLI tool that persists, browses, and searches Claude Code sessions in a local SQLite database with full-text search (FTS5). It captures session events via Claude Code hooks and provides an interactive TUI browser similar to `claude --resume` but with powerful search capabilities.

## Tech Stack

- **Language**: Python 3.10+
- **CLI Framework**: Click
- **Terminal UI**: Rich (formatting), Textual (TUI browser)
- **Database**: SQLite with FTS5 for full-text search
- **Package Manager**: Hatchling (build backend)

## Architecture

```
src/claude_vault/
├── cli.py          # Main CLI entry point (Click commands)
├── db.py           # Database operations (SQLite + FTS5)
├── tui.py          # Interactive TUI browser (Textual)
├── utils.py        # Shared utility functions
├── hooks.py        # Claude Code hooks handler
├── installer.py    # Installation utilities
└── mcp_server.py   # MCP server functionality
```

### Key Components

| Module | Purpose |
|--------|---------|
| `cli.py` | Click-based CLI with commands: browse, search, sessions, show, export, sync, stats, check |
| `db.py` | SQLite operations with FTS5 virtual tables, context manager for safe DB access |
| `tui.py` | Textual-based interactive browser with fuzzy search and keyboard navigation |
| `utils.py` | Shared helpers: datetime parsing, content extraction, path decoding, session file lookup |
| `hooks.py` | Processes Claude Code hook events (SessionStart, UserPromptSubmit, PostToolUse, SessionEnd) |
| `installer.py` | Configures Claude Code settings.json with required hooks |

## Database Schema

Three main tables with FTS5 virtual tables for search:

```sql
-- Sessions metadata
sessions (session_id, project_name, custom_name, started_at, ended_at)

-- Hook events (real-time capture)
events (session_id, event_type, tool_name, tool_input, prompt, timestamp)

-- Full transcript content (synced from JSONL)
transcript_entries (session_id, line_number, entry_type, role, content, raw_json)
```

FTS5 tables: `events_fts`, `transcript_fts`

## Development Commands

```bash
# Install in editable mode
pip install -e .

# Run the CLI
claude-vault browse          # Interactive TUI
claude-vault search "query"  # Full-text search
claude-vault stats           # Usage statistics
claude-vault sync --all      # Sync all JSONL files
```

## Code Conventions

- **CLI Commands**: Use Click decorators with explicit help text and examples
- **Database**: Prefer `db_cursor()` context manager for safe DB operations
- **Utilities**: Use functions from `utils.py` for datetime parsing, content extraction, etc.
- **Error Handling**: Use Rich console for user-friendly error messages
- **Type Hints**: Required for all function signatures
- **Docstrings**: Required for all public functions
- **DRY Principle**: Extract shared logic to `utils.py` to avoid duplication

## Entry Points

Defined in `pyproject.toml`:
- `claude-vault` → `cli:main` (main CLI)
- `claude-vault-hook` → `hooks:main` (hook handler)
- `claude-vault-install` → `installer:main` (installer)
- `claude-vault-mcp` → `mcp_server:main` (MCP server)

## Data Flow

1. **Real-time capture**: Claude Code hooks → `hooks.py` → `events` table
2. **Batch sync**: JSONL files → `sync` command → `transcript_entries` table
3. **Search**: Query → FTS5 virtual tables → Results with highlighting
4. **Export**: Database entries → Markdown/JSON formatted output

## Testing

No test framework currently configured. When adding tests:
- Use pytest
- Mock SQLite connections
- Test CLI commands with Click's CliRunner

## Important Notes

- Database location: `~/.claude/vault.db`
- Claude Code projects: `~/.claude/projects/`
- The TUI browser is the default command (running `claude-vault` without arguments)
- FTS5 uses SQLite's built-in full-text search with triggers for auto-sync
