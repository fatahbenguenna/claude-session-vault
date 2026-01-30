#!/usr/bin/env python3
"""CLI for searching and browsing Claude Code sessions."""

import json
import click
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich import box

from claude_vault.db import (
    init_db,
    search_events,
    list_sessions,
    get_session_events,
    get_stats,
    get_db_path,
    get_transcript_entries,
    sync_transcript_entries,
)

console = Console()


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate the Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def find_similar_commands(cmd: str, commands: List[str], max_distance: int = 2) -> List[str]:
    """Find similar commands based on Levenshtein distance."""
    suggestions = []
    for command in commands:
        distance = levenshtein_distance(cmd.lower(), command.lower())
        if distance <= max_distance:
            suggestions.append((command, distance))

    # Sort by distance and return command names
    suggestions.sort(key=lambda x: x[1])
    return [s[0] for s in suggestions]


class SuggestingGroup(click.Group):
    """Custom Click Group that suggests similar commands on typos."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as e:
            # Command not found, try to suggest
            if args:
                cmd_name = args[0]
                available_commands = list(self.commands.keys())
                suggestions = find_similar_commands(cmd_name, available_commands)

                console.print(f"\n[red]Error:[/red] '{cmd_name}' is not a valid command.\n")

                if suggestions:
                    suggestion = suggestions[0]
                    console.print(f"[yellow]Did you mean:[/yellow] [green]claude-vault {suggestion}[/green] ?\n")
                else:
                    console.print("[yellow]No similar command found.[/yellow]\n")

                console.print(f"[dim]Available commands: {', '.join(sorted(available_commands))}[/dim]\n")
                ctx.exit(1)
            raise


@click.group(cls=SuggestingGroup, invoke_without_command=True)
@click.version_option(version="1.0.0")
@click.pass_context
def main(ctx):
    """Claude Session Vault - Search and browse your Claude Code history.

    \b
    Running 'claude-vault' without arguments opens the interactive browser.

    \b
    Examples:
        claude-vault                      # Opens interactive browser
        claude-vault browse               # Same as above
        claude-vault search "auth"        # Search sessions
        claude-vault sessions             # List all sessions
        claude-vault show abc123          # Show session details
        claude-vault stats                # Usage statistics
    """
    # Ensure DB is initialized
    init_db()

    # If no subcommand given, invoke browse
    if ctx.invoked_subcommand is None:
        ctx.invoke(browse)


@main.command()
@click.argument("query")
@click.option("-n", "--limit", default=20, help="Number of results to return")
@click.option("-s", "--session", default=None, help="Filter by session ID")
@click.option("-t", "--type", "event_type", default=None, help="Filter by event type")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("-i", "--interactive", is_flag=True, help="Interactive mode: choose session to show/export")
def search(query: str, limit: int, session: Optional[str], event_type: Optional[str], as_json: bool, interactive: bool):
    """Full-text search across all sessions.

    \b
    Examples:
        claude-vault search "login bug"
        claude-vault search "Edit" --type PostToolUse
        claude-vault search "database" --session abc123
        claude-vault search "auth" -i   # Interactive mode
    """
    results = search_events(query, limit=limit, session_id=session, event_type=event_type)

    if not results:
        console.print(f"[yellow]No results found for '{query}'[/yellow]")
        return

    if as_json:
        click.echo(json.dumps(results, indent=2, default=str))
        return

    # Extract unique sessions (preserving order of first occurrence)
    unique_sessions = []
    seen_sessions = set()
    for r in results:
        sid = r.get('session_id', '')
        if sid and sid not in seen_sessions:
            seen_sessions.add(sid)
            unique_sessions.append({
                'session_id': sid,
                'project_name': r.get('project_name', '-'),
                'timestamp': r.get('timestamp', '')
            })

    table = Table(title=f"Search Results for '{query}'", box=box.ROUNDED)
    if interactive:
        table.add_column("#", style="bold white", width=3)
    table.add_column("Session", style="magenta", width=8)
    table.add_column("Time", style="dim", width=19)
    table.add_column("Project", style="cyan", width=15)
    table.add_column("Type", style="green", width=15)
    table.add_column("Content", style="yellow", max_width=40)

    # Map row index to session for interactive mode
    row_to_session = {}
    row_idx = 0

    for r in results:
        session_id = r.get('session_id', '')[:8] if r.get('session_id') else '-'
        timestamp = r.get('timestamp', '')[:19] if r.get('timestamp') else ''
        project = r.get('project_name', '-')[:15] if r.get('project_name') else '-'
        evt_type = r.get('event_type', '-')

        # Show relevant content based on event type
        content = ''
        if r.get('tool_name'):
            content = r['tool_name']
        elif r.get('prompt'):
            prompt = r.get('prompt', '')
            content = prompt[:40] + '...' if len(prompt) > 40 else prompt

        if interactive:
            row_idx += 1
            row_to_session[row_idx] = r.get('session_id', '')
            table.add_row(str(row_idx), session_id, timestamp, project, evt_type, content)
        else:
            table.add_row(session_id, timestamp, project, evt_type, content)

    console.print(table)

    if interactive and unique_sessions:
        console.print(f"\n[dim]Found {len(results)} results in {len(unique_sessions)} sessions.[/dim]\n")

        # Show unique sessions menu
        console.print("[bold]Sessions found:[/bold]")
        for idx, s in enumerate(unique_sessions, 1):
            sid_short = s['session_id'][:8]
            proj = s['project_name'][:20] if s['project_name'] else '-'
            console.print(f"  [bold white]{idx}[/bold white]. [magenta]{sid_short}[/magenta] - [cyan]{proj}[/cyan]")

        console.print(f"\n  [dim]0. Exit[/dim]")

        try:
            choice = click.prompt("\nSelect session number", type=int, default=0)

            if choice == 0:
                return

            if 1 <= choice <= len(unique_sessions):
                selected = unique_sessions[choice - 1]
                sid = selected['session_id']

                console.print(f"\n[bold]Selected:[/bold] [magenta]{sid[:8]}[/magenta]\n")
                console.print("  [bold white]1[/bold white]. Show session details")
                console.print("  [bold white]2[/bold white]. Export to Markdown")
                console.print("  [bold white]3[/bold white]. Export to JSON")
                console.print("  [dim]0. Cancel[/dim]")

                action = click.prompt("\nAction", type=int, default=0)

                if action == 1:
                    # Invoke show command
                    ctx = click.get_current_context()
                    ctx.invoke(show, session_id=sid, limit=100, as_json=False, prompts_only=False, tools_only=False)
                elif action == 2:
                    filename = click.prompt("Output file", default=f"session-{sid[:8]}.md")
                    ctx = click.get_current_context()
                    ctx.invoke(export, output_path=filename, session=sid, fmt='md')
                elif action == 3:
                    filename = click.prompt("Output file", default=f"session-{sid[:8]}.json")
                    ctx = click.get_current_context()
                    ctx.invoke(export, output_path=filename, session=sid, fmt='json')
            else:
                console.print("[red]Invalid selection[/red]")

        except (click.Abort, KeyboardInterrupt):
            console.print("\n[dim]Cancelled[/dim]")
    else:
        console.print(f"\n[dim]Found {len(results)} results. Use 'claude-vault export <session-id>' to export.[/dim]")


@main.command()
@click.option("-n", "--limit", default=20, help="Number of sessions to show")
@click.option("-p", "--project", default=None, help="Filter by project name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def sessions(limit: int, project: Optional[str], as_json: bool):
    """List all recorded sessions.

    \b
    Examples:
        claude-vault sessions
        claude-vault sessions --project fps-api
        claude-vault sessions -n 50
    """
    results = list_sessions(limit=limit, project_filter=project)

    if not results:
        console.print("[yellow]No sessions found[/yellow]")
        return

    if as_json:
        click.echo(json.dumps(results, indent=2, default=str))
        return

    table = Table(title="Claude Code Sessions", box=box.ROUNDED)
    table.add_column("Session ID", style="cyan", width=12)
    table.add_column("Project", style="green", width=20)
    table.add_column("Events", justify="right", style="yellow", width=8)
    table.add_column("Started", style="dim", width=19)
    table.add_column("Last Activity", style="dim", width=19)

    for s in results:
        session_id = s.get('session_id', '')[:12] if s.get('session_id') else ''
        project_name = s.get('project_name', '-')[:20] if s.get('project_name') else '-'
        event_count = str(s.get('event_count', 0))
        started = s.get('started_at', '')[:19] if s.get('started_at') else '-'
        last = s.get('last_activity', '')[:19] if s.get('last_activity') else '-'

        table.add_row(session_id, project_name, event_count, started, last)

    console.print(table)


@main.command()
@click.argument("session_id")
@click.option("-n", "--limit", default=100, help="Number of events to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.option("--prompts-only", is_flag=True, help="Show only user prompts")
@click.option("--tools-only", is_flag=True, help="Show only tool uses")
def show(session_id: str, limit: int, as_json: bool, prompts_only: bool, tools_only: bool):
    """Show events from a specific session.

    \b
    Examples:
        claude-vault show abc123
        claude-vault show abc123 --prompts-only
        claude-vault show abc123 --tools-only
    """
    events = get_session_events(session_id, limit=limit)

    if not events:
        # Try partial match
        from claude_vault.db import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT session_id FROM sessions WHERE session_id LIKE ? LIMIT 1",
            (f"{session_id}%",)
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            events = get_session_events(row[0], limit=limit)
        else:
            console.print(f"[red]Session '{session_id}' not found[/red]")
            return

    # Filter if requested
    if prompts_only:
        events = [e for e in events if e.get('event_type') == 'UserPromptSubmit']
    elif tools_only:
        events = [e for e in events if e.get('tool_name')]

    if as_json:
        click.echo(json.dumps(events, indent=2, default=str))
        return

    console.print(Panel(f"[bold]Session: {session_id}[/bold]", subtitle=f"{len(events)} events"))

    for e in events:
        event_type = e.get('event_type', 'unknown')
        timestamp = e.get('timestamp', '')[:19] if e.get('timestamp') else ''

        if event_type == 'UserPromptSubmit' and e.get('prompt'):
            console.print(f"\n[bold blue]► User Prompt[/bold blue] [dim]{timestamp}[/dim]")
            console.print(Panel(e['prompt'], border_style="blue"))

        elif e.get('tool_name'):
            style = "green" if event_type == 'PostToolUse' else "yellow"
            console.print(f"\n[bold {style}]⚡ {e['tool_name']}[/bold {style}] [dim]{timestamp}[/dim]")

            if e.get('tool_input'):
                try:
                    input_data = json.loads(e['tool_input']) if isinstance(e['tool_input'], str) else e['tool_input']
                    input_str = json.dumps(input_data, indent=2)[:500]
                    console.print(Syntax(input_str, "json", theme="monokai", line_numbers=False))
                except:
                    console.print(f"[dim]{str(e['tool_input'])[:500]}[/dim]")

        elif event_type in ('SessionStart', 'SessionEnd'):
            icon = "🚀" if event_type == 'SessionStart' else "🏁"
            console.print(f"\n{icon} [bold]{event_type}[/bold] [dim]{timestamp}[/dim]")


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def stats(as_json: bool):
    """Show vault statistics.

    \b
    Examples:
        claude-vault stats
        claude-vault stats --json
    """
    data = get_stats()

    if as_json:
        click.echo(json.dumps(data, indent=2))
        return

    console.print(Panel("[bold]Claude Session Vault Statistics[/bold]"))

    # General stats
    table = Table(box=box.SIMPLE)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")

    table.add_row("Total Sessions", str(data.get('total_sessions', 0)))
    table.add_row("Total Events", str(data.get('total_events', 0)))
    table.add_row("Transcript Entries", str(data.get('total_transcript_entries', 0)))
    table.add_row("Sessions with Full Transcript", str(data.get('sessions_with_transcripts', 0)))
    table.add_row("Database Size", f"{data.get('db_size_mb', 0)} MB")

    console.print(table)

    # Events by type
    if data.get('events_by_type'):
        console.print("\n[bold]Events by Type:[/bold]")
        for event_type, count in data['events_by_type'].items():
            bar_len = min(count // 10, 40)
            bar = "█" * bar_len
            console.print(f"  {event_type:25} {bar} {count}")

    # Top projects
    if data.get('top_projects'):
        console.print("\n[bold]Top Projects:[/bold]")
        for project, count in list(data['top_projects'].items())[:5]:
            console.print(f"  [cyan]{project}[/cyan]: {count} sessions")

    # Top tools
    if data.get('top_tools'):
        console.print("\n[bold]Most Used Tools:[/bold]")
        for tool, count in list(data['top_tools'].items())[:5]:
            console.print(f"  [yellow]{tool}[/yellow]: {count} uses")


def parse_jsonl_transcript(transcript_path: str) -> list:
    """Parse a Claude Code JSONL transcript file into conversation format."""
    messages = []
    transcript = Path(transcript_path)

    if not transcript.exists():
        return []

    with open(transcript, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                msg_type = entry.get('type')

                if msg_type == 'user':
                    # User message
                    message = entry.get('message', {})
                    content = message.get('content', '')
                    if isinstance(content, list):
                        # Extract text from content blocks
                        content = '\n'.join(
                            block.get('text', '') for block in content
                            if isinstance(block, dict) and block.get('type') == 'text'
                        )
                    if content:
                        messages.append({
                            'role': 'user',
                            'content': content,
                            'timestamp': entry.get('timestamp', '')
                        })

                elif msg_type == 'assistant':
                    # Assistant message
                    message = entry.get('message', {})
                    content_blocks = message.get('content', [])

                    text_parts = []
                    tool_uses = []

                    for block in content_blocks:
                        if isinstance(block, dict):
                            if block.get('type') == 'text':
                                text_parts.append(block.get('text', ''))
                            elif block.get('type') == 'tool_use':
                                tool_uses.append({
                                    'name': block.get('name', 'unknown'),
                                    'input': block.get('input', {})
                                })

                    if text_parts or tool_uses:
                        messages.append({
                            'role': 'assistant',
                            'content': '\n'.join(text_parts),
                            'tool_uses': tool_uses,
                            'timestamp': entry.get('timestamp', '')
                        })

                elif msg_type == 'tool_result':
                    # Tool result - we can skip these in export or include as collapsed
                    pass

            except json.JSONDecodeError:
                continue

    return messages


def parse_db_transcript_entries(entries: list) -> list:
    """Convert database transcript entries to conversation format using raw_json."""
    messages = []

    for entry in entries:
        raw_json = entry.get('raw_json')
        if not raw_json:
            continue

        try:
            data = json.loads(raw_json)
            msg_type = data.get('type')

            if msg_type == 'user':
                message = data.get('message', {})
                content = message.get('content', '')
                if isinstance(content, list):
                    content = '\n'.join(
                        block.get('text', '') for block in content
                        if isinstance(block, dict) and block.get('type') == 'text'
                    )
                if content:
                    messages.append({
                        'role': 'user',
                        'content': content,
                        'timestamp': data.get('timestamp', '')
                    })

            elif msg_type == 'assistant':
                message = data.get('message', {})
                content_blocks = message.get('content', [])

                text_parts = []
                tool_uses = []

                for block in content_blocks:
                    if isinstance(block, dict):
                        if block.get('type') == 'text':
                            text_parts.append(block.get('text', ''))
                        elif block.get('type') == 'tool_use':
                            tool_uses.append({
                                'name': block.get('name', 'unknown'),
                                'input': block.get('input', {})
                            })

                if text_parts or tool_uses:
                    messages.append({
                        'role': 'assistant',
                        'content': '\n'.join(text_parts),
                        'tool_uses': tool_uses,
                        'timestamp': data.get('timestamp', '')
                    })

        except json.JSONDecodeError:
            continue

    return messages


@main.command()
@click.argument("output_path", type=click.Path())
@click.option("-s", "--session", required=True, help="Session ID to export")
@click.option("-f", "--format", "fmt", type=click.Choice(['json', 'md', 'txt']), default='md')
def export(output_path: str, session: str, fmt: str):
    """Export a session to a file (reads from vault database, independent of JSONL files).

    \b
    The vault stores full conversation content in its database, so exports work
    even if Claude's JSONL files are deleted or the machine was restored.

    \b
    Examples:
        claude-vault export session.md --session abc123
        claude-vault export session.json --session abc123 --format json
    """
    from claude_vault.db import get_connection

    # Find full session ID and transcript path
    conn = get_connection()
    cursor = conn.cursor()

    # Try exact match first, then partial
    cursor.execute("""
        SELECT e.session_id, e.transcript_path, s.project_name
        FROM events e
        LEFT JOIN sessions s ON e.session_id = s.session_id
        WHERE e.session_id = ? AND e.transcript_path IS NOT NULL
        LIMIT 1
    """, (session,))
    row = cursor.fetchone()

    if not row:
        cursor.execute("""
            SELECT e.session_id, e.transcript_path, s.project_name
            FROM events e
            LEFT JOIN sessions s ON e.session_id = s.session_id
            WHERE e.session_id LIKE ? AND e.transcript_path IS NOT NULL
            LIMIT 1
        """, (f"{session}%",))
        row = cursor.fetchone()

    conn.close()

    if not row:
        console.print(f"[red]Session '{session}' not found[/red]")
        return

    full_session_id = row[0]
    transcript_path = row[1]
    project_name = row[2] or "Unknown Project"

    output = Path(output_path)

    # Strategy: Try database first (independent), then fallback to JSONL
    messages = []

    # 1. First try to get from database (independent of JSONL files)
    db_entries = get_transcript_entries(full_session_id)
    if db_entries:
        messages = parse_db_transcript_entries(db_entries)
        console.print(f"[dim]Using {len(db_entries)} entries from vault database[/dim]")

    # 2. If no database entries, try to sync from JSONL and retry
    if not messages and transcript_path:
        synced = sync_transcript_entries(full_session_id, transcript_path)
        if synced > 0:
            console.print(f"[dim]Synced {synced} entries from JSONL to database[/dim]")
            db_entries = get_transcript_entries(full_session_id)
            messages = parse_db_transcript_entries(db_entries)

    # 3. Final fallback: direct JSONL parsing (for backward compatibility)
    if not messages and transcript_path:
        messages = parse_jsonl_transcript(transcript_path)
        if messages:
            console.print(f"[yellow]Warning: Using JSONL directly (not synced to vault)[/yellow]")

    if fmt == 'json':
        output.write_text(json.dumps(messages, indent=2, default=str, ensure_ascii=False))
        console.print(f"[green]Exported {len(messages)} messages to {output_path}[/green]")
        return

    elif fmt == 'md':
        lines = [
            f"# Claude Code Session",
            f"",
            f"**Project:** {project_name}",
            f"**Session ID:** `{full_session_id}`",
            f"",
            f"---",
            f""
        ]

        for msg in messages:
            timestamp = msg.get('timestamp', '')[:19].replace('T', ' ')
            role = msg.get('role', 'unknown')

            if role == 'user':
                lines.append(f"## 👤 User")
                if timestamp:
                    lines.append(f"*{timestamp}*")
                lines.append("")
                lines.append(msg.get('content', ''))
                lines.append("")

            elif role == 'assistant':
                lines.append(f"## 🤖 Assistant")
                if timestamp:
                    lines.append(f"*{timestamp}*")
                lines.append("")

                content = msg.get('content', '')
                if content:
                    lines.append(content)
                    lines.append("")

                # Show tool uses
                tool_uses = msg.get('tool_uses', [])
                if tool_uses:
                    for tool in tool_uses:
                        tool_name = tool.get('name', 'unknown')
                        tool_input = tool.get('input', {})
                        lines.append(f"**Tool:** `{tool_name}`")

                        # Format tool input nicely
                        if isinstance(tool_input, dict):
                            if tool_name == 'Bash' and 'command' in tool_input:
                                lines.append(f"```bash")
                                lines.append(tool_input['command'])
                                lines.append(f"```")
                            elif tool_name in ('Read', 'Write', 'Edit', 'Glob', 'Grep'):
                                lines.append(f"```")
                                for k, v in tool_input.items():
                                    if isinstance(v, str) and len(v) > 200:
                                        v = v[:200] + "..."
                                    lines.append(f"{k}: {v}")
                                lines.append(f"```")
                            else:
                                lines.append(f"```json")
                                lines.append(json.dumps(tool_input, indent=2, ensure_ascii=False)[:500])
                                lines.append(f"```")
                        lines.append("")

            lines.append("---")
            lines.append("")

        output.write_text('\n'.join(lines), encoding='utf-8')
        console.print(f"[green]Exported {len(messages)} messages to {output_path}[/green]")
        return

    else:  # txt
        lines = []
        for e in events:
            lines.append(f"[{e.get('timestamp', '')}] {e.get('event_type', '')}")
            if e.get('prompt'):
                lines.append(f"  Prompt: {e['prompt'][:200]}")
            if e.get('tool_name'):
                lines.append(f"  Tool: {e['tool_name']}")
        output.write_text('\n'.join(lines))

    console.print(f"[green]Exported to {output_path}[/green]")


@main.command()
def install():
    """Show installation instructions for Claude Code hooks."""
    instructions = """
