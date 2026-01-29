#!/usr/bin/env python3
"""CLI for searching and browsing Claude Code sessions."""

import json
import click
from pathlib import Path
from datetime import datetime
from typing import Optional

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
)

console = Console()


@click.group()
@click.version_option(version="1.0.0")
def main():
    """Claude Session Vault - Search and browse your Claude Code history.

    \b
    Examples:
        claude-vault search "authentication"
        claude-vault sessions --project fps-api
        claude-vault show abc123
        claude-vault stats
    """
    # Ensure DB is initialized
    init_db()


@main.command()
@click.argument("query")
@click.option("-n", "--limit", default=20, help="Number of results to return")
@click.option("-s", "--session", default=None, help="Filter by session ID")
@click.option("-t", "--type", "event_type", default=None, help="Filter by event type")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def search(query: str, limit: int, session: Optional[str], event_type: Optional[str], as_json: bool):
    """Full-text search across all sessions.

    \b
    Examples:
        claude-vault search "login bug"
        claude-vault search "Edit" --type PostToolUse
        claude-vault search "database" --session abc123
    """
    results = search_events(query, limit=limit, session_id=session, event_type=event_type)

    if not results:
        console.print(f"[yellow]No results found for '{query}'[/yellow]")
        return

    if as_json:
        click.echo(json.dumps(results, indent=2, default=str))
        return

    table = Table(title=f"Search Results for '{query}'", box=box.ROUNDED)
    table.add_column("Time", style="dim", width=19)
    table.add_column("Project", style="cyan", width=15)
    table.add_column("Type", style="green", width=18)
    table.add_column("Tool/Prompt", style="yellow", max_width=50)

    for r in results:
        timestamp = r.get('timestamp', '')[:19] if r.get('timestamp') else ''
        project = r.get('project_name', '-')[:15] if r.get('project_name') else '-'
        event_type = r.get('event_type', '-')

        # Show relevant content based on event type
        content = ''
        if r.get('tool_name'):
            content = r['tool_name']
        elif r.get('prompt'):
            content = r['prompt'][:50] + '...' if len(r.get('prompt', '')) > 50 else r.get('prompt', '')

        table.add_row(timestamp, project, event_type, content)

    console.print(table)
    console.print(f"\n[dim]Found {len(results)} results[/dim]")


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


@main.command()
@click.argument("output_path", type=click.Path())
@click.option("-s", "--session", required=True, help="Session ID to export")
@click.option("-f", "--format", "fmt", type=click.Choice(['json', 'md', 'txt']), default='md')
def export(output_path: str, session: str, fmt: str):
    """Export a session to a file.

    \b
    Examples:
        claude-vault export session.md --session abc123
        claude-vault export session.json --session abc123 --format json
    """
    events = get_session_events(session, limit=10000)

    if not events:
        console.print(f"[red]Session '{session}' not found[/red]")
        return

    output = Path(output_path)

    if fmt == 'json':
        output.write_text(json.dumps(events, indent=2, default=str))

    elif fmt == 'md':
        lines = [f"# Claude Session: {session}\n"]
        for e in events:
            timestamp = e.get('timestamp', '')[:19]
            event_type = e.get('event_type', '')

            if event_type == 'UserPromptSubmit' and e.get('prompt'):
                lines.append(f"\n## User Prompt ({timestamp})\n")
                lines.append(e['prompt'])
                lines.append("\n")

            elif e.get('tool_name'):
                lines.append(f"\n### Tool: {e['tool_name']} ({timestamp})\n")
                if e.get('tool_input'):
                    lines.append("```json\n")
                    lines.append(e['tool_input'][:1000])
                    lines.append("\n```\n")

        output.write_text('\n'.join(lines))

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


if __name__ == "__main__":
    main()
