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
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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
    """List all sessions with event counts."""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    sql = """
        SELECT
            s.*,
            COUNT(e.id) as event_count,
            MAX(e.timestamp) as last_activity
        FROM sessions s
        LEFT JOIN events e ON s.session_id = e.session_id
    """
    params = []

    if project_filter:
        sql += " WHERE s.project_name LIKE ?"
        params.append(f"%{project_filter}%")

    sql += """
        GROUP BY s.session_id
        ORDER BY last_activity DESC
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
