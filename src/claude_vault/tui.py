#!/usr/bin/env python3
"""Interactive TUI browser for Claude Session Vault using Textual.

Features like claude --resume:
- Tree structure with projects
- Fuzzy search
- Preview panel (Ctrl+V)
- Rename session (Ctrl+R)
- Arrow key navigation
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import defaultdict

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, Static, Tree, TextArea
from textual.widgets.tree import TreeNode
from textual.containers import Container, Vertical, Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual import on
from rich.text import Text
from rich.panel import Panel
from rich.syntax import Syntax

from claude_vault.db import (
    init_db,
    list_sessions,
    get_session_events,
    get_connection,
    rename_session,
    get_session_custom_name,
    search_sessions_by_content,
    search_sessions_with_content,
)


def relative_time(dt: datetime) -> str:
    """Convert datetime to human-readable relative time."""
    now = datetime.now()
    # Handle timezone-aware datetimes by making dt naive
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 0:
        return "just now"
    elif seconds < 60:
        n = int(seconds)
        return f"{n} second{'s' if n != 1 else ''} ago"
    elif seconds < 3600:
        n = int(seconds / 60)
        return f"{n} minute{'s' if n != 1 else ''} ago"
    elif seconds < 86400:
        n = int(seconds / 3600)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    elif seconds < 604800:
        n = int(seconds / 86400)
        return f"{n} day{'s' if n != 1 else ''} ago"
    elif seconds < 2592000:
        n = int(seconds / 604800)
        return f"{n} week{'s' if n != 1 else ''} ago"
    else:
        n = int(seconds / 2592000)
        return f"{n} month{'s' if n != 1 else ''} ago"


def _is_system_context(text: str) -> bool:
    """Check if text is system-injected context rather than a real user prompt."""
    if not text:
        return True
    text_lower = text.strip().lower()
    # Patterns that indicate system context, not a real user message
    system_patterns = [
        '## your environment',
        '<system-reminder>',
        'sessionstart:',
        'working directory:',
        '**working directory:**',
        'this session is being continued',
        'summary:',  # Session continuation summaries
        'if you need specific details',
        'please continue the conversation',
    ]
    for pattern in system_patterns:
        if text_lower.startswith(pattern):
            return True
    return False


def get_session_title(session_id: str, transcript_path: Optional[str] = None) -> str:
    """Get the first real user prompt as session title (skipping system context)."""
    from claude_vault.db import get_transcript_entries

    # First check for custom name
    custom_name = get_session_custom_name(session_id)
    if custom_name:
        return custom_name

    # Try transcript_entries from database (most reliable after sync)
    entries = get_transcript_entries(session_id)
    for entry in entries:
        raw_json = entry.get('raw_json', '')
        if not raw_json:
            continue
        try:
            data = json.loads(raw_json)
            if data.get('type') in ('user', 'human'):
                message = data.get('message', {})
                content = message.get('content', '')
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'text':
                            text = item.get('text', '').strip()
                            # Skip system-injected context
                            if _is_system_context(text):
                                continue
                            text = text.replace('\n', ' ')[:80]
                            if len(text) > 77:
                                text = text[:77] + "..."
                            return text
                elif isinstance(content, str) and content:
                    text = content.strip()
                    # Skip system-injected context
                    if _is_system_context(text):
                        continue
                    text = text.replace('\n', ' ')[:80]
                    if len(text) > 77:
                        text = text[:77] + "..."
                    return text
        except:
            continue

    # Fallback: events table (from hooks)
    events = get_session_events(session_id, limit=5)
    for event in events:
        if event.get('event_type') == 'UserPromptSubmit' and event.get('prompt'):
            text = event['prompt'].strip().replace('\n', ' ')[:80]
            if len(text) > 77:
                text = text[:77] + "..."
            return text

    return f"Session {session_id[:8]}"


def get_session_preview(session_id: str, transcript_path: Optional[str] = None, max_messages: int = 20) -> str:
    """Get a formatted preview of the session content like Claude Code display."""
    from claude_vault.db import get_transcript_entries

    lines = []

    # Try transcript_entries from database first (synced content)
    entries = get_transcript_entries(session_id)
    if entries:
        message_count = 0
        for entry in entries:
            if message_count >= max_messages:
                remaining = len(entries) - message_count
                if remaining > 0:
                    lines.append(f"\n[dim]... and {remaining} more entries[/dim]")
                break

            raw_json = entry.get('raw_json', '')
            if not raw_json:
                continue

            try:
                data = json.loads(raw_json)
                entry_type = data.get('type', '')

                # User message
                if entry_type in ('user', 'human'):
                    message = data.get('message', {})
                    content = message.get('content', '')

                    # Handle content as list of blocks
                    if isinstance(content, list):
                        content = ' '.join(
                            item.get('text', '') for item in content
                            if isinstance(item, dict) and item.get('type') == 'text'
                        )

                    if content:
                        lines.append("")
                        lines.append("[bold cyan]━━━ 👤 User ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
                        if len(content) > 500:
                            lines.append(content[:500] + "...")
                        else:
                            lines.append(content)
                        message_count += 1

                # Assistant message
                elif entry_type == 'assistant':
                    message = data.get('message', {})
                    content_blocks = message.get('content', [])

                    text_parts = []
                    tool_uses = []

                    for block in content_blocks:
                        if isinstance(block, dict):
                            if block.get('type') == 'text':
                                text_parts.append(block.get('text', ''))
                            elif block.get('type') == 'tool_use':
                                tool_name = block.get('name', 'Unknown')
                                tool_input = block.get('input', {})
                                tool_uses.append((tool_name, tool_input))

                    if text_parts or tool_uses:
                        lines.append("")
                        lines.append("[bold green]━━━ 🤖 Assistant ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold green]")

                        if text_parts:
                            text = '\n'.join(text_parts)
                            if len(text) > 500:
                                lines.append(text[:500] + "...")
                            else:
                                lines.append(text)

                        for tool_name, tool_input in tool_uses:
                            lines.append(f"[bold yellow]⚡ {tool_name}[/bold yellow]")
                            # Show brief input summary
                            if isinstance(tool_input, dict):
                                if 'command' in tool_input:
                                    cmd = str(tool_input['command'])[:80]
                                    lines.append(f"[dim]  $ {cmd}{'...' if len(str(tool_input.get('command', ''))) > 80 else ''}[/dim]")
                                elif 'file_path' in tool_input:
                                    lines.append(f"[dim]  📄 {tool_input['file_path']}[/dim]")
                                elif 'pattern' in tool_input:
                                    lines.append(f"[dim]  🔍 {tool_input['pattern']}[/dim]")

                        message_count += 1

            except json.JSONDecodeError:
                continue

        if lines:
            return '\n'.join(lines)

    # Fallback: database events (from hooks)
    events = get_session_events(session_id, limit=max_messages)
    if events:
        for event in events:
            if event.get('event_type') == 'UserPromptSubmit' and event.get('prompt'):
                lines.append("")
                lines.append("[bold cyan]━━━ 👤 User ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
                text = event['prompt'][:300]
                lines.append(text + ("..." if len(event['prompt']) > 300 else ""))
            elif event.get('tool_name'):
                lines.append(f"[bold yellow]⚡ {event['tool_name']}[/bold yellow]")

    return '\n'.join(lines) if lines else "[dim]No conversation content in this session.\n\nThis session may only contain metadata (file snapshots, etc.)\nor the transcript was not synced yet.\n\nTry: claude-vault sync --all[/dim]"


def session_file_exists(session_id: str, transcript_path: Optional[str] = None) -> bool:
    """Check if the session's JSONL file still exists in Claude's projects directory."""
    # Check transcript_path if provided
    if transcript_path:
        return Path(transcript_path).exists()

    # Search in Claude's projects directory
    claude_projects = Path.home() / ".claude" / "projects"
    if claude_projects.exists():
        for jsonl_file in claude_projects.rglob(f"{session_id}.jsonl"):
            return True

    return False


def get_enriched_sessions(limit: int = 100) -> List[Dict[str, Any]]:
    """Get sessions with enriched data from database."""
    sessions = list_sessions(limit=limit)
    enriched = []

    conn = get_connection()
    cursor = conn.cursor()

    for session in sessions:
        session_id = session['session_id']

        # Get message count from transcript_entries (only real messages, not metadata)
        cursor.execute(
            "SELECT COUNT(*) FROM transcript_entries WHERE session_id = ? AND entry_type IN ('user', 'human', 'assistant')",
            (session_id,)
        )
        row = cursor.fetchone()
        message_count = row[0] if row else 0

        # Skip sessions with no real messages (only metadata)
        if message_count == 0:
            continue

        # Get transcript path from events if available
        cursor.execute(
            "SELECT transcript_path FROM events WHERE session_id = ? AND transcript_path IS NOT NULL LIMIT 1",
            (session_id,)
        )
        row = cursor.fetchone()
        transcript_path = row[0] if row else None

        # Parse last activity time (always use naive datetime for comparison)
        last_activity = session.get('last_activity', '')
        try:
            if last_activity:
                if 'T' in str(last_activity):
                    dt = datetime.fromisoformat(str(last_activity).replace('Z', '+00:00'))
                    # Convert to naive datetime for consistent comparison
                    if dt.tzinfo is not None:
                        dt = dt.replace(tzinfo=None)
                else:
                    dt = datetime.strptime(str(last_activity), '%Y-%m-%d %H:%M:%S')
            else:
                dt = datetime.now()
        except:
            dt = datetime.now()

        # Extract project name
        project_name = session.get('project_name') or session.get('project')
        if not project_name and transcript_path:
            try:
                parts = Path(transcript_path).parent.name
                if parts.startswith('-'):
                    project_name = parts.split('-')[-1]
                else:
                    project_name = parts
            except:
                pass
        if not project_name:
            project_name = 'unknown'

        enriched.append({
            'session_id': session_id,
            'project': project_name,
            'title': get_session_title(session_id, transcript_path),
            'relative_time': relative_time(dt),
            'message_count': message_count,
            'last_activity': dt,
            'transcript_path': transcript_path,
        })

    conn.close()
    enriched.sort(key=lambda x: x['last_activity'], reverse=True)
    return enriched


class RenameScreen(ModalScreen):
    """Modal screen for renaming a session."""

    CSS = """
    RenameScreen {
        align: center middle;
    }

    #rename-dialog {
        width: 60;
        height: 7;
        border: solid #0078d4;
        background: #1e1e1e;
        padding: 1;
    }

    #rename-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #rename-input {
        width: 100%;
    }
    """

    def __init__(self, session: Dict[str, Any]):
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Rename Session", id="rename-title"),
            Input(value=self.session['title'], id="rename-input"),
            id="rename-dialog"
        )

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    @on(Input.Submitted, "#rename-input")
    def on_submit(self, event: Input.Submitted) -> None:
        new_name = event.value.strip()
        if new_name:
            rename_session(self.session['session_id'], new_name)
            self.dismiss(new_name)
        else:
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class PreviewScreen(ModalScreen):
    """Modal screen for previewing a session with conversation format."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
        Binding("e", "export_file", "Export", show=False),
        Binding("c", "copy_clipboard", "Copy", show=False),
        Binding("o", "open_claude", "Open", show=False),
        Binding("enter", "open_claude", "Open in Claude", show=False),
    ]

    CSS = """
    PreviewScreen {
        align: center middle;
    }

    #preview-dialog {
        width: 90%;
        height: 90%;
        border: solid #0078d4;
        background: #1e1e1e;
        padding: 1;
    }

    #preview-title {
        text-align: center;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
        color: #58a6ff;
    }

    #preview-scroll {
        height: 1fr;
        background: #0d1117;
        border: solid #30363d;
    }

    #preview-content {
        padding: 1 2;
    }

    #preview-footer {
        height: auto;
        text-align: center;
        color: #888;
        margin-top: 1;
    }

    #preview-status {
        height: 1;
        text-align: center;
        color: #58a6ff;
    }
    """

    def __init__(self, session: Dict[str, Any]):
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        preview = get_session_preview(
            self.session['session_id'],
            self.session.get('transcript_path'),
            max_messages=50  # Show more messages in full preview
        )

        title = self.session.get('custom_name') or self.session.get('title', 'Session')
        if len(title) > 60:
            title = title[:60] + "..."

        yield Container(
            Static(f"📋 {title}", id="preview-title"),
            VerticalScroll(
                Static(preview, id="preview-content", markup=True),
                id="preview-scroll"
            ),
            Static("", id="preview-status"),
            Static("↑↓:Scroll · e:Export · c:Copy · o/Enter:Open in Claude · Esc:Close", id="preview-footer"),
            id="preview-dialog"
        )

    def action_close(self) -> None:
        """Close the preview and return to session list."""
        self.dismiss()

    def action_export_file(self) -> None:
        """Export session to Markdown file."""
        self.dismiss({"action": "export_md", "session": self.session})

    def action_copy_clipboard(self) -> None:
        """Copy session content to clipboard."""
        import subprocess

        # Get the preview content (plain text without Rich markup)
        session_id = self.session['session_id']
        transcript_path = self.session.get('transcript_path')

        # Build plain text export
        from claude_vault.db import get_transcript_entries
        lines = []
        entries = get_transcript_entries(session_id)

        for entry in entries:
            raw_json = entry.get('raw_json', '')
            if not raw_json:
                continue
            try:
                import json
                data = json.loads(raw_json)
                entry_type = data.get('type', '')

                if entry_type in ('user', 'human'):
                    message = data.get('message', {})
                    content = message.get('content', '')
                    if isinstance(content, list):
                        content = ' '.join(
                            item.get('text', '') for item in content
                            if isinstance(item, dict) and item.get('type') == 'text'
                        )
                    if content:
                        lines.append(f"## User\n{content}\n")

                elif entry_type == 'assistant':
                    message = data.get('message', {})
                    content_blocks = message.get('content', [])
                    text_parts = []
                    for block in content_blocks:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            text_parts.append(block.get('text', ''))
                    if text_parts:
                        lines.append(f"## Assistant\n{chr(10).join(text_parts)}\n")
            except:
                continue

        text = '\n'.join(lines) if lines else "No content to copy"

        # Copy to clipboard using pbcopy (macOS) or xclip (Linux)
        try:
            process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            process.communicate(text.encode('utf-8'))
            self._show_status("[green]✓ Copied to clipboard[/green]")
        except FileNotFoundError:
            try:
                process = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
                process.communicate(text.encode('utf-8'))
                self._show_status("[green]✓ Copied to clipboard[/green]")
            except FileNotFoundError:
                self._show_status("[red]✗ Clipboard not available (install pbcopy or xclip)[/red]")

    def action_open_claude(self) -> None:
        """Open session in Claude Code."""
        session_id = self.session.get('session_id', '')
        transcript_path = self.session.get('transcript_path')

        # Check if the session file still exists
        if not session_file_exists(session_id, transcript_path):
            self._show_status("[red]✗ Cannot open: Claude has deleted this session file.[/red]")
            return

        # Exit and tell CLI to run claude --resume
        self.app.exit({"action": "resume_claude", "session": self.session})

    def _show_status(self, message: str) -> None:
        """Show a status message."""
        status = self.query_one("#preview-status", Static)
        status.update(message)


