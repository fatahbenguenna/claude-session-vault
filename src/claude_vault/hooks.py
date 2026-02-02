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


def sync_in_background(session_id: str, transcript_path: str):
    """Spawn a background process to sync transcript entries after a small delay.

    This allows Claude to finish writing to the JSONL file before we read it.
    """
    try:
        # Fork a child process
        pid = os.fork()
        if pid > 0:
            # Parent process - return immediately
            return

        # Child process - detach and sync after delay
        os.setsid()  # Create new session

        # Wait a bit for Claude to finish writing
        import time
        time.sleep(0.5)

        # Now sync the transcript
        sync_transcript_entries(session_id, transcript_path)

        # Exit child process
        os._exit(0)

    except OSError:
        # Fork failed, fall back to synchronous sync
        sync_transcript_entries(session_id, transcript_path)


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
