# Generation Timestamp: 2026-04-28T15:45:00Z
"""
Tool handlers — DEPRECATED in v2.0.0.

All tool logic has moved into AstralCoreMemoryProvider.handle_tool_call()
in __init__.py.  This file exists only for backward compatibility.

If you were importing from this module directly, switch to using the
memory provider's handle_tool_call() dispatch instead.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "astral_memory.tools is deprecated in v2.0.0. "
    "Tool handlers are now methods on AstralCoreMemoryProvider.",
    DeprecationWarning,
    stacklevel=2,
)


def recall(params, **kwargs) -> str:
    raise NotImplementedError("Use AstralCoreMemoryProvider.handle_tool_call('astral_recall', params)")


def store(params, **kwargs) -> str:
    raise NotImplementedError("Use AstralCoreMemoryProvider.handle_tool_call('astral_store', params)")


def forget(params, **kwargs) -> str:
    raise NotImplementedError("Use AstralCoreMemoryProvider.handle_tool_call('astral_forget', params)")


def briefing(params, **kwargs) -> str:
    raise NotImplementedError("Use AstralCoreMemoryProvider.handle_tool_call('astral_briefing', params)")


def diary(params, **kwargs) -> str:
    raise NotImplementedError("Use AstralCoreMemoryProvider.handle_tool_call('astral_diary', params)")


def stats(params, **kwargs) -> str:
    raise NotImplementedError("Use AstralCoreMemoryProvider.handle_tool_call('astral_stats', params)")


def sync(params, **kwargs) -> str:
    raise NotImplementedError("Use AstralCoreMemoryProvider.handle_tool_call('astral_sync', params)")
