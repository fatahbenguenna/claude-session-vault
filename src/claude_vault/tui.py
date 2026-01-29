#!/usr/bin/env python3
"""Interactive TUI browser for Claude Session Vault using Textual."""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer, Input, Static, ListItem, ListView
from textual.containers import Container, Vertical, Horizontal
from textual.reactive import reactive
from textual import on
from rich.text import Text
from rich.console import RenderableType

from claude_vault.db import (
    init_db,
    list_sessions,
    get_session_events,
    search_events,
    get_db_path,
    get_connection,
)


def relative_time(dt: datetime) -> str:
    """Convert datetime to human-readable relative time like '2 minutes ago'."""
    now = datetime.now()
    diff = now - dt

    seconds = diff.total_seconds()

    if seconds < 60:
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
    # Try to read from transcript
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
                                                # Truncate and clean
                                                text = text.strip().replace('\n', ' ')[:80]
                                                if len(text) > 77:
                                                    text = text[:77] + "..."
                                                return text
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

    # Fallback: get from database
    events = get_session_events(session_id, limit=5)
    for event in events:
        if event.get('event_type') == 'UserPromptSubmit' and event.get('prompt'):
            text = event['prompt'].strip().replace('\n', ' ')[:80]
            if len(text) > 77:
                text = text[:77] + "..."
            return text

    return "No title available"


def get_enriched_sessions() -> List[Dict[str, Any]]:
    """Get sessions with enriched data (title, relative time, message count)."""
    sessions = list_sessions()
    enriched = []

    for session in sessions:
        session_id = session['session_id']

        # Get transcript path from database
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
            if 'T' in last_activity:
                dt = datetime.fromisoformat(last_activity.replace('Z', '+00:00'))
            else:
                dt = datetime.strptime(last_activity, '%Y-%m-%d %H:%M:%S')
        except:
            dt = datetime.now()

        # Get message count from transcript
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

        # Extract project name from various sources
        project_name = session.get('project_name') or session.get('project')
        if not project_name and transcript_path:
            # Extract from transcript path: ~/.claude/projects/-Users-xxx-project/session.jsonl
            try:
                parts = Path(transcript_path).parent.name
                # Convert -Users-fatah-project to project
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

    # Sort by most recent
    enriched.sort(key=lambda x: x['last_activity'], reverse=True)
    return enriched


class SessionItem(ListItem):
    """A single session item in the list."""

    def __init__(self, session: Dict[str, Any], index: int, total: int) -> None:
        super().__init__()
        self.session = session
        self.index = index
        self.total = total

    def compose(self) -> ComposeResult:
        title = self.session['title']
        time_ago = self.session['relative_time']
        msg_count = self.session['message_count']
        project = self.session['project']

        # Create the display
        yield Static(
            Text.assemble(
                ("▸ ", "bold cyan"),
                (title, "bold white"),
            ),
            classes="session-title"
        )
        yield Static(
            Text.assemble(
                (f"  {time_ago}", "dim"),
                (" · ", "dim"),
                (f"{msg_count} messages", "dim"),
                (" · ", "dim"),
                (project, "dim cyan"),
            ),
            classes="session-meta"
        )


