#!/usr/bin/env python3
"""Hook script for Claude Code events - called by Claude Code hooks."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add package to path if running as script
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_vault.db import init_db, insert_event, end_session, sync_transcript_entries


def process_hook_input() -> dict:
    """Read and parse hook input from stdin."""
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            return {}
        return json.loads(raw_input)
    except json.JSONDecodeError:
        return {}


def find_active_transcript(session_id: str, reported_path: str) -> str:
    """Find the actively written transcript file for a session.

    After compaction, Claude may report the old transcript_path but write to a new file.
    This function detects stale paths and finds the active one.
    """
    from pathlib import Path
    import time

    reported = Path(reported_path)
    if not reported.exists():
        return reported_path

    # Check if the reported file is being actively written
    # (modified in the last 60 seconds)
    file_mtime = reported.stat().st_mtime
    if time.time() - file_mtime < 60:
        return reported_path

    # The reported file is stale - look for a newer file in the same directory
    project_dir = reported.parent
    newest_file = None
    newest_mtime = 0

    for jsonl_file in project_dir.glob("*.jsonl"):
        # Skip subagent files
        if jsonl_file.stem.startswith('agent-'):
            continue
        mtime = jsonl_file.stat().st_mtime
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest_file = jsonl_file

    if newest_file and newest_mtime > file_mtime:
        return str(newest_file)

    return reported_path


def sync_in_background(session_id: str, transcript_path: str):
    """Spawn a background process to sync transcript entries after a small delay.

    This allows Claude to finish writing to the JSONL file before we read it.
    Uses subprocess to ensure it works in managed environments (pipx, uv).
    """
    import subprocess

    # Find the active transcript (handles post-compaction case)
    active_path = find_active_transcript(session_id, transcript_path)

    # Extract session ID from the active file (may differ after compaction)
    from pathlib import Path
    active_session_id = Path(active_path).stem

    # Sync the active session
    try:
        subprocess.Popen(
            ['sh', '-c', f'sleep 0.5 && claude-vault sync -s {active_session_id[:8]} >/dev/null 2>&1'],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # If subprocess fails, fall back to synchronous sync
        sync_transcript_entries(active_session_id, active_path)


def main():
    """Main entry point for hook processing."""
    try:
        # Ensure database exists
        init_db()

        # Read hook input
        input_data = process_hook_input()

        if not input_data:
            sys.exit(0)

        event_type = input_data.get('hook_event_name', 'unknown')

        # Build event record
        event = {
            'timestamp': datetime.now().isoformat(),
            'session_id': input_data.get('session_id'),
            'event_type': event_type,
            'cwd': input_data.get('cwd'),
            'transcript_path': input_data.get('transcript_path'),
        }

        # Add event-specific data
        if event_type == 'UserPromptSubmit':
            event['prompt'] = input_data.get('prompt')

        elif event_type in ('PreToolUse', 'PostToolUse', 'PostToolUseFailure'):
            event['tool_name'] = input_data.get('tool_name')
            event['tool_input'] = input_data.get('tool_input')
            if event_type == 'PostToolUse':
                event['tool_response'] = input_data.get('tool_response')

        elif event_type == 'SessionEnd':
            # Mark session as ended
            if input_data.get('session_id'):
                end_session(input_data['session_id'])

        # Insert event into database (synchronous - fast)
        insert_event(event)

        # Sync transcript entries in background (after delay for Claude to finish writing)
        session_id = input_data.get('session_id')
        transcript_path = input_data.get('transcript_path')
        if session_id and transcript_path:
            sync_in_background(session_id, transcript_path)

        # Output empty JSON to not block Claude
        print(json.dumps({}))
        sys.exit(0)

    except Exception as e:
        # Never block Claude Code - just log and exit cleanly
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
