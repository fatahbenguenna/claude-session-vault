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
from textual.containers import Container, Vertical, Horizontal
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
    # First check for custom name
    custom_name = get_session_custom_name(session_id)
    if custom_name:
        return custom_name

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


def get_session_preview(session_id: str, transcript_path: Optional[str] = None) -> str:
    """Get a preview of the session content."""
    lines = []

    if transcript_path:
        path = Path(transcript_path)
        if path.exists():
            try:
                with open(path, 'r') as f:
                    count = 0
                    for line in f:
                        if count >= 10:
                            break
                        try:
                            entry = json.loads(line)
                            if entry.get('type') == 'human':
                                message = entry.get('message', {})
                                if isinstance(message, dict):
                                    content = message.get('content', [])
                                    if isinstance(content, list):
                                        for item in content:
                                            if isinstance(item, dict) and item.get('type') == 'text':
                                                text = item.get('text', '')[:100].replace('\n', ' ')
                                                lines.append(f"👤 {text}...")
                                                count += 1
                                                break
                            elif entry.get('type') == 'assistant':
                                message = entry.get('message', {})
                                if isinstance(message, dict):
                                    content = message.get('content', [])
                                    if isinstance(content, list):
                                        for item in content:
                                            if isinstance(item, dict):
                                                if item.get('type') == 'text':
                                                    text = item.get('text', '')[:100].replace('\n', ' ')
                                                    lines.append(f"🤖 {text}...")
                                                    count += 1
                                                    break
                                                elif item.get('type') == 'tool_use':
                                                    tool_name = item.get('name', 'Unknown')
                                                    lines.append(f"⚡ Tool: {tool_name}")
                                                    count += 1
                                                    break
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

    if not lines:
        # Fallback to database events
        events = get_session_events(session_id, limit=10)
        for event in events:
            if event.get('event_type') == 'UserPromptSubmit' and event.get('prompt'):
                text = event['prompt'][:100].replace('\n', ' ')
                lines.append(f"👤 {text}...")
            elif event.get('tool_name'):
                lines.append(f"⚡ Tool: {event['tool_name']}")

    return '\n'.join(lines) if lines else "No preview available"


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
    """Modal screen for previewing a session."""

    CSS = """
    PreviewScreen {
        align: center middle;
    }

    #preview-dialog {
        width: 80%;
        height: 80%;
        border: solid #0078d4;
        background: #1e1e1e;
        padding: 1;
    }

    #preview-title {
        text-align: center;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }

    #preview-content {
        height: 1fr;
        overflow-y: auto;
        padding: 1;
        background: #2d2d2d;
    }

    #preview-footer {
        height: 1;
        text-align: center;
        color: #888;
    }
    """

    def __init__(self, session: Dict[str, Any]):
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        preview = get_session_preview(self.session['session_id'], self.session.get('transcript_path'))
        yield Container(
            Static(f"Preview: {self.session['title'][:50]}...", id="preview-title"),
            Static(preview, id="preview-content"),
            Static("Press Esc or Enter to close", id="preview-footer"),
            id="preview-dialog"
        )

    def on_key(self, event) -> None:
        if event.key in ("escape", "enter"):
            self.dismiss()


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
        Binding("ctrl+a", "toggle_all", "Toggle All", show=True),
        Binding("ctrl+f", "focus_search", "Search", show=False),
    ]

    show_all = reactive(True)

    def __init__(self, project_filter: Optional[str] = None):
        super().__init__()
        self.project_filter = project_filter
        self.all_sessions: List[Dict[str, Any]] = []
        self.session_nodes: Dict[str, TreeNode] = {}

    def compose(self) -> ComposeResult:
        yield Static("Browse Sessions", id="header")
        yield Container(Input(placeholder="🔍 Type to search...", id="search-input"), id="search-box")
        yield Tree("Sessions", id="session-tree")
        yield Static("↑↓:nav · Enter:select · ^V:preview · ^R:rename · ^E:export · ^A:toggle · Esc:quit", id="footer")

    def on_mount(self) -> None:
        """Initialize."""
        init_db()
        self.load_sessions()
        tree = self.query_one("#session-tree", Tree)
        tree.root.expand()
        tree.focus()

    def load_sessions(self, search_query: str = "") -> None:
        """Load and display sessions."""
        if not self.all_sessions:
            self.all_sessions = get_enriched_sessions(limit=100)

        # Filter
        sessions = self.all_sessions
        if self.project_filter:
            sessions = [s for s in sessions if self.project_filter.lower() in s['project'].lower()]
        if search_query:
            q = search_query.lower()
            sessions = [s for s in sessions if q in s['title'].lower() or q in s['project'].lower()]

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
            first = project_sessions[0]
            others = len(project_sessions) - 1

            # Project node label
            label = Text()
            label.append("▼ " if self.show_all else "▸ ", style="cyan")
            label.append(first['title'], style="bold white")
            if others > 0:
                label.append(f" (+{others} other session{'s' if others > 1 else ''})", style="dim")

            project_node = tree.root.add(label, data=first, expand=self.show_all)
            self.session_nodes[first['session_id']] = project_node

            # Meta info line
            meta = Text()
            meta.append(f"{first['relative_time']}", style="dim")
            meta.append(" · ", style="dim")
            meta.append(f"{first['message_count']} messages", style="dim")
            meta.append(" · ", style="dim")
            meta.append(project, style="cyan dim")
            project_node.add_leaf(meta)

            # Add other sessions
            for session in project_sessions[1:]:
                child_label = Text()
                child_label.append("  ▸ ", style="cyan")
                child_label.append(session['title'], style="white")

                child_node = project_node.add(child_label, data=session)
                self.session_nodes[session['session_id']] = child_node

                child_meta = Text()
                child_meta.append(f"    {session['relative_time']}", style="dim")
                child_meta.append(" · ", style="dim")
                child_meta.append(f"{session['message_count']} messages", style="dim")
                child_node.add_leaf(child_meta)

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

    @on(Tree.NodeSelected)
    def on_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data and isinstance(event.node.data, dict) and 'session_id' in event.node.data:
            self.exit(event.node.data)

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
            self.push_screen(PreviewScreen(session))

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

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        tree = self.query_one("#session-tree", Tree)
        for node in tree.root.children:
            if self.show_all:
                node.expand()
            else:
                node.collapse()

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_quit(self) -> None:
        self.exit(None)

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
