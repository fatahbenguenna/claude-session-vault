#!/usr/bin/env python3
"""Interactive TUI browser for Claude Session Vault using Textual.

Features like claude --resume:
- Tree structure with projects
- Fuzzy search
- Preview panel (Ctrl+V)
- Rename session (Ctrl+R)
- Arrow key navigation
- In-preview search (Ctrl+F)
"""

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import defaultdict

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, Static, Tree, TextArea, LoadingIndicator
from textual.widgets.tree import TreeNode
from textual.containers import Container, Vertical, Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.worker import Worker, get_current_worker
from textual import on, work
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
from claude_vault.utils import (
    relative_time,
    parse_datetime_safe,
    session_file_exists,
    extract_text_from_content,
)


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


def get_session_preview(
    session_id: str,
    transcript_path: Optional[str] = None,
    max_messages: int = 20,
    offset: int = 0
) -> Tuple[str, int, int]:
    """Get a formatted preview of the session content like Claude Code display.

    Args:
        session_id: The session ID to preview
        transcript_path: Optional path to transcript file
        max_messages: Maximum messages to return
        offset: Number of messages to skip (for pagination)

    Returns:
        Tuple of (preview_text, total_entries, loaded_count)
    """
    from claude_vault.db import get_transcript_entries

    lines = []

    # Try transcript_entries from database first (synced content)
    entries = get_transcript_entries(session_id)
    total_entries = len(entries) if entries else 0

    if entries:
        message_count = 0
        skipped = 0
        for entry in entries:
            # Skip entries for pagination
            if skipped < offset:
                raw_json = entry.get('raw_json', '')
                if raw_json:
                    try:
                        data = json.loads(raw_json)
                        if data.get('type') in ('user', 'human', 'assistant'):
                            skipped += 1
                    except:
                        pass
                continue

            if message_count >= max_messages:
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
            loaded_count = offset + message_count
            return '\n'.join(lines), total_entries, loaded_count

    # Fallback: database events (from hooks)
    events = get_session_events(session_id, limit=max_messages)
    total_events = len(events) if events else 0
    if events:
        for event in events:
            if event.get('event_type') == 'UserPromptSubmit' and event.get('prompt'):
                lines.append("")
                lines.append("[bold cyan]━━━ 👤 User ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
                text = event['prompt'][:300]
                lines.append(text + ("..." if len(event['prompt']) > 300 else ""))
            elif event.get('tool_name'):
                lines.append(f"[bold yellow]⚡ {event['tool_name']}[/bold yellow]")

    if lines:
        return '\n'.join(lines), total_events, len(events)

    return "[dim]No conversation content in this session.\n\nThis session may only contain metadata (file snapshots, etc.)\nor the transcript was not synced yet.\n\nTry: claude-vault sync --all[/dim]", 0, 0


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

        # Parse last activity time
        dt = parse_datetime_safe(session.get('last_activity', ''))

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


def get_orphaned_sessions(limit: int = 200) -> List[Dict[str, Any]]:
    """Get sessions that exist in database but whose files were deleted by Claude."""
    # 1. Scan filesystem for existing session IDs
    claude_projects = Path.home() / ".claude" / "projects"
    fs_session_ids = set()
    if claude_projects.exists():
        for jsonl_file in claude_projects.rglob("*.jsonl"):
            session_id = jsonl_file.stem
            # Skip subagent sessions
            if '/subagents/' in str(jsonl_file) or '\\subagents\\' in str(jsonl_file) or session_id.startswith('agent-'):
                continue
            fs_session_ids.add(session_id)

    # 2. Get all session IDs from database (excluding subagent sessions)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT session_id FROM sessions WHERE session_id NOT LIKE 'agent-%'
        UNION
        SELECT DISTINCT session_id FROM transcript_entries WHERE session_id NOT LIKE 'agent-%'
    """)
    db_session_ids = set(row[0] for row in cursor.fetchall())

    # 3. Find orphaned sessions (in DB but not in filesystem)
    orphaned_ids = db_session_ids - fs_session_ids

    # 4. Build enriched session data for orphaned sessions
    orphaned = []
    for session_id in orphaned_ids:
        # Get message count
        cursor.execute(
            "SELECT COUNT(*) FROM transcript_entries WHERE session_id = ? AND entry_type IN ('user', 'human', 'assistant')",
            (session_id,)
        )
        row = cursor.fetchone()
        message_count = row[0] if row else 0

        if message_count == 0:
            continue

        # Get session info
        cursor.execute(
            "SELECT project_name, custom_name FROM sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        project_name = row[0] if row else 'unknown'
        custom_name = row[1] if row else None

        # Get last activity from transcript
        cursor.execute(
            "SELECT MAX(timestamp) FROM transcript_entries WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        last_activity = row[0] if row else None

        # Parse last activity time
        dt = parse_datetime_safe(last_activity)

        if not project_name:
            project_name = 'deleted'

        orphaned.append({
            'session_id': session_id,
            'project': project_name,
            'title': custom_name or get_session_title(session_id, None),
            'relative_time': relative_time(dt),
            'message_count': message_count,
            'last_activity': dt,
            'transcript_path': None,
            'orphaned': True,
        })

        if len(orphaned) >= limit:
            break

    conn.close()
    orphaned.sort(key=lambda x: x['last_activity'], reverse=True)
    return orphaned


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


class SearchInput(Input):
    """Custom Input that sends arrow key events to parent for search navigation."""

    BINDINGS = [
        Binding("left", "nav_prev", "Previous match", show=False),
        Binding("right", "nav_next", "Next match", show=False),
    ]

    def action_nav_prev(self) -> None:
        """Navigate to previous search result."""
        screen = self.screen
        if hasattr(screen, 'action_find_prev'):
            screen.action_find_prev()

    def action_nav_next(self) -> None:
        """Navigate to next search result."""
        screen = self.screen
        if hasattr(screen, 'action_find_next'):
            screen.action_find_next()


class PreviewScreen(ModalScreen):
    """Modal screen for previewing a session with conversation format.

    Features:
    - Full conversation preview with syntax highlighting
    - In-preview search with Ctrl+F (navigate with arrows for next/prev)
    - Export to markdown/clipboard
    - Open session in Claude Code
    """

    BINDINGS = [
        Binding("escape", "close_or_cancel_search", "Close", show=False),
        Binding("q", "close", "Close", show=False),
        Binding("e", "export_file", "Export", show=False),
        Binding("c", "copy_clipboard", "Copy", show=False),
        Binding("o", "open_claude", "Open", show=False),
        Binding("enter", "open_claude_or_confirm", "Open in Claude", show=False),
        Binding("ctrl+f", "toggle_search", "Find", show=False),
        Binding("f3", "find_next", "Next", show=False),
        Binding("shift+f3", "find_prev", "Prev", show=False),
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

    #search-row {
        height: 1;
        display: none;
        background: #2d2d2d;
        margin-bottom: 1;
    }

    #search-row.visible {
        display: block;
    }

    #preview-footer {
        height: 1;
        text-align: center;
        color: #888;
        margin-top: 1;
    }

    #search-label {
        width: auto;
        padding: 0 1;
        color: #58a6ff;
    }

    #preview-search-input {
        width: 30;
        background: #1e1e1e;
        border: none;
        height: 1;
    }

    #search-info {
        width: auto;
        min-width: 15;
        padding: 0 1;
        color: #888;
    }

    #search-nav {
        width: auto;
        padding: 0 1;
        color: #666;
    }

    #preview-status {
        height: 1;
        text-align: center;
        color: #58a6ff;
    }
    """

    def __init__(self, session: Dict[str, Any], initial_search: str = ""):
        super().__init__()
        self.session = session
        self.search_mode = False
        self.search_query = ""
        self.initial_search = initial_search  # Search term from parent browser
        self.match_lines: List[int] = []  # Line numbers containing matches
        self.current_match_index = 0
        self.raw_preview = ""  # Store raw preview content
        self.preview_lines: List[str] = []  # Lines for search
        self.total_entries = 0  # Total entries available

    def compose(self) -> ComposeResult:
        # Load ALL messages (no pagination - better UX for scrolling)
        self.raw_preview, self.total_entries, _ = get_session_preview(
            self.session['session_id'],
            self.session.get('transcript_path'),
            max_messages=10000,  # Load all
            offset=0
        )
        self.preview_lines = self.raw_preview.split('\n')

        title = self.session.get('custom_name') or self.session.get('title', 'Session')
        if len(title) > 60:
            title = title[:60] + "..."

        yield Container(
            Static(f"📋 {title}", id="preview-title"),
            Horizontal(
                Static("🔍", id="search-label"),
                SearchInput(placeholder="search...", id="preview-search-input"),
                Static("", id="search-info"),
                Static("[dim]Enter:next · Esc:close[/dim]", id="search-nav"),
                id="search-row"
            ),
            VerticalScroll(
                Static(self.raw_preview, id="preview-content", markup=True),
                id="preview-scroll"
            ),
            Static(f"[dim]{self.total_entries} entries[/dim]" if self.total_entries > 0 else "", id="preview-status"),
            Static("↑↓:Scroll · Ctrl+F:Find · e:Export · c:Copy · o/Enter:Open in Claude · Esc:Close", id="preview-footer"),
            id="preview-dialog"
        )

    def on_mount(self) -> None:
        """Initialize preview, activate search if initial_search provided."""
        if self.initial_search:
            # Activate search mode with the initial term
            search_row = self.query_one("#search-row", Horizontal)
            preview_footer = self.query_one("#preview-footer", Static)
            search_input = self.query_one("#preview-search-input", SearchInput)

            search_row.add_class("visible")
            preview_footer.add_class("hidden")
            self.search_mode = True
            search_input.value = self.initial_search
            self.search_query = self.initial_search
            self._perform_search()
            search_input.focus()

    def action_toggle_search(self) -> None:
        """Toggle search bar visibility in footer."""
        search_row = self.query_one("#search-row", Horizontal)
        preview_footer = self.query_one("#preview-footer", Static)
        search_input = self.query_one("#preview-search-input", SearchInput)

        if self.search_mode:
            # Hide search row, show normal footer
            search_row.remove_class("visible")
            preview_footer.remove_class("hidden")
            self.search_mode = False
            self._clear_highlights()
            self.search_query = ""
            search_input.value = ""
        else:
            # Show search row, hide normal footer
            search_row.add_class("visible")
            preview_footer.add_class("hidden")
            self.search_mode = True
            search_input.focus()

    def action_close_or_cancel_search(self) -> None:
        """Close search if active, otherwise close preview."""
        if self.search_mode:
            self.action_toggle_search()
        else:
            self.action_close()

    def action_open_claude_or_confirm(self) -> None:
        """If in search mode, go to next match. Otherwise open in Claude."""
        if self.search_mode:
            self.action_find_next()
        else:
            self.action_open_claude()

    def action_close(self) -> None:
        """Close the preview and return to session list."""
        self.dismiss()

    @on(Input.Changed, "#preview-search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """Update search results as user types."""
        self.search_query = event.value
        self._perform_search()

    @on(Input.Submitted, "#preview-search-input")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        """Go to next match when Enter is pressed in search."""
        self.action_find_next()

    def _perform_search(self) -> None:
        """Find all matches and highlight them."""
        query = self.search_query.strip().lower()
        self.match_lines = []
        self.current_match_index = 0

        if not query:
            self._clear_highlights()
            self._update_search_info()
            return

        # Find lines containing the query (case-insensitive)
        for i, line in enumerate(self.preview_lines):
            # Strip Rich markup for search
            plain_line = self._strip_markup(line).lower()
            if query in plain_line:
                self.match_lines.append(i)

        self._update_search_info()
        self._highlight_content()

        # Scroll to first match
        if self.match_lines:
            self._scroll_to_match(0)

    def _strip_markup(self, text: str) -> str:
        """Remove Rich markup tags from text for plain search."""
        # Remove [tag] and [/tag] patterns
        return re.sub(r'\[[^\]]*\]', '', text)

    def _highlight_content(self) -> None:
        """Update content with highlighted matches."""
        if not self.search_query.strip():
            return

        query = self.search_query.strip()
        highlighted_lines = []

        for i, line in enumerate(self.preview_lines):
            if i in self.match_lines:
                # Highlight matches in this line
                # Check if this is the current match line
                is_current = (self.match_lines and
                              self.current_match_index < len(self.match_lines) and
                              self.match_lines[self.current_match_index] == i)

                highlighted_line = self._highlight_line(line, query, is_current)
                highlighted_lines.append(highlighted_line)
            else:
                highlighted_lines.append(line)

        content = self.query_one("#preview-content", Static)
        content.update('\n'.join(highlighted_lines))

    def _highlight_line(self, line: str, query: str, is_current: bool) -> str:
        """Highlight query matches in a single line."""
        # For Rich markup, we need to be careful not to break existing tags
        # Simple approach: highlight in the visible text parts
        result = []
        i = 0
        line_lower = line.lower()
        query_lower = query.lower()

        while i < len(line):
            # Check if we're at a Rich markup tag
            if line[i] == '[':
                # Find the end of the tag
                end = line.find(']', i)
                if end != -1:
                    result.append(line[i:end+1])
                    i = end + 1
                    continue

            # Check for match at this position
            if line_lower[i:i+len(query)] == query_lower:
                matched_text = line[i:i+len(query)]
                if is_current:
                    # Current match: green highlight
                    result.append(f"[black on green]{matched_text}[/black on green]")
                else:
                    # Other matches: yellow highlight
                    result.append(f"[black on yellow]{matched_text}[/black on yellow]")
                i += len(query)
            else:
                result.append(line[i])
                i += 1

        return ''.join(result)

    def _clear_highlights(self) -> None:
        """Restore original content without highlights."""
        content = self.query_one("#preview-content", Static)
        content.update(self.raw_preview)

    def _update_search_info(self) -> None:
        """Update the match count display with format 'term X of Y'."""
        search_info = self.query_one("#search-info", Static)
        query = self.search_query.strip()
        if not query:
            search_info.update("")
        elif not self.match_lines:
            search_info.update("[red]no matches[/red]")
        else:
            current = self.current_match_index + 1
            total = len(self.match_lines)
            # Show truncated query if too long
            display_query = query if len(query) <= 15 else query[:12] + "..."
            search_info.update(f"[cyan]{display_query}[/cyan] [white]{current} of {total}[/white]")

    def _scroll_to_match(self, match_index: int) -> None:
        """Scroll to show the specified match (instant jump, no animation)."""
        if not self.match_lines or match_index >= len(self.match_lines):
            return

        line_num = self.match_lines[match_index]
        scroll = self.query_one("#preview-scroll", VerticalScroll)

        # Calculate approximate scroll position
        # Each line is roughly 1 unit of height
        total_lines = len(self.preview_lines)
        if total_lines > 0:
            # Scroll to position the match near the top third of the viewport
            scroll_fraction = max(0, (line_num - 5) / total_lines)
            scroll.scroll_to(y=scroll_fraction * scroll.max_scroll_y, animate=False)

    def action_find_next(self) -> None:
        """Go to next match."""
        if not self.match_lines:
            return

        self.current_match_index = (self.current_match_index + 1) % len(self.match_lines)
        self._update_search_info()
        self._highlight_content()
        self._scroll_to_match(self.current_match_index)

    def action_find_prev(self) -> None:
        """Go to previous match."""
        if not self.match_lines:
            return

        self.current_match_index = (self.current_match_index - 1) % len(self.match_lines)
        self._update_search_info()
        self._highlight_content()
        self._scroll_to_match(self.current_match_index)

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

    #loading-container {
        width: 100%;
        height: 1fr;
        align: center middle;
    }

    #loading-container.hidden {
        display: none;
    }

    #loading-text {
        text-align: center;
        color: #58a6ff;
        padding: 1;
    }

    #session-tree.hidden {
        display: none;
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
        Binding("ctrl+o", "toggle_orphans", "Orphans", show=True),
        Binding("ctrl+f", "focus_search", "Search", show=False),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("left", "collapse_group", "Collapse", show=False, priority=True),
        Binding("right", "expand_group", "Expand", show=False, priority=True),
    ]

    def __init__(self, project_filter: Optional[str] = None, orphans_only: bool = False):
        super().__init__()
        self.project_filter = project_filter
        self.orphans_only = orphans_only
        self.all_sessions: List[Dict[str, Any]] = []
        self.session_nodes: Dict[str, TreeNode] = {}
        self.all_expanded: bool = True  # Track expand/collapse all state
        self.loading = True

    def compose(self) -> ComposeResult:
        header_text = "Orphaned Sessions (deleted by Claude)" if self.orphans_only else "Browse Sessions"
        yield Static(header_text, id="header")
        yield Container(Input(placeholder="🔍 Type to search...", id="search-input"), id="search-box")
        yield Container(
            LoadingIndicator(),
            Static("Loading sessions...", id="loading-text"),
            id="loading-container"
        )
        yield Tree("Sessions", id="session-tree", classes="hidden")
        yield Static("↑↓:nav · ←→:fold · ^A:fold all · ^O:orphans · Enter:select · ^V:preview · ^E:export", id="footer")

    def on_mount(self) -> None:
        """Initialize with async loading."""
        init_db()
        self._update_footer()  # Set correct footer based on orphans_only mode
        self._load_sessions_async()

    @work(exclusive=True, thread=True)
    def _load_sessions_async(self) -> None:
        """Load sessions in background thread."""
        try:
            if self.orphans_only:
                sessions = get_orphaned_sessions(limit=200)
            else:
                sessions = get_enriched_sessions(limit=100)
            # Call UI update on main thread
            self.call_from_thread(self._on_sessions_loaded, sessions)
        except Exception as e:
            import traceback
            self.call_from_thread(self._on_load_error, str(e), traceback.format_exc())

    def _on_sessions_loaded(self, sessions: List[Dict[str, Any]]) -> None:
        """Called when sessions are loaded - update UI."""
        self.all_sessions = sessions
        self.loading = False

        # Hide loading, show tree
        loading = self.query_one("#loading-container", Container)
        tree = self.query_one("#session-tree", Tree)
        loading.add_class("hidden")
        tree.remove_class("hidden")

        # Build tree and focus
        try:
            self._build_tree(sessions)
            tree.root.expand()
            tree.focus()
            self.call_later(self._select_first_session)
        except Exception as e:
            import traceback
            import sys
            header = self.query_one("#header", Static)
            header.update(f"Error building tree: {e}")
            print(traceback.format_exc(), file=sys.stderr)

    def _on_load_error(self, error: str, traceback_str: str) -> None:
        """Called when session loading fails."""
        self.loading = False
        loading = self.query_one("#loading-container", Container)
        loading.add_class("hidden")
        header = self.query_one("#header", Static)
        header.update(f"Error loading sessions: {error}")
        # Log full traceback
        import sys
        print(traceback_str, file=sys.stderr)

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
        """Filter and display sessions, searching in content when query >= 3 chars."""
        # Skip if still loading
        if self.loading:
            return

        # Filter
        sessions = self.all_sessions
        if self.project_filter:
            sessions = [s for s in sessions if self.project_filter.lower() in s['project'].lower()]

        # Only filter when query is 3+ characters (both title and content search)
        # For 1-2 chars, show all sessions - too short to filter meaningfully
        if search_query and len(search_query) >= 3:
            q = search_query.lower()
            # First, filter by title/project (fast)
            title_matches = [s for s in sessions if q in s['title'].lower() or q in s['project'].lower()]

            # Also search in content (full-text)
            content_sessions = []
            try:
                # Get sessions with metadata from content search
                content_results = search_sessions_with_content(search_query, limit=50)
                existing_ids = {s['session_id'] for s in title_matches}

                for cr in content_results:
                    if cr['session_id'] not in existing_ids:
                        # Parse last_activity to datetime
                        dt = parse_datetime_safe(cr['last_activity'])

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

        self._build_tree(sessions)

    def _build_tree(self, sessions: List[Dict[str, Any]]) -> None:
        """Build the session tree from a list of sessions."""
        # Update header (preserve orphan mode indicator)
        header = self.query_one("#header", Static)
        total = len(self.all_sessions)
        filtered = len(sessions)
        prefix = "Orphaned Sessions" if self.orphans_only else "Browse Sessions"
        if filtered != total:
            header.update(f"{prefix} ({filtered} of {total})")
        else:
            header.update(f"{prefix} ({total})")

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

    @on(Input.Submitted, "#search-input")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        """Select current session when Enter is pressed in search."""
        self.action_select()

    @on(Tree.NodeSelected, "#session-tree")
    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Prevent session nodes from collapsing on click (only groups can toggle)."""
        node = event.node
        # If it's a session node (has session_id), prevent toggle by re-expanding
        if self._is_session_node(node):
            # Re-expand the node to prevent it from collapsing
            if not node.is_expanded:
                node.expand()

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

            # Pass current search query to preview for initial search
            search_query = self.query_one("#search-input", Input).value
            self.push_screen(PreviewScreen(session, initial_search=search_query), on_preview_result)

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

    def action_toggle_orphans(self) -> None:
        """Toggle between all sessions and orphaned sessions only (Ctrl+O)."""
        self.orphans_only = not self.orphans_only
        self.loading = True

        # Update footer to reflect new mode
        self._update_footer()

        # Show loading indicator
        loading = self.query_one("#loading-container", Container)
        tree = self.query_one("#session-tree", Tree)
        tree.add_class("hidden")
        loading.remove_class("hidden")

        # Clear search
        search_input = self.query_one("#search-input", Input)
        search_input.value = ""

        # Reload sessions
        self._load_sessions_async()

    def _update_footer(self) -> None:
        """Update footer text based on current mode."""
        footer = self.query_one("#footer", Static)
        toggle_label = "^O:non-orphans" if self.orphans_only else "^O:orphans"
        footer.update(f"↑↓:nav · ←→:fold · ^A:fold all · {toggle_label} · Enter:select · ^V:preview · ^E:export")

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
        tree = self.query_one("#session-tree", Tree)

        # If search input has focus, handle navigation and shortcuts
        if search.has_focus:
            # Arrow up/down: navigate tree without losing search focus
            if event.key == "down":
                self.action_cursor_down()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "up":
                self.action_cursor_up()
                event.prevent_default()
                event.stop()
                return
            # Ctrl shortcuts: execute actions even when input has focus
            elif event.key == "ctrl+v":
                self.action_preview()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "ctrl+e":
                self.action_export_md()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "ctrl+j":
                self.action_export_json()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "ctrl+r":
                self.action_rename()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "ctrl+a":
                self.action_toggle_all()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "escape":
                # Clear search and focus tree
                search.value = ""
                tree.focus()
                event.prevent_default()
                event.stop()
                return

        # Auto-focus search when typing printable characters
        if event.character and event.character.isprintable() and not search.has_focus:
            search.focus()
            search.value += event.character
            search.cursor_position = len(search.value)


def run_browser(project_filter: Optional[str] = None, orphans_only: bool = False) -> Optional[Dict[str, Any]]:
    """Run the TUI browser."""
    app = SessionBrowser(project_filter=project_filter, orphans_only=orphans_only)
    return app.run()


if __name__ == "__main__":
    result = run_browser()
    if result:
        print(f"Selected: {result}")
