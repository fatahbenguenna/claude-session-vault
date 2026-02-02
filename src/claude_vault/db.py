"""SQLite database management for Claude Session Vault."""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

DEFAULT_DB_PATH = Path.home() / ".claude" / "vault.db"


def get_db_path() -> Path:
    """Get the database path, creating parent directories if needed."""
    db_path = DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a database connection with row factory."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize the database schema with FTS5 for full-text search."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Main sessions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            project_path TEXT,
            project_name TEXT,
            custom_name TEXT,
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add custom_name column if it doesn't exist (migration for existing DBs)
    try:
        cursor.execute("ALTER TABLE sessions ADD COLUMN custom_name TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Events table for all hook events
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            tool_name TEXT,
            tool_input TEXT,
            tool_response TEXT,
            prompt TEXT,
            cwd TEXT,
            transcript_path TEXT,
            timestamp TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    # Full-text search virtual table
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
            session_id,
            event_type,
            tool_name,
            tool_input,
            tool_response,
            prompt,
            content='events',
            content_rowid='id'
        )
    """)

    # Triggers to keep FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
            INSERT INTO events_fts(rowid, session_id, event_type, tool_name, tool_input, tool_response, prompt)
            VALUES (new.id, new.session_id, new.event_type, new.tool_name, new.tool_input, new.tool_response, new.prompt);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
            INSERT INTO events_fts(events_fts, rowid, session_id, event_type, tool_name, tool_input, tool_response, prompt)
            VALUES ('delete', old.id, old.session_id, old.event_type, old.tool_name, old.tool_input, old.tool_response, old.prompt);
        END
    """)

    # Indexes for faster queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")

    # Transcript entries table - stores full conversation incrementally
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcript_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            line_number INTEGER NOT NULL,
            entry_type TEXT,
            role TEXT,
            content TEXT,
            raw_json TEXT,
            timestamp TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(session_id, line_number)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transcript_session ON transcript_entries(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transcript_type ON transcript_entries(entry_type)")

    # Full-text search for transcript content
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
            session_id,
            role,
            content,
            content='transcript_entries',
            content_rowid='id'
        )
    """)

    # Triggers to keep transcript FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS transcript_ai AFTER INSERT ON transcript_entries BEGIN
            INSERT INTO transcript_fts(rowid, session_id, role, content)
            VALUES (new.id, new.session_id, new.role, new.content);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS transcript_ad AFTER DELETE ON transcript_entries BEGIN
            INSERT INTO transcript_fts(transcript_fts, rowid, session_id, role, content)
            VALUES ('delete', old.id, old.session_id, old.role, old.content);
        END
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_name)")

    conn.commit()
    conn.close()


def insert_event(event: Dict[str, Any], db_path: Optional[Path] = None) -> int:
    """Insert a new event into the database."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    session_id = event.get('session_id')

    # Ensure session exists
    cursor.execute("""
        INSERT OR IGNORE INTO sessions (session_id, project_path, project_name, started_at)
        VALUES (?, ?, ?, ?)
    """, (
        session_id,
        event.get('cwd'),
        Path(event.get('cwd', '')).name if event.get('cwd') else None,
        event.get('timestamp')
    ))

    # Insert event
    cursor.execute("""
        INSERT INTO events (
            session_id, event_type, tool_name, tool_input, tool_response,
            prompt, cwd, transcript_path, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        event.get('event_type'),
        event.get('tool_name'),
        json.dumps(event.get('tool_input')) if event.get('tool_input') else None,
        json.dumps(event.get('tool_response')) if event.get('tool_response') else None,
        event.get('prompt'),
        event.get('cwd'),
        event.get('transcript_path'),
        event.get('timestamp')
    ))

    event_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return event_id


def end_session(session_id: str, db_path: Optional[Path] = None) -> None:
    """Mark a session as ended."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE sessions SET ended_at = ? WHERE session_id = ?
    """, (datetime.now().isoformat(), session_id))

    conn.commit()
    conn.close()


def search_events(
    query: str,
    limit: int = 50,
    session_id: Optional[str] = None,
    event_type: Optional[str] = None,
    db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Full-text search across events."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    sql = """
        SELECT e.*, s.project_name, s.project_path
        FROM events e
        JOIN sessions s ON e.session_id = s.session_id
        JOIN events_fts fts ON e.id = fts.rowid
        WHERE events_fts MATCH ?
    """
    params = [query]

    if session_id:
        sql += " AND e.session_id = ?"
        params.append(session_id)

    if event_type:
        sql += " AND e.event_type = ?"
        params.append(event_type)

    sql += " ORDER BY e.timestamp DESC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def list_sessions(
    limit: int = 20,
    project_filter: Optional[str] = None,
    db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """List all sessions with message counts from transcript_entries."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Use transcript_entries for last_activity (more reliable after sync)
    # Fall back to events or started_at if no transcript data
    sql = """
        SELECT
            s.*,
            COUNT(t.id) as message_count,
            COALESCE(MAX(t.timestamp), MAX(e.timestamp), s.started_at) as last_activity
        FROM sessions s
        LEFT JOIN transcript_entries t ON s.session_id = t.session_id
        LEFT JOIN events e ON s.session_id = e.session_id
    """
    params = []

    if project_filter:
        sql += " WHERE s.project_name LIKE ?"
        params.append(f"%{project_filter}%")

    sql += """
        GROUP BY s.session_id
        ORDER BY last_activity DESC NULLS LAST
        LIMIT ?
    """
    params.append(limit)

    cursor.execute(sql, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def get_session_events(
    session_id: str,
    limit: int = 100,
    db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Get all events for a specific session."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM events
        WHERE session_id = ?
        ORDER BY timestamp ASC
        LIMIT ?
    """, (session_id, limit))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def get_stats(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get vault statistics."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    stats = {}

    cursor.execute("SELECT COUNT(*) FROM sessions")
    stats['total_sessions'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM events")
    stats['total_events'] = cursor.fetchone()[0]

    # Transcript entries stats
    cursor.execute("SELECT COUNT(*) FROM transcript_entries")
    stats['total_transcript_entries'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT session_id) FROM transcript_entries")
    stats['sessions_with_transcripts'] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT event_type, COUNT(*) as count
        FROM events
        GROUP BY event_type
        ORDER BY count DESC
    """)
    stats['events_by_type'] = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute("""
        SELECT project_name, COUNT(*) as session_count
        FROM sessions
        WHERE project_name IS NOT NULL
        GROUP BY project_name
        ORDER BY session_count DESC
        LIMIT 10
    """)
    stats['top_projects'] = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute("""
        SELECT tool_name, COUNT(*) as count
        FROM events
        WHERE tool_name IS NOT NULL
        GROUP BY tool_name
        ORDER BY count DESC
        LIMIT 10
    """)
    stats['top_tools'] = {row[0]: row[1] for row in cursor.fetchall()}

    # Database file size
    db_file = db_path or get_db_path()
    if db_file.exists():
        stats['db_size_mb'] = round(db_file.stat().st_size / (1024 * 1024), 2)

    conn.close()
    return stats


def rename_session(session_id: str, custom_name: str, db_path: Optional[Path] = None) -> bool:
    """Rename a session with a custom name."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Handle partial session IDs
    cursor.execute(
        "SELECT session_id FROM sessions WHERE session_id LIKE ?",
        (f"{session_id}%",)
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False

    full_session_id = row[0]

    cursor.execute(
        "UPDATE sessions SET custom_name = ? WHERE session_id = ?",
        (custom_name, full_session_id)
    )
    conn.commit()
    conn.close()
    return True


def get_session_custom_name(session_id: str, db_path: Optional[Path] = None) -> Optional[str]:
    """Get the custom name for a session if set."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT custom_name FROM sessions WHERE session_id LIKE ?",
        (f"{session_id}%",)
    )
    row = cursor.fetchone()
    conn.close()

    if row and row[0]:
        return row[0]
    return None


def get_last_synced_line(session_id: str, db_path: Optional[Path] = None) -> int:
    """Get the last synced line number for a session."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT MAX(line_number) FROM transcript_entries WHERE session_id = ?",
        (session_id,)
    )
    row = cursor.fetchone()
    conn.close()

    return row[0] if row and row[0] is not None else -1


def sync_transcript_entries(
    session_id: str,
    transcript_path: Optional[str] = None,
    db_path: Optional[Path] = None
) -> int:
    """
    Sync new transcript entries from JSONL file to database.

    Returns the number of new entries synced.
    """
    if not transcript_path:
        # Try to find transcript path from events
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transcript_path FROM events WHERE session_id = ? AND transcript_path IS NOT NULL LIMIT 1",
            (session_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            transcript_path = row[0]
        else:
            return 0

    jsonl_file = Path(transcript_path)
    if not jsonl_file.exists():
        return 0

    last_line = get_last_synced_line(session_id, db_path)
    new_entries = 0

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Ensure session exists in sessions table
    # Extract project info from path: ~/.claude/projects/-Users-fatah-project-name/session.jsonl
    project_path = str(jsonl_file.parent)
    project_name = jsonl_file.parent.name
    # Convert -Users-fatah-project-name to just project-name
    if project_name.startswith('-'):
        parts = project_name.split('-')
        # Find the last meaningful part (skip Users, username, etc.)
        if len(parts) > 3:
            project_name = '-'.join(parts[3:])  # Skip -Users-username-
        else:
            project_name = parts[-1] if parts else project_name

    cursor.execute("""
        INSERT OR IGNORE INTO sessions (session_id, project_path, project_name, started_at)
        VALUES (?, ?, ?, datetime('now'))
    """, (session_id, project_path, project_name))

    try:
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f):
                # Skip already synced lines
                if line_num <= last_line:
                    continue

                line = line.strip()
                if not line:
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Extract entry metadata
                entry_type = entry.get('type')
                role = entry.get('message', {}).get('role') if entry.get('message') else None

                # Extract content based on entry type
                content = None
                if entry_type == 'user' and entry.get('message'):
                    msg = entry['message']
                    if isinstance(msg.get('content'), str):
                        content = msg['content']
                    elif isinstance(msg.get('content'), list):
                        # Extract text from content blocks
                        texts = []
                        for block in msg['content']:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                texts.append(block.get('text', ''))
                            elif isinstance(block, str):
                                texts.append(block)
                        content = '\n'.join(texts) if texts else None
                elif entry_type == 'assistant' and entry.get('message'):
                    msg = entry['message']
                    if isinstance(msg.get('content'), list):
                        texts = []
                        for block in msg['content']:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                texts.append(block.get('text', ''))
                        content = '\n'.join(texts) if texts else None
                elif entry_type == 'summary':
                    content = entry.get('summary')

                # Get timestamp
                timestamp = entry.get('timestamp')

                # Insert entry
                cursor.execute("""
                    INSERT OR IGNORE INTO transcript_entries
                    (session_id, line_number, entry_type, role, content, raw_json, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    line_num,
                    entry_type,
                    role,
                    content,
                    line,  # Store raw JSON for full reconstruction
                    timestamp
                ))

                if cursor.rowcount > 0:
                    new_entries += 1

        conn.commit()
    finally:
        conn.close()

    return new_entries


