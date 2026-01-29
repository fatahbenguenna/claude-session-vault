#!/usr/bin/env python3
"""MCP Server for Claude Session Vault - exposes vault commands as Claude Code tools."""

import json
import sys
from typing import Any

from claude_vault.db import (
    init_db,
    search_events,
    list_sessions,
    get_session_events,
    get_stats,
)

# MCP Protocol implementation
def send_response(id: Any, result: Any = None, error: Any = None):
    """Send a JSON-RPC response."""
    response = {"jsonrpc": "2.0", "id": id}
    if error:
        response["error"] = error
    else:
        response["result"] = result
    print(json.dumps(response), flush=True)


def send_notification(method: str, params: Any = None):
    """Send a JSON-RPC notification."""
    notification = {"jsonrpc": "2.0", "method": method}
    if params:
        notification["params"] = params
    print(json.dumps(notification), flush=True)


def handle_initialize(id: Any, params: dict):
    """Handle initialize request."""
    send_response(id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {}
        },
        "serverInfo": {
            "name": "claude-session-vault",
            "version": "1.0.0"
        }
    })


def handle_tools_list(id: Any):
    """Return available tools."""
    tools = [
        {
            "name": "vault_search",
            "description": "Search through all Claude Code session history using full-text search. Use this to find previous conversations, code snippets, or solutions from past sessions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (supports full-text search)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 20)",
                        "default": 20
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Filter by specific session ID (optional)"
                    },
                    "event_type": {
                        "type": "string",
                        "description": "Filter by event type: UserPromptSubmit, PostToolUse, etc. (optional)"
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "vault_sessions",
            "description": "List all recorded Claude Code sessions with event counts and timestamps.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sessions to return (default: 20)",
                        "default": 20
                    },
                    "project": {
                        "type": "string",
                        "description": "Filter by project name (optional)"
                    }
                }
            }
        },
        {
            "name": "vault_show_session",
            "description": "Show all events from a specific Claude Code session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID to retrieve (can be partial)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of events (default: 100)",
                        "default": 100
                    },
                    "prompts_only": {
                        "type": "boolean",
                        "description": "Show only user prompts",
                        "default": False
                    },
                    "tools_only": {
                        "type": "boolean",
                        "description": "Show only tool uses",
                        "default": False
                    }
                },
                "required": ["session_id"]
            }
        },
        {
            "name": "vault_stats",
            "description": "Get statistics about the Claude Session Vault: total sessions, events, top projects, most used tools.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        }
    ]
    send_response(id, {"tools": tools})


def handle_tool_call(id: Any, params: dict):
    """Handle a tool call."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    try:
        # Ensure DB is initialized
        init_db()

        if tool_name == "vault_search":
            results = search_events(
                query=arguments["query"],
                limit=arguments.get("limit", 20),
                session_id=arguments.get("session_id"),
                event_type=arguments.get("event_type")
            )

            # Format results for readability
            formatted = []
            for r in results:
                entry = {
                    "timestamp": r.get("timestamp", "")[:19],
                    "project": r.get("project_name", "-"),
                    "event_type": r.get("event_type", "-"),
                    "session_id": r.get("session_id", "")[:12],
                }
                if r.get("tool_name"):
                    entry["tool"] = r["tool_name"]
                if r.get("prompt"):
                    entry["prompt"] = r["prompt"][:500]
                if r.get("tool_input"):
                    entry["tool_input"] = r["tool_input"][:300]
                formatted.append(entry)

            content = f"Found {len(results)} results for '{arguments['query']}':\n\n"
            content += json.dumps(formatted, indent=2, ensure_ascii=False)

        elif tool_name == "vault_sessions":
            results = list_sessions(
                limit=arguments.get("limit", 20),
                project_filter=arguments.get("project")
            )

            formatted = []
            for s in results:
                formatted.append({
                    "session_id": s.get("session_id", "")[:12],
                    "project": s.get("project_name", "-"),
                    "events": s.get("event_count", 0),
                    "started": s.get("started_at", "")[:19] if s.get("started_at") else "-",
                    "last_activity": s.get("last_activity", "")[:19] if s.get("last_activity") else "-"
                })

            content = f"Found {len(results)} sessions:\n\n"
            content += json.dumps(formatted, indent=2, ensure_ascii=False)

        elif tool_name == "vault_show_session":
            session_id = arguments["session_id"]
            events = get_session_events(session_id, limit=arguments.get("limit", 100))

            # Try partial match if no results
            if not events:
                from claude_vault.db import get_connection
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT session_id FROM sessions WHERE session_id LIKE ? LIMIT 1",
                    (f"{session_id}%",)
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    events = get_session_events(row[0], limit=arguments.get("limit", 100))
                    session_id = row[0]

            if not events:
                content = f"Session '{session_id}' not found"
            else:
                # Filter if requested
                if arguments.get("prompts_only"):
                    events = [e for e in events if e.get("event_type") == "UserPromptSubmit"]
                elif arguments.get("tools_only"):
                    events = [e for e in events if e.get("tool_name")]

                formatted = []
                for e in events:
                    entry = {
                        "timestamp": e.get("timestamp", "")[:19],
                        "event_type": e.get("event_type", "-"),
                    }
                    if e.get("tool_name"):
                        entry["tool"] = e["tool_name"]
                    if e.get("prompt"):
                        entry["prompt"] = e["prompt"][:500]
                    if e.get("tool_input"):
                        try:
                            ti = json.loads(e["tool_input"]) if isinstance(e["tool_input"], str) else e["tool_input"]
                            entry["tool_input"] = json.dumps(ti)[:300]
                        except:
                            entry["tool_input"] = str(e["tool_input"])[:300]
                    formatted.append(entry)

                content = f"Session {session_id[:12]}... ({len(events)} events):\n\n"
                content += json.dumps(formatted, indent=2, ensure_ascii=False)

        elif tool_name == "vault_stats":
            stats = get_stats()
            content = "Claude Session Vault Statistics:\n\n"
            content += json.dumps(stats, indent=2, ensure_ascii=False)

        else:
            send_response(id, error={"code": -32601, "message": f"Unknown tool: {tool_name}"})
            return

        send_response(id, {
            "content": [{"type": "text", "text": content}]
        })

    except Exception as e:
        send_response(id, {
            "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            "isError": True
        })


def main():
    """Main MCP server loop."""
    # Initialize database on startup
    init_db()

    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            id = request.get("id")
            params = request.get("params", {})

            if method == "initialize":
                handle_initialize(id, params)
            elif method == "notifications/initialized":
                pass  # Client acknowledged initialization
            elif method == "tools/list":
                handle_tools_list(id)
            elif method == "tools/call":
                handle_tool_call(id, params)
            elif method == "ping":
                send_response(id, {})
            else:
                if id:  # Only respond to requests, not notifications
                    send_response(id, error={"code": -32601, "message": f"Method not found: {method}"})

        except json.JSONDecodeError:
            pass
        except Exception as e:
            if 'id' in dir() and id:
                send_response(id, error={"code": -32603, "message": str(e)})


if __name__ == "__main__":
    main()
