#!/usr/bin/env python3
"""Hook script for Claude Code events - called by Claude Code hooks."""

import json
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

        # Insert event into database
        insert_event(event)

        # Sync transcript entries incrementally
        session_id = input_data.get('session_id')
        transcript_path = input_data.get('transcript_path')
        if session_id and transcript_path:
            sync_transcript_entries(session_id, transcript_path)

        # Output empty JSON to not block Claude
        print(json.dumps({}))
        sys.exit(0)

    except Exception as e:
        # Never block Claude Code - just log and exit cleanly
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
