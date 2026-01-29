#!/bin/bash
# Claude Session Vault - Universal Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/yourusername/claude-session-vault/main/install.sh | bash

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Claude Session Vault Installer       ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
echo ""

# Detect installation method
INSTALL_METHOD=""

# Check for pipx (preferred for CLI tools)
if command -v pipx &> /dev/null; then
    INSTALL_METHOD="pipx"
    echo -e "${GREEN}✓${NC} Found pipx"
fi

# Check for uv (fast Python package installer)
if [ -z "$INSTALL_METHOD" ] && command -v uv &> /dev/null; then
    INSTALL_METHOD="uv"
    echo -e "${GREEN}✓${NC} Found uv"
fi

# Fallback to pip with --user
if [ -z "$INSTALL_METHOD" ]; then
    if command -v pip3 &> /dev/null; then
        INSTALL_METHOD="pip3"
        echo -e "${YELLOW}!${NC} Using pip3 --user (consider installing pipx)"
    elif command -v pip &> /dev/null; then
        INSTALL_METHOD="pip"
        echo -e "${YELLOW}!${NC} Using pip --user (consider installing pipx)"
    fi
fi

if [ -z "$INSTALL_METHOD" ]; then
    echo -e "${RED}✗${NC} No Python package manager found!"
    echo "  Please install pipx: brew install pipx (macOS) or apt install pipx (Ubuntu)"
    exit 1
fi

# Install the package
echo ""
echo "Installing claude-session-vault..."

REPO_URL="https://github.com/fatahbenguenna/claude-session-vault.git"

case $INSTALL_METHOD in
    pipx)
        pipx install "git+${REPO_URL}" || pipx upgrade claude-session-vault
        ;;
    uv)
        uv tool install "git+${REPO_URL}" || uv tool upgrade claude-session-vault
        ;;
    pip3)
        pip3 install --user "git+${REPO_URL}"
        ;;
    pip)
        pip install --user "git+${REPO_URL}"
        ;;
esac

# Verify installation
echo ""
if command -v claude-vault &> /dev/null; then
    echo -e "${GREEN}✓${NC} claude-vault command installed"
else
    echo -e "${YELLOW}!${NC} Adding to PATH might be required"
    echo "  Add this to your ~/.bashrc or ~/.zshrc:"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
fi

# Install hooks
echo ""
echo "Installing Claude Code hooks..."

if command -v claude-vault-install &> /dev/null; then
    claude-vault-install
else
    # Fallback: manual hook installation
    SETTINGS_FILE="$HOME/.claude/settings.json"
    HOOKS_CONFIG='{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "claude-vault-hook"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "claude-vault-hook"}]}],
    "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "claude-vault-hook"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "claude-vault-hook"}]}]
  }
}'

    mkdir -p "$HOME/.claude"

    if [ -f "$SETTINGS_FILE" ]; then
        # Backup existing settings
        cp "$SETTINGS_FILE" "$SETTINGS_FILE.backup"
        echo -e "${GREEN}✓${NC} Backed up existing settings"

        # Merge hooks using Python
        python3 -c "
import json
from pathlib import Path

settings_file = Path('$SETTINGS_FILE')
new_hooks = json.loads('$HOOKS_CONFIG')['hooks']

settings = json.loads(settings_file.read_text()) if settings_file.exists() else {}
settings.setdefault('hooks', {})

for hook_name, configs in new_hooks.items():
    if hook_name not in settings['hooks']:
        settings['hooks'][hook_name] = configs
    else:
        # Check if already installed
        existing_cmds = [h.get('command', '') for c in settings['hooks'][hook_name] for h in c.get('hooks', [])]
        if 'claude-vault-hook' not in str(existing_cmds):
            settings['hooks'][hook_name].extend(configs)

settings_file.write_text(json.dumps(settings, indent=2))
"
    else
        echo "$HOOKS_CONFIG" > "$SETTINGS_FILE"
    fi

    echo -e "${GREEN}✓${NC} Hooks installed in $SETTINGS_FILE"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Installation Complete!               ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
echo ""
echo "Usage:"
echo "  claude-vault search 'your query'   # Search all sessions"
echo "  claude-vault sessions              # List sessions"
echo "  claude-vault show <session-id>     # View a session"
echo "  claude-vault stats                 # View statistics"
echo ""
echo -e "${YELLOW}⚠${NC}  Restart Claude Code for hooks to take effect"
echo ""