class SessionBrowser(App):
    """Textual app for browsing Claude sessions."""

    CSS = """
    Screen {
        background: $surface;
    }

    #header-container {
        height: 3;
        background: $primary;
        padding: 0 1;
    }

    #header-title {
        color: $text;
        text-style: bold;
    }

    #search-container {
        height: 3;
        padding: 0 1;
        background: $surface-darken-1;
    }

    #search-input {
        border: none;
        background: $surface-darken-2;
        padding: 0 1;
    }

    #search-input:focus {
        border: none;
    }

    #sessions-list {
        height: 1fr;
        padding: 0 1;
    }

    SessionItem {
        height: auto;
        padding: 0 0 1 0;
    }

    SessionItem:hover {
        background: $surface-lighten-1;
    }

    SessionItem.-selected {
        background: $primary-darken-1;
    }

    .session-title {
        height: 1;
    }

    .session-meta {
        height: 1;
    }

    #footer-help {
        height: 1;
        background: $surface-darken-2;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Exit"),
        Binding("enter", "select_session", "Select"),
        Binding("ctrl+e", "export_md", "Export MD"),
        Binding("ctrl+j", "export_json", "Export JSON"),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self, project_filter: Optional[str] = None):
        super().__init__()
        self.project_filter = project_filter
        self.all_sessions: List[Dict[str, Any]] = []
        self.filtered_sessions: List[Dict[str, Any]] = []
        self.selected_session: Optional[Dict[str, Any]] = None

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Browse Sessions", id="header-title"),
            id="header-container"
        )
        yield Container(
            Input(placeholder="🔍 Search...", id="search-input"),
            id="search-container"
        )
        yield ListView(id="sessions-list")
        yield Static(
            "Enter: select · Ctrl+E: export MD · Ctrl+J: export JSON · Esc: quit · Type to search",
            id="footer-help"
        )

    def on_mount(self) -> None:
        """Load sessions when app starts."""
        init_db()
        self.load_sessions()
        self.query_one("#search-input", Input).focus()

    def load_sessions(self, search_query: str = "") -> None:
        """Load and filter sessions."""
        if not self.all_sessions:
            self.all_sessions = get_enriched_sessions()

        # Filter by project if specified
        sessions = self.all_sessions
        if self.project_filter:
            sessions = [s for s in sessions if self.project_filter.lower() in s['project'].lower()]

        # Filter by search query
        if search_query:
            query_lower = search_query.lower()
            sessions = [
                s for s in sessions
                if query_lower in s['title'].lower()
                or query_lower in s['project'].lower()
            ]

        self.filtered_sessions = sessions
        self.update_session_list()
        self.update_header()

    def update_header(self) -> None:
        """Update header with count."""
        total = len(self.all_sessions)
        filtered = len(self.filtered_sessions)
        header = self.query_one("#header-title", Static)
        if filtered == total:
            header.update(f"Browse Sessions ({total})")
        else:
            header.update(f"Browse Sessions ({filtered} of {total})")

    def update_session_list(self) -> None:
        """Update the ListView with filtered sessions."""
        list_view = self.query_one("#sessions-list", ListView)
        list_view.clear()

        for i, session in enumerate(self.filtered_sessions):
            item = SessionItem(session, i + 1, len(self.filtered_sessions))
            list_view.append(item)

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        self.load_sessions(event.value)

    @on(ListView.Selected, "#sessions-list")
    def on_session_selected(self, event: ListView.Selected) -> None:
        """Handle session selection."""
        if event.item and isinstance(event.item, SessionItem):
            self.selected_session = event.item.session
            self.show_session_actions()

    def action_select_session(self) -> None:
        """Select the highlighted session."""
        list_view = self.query_one("#sessions-list", ListView)
        if list_view.highlighted_child and isinstance(list_view.highlighted_child, SessionItem):
            self.selected_session = list_view.highlighted_child.session
            self.show_session_actions()

    def show_session_actions(self) -> None:
        """Show actions for selected session."""
        if self.selected_session:
            # For now, just exit with the selected session
            self.exit(self.selected_session)

    def action_export_md(self) -> None:
        """Export selected session to Markdown."""
        list_view = self.query_one("#sessions-list", ListView)
        if list_view.highlighted_child and isinstance(list_view.highlighted_child, SessionItem):
            session = list_view.highlighted_child.session
            self.exit({"action": "export_md", "session": session})

    def action_export_json(self) -> None:
        """Export selected session to JSON."""
        list_view = self.query_one("#sessions-list", ListView)
        if list_view.highlighted_child and isinstance(list_view.highlighted_child, SessionItem):
            session = list_view.highlighted_child.session
            self.exit({"action": "export_json", "session": session})

    def action_quit(self) -> None:
        """Quit the app."""
        self.exit(None)


def run_browser(project_filter: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Run the TUI browser and return selected session."""
    app = SessionBrowser(project_filter=project_filter)
    return app.run()


if __name__ == "__main__":
    result = run_browser()
    if result:
        print(f"Selected: {result}")
