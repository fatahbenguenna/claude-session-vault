#!/usr/bin/env python3
"""Automatic installer for Claude Session Vault hooks."""

import json
import shutil
from pathlib import Path
from typing import Dict, Any

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

HOOKS_CONFIG = {
    "SessionStart": [{
        "hooks": [{
            "type": "command",
            "command": "claude-vault-hook"
        }]
    }],
    "UserPromptSubmit": [{
        "hooks": [{
            "type": "command",
            "command": "claude-vault-hook"
        }]
    }],
    "PostToolUse": [{
        "matcher": "*",
        "hooks": [{
            "type": "command",
            "command": "claude-vault-hook"
        }]
    }],
    "SessionEnd": [{
        "hooks": [{
            "type": "command",
            "command": "claude-vault-hook"
        }]
    }]
}


def load_settings() -> Dict[str, Any]:
    """Load existing Claude settings or return empty dict."""
    if CLAUDE_SETTINGS_PATH.exists():
        try:
            return json.loads(CLAUDE_SETTINGS_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_settings(settings: Dict[str, Any]) -> None:
    """Save settings to Claude settings file."""
    CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing settings
    if CLAUDE_SETTINGS_PATH.exists():
        backup_path = CLAUDE_SETTINGS_PATH.with_suffix('.json.backup')
        shutil.copy(CLAUDE_SETTINGS_PATH, backup_path)
        print(f"📦 Backed up existing settings to {backup_path}")

    CLAUDE_SETTINGS_PATH.write_text(json.dumps(settings, indent=2))


def merge_hooks(existing_hooks: Dict, new_hooks: Dict) -> Dict:
    """Merge new hooks with existing ones without duplicates."""
    merged = existing_hooks.copy()

    for hook_name, hook_configs in new_hooks.items():
        if hook_name not in merged:
            merged[hook_name] = hook_configs
        else:
            # Check if vault hook already exists
            existing_commands = []
            for config in merged[hook_name]:
                for hook in config.get('hooks', []):
                    if hook.get('command'):
                        existing_commands.append(hook['command'])

            # Add only if not already present
            for config in hook_configs:
                for hook in config.get('hooks', []):
                    if hook.get('command') not in existing_commands:
                        merged[hook_name].append(config)

    return merged


def install_hooks(force: bool = False) -> bool:
    """Install Claude Session Vault hooks.

    Args:
        force: If True, overwrite existing vault hooks

    Returns:
        True if installation successful
    """
    settings = load_settings()

    # Get or create hooks section
    existing_hooks = settings.get('hooks', {})

    # Check if already installed
    already_installed = False
    for hook_name, configs in existing_hooks.items():
        for config in configs:
            for hook in config.get('hooks', []):
                if 'claude-vault-hook' in hook.get('command', ''):
                    already_installed = True
                    break

    if already_installed and not force:
        print("✅ Claude Session Vault hooks are already installed!")
        print("   Use --force to reinstall")
        return True

    # Merge hooks
    settings['hooks'] = merge_hooks(existing_hooks, HOOKS_CONFIG)

    # Save
    save_settings(settings)

    print("✅ Claude Session Vault hooks installed successfully!")
    print(f"   Settings file: {CLAUDE_SETTINGS_PATH}")
    print("\n🔄 Restart Claude Code for changes to take effect")

    return True


def uninstall_hooks() -> bool:
    """Remove Claude Session Vault hooks."""
    settings = load_settings()

    if 'hooks' not in settings:
        print("No hooks found in settings")
        return True

    # Remove vault hooks
    for hook_name in list(settings['hooks'].keys()):
        configs = settings['hooks'][hook_name]
        new_configs = []

        for config in configs:
            new_hooks = []
            for hook in config.get('hooks', []):
                if 'claude-vault-hook' not in hook.get('command', ''):
                    new_hooks.append(hook)

            if new_hooks:
                config['hooks'] = new_hooks
                new_configs.append(config)

        if new_configs:
            settings['hooks'][hook_name] = new_configs
        else:
            del settings['hooks'][hook_name]

    save_settings(settings)

    print("✅ Claude Session Vault hooks removed")
    return True


def main():
    """CLI entry point for installer."""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--uninstall':
        uninstall_hooks()
    elif len(sys.argv) > 1 and sys.argv[1] == '--force':
        install_hooks(force=True)
    else:
        install_hooks()


if __name__ == "__main__":
    main()
