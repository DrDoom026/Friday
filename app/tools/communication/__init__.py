"""PART 13 communication tools: the only way anything reaches an external
messaging platform from FIRDAY - always through Core -> Security Engine ->
Tool Framework, never directly from an adapter.
"""

from app.tools.communication import gmail

__all__ = ["gmail"]
