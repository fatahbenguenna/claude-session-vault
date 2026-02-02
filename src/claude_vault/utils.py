"""Shared utility functions for Claude Session Vault."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from contextlib import contextmanager


def parse_datetime_safe(value: Any) -> datetime:
    """Parse a datetime string safely, handling various formats and timezones.

    Handles:
    - ISO format with timezone (2024-01-15T10:30:00+00:00)
    - ISO format with Z suffix (2024-01-15T10:30:00Z)
    - SQLite format (2024-01-15 10:30:00)
    - None or empty values (returns datetime.now())

    Always returns a naive datetime (no timezone info) for consistent comparison.
    """
    if not value:
        return datetime.now()

    try:
        value_str = str(value)
        if 'T' in value_str:
            # ISO format
            dt = datetime.fromisoformat(value_str.replace('Z', '+00:00'))
            # Convert to naive datetime
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        else:
            # SQLite format
            return datetime.strptime(value_str, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return datetime.now()


def extract_text_from_content(content: Any) -> str:
    """Extract text from Claude message content (handles both string and list formats).

    Claude messages can have content as:
    - A simple string
    - A list of blocks: [{"type": "text", "text": "..."}, ...]

    Returns the combined text content.
    """
    if not content:
        return ""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'text':
                text_parts.append(item.get('text', ''))
        return ' '.join(text_parts)

    return str(content)


def parse_message_entry(entry: Dict[str, Any], include_tool_details: bool = False) -> Optional[Dict[str, Any]]:
    """Parse a JSONL or DB transcript entry into a normalized message dict.

    Args:
        entry: The raw entry dict (from JSONL or DB raw_json)
        include_tool_details: If True, include full tool input details; if False, just names

    Returns a dict with:
    - role: 'user' or 'assistant'
    - content: extracted text content
    - tool_uses: list of tool info (names or full details)
    - timestamp: the entry timestamp

    Returns None if the entry is not a user/assistant message.
    """
    entry_type = entry.get('type', '')
    timestamp = entry.get('timestamp', '')

    if entry_type in ('user', 'human'):
        message = entry.get('message', {})
        content = message.get('content', '')
        text = extract_text_from_content(content)

        if text:
            return {
                'role': 'user',
                'content': text,
                'tool_uses': [],
                'timestamp': timestamp,
            }

    elif entry_type == 'assistant':
        message = entry.get('message', {})
        content_blocks = message.get('content', [])

        text_parts = []
        tool_uses = []

        if isinstance(content_blocks, list):
            for block in content_blocks:
                if isinstance(block, dict):
                    if block.get('type') == 'text':
                        text_parts.append(block.get('text', ''))
                    elif block.get('type') == 'tool_use':
                        if include_tool_details:
                            tool_uses.append({
                                'name': block.get('name', 'unknown'),
                                'input': block.get('input', {}),
                            })
                        else:
                            tool_uses.append(block.get('name', 'Unknown'))
        elif isinstance(content_blocks, str):
            text_parts.append(content_blocks)

        if text_parts or tool_uses:
            return {
                'role': 'assistant',
                'content': '\n'.join(text_parts),
                'tool_uses': tool_uses,
                'timestamp': timestamp,
            }

    return None


def parse_transcript_to_messages(entries: List[Dict[str, Any]], from_raw_json: bool = False) -> List[Dict[str, Any]]:
    """Parse a list of transcript entries into conversation messages.

    Args:
        entries: List of entries (either raw JSONL dicts or DB entries with raw_json)
        from_raw_json: If True, entries have raw_json field to parse; if False, entries are already dicts

    Returns:
        List of parsed message dicts with role, content, tool_uses, timestamp
    """
    messages = []

    for entry in entries:
        if from_raw_json:
            raw_json = entry.get('raw_json')
            if not raw_json:
                continue
            try:
                data = json.loads(raw_json)
            except json.JSONDecodeError:
                continue
        else:
            data = entry

        parsed = parse_message_entry(data, include_tool_details=True)
        if parsed:
            messages.append(parsed)

    return messages


def decode_project_path(encoded_name: str) -> str:
    """Decode Claude's encoded project path.

    Claude encodes project paths by replacing:
    - / with -
    - Paths start with - (e.g., -Users-fatah-project)

    Example: -Users-fatah-my-project -> /Users/fatah/my-project
    """
    if not encoded_name.startswith('-'):
        return encoded_name
    # Simple decode: replace - with /
    return encoded_name.replace('-', '/')


def find_session_file(session_id: str, transcript_path: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Find the JSONL file for a session.

    Args:
        session_id: The session ID to find
        transcript_path: Optional known path to check first

    Returns:
        Tuple of (file_path, project_dir) or (None, None) if not found.
    """
    # Strategy 1: Use transcript_path if provided and exists
    if transcript_path and Path(transcript_path).exists():
        parent_name = Path(transcript_path).parent.name
        project_dir = decode_project_path(parent_name)
        return transcript_path, project_dir

    # Strategy 2: Search in Claude's projects directory
    claude_projects = Path.home() / ".claude" / "projects"
    if claude_projects.exists():
        for jsonl_file in claude_projects.rglob(f"{session_id}.jsonl"):
            parent_name = jsonl_file.parent.name
            project_dir = decode_project_path(parent_name)
            return str(jsonl_file), project_dir

    return None, None


def session_file_exists(session_id: str, transcript_path: Optional[str] = None) -> bool:
    """Check if a session's JSONL file still exists.

    Args:
        session_id: The session ID to check
        transcript_path: Optional known path to check

    Returns:
        True if the file exists, False otherwise.
    """
    file_path, _ = find_session_file(session_id, transcript_path)
    return file_path is not None


def relative_time(dt: datetime) -> str:
    """Convert datetime to human-readable relative time (e.g., '5 minutes ago').

    Args:
        dt: The datetime to convert (should be naive/no timezone)

    Returns:
        Human-readable relative time string.
    """
    now = datetime.now()

    # Handle timezone-aware datetimes by making dt naive
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)

    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 0:
        return "just now"

    # Time units: (threshold_seconds, unit_name, divisor)
    units = [
        (60, 'second', 1),
        (3600, 'minute', 60),
        (86400, 'hour', 3600),
        (604800, 'day', 86400),
        (2592000, 'week', 604800),
        (float('inf'), 'month', 2592000),
    ]

    for threshold, unit, divisor in units:
        if seconds < threshold:
            n = int(seconds / divisor) if divisor > 1 else int(seconds)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"

    return "long ago"
