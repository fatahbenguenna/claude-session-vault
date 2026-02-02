"""Claude Session Vault - Persist and search Claude Code sessions."""

try:
    from claude_vault._version import __version__
except ImportError:
    # Fallback for development without build
    __version__ = "0.0.0.dev"