# Claude Session Vault - Installation

## 1. Add hooks to your Claude Code settings

Add this to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "claude-vault-hook"
      }]
    }],
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "claude-vault-hook"
      }]
    }],
    "PostToolUse": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "claude-vault-hook"
      }]
    }],
    "SessionEnd": [{
      "hooks": [{
        "type": "command",
        "command": "claude-vault-hook"
      }]
    }]
  }
}
```

## 2. Verify installation

```bash
# Check vault status
claude-vault stats

# Search your history
claude-vault search "your query"
```

## 3. Database location

Your sessions are stored in: ~/.claude/vault.db
    """
    console.print(Markdown(instructions))


@main.command()
def path():
    """Show the database file path."""
    db_path = get_db_path()
    console.print(f"[cyan]{db_path}[/cyan]")

    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        console.print(f"[dim]Size: {size_mb:.2f} MB[/dim]")
    else:
        console.print("[yellow]Database not yet created[/yellow]")


@main.command()
@click.option("-s", "--session", default=None, help="Sync a specific session ID")
@click.option("-a", "--all", "sync_all", is_flag=True, help="Scan all JSONL files in ~/.claude/projects")
@click.option("-f", "--force", is_flag=True, help="Delete existing entries and re-sync from scratch")
def sync(session: Optional[str], sync_all: bool, force: bool):
    """Sync transcript content from JSONL files to the vault database.

    \b
    This enables full-text search in session content and makes exports
    independent of Claude's original files.

    \b
    Examples:
        claude-vault sync              # Sync sessions tracked by hooks
        claude-vault sync --all        # Scan ALL JSONL files (~15k files)
        claude-vault sync --force      # Re-sync from scratch (deletes existing)
        claude-vault sync -s abc123    # Sync specific session
    """
    from claude_vault.db import get_connection, sync_transcript_entries, init_db

    init_db()
    conn = get_connection()
    cursor = conn.cursor()

    # Handle --force: delete all existing transcript entries
    if force:
        cursor.execute("SELECT COUNT(*) FROM transcript_entries")
        count = cursor.fetchone()[0]
        if count > 0:
            console.print(f"[yellow]Deleting {count} existing transcript entries...[/yellow]")
            cursor.execute("DELETE FROM transcript_entries")
            # Also rebuild FTS index
            try:
                cursor.execute("DELETE FROM transcript_fts")
            except:
                pass
            conn.commit()
            console.print("[green]✓ Cleared existing data[/green]")

    synced_total = 0
    sessions_synced = 0

    if session:
        # Sync single session - find its transcript path
        cursor.execute("""
            SELECT DISTINCT session_id, transcript_path
            FROM events
            WHERE session_id LIKE ? AND transcript_path IS NOT NULL
            LIMIT 1
        """, (f"{session}%",))
        row = cursor.fetchone()

        if not row:
            # Try finding the JSONL file directly
            claude_projects = Path.home() / ".claude" / "projects"
            for jsonl_file in claude_projects.rglob(f"{session}*.jsonl"):
                full_session_id = jsonl_file.stem
                synced = sync_transcript_entries(full_session_id, str(jsonl_file))
                console.print(f"[green]Synced {synced} entries for session {full_session_id[:8]}[/green]")
                conn.close()
                return

            console.print(f"[red]Session '{session}' not found[/red]")
            conn.close()
            return

        full_session_id, transcript_path = row[0], row[1]
        synced = sync_transcript_entries(full_session_id, transcript_path)
        console.print(f"[green]Synced {synced} entries for session {full_session_id[:8]}[/green]")
        conn.close()
        return

    if sync_all:
        # Scan Claude's project directories for ALL JSONL files
        claude_projects = Path.home() / ".claude" / "projects"
        if not claude_projects.exists():
            console.print("[yellow]No Claude projects directory found[/yellow]")
            conn.close()
            return

        jsonl_files = list(claude_projects.rglob("*.jsonl"))
        console.print(f"[cyan]Found {len(jsonl_files)} JSONL files to scan...[/cyan]")

        with console.status("[bold green]Syncing transcripts...") as status:
            for jsonl_file in jsonl_files:
                session_id = jsonl_file.stem
                try:
                    new_entries = sync_transcript_entries(session_id, str(jsonl_file))
                    if new_entries > 0:
                        synced_total += new_entries
                        sessions_synced += 1
                        status.update(f"[bold green]Synced {sessions_synced} sessions ({synced_total} entries)")
                except Exception as e:
                    pass  # Skip errors silently
    else:
        # Sync only sessions tracked in the vault (from hooks)
        cursor.execute("""
            SELECT DISTINCT session_id, transcript_path
            FROM events
            WHERE transcript_path IS NOT NULL
        """)
        rows = cursor.fetchall()

        if not rows:
            console.print("[yellow]No sessions tracked yet. Use --all to scan all JSONL files.[/yellow]")
            conn.close()
            return

        console.print(f"[cyan]Found {len(rows)} tracked sessions to sync...[/cyan]")

        with console.status("[bold green]Syncing sessions...") as status:
            for row in rows:
                session_id, transcript_path = row[0], row[1]
                status.update(f"[bold green]Syncing {session_id[:8]}...")
                try:
                    synced = sync_transcript_entries(session_id, transcript_path)
                    if synced > 0:
                        synced_total += synced
                        sessions_synced += 1
                except:
                    pass

    conn.close()

    if synced_total > 0:
        console.print(f"[green]✅ Synced {synced_total} entries across {sessions_synced} sessions[/green]")
    else:
        console.print("[dim]All transcripts already synced (no new content)[/dim]")

    # Rebuild sessions table from transcript entries
    from claude_vault.db import rebuild_sessions_from_transcripts
    console.print("[cyan]Rebuilding sessions index...[/cyan]")
    created = rebuild_sessions_from_transcripts()
    if created > 0:
        console.print(f"[green]✅ Created {created} session records[/green]")
    else:
        console.print("[dim]Sessions index up to date[/dim]")


@main.command()
def update():
    """Update claude-session-vault to the latest version from GitHub."""
    import subprocess
    import shutil

    console.print("[bold]Updating Claude Session Vault...[/bold]\n")

    # Check for pipx
    pipx_path = shutil.which("pipx")
    if pipx_path:
        console.print("[dim]Using pipx...[/dim]")
        result = subprocess.run(
            ["pipx", "install", "git+https://github.com/fatahbenguenna/claude-session-vault.git", "--force"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            console.print("[green]✅ Updated successfully![/green]")
            console.print("[dim]Restart your terminal to use the new version.[/dim]")
        else:
            console.print(f"[red]Update failed:[/red]\n{result.stderr}")
        return

    # Check for uv
    uv_path = shutil.which("uv")
    if uv_path:
        console.print("[dim]Using uv...[/dim]")
        result = subprocess.run(
            ["uv", "tool", "install", "git+https://github.com/fatahbenguenna/claude-session-vault.git", "--force"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            console.print("[green]✅ Updated successfully![/green]")
        else:
            console.print(f"[red]Update failed:[/red]\n{result.stderr}")
        return

    # Fallback to pip
    pip_path = shutil.which("pip3") or shutil.which("pip")
    if pip_path:
        console.print("[dim]Using pip...[/dim]")
        result = subprocess.run(
            [pip_path, "install", "--user", "--upgrade", "git+https://github.com/fatahbenguenna/claude-session-vault.git"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            console.print("[green]✅ Updated successfully![/green]")
        else:
            console.print(f"[red]Update failed:[/red]\n{result.stderr}")
        return

    console.print("[red]No package manager found (pipx, uv, or pip)[/red]")


@main.command()
def version():
    """Show the current version."""
    from claude_vault import __version__
    console.print(f"claude-session-vault [cyan]v{__version__}[/cyan]")


@main.command()
@click.option("--keep-db", is_flag=True, help="Keep the database file")
@click.confirmation_option(prompt="This will remove hooks, database, and uninstall. Continue?")
def uninstall(keep_db: bool):
    """Completely uninstall claude-session-vault.

    \b
    This will:
    1. Remove hooks from Claude Code settings
    2. Delete the database (~800 MB)
    3. Uninstall the package

    \b
    Example:
        claude-vault uninstall           # Full uninstall
        claude-vault uninstall --keep-db # Keep database for later
    """
    import subprocess
    import shutil

    # 1. Remove hooks
    console.print("[cyan]Removing hooks from Claude Code...[/cyan]")
    try:
        from claude_vault.installer import uninstall_hooks
        uninstall_hooks()
        console.print("[green]✓ Hooks removed[/green]")
    except Exception as e:
        console.print(f"[yellow]Warning: Could not remove hooks: {e}[/yellow]")

    # 2. Delete database
    if not keep_db:
        db_path = Path.home() / ".claude" / "vault.db"
        if db_path.exists():
            size_mb = db_path.stat().st_size / (1024 * 1024)
            db_path.unlink()
            console.print(f"[green]✓ Database deleted ({size_mb:.1f} MB freed)[/green]")
        else:
            console.print("[dim]Database not found[/dim]")
    else:
        console.print("[dim]Database kept (--keep-db)[/dim]")

    # 3. Uninstall package
    console.print("[cyan]Uninstalling package...[/cyan]")
    console.print("")
    console.print("[bold]Run this command to complete uninstallation:[/bold]")
    console.print("[yellow]pipx uninstall claude-session-vault[/yellow]")
    console.print("")
    console.print("[dim]Or if installed with pip:[/dim]")
    console.print("[yellow]pip uninstall claude-session-vault[/yellow]")


@main.command()
@click.option("-p", "--project", default=None, help="Filter by project name")
@click.pass_context
def browse(ctx, project: Optional[str]):
    """Interactive TUI to browse and search sessions (like claude --resume).

    \b
    Features:
        - Real-time fuzzy search through all sessions
        - Shows first prompt as session title
        - Relative time (1 minute ago, 2 days ago)
        - Message count per session

    \b
    Keyboard shortcuts:
        - Type: search/filter sessions
        - Enter: select session
        - Ctrl+E: export to Markdown
        - Ctrl+J: export to JSON
        - Esc: quit

    \b
    Examples:
        claude-vault             # Opens browse directly
        claude-vault browse
        claude-vault browse --project fps-api
    """
    try:
        from claude_vault.tui import run_browser
    except ImportError as e:
        console.print(f"[red]TUI dependencies not installed: {e}[/red]")
        console.print("[yellow]Run: pip install textual[/yellow]")
        return

    try:
        result = run_browser(project_filter=project)

        if result is None:
            # User cancelled
            return

        # Handle action results from TUI
        if isinstance(result, dict):
            if result.get('action') == 'export_md':
                session = result['session']
                default_name = f"session-{session['session_id'][:8]}.md"
                console.print(f"\n[cyan]Exporting to {default_name}...[/cyan]")
                ctx.invoke(export, output_path=default_name, session=session['session_id'], fmt='md')

            elif result.get('action') == 'export_json':
                session = result['session']
                default_name = f"session-{session['session_id'][:8]}.json"
                console.print(f"\n[cyan]Exporting to {default_name}...[/cyan]")
                ctx.invoke(export, output_path=default_name, session=session['session_id'], fmt='json')

            elif 'session_id' in result:
                # User selected a session - show it
                ctx.invoke(show, session_id=result['session_id'], limit=100, as_json=False, prompts_only=False, tools_only=False)

    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled[/dim]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
