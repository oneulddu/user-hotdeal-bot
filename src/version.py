"""Backward-compatible import location for application version."""

from src import __version__, get_version

__all__ = ["__version__", "get_version"]