def get_transcript_entries(
    session_id: str,
    db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Get all transcript entries for a session from the database."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM transcript_entries
        WHERE session_id = ?
        ORDER BY line_number ASC
    """, (session_id,))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def rebuild_sessions_from_transcripts(db_path: Optional[Path] = None) -> int:
    """Rebuild sessions table from transcript_entries for sessions that don't exist.

    Returns the number of sessions created.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Find all unique session_ids in transcript_entries that don't have a session record
    cursor.execute("""
        SELECT DISTINCT t.session_id
        FROM transcript_entries t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        WHERE s.session_id IS NULL
    """)
    orphan_sessions = [row[0] for row in cursor.fetchall()]

    created = 0
    for session_id in orphan_sessions:
        # Get first and last timestamps
        cursor.execute("""
            SELECT MIN(timestamp), MAX(timestamp)
            FROM transcript_entries
            WHERE session_id = ?
        """, (session_id,))
        row = cursor.fetchone()
        started_at = row[0] if row else None
        ended_at = row[1] if row else None

        # Try to extract project name from raw_json (cwd field)
        project_name = 'Unknown'
        project_path = None
        cursor.execute("""
            SELECT raw_json FROM transcript_entries
            WHERE session_id = ? AND raw_json IS NOT NULL
            LIMIT 1
        """, (session_id,))
        raw_row = cursor.fetchone()
        if raw_row and raw_row[0]:
            try:
                data = json.loads(raw_row[0])
                cwd = data.get('cwd', '')
                if cwd:
                    project_path = cwd
                    project_name = Path(cwd).name
            except:
                pass

        cursor.execute("""
            INSERT OR IGNORE INTO sessions (session_id, project_path, project_name, started_at, ended_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, project_path, project_name, started_at, ended_at))

        if cursor.rowcount > 0:
            created += 1

    conn.commit()
    conn.close()
    return created


def search_transcripts(
    query: str,
    limit: int = 50,
    db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Search transcript content across all sessions using FTS5."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    try:
        # Try FTS5 search first (fast)
        cursor.execute("""
            SELECT t.*, s.project_name, s.custom_name
            FROM transcript_entries t
            JOIN sessions s ON t.session_id = s.session_id
            JOIN transcript_fts fts ON t.id = fts.rowid
            WHERE transcript_fts MATCH ?
            ORDER BY t.timestamp DESC
            LIMIT ?
        """, (query, limit))
    except sqlite3.OperationalError:
        # Fallback to LIKE if FTS table doesn't exist yet
        cursor.execute("""
            SELECT t.*, s.project_name, s.custom_name
            FROM transcript_entries t
            JOIN sessions s ON t.session_id = s.session_id
            WHERE t.content LIKE ?
            ORDER BY t.timestamp DESC
            LIMIT ?
        """, (f"%{query}%", limit))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return results


def search_sessions_with_content(
    query: str,
    limit: int = 20,
    db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Search content and return session info with metadata.

    Returns sessions enriched with data from both sessions table (if available)
    and transcript_entries (always available for synced sessions).

    Uses FTS5 with prefix search first, then falls back to LIKE for substring matches.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()
    results = []

    # Prepare FTS query with prefix search (word*)
    fts_query = f'"{query}"*'

    try:
        # FTS search with prefix matching
        cursor.execute("""
            SELECT
                t.session_id,
                COALESCE(s.project_name, 'Unknown') as project_name,
                COALESCE(s.custom_name, '') as custom_name,
                MIN(t.timestamp) as first_activity,
                MAX(t.timestamp) as last_activity,
                COUNT(*) as entry_count
            FROM transcript_entries t
            LEFT JOIN sessions s ON t.session_id = s.session_id
            JOIN transcript_fts fts ON t.id = fts.rowid
            WHERE transcript_fts MATCH ?
            GROUP BY t.session_id
            ORDER BY last_activity DESC
            LIMIT ?
        """, (fts_query, limit))
        results = [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        pass

    # If no FTS results, fallback to LIKE for substring search
    if not results:
        cursor.execute("""
            SELECT
                t.session_id,
                COALESCE(s.project_name, 'Unknown') as project_name,
                COALESCE(s.custom_name, '') as custom_name,
                MIN(t.timestamp) as first_activity,
                MAX(t.timestamp) as last_activity,
                COUNT(*) as entry_count
            FROM transcript_entries t
            LEFT JOIN sessions s ON t.session_id = s.session_id
            WHERE t.content LIKE ? COLLATE NOCASE
            GROUP BY t.session_id
            ORDER BY last_activity DESC
            LIMIT ?
        """, (f"%{query}%", limit))
        results = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return results


def search_sessions_by_content(
    query: str,
    limit: int = 20,
    db_path: Optional[Path] = None
) -> List[str]:
    """Search and return unique session IDs that contain the query in their content.

    Uses FTS5 with prefix search first, then falls back to LIKE for substring matches.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()
    results = []

    # Prepare FTS query with prefix search (word*)
    fts_query = f'"{query}"*'

    try:
        # Try FTS5 search with prefix
        cursor.execute("""
            SELECT DISTINCT t.session_id
            FROM transcript_entries t
            JOIN transcript_fts fts ON t.id = fts.rowid
            WHERE transcript_fts MATCH ?
            ORDER BY t.timestamp DESC
            LIMIT ?
        """, (fts_query, limit))
        results = [row[0] for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        pass

    # If no FTS results, fallback to LIKE for substring search
    if not results:
        cursor.execute("""
            SELECT DISTINCT session_id
            FROM transcript_entries
            WHERE content LIKE ? COLLATE NOCASE
            ORDER BY timestamp DESC
            LIMIT ?
        """, (f"%{query}%", limit))
        results = [row[0] for row in cursor.fetchall()]

    conn.close()

    return results