class SessionBrowser(App):
    """TUI for browsing Claude sessions - like claude --resume."""

    CSS = """
    Screen {
        background: #1e1e1e;
    }

    #header {
        height: 1;
        background: #0078d4;
        color: white;
        padding: 0 1;
        text-style: bold;
    }

    #search-box {
        height: 1;
        background: #2d2d2d;
        padding: 0 1;
    }

    #search-input {
        background: #2d2d2d;
        border: none;
        height: 1;
    }

    #session-tree {
        height: 1fr;
        padding: 0 1;
        background: #1e1e1e;
    }

    #session-tree > .tree--cursor {
        background: #094771;
    }

    #session-tree > .tree--guides {
        color: #555;
    }

    #footer {
        height: 1;
        background: #2d2d2d;
        color: #888888;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Exit", show=True),
        Binding("enter", "select", "Select", show=True),
        Binding("ctrl+e", "export_md", "Export MD", show=True),
        Binding("ctrl+j", "export_json", "JSON", show=True),
        Binding("ctrl+v", "preview", "Preview", show=True),
        Binding("ctrl+r", "rename", "Rename", show=True),
        Binding("ctrl+a", "toggle_all", "Fold All", show=True),
        Binding("ctrl+f", "focus_search", "Search", show=False),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("left", "collapse_group", "Collapse", show=False, priority=True),
        Binding("right", "expand_group", "Expand", show=False, priority=True),
    ]

    def __init__(self, project_filter: Optional[str] = None):
        super().__init__()
        self.project_filter = project_filter
        self.all_sessions: List[Dict[str, Any]] = []
        self.session_nodes: Dict[str, TreeNode] = {}
        self.all_expanded: bool = True  # Track expand/collapse all state

    def compose(self) -> ComposeResult:
        yield Static("Browse Sessions", id="header")
        yield Container(Input(placeholder="🔍 Type to search...", id="search-input"), id="search-box")
        yield Tree("Sessions", id="session-tree")
        yield Static("↑↓:nav · ←→:fold · ^A:fold all · Enter:select · ^V:preview · ^E:export · Esc:quit", id="footer")

    def on_mount(self) -> None:
        """Initialize."""
        init_db()
        self.load_sessions()
        tree = self.query_one("#session-tree", Tree)
        tree.root.expand()
        tree.focus()
        # Position cursor on first session node (skip metadata)
        self.call_later(self._select_first_session)

    def _select_first_session(self) -> None:
        """Select the first navigable node (group or session)."""
        tree = self.query_one("#session-tree", Tree)
        tree.action_cursor_down()
        # Skip metadata nodes
        attempts = 0
        while tree.cursor_node and not self._is_navigable_node(tree.cursor_node) and attempts < 50:
            tree.action_cursor_down()
            attempts += 1

    def load_sessions(self, search_query: str = "") -> None:
        """Load and display sessions, searching in content when query >= 3 chars."""
        if not self.all_sessions:
            self.all_sessions = get_enriched_sessions(limit=100)

        # Filter
        sessions = self.all_sessions
        if self.project_filter:
            sessions = [s for s in sessions if self.project_filter.lower() in s['project'].lower()]
        if search_query:
            q = search_query.lower()
            # First, filter by title/project (fast)
            title_matches = [s for s in sessions if q in s['title'].lower() or q in s['project'].lower()]

            # If query is 3+ chars, also search in content (full-text)
            content_sessions = []
            if len(search_query) >= 3:
                try:
                    # Get sessions with metadata from content search
                    content_results = search_sessions_with_content(search_query, limit=50)
                    existing_ids = {s['session_id'] for s in title_matches}

                    for cr in content_results:
                        if cr['session_id'] not in existing_ids:
                            # Parse last_activity to datetime (always naive for comparison)
                            last_activity = cr['last_activity']
                            try:
                                if last_activity and 'T' in str(last_activity):
                                    dt = datetime.fromisoformat(str(last_activity).replace('Z', '+00:00'))
                                    # Convert to naive datetime for consistent comparison
                                    if dt.tzinfo is not None:
                                        dt = dt.replace(tzinfo=None)
                                elif last_activity:
                                    dt = datetime.strptime(str(last_activity), '%Y-%m-%d %H:%M:%S')
                                else:
                                    dt = datetime.now()
                            except:
                                dt = datetime.now()

                            # Create session entry for content match
                            content_sessions.append({
                                'session_id': cr['session_id'],
                                'project': cr['project_name'] or 'Unknown',
                                'title': f"[Content match: {search_query}]",
                                'last_activity': dt,
                                'relative_time': relative_time(dt),
                                'message_count': cr['entry_count'],
                                'transcript_path': None,
                                'custom_name': cr.get('custom_name', ''),
                            })
                except Exception as e:
                    pass  # FTS table might not exist yet

            # Combine title matches + content matches
            sessions = title_matches + content_sessions

        # Update header
        header = self.query_one("#header", Static)
        total = len(self.all_sessions)
        filtered = len(sessions)
        header.update(f"Browse Sessions ({filtered} of {total})" if filtered != total else f"Browse Sessions ({total})")

        # Build tree
        tree = self.query_one("#session-tree", Tree)
        tree.clear()
        self.session_nodes.clear()

        # Group by project
        groups = defaultdict(list)
        for s in sessions:
            groups[s['project']].append(s)

        # Sort projects by most recent
        sorted_projects = sorted(
            groups.keys(),
            key=lambda p: max(s['last_activity'] for s in groups[p]),
            reverse=True
        )

        for project in sorted_projects:
            project_sessions = sorted(groups[project], key=lambda x: x['last_activity'], reverse=True)

            # Project group node (collapsible)
            group_label = Text()
            group_label.append("📁 ", style="cyan")
            group_label.append(project, style="cyan bold")
            group_label.append(f" ({len(project_sessions)})", style="dim")

            # Group node has no session data - it's just for organization
            project_group = tree.root.add(group_label, expand=True)

            # Add all sessions as children of the group
            for session in project_sessions:
                session_label = Text()
                session_label.append("▸ ", style="cyan")
                session_label.append(session['title'], style="white")

                session_node = project_group.add(session_label, data=session, expand=True)
                self.session_nodes[session['session_id']] = session_node

                # Meta info as leaf (will be skipped in navigation)
                meta = Text()
                meta.append(f"  {session['relative_time']} · {session['message_count']} msg", style="dim")
                session_node.add_leaf(meta)  # No data = metadata node

        tree.root.expand()

    def get_selected_session(self) -> Optional[Dict[str, Any]]:
        """Get the currently selected session."""
        tree = self.query_one("#session-tree", Tree)
        if tree.cursor_node and tree.cursor_node.data:
            data = tree.cursor_node.data
            if isinstance(data, dict) and 'session_id' in data:
                return data
        return None

    @on(Input.Changed, "#search-input")
    def on_search(self, event: Input.Changed) -> None:
        self.load_sessions(event.value)

    def action_select(self) -> None:
        session = self.get_selected_session()
        if session:
            self.exit(session)
        else:
            tree = self.query_one("#session-tree", Tree)
            if tree.cursor_node:
                tree.cursor_node.toggle()

    def action_export_md(self) -> None:
        session = self.get_selected_session()
        if session:
            self.exit({"action": "export_md", "session": session})

    def action_export_json(self) -> None:
        session = self.get_selected_session()
        if session:
            self.exit({"action": "export_json", "session": session})

    def action_preview(self) -> None:
        session = self.get_selected_session()
        if session:
            def on_preview_result(result) -> None:
                if isinstance(result, dict) and result.get('action'):
                    # Export action from preview - exit browser with this action
                    self.exit(result)

            self.push_screen(PreviewScreen(session), on_preview_result)

    def action_rename(self) -> None:
        session = self.get_selected_session()
        if session:
            def on_rename(new_name: Optional[str]) -> None:
                if new_name:
                    # Refresh the session title
                    session['title'] = new_name
                    search = self.query_one("#search-input", Input).value
                    self.all_sessions = []  # Force reload
                    self.load_sessions(search)

            self.push_screen(RenameScreen(session), on_rename)

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_quit(self) -> None:
        self.exit(None)

    def _is_session_node(self, node) -> bool:
        """Check if node is a session (has data with session_id)."""
        return node and node.data and isinstance(node.data, dict) and 'session_id' in node.data

    def _is_group_node(self, node) -> bool:
        """Check if node is a project group (expandable, no session data)."""
        return node and node.allow_expand and not self._is_session_node(node) and node.parent == self.query_one("#session-tree", Tree).root

    def _find_parent_group(self, node):
        """Find the parent project group of a node."""
        tree = self.query_one("#session-tree", Tree)
        current = node
        while current and current.parent != tree.root:
            current = current.parent
        return current if current and current.parent == tree.root else None

    def action_collapse_group(self) -> None:
        """Collapse the project group (left arrow)."""
        tree = self.query_one("#session-tree", Tree)
        if not tree.cursor_node:
            return

        # Find the project group to collapse
        if self._is_group_node(tree.cursor_node):
            # Already on a group node - collapse it
            tree.cursor_node.collapse()
        else:
            # Find parent group and collapse it
            group = self._find_parent_group(tree.cursor_node)
            if group:
                group.collapse()
                tree.select_node(group)

    def action_expand_group(self) -> None:
        """Expand the project group (right arrow)."""
        tree = self.query_one("#session-tree", Tree)
        if not tree.cursor_node:
            return

        # Find the project group to expand
        if self._is_group_node(tree.cursor_node):
            tree.cursor_node.expand()
            # Move to first session in the group
            self.action_cursor_down()
        else:
            # Find parent group and expand it
            group = self._find_parent_group(tree.cursor_node)
            if group:
                group.expand()

    def action_toggle_all(self) -> None:
        """Toggle expand/collapse all project groups (Ctrl+A)."""
        tree = self.query_one("#session-tree", Tree)

        # Get all project group nodes (direct children of root)
        groups = [child for child in tree.root.children if self._is_group_node(child)]

        if self.all_expanded:
            # Collapse all groups
            for group in groups:
                group.collapse()
            self.all_expanded = False
        else:
            # Expand all groups
            for group in groups:
                group.expand()
            self.all_expanded = True

    def _is_navigable_node(self, node) -> bool:
        """Check if node is navigable (session or project group, not metadata)."""
        return self._is_session_node(node) or self._is_group_node(node)

    def action_cursor_up(self) -> None:
        """Move cursor up, skipping metadata nodes."""
        tree = self.query_one("#session-tree", Tree)
        tree.action_cursor_up()
        # Skip metadata nodes (keep sessions and groups)
        attempts = 0
        while tree.cursor_node and not self._is_navigable_node(tree.cursor_node) and attempts < 50:
            tree.action_cursor_up()
            attempts += 1

    def action_cursor_down(self) -> None:
        """Move cursor down, skipping metadata nodes."""
        tree = self.query_one("#session-tree", Tree)
        tree.action_cursor_down()
        # Skip metadata nodes (keep sessions and groups)
        attempts = 0
        while tree.cursor_node and not self._is_navigable_node(tree.cursor_node) and attempts < 50:
            tree.action_cursor_down()
            attempts += 1

    def on_key(self, event) -> None:
        search = self.query_one("#search-input", Input)
        if event.character and event.character.isprintable() and not search.has_focus:
            search.focus()
            search.value += event.character
            search.cursor_position = len(search.value)


def run_browser(project_filter: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Run the TUI browser."""
    app = SessionBrowser(project_filter=project_filter)
    return app.run()


if __name__ == "__main__":
    result = run_browser()
    if result:
        print(f"Selected: {result}")
