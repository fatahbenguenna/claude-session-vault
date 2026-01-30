#!/usr/bin/env python3
"""Interactive TUI browser for Claude Session Vault using Textual.

Features:
- Real-time search filtering
- Arrow key navigation
- Keyboard shortcuts for export
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, Static, ListView, ListItem, Label
from textual.containers import Container, Vertical
from textual.reactive import reactive
from textual import on
from rich.text import Text

from claude_vault.db import (
    init_db,
    list_sessions,
    get_session_events,
    get_connection,
)


def relative_time(dt: datetime) -> str:
    """Convert datetime to human-readable relative time."""
    now = datetime.now()
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


def get_session_title(session_id: str, transcript_path: Optional[str] = None) -> str:
    """Get the first user prompt as session title."""
    if transcript_path:
        path = Path(transcript_path)
        if path.exists():
            try:
                with open(path, 'r') as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get('type') == 'human':
                                message = entry.get('message', {})
                                if isinstance(message, dict):
                                    content = message.get('content', [])
                                    if isinstance(content, list):
                                        for item in content:
                                            if isinstance(item, dict) and item.get('type') == 'text':
                                                text = item.get('text', '')
                                                text = text.strip().replace('\n', ' ')[:100]
                                                if len(text) > 97:
                                                    text = text[:97] + "..."
                                                return text
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

    # Fallback: get from database
    events = get_session_events(session_id, limit=5)
    for event in events:
        if event.get('event_type') == 'UserPromptSubmit' and event.get('prompt'):
            text = event['prompt'].strip().replace('\n', ' ')[:100]
            if len(text) > 97:
                text = text[:97] + "..."
            return text

    return "No title available"


def get_enriched_sessions(limit: int = 100) -> List[Dict[str, Any]]:
    """Get sessions with enriched data."""
    sessions = list_sessions(limit=limit)
    enriched = []

    for session in sessions:
        session_id = session['session_id']

        # Get transcript path
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transcript_path FROM events WHERE session_id = ? AND transcript_path IS NOT NULL LIMIT 1",
            (session_id,)
        )
        row = cursor.fetchone()
        transcript_path = row[0] if row else None

        # Parse last activity time
        last_activity = session.get('last_activity', '')
        try:
            if 'T' in str(last_activity):
                dt = datetime.fromisoformat(str(last_activity).replace('Z', '+00:00'))
            else:
                dt = datetime.strptime(str(last_activity), '%Y-%m-%d %H:%M:%S')
        except:
            dt = datetime.now()

        # Get message count
        message_count = 0
        if transcript_path:
            path = Path(transcript_path)
            if path.exists():
                try:
                    with open(path, 'r') as f:
                        message_count = sum(1 for line in f if line.strip())
                except:
                    pass
        if message_count == 0:
            message_count = session.get('event_count', 0)

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

    enriched.sort(key=lambda x: x['last_activity'], reverse=True)
    return enriched


class SessionItem(ListItem):
    """A session item in the list."""

    def __init__(self, session: Dict[str, Any]) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        """Compose the session item."""
        title = self.session['title']
        time_ago = self.session['relative_time']
        msg_count = self.session['message_count']
        project = self.session['project']

        yield Label(Text(f"▸ {title}", style="bold"))
        yield Label(Text(f"  {time_ago} · {msg_count} messages · {project}", style="dim"))


class SessionBrowser(App):
    """TUI for browsing Claude sessions."""

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

    #search-input:focus {
        border: none;
    }

    #session-list {
        height: 1fr;
        background: #1e1e1e;
    }

    #session-list > ListItem {
        padding: 0 1;
        height: auto;
    }

    #session-list > ListItem:hover {
        background: #2d2d2d;
    }

    #session-list > ListItem.-selected {
        background: #094771;
    }

    #session-list > ListItem Label {
        width: 100%;
    }

    #footer {
        height: 1;
        background: #2d2d2d;
        color: #888888;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Exit"),
        Binding("enter", "select", "Select"),
        Binding("ctrl+e", "export_md", "Export MD"),
        Binding("ctrl+j", "export_json", "Export JSON"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, project_filter: Optional[str] = None):
        super().__init__()
        self.project_filter = project_filter
        self.all_sessions: List[Dict[str, Any]] = []
        self.filtered_sessions: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static("Browse Sessions", id="header")
        yield Container(Input(placeholder="🔍 Type to search...", id="search-input"), id="search-box")
        yield ListView(id="session-list")
        yield Static("↑↓: navigate · Enter: select · Ctrl+E: export MD · Ctrl+J: export JSON · Esc: quit", id="footer")

    def on_mount(self) -> None:
        """Initialize on mount."""
        init_db()
        self.load_sessions()
        # Focus the list for arrow key navigation
        self.query_one("#session-list", ListView).focus()

    def load_sessions(self, search_query: str = "") -> None:
        """Load and filter sessions."""
        if not self.all_sessions:
            self.all_sessions = get_enriched_sessions(limit=100)

        # Apply filters
        sessions = self.all_sessions

        if self.project_filter:
            sessions = [s for s in sessions if self.project_filter.lower() in s['project'].lower()]

        if search_query:
            q = search_query.lower()
            sessions = [
                s for s in sessions
                if q in s['title'].lower() or q in s['project'].lower()
            ]

        self.filtered_sessions = sessions

        # Update header
        header = self.query_one("#header", Static)
        total = len(self.all_sessions)
        filtered = len(self.filtered_sessions)
        if filtered == total:
            header.update(f"Browse Sessions ({total})")
        else:
            header.update(f"Browse Sessions ({filtered} of {total})")

        # Update list
        list_view = self.query_one("#session-list", ListView)
        list_view.clear()

        for session in self.filtered_sessions:
            item = SessionItem(session)
            list_view.append(item)

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Handle search input."""
        self.load_sessions(event.value)
        # Refocus list after filtering
        self.query_one("#session-list", ListView).focus()

    def action_cursor_up(self) -> None:
        """Move cursor up."""
        list_view = self.query_one("#session-list", ListView)
        list_view.action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move cursor down."""
        list_view = self.query_one("#session-list", ListView)
        list_view.action_cursor_down()

    def action_select(self) -> None:
        """Select the highlighted session."""
        list_view = self.query_one("#session-list", ListView)
        if list_view.highlighted_child and isinstance(list_view.highlighted_child, SessionItem):
            self.exit(list_view.highlighted_child.session)

    @on(ListView.Selected, "#session-list")
    def on_list_selected(self, event: ListView.Selected) -> None:
        """Handle list selection."""
        if isinstance(event.item, SessionItem):
            self.exit(event.item.session)

    def action_export_md(self) -> None:
        """Export selected to Markdown."""
        list_view = self.query_one("#session-list", ListView)
        if list_view.highlighted_child and isinstance(list_view.highlighted_child, SessionItem):
            self.exit({"action": "export_md", "session": list_view.highlighted_child.session})

    def action_export_json(self) -> None:
        """Export selected to JSON."""
        list_view = self.query_one("#session-list", ListView)
        if list_view.highlighted_child and isinstance(list_view.highlighted_child, SessionItem):
            self.exit({"action": "export_json", "session": list_view.highlighted_child.session})

    def action_refresh(self) -> None:
        """Refresh sessions."""
        self.all_sessions = []
        search = self.query_one("#search-input", Input).value
        self.load_sessions(search)

    def action_quit(self) -> None:
        """Quit."""
        self.exit(None)

    def on_key(self, event) -> None:
        """Capture typing to search."""
        search_input = self.query_one("#search-input", Input)
        # If user types a letter and search isn't focused, focus it and add the character
        if event.character and event.character.isprintable() and not search_input.has_focus:
            search_input.focus()
            search_input.value += event.character
            search_input.cursor_position = len(search_input.value)


def run_browser(project_filter: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Run the TUI browser."""
    app = SessionBrowser(project_filter=project_filter)
    return app.run()


if __name__ == "__main__":
    result = run_browser()
    if result:
        print(f"Selected: {result}")
