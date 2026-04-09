# Generation Timestamp: 2026-04-10T03:45:00Z
"""
Astral Core Memory — Hermes Agent Plugin

Offline-first persistent memory with surprise-gated learning.
Drop this directory into ~/.hermes/plugins/astral-memory/ and restart Hermes.

Hooks:
  pre_llm_call       — inject relevant memories into system prompt
  post_tool_call     — capture novel information from conversations
  on_session_finalize — write session diary summary

Tools:
  astral_recall, astral_store, astral_forget, astral_briefing,
  astral_diary, astral_stats, astral_sync

All communication goes through the Astral Core Memory API server
running on localhost:8090. No direct database access, no extra
dependencies beyond `requests` (bundled with Hermes).

Repository: https://github.com/Suo-commerce/memory-hermes
License: MIT
"""

from __future__ import annotations

import json
import logging
import os

import requests

from . import schemas, tools

logger = logging.getLogger("astral-memory")

# ---------------------------------------------------------------------------
# Configuration — read from Hermes config or use defaults
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "server_url": "http://localhost:8090",
    "auto_capture": True,
    "auto_recall": True,
    "max_recall_memories": 5,
    "min_similarity": 0.45,
    "briefing_card_on_start": True,
    "briefing_max_tokens": 200,
    "capture_min_messages": 2,
    "capture_max_chars": 8000,
    "fortress_url": "",
    "health_check_on_start": True,
}

_config = dict(_DEFAULT_CONFIG)
_message_count = 0
_session_messages: list = []


def _load_config_from_hermes():
    """Try to load plugin config from ~/.hermes/config.yaml."""
    try:
        from pathlib import Path
        import yaml
        config_path = Path.home() / ".hermes" / "config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                full = yaml.safe_load(f) or {}
            plugin_cfg = (full.get("plugins") or {}).get("astral-memory") or {}
            _config.update({k: v for k, v in plugin_cfg.items() if k in _DEFAULT_CONFIG})
    except Exception as e:
        logger.debug("Could not load Hermes config (using defaults): %s", e)


def _health_check() -> bool:
    """Check if the memory server is reachable."""
    try:
        r = requests.get(f"{_config['server_url']}/health", timeout=3)
        if r.status_code == 200:
            data = r.json()
            logger.info(
                "Astral Core Memory: %s v%s — %d memories, diary=%s",
                data.get("status", "?"),
                data.get("version", "?"),
                data.get("total_memories", 0),
                "yes" if data.get("diary_available") else "no",
            )
            return True
    except requests.ConnectionError:
        logger.warning(
            "Astral Core Memory server not reachable at %s. "
            "Start it with: ./astral-memory-server",
            _config["server_url"],
        )
    except Exception as e:
        logger.warning("Astral Core health check failed: %s", e)
    return False


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def _on_pre_llm_call(messages=None, task_id=None, **kwargs):
    """
    pre_llm_call hook — inject relevant memories into the system prompt.

    If this returns a dict with a "context" key, Hermes appends it to
    the ephemeral system prompt for the current turn.
    """
    if not _config["auto_recall"]:
        return None

    # Find the latest user message
    user_msg = ""
    if messages:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") for b in content if b.get("type") == "text"
                    )
                user_msg = str(content).strip()
                break

    if not user_msg:
        return None

    try:
        resp = requests.post(
            f"{_config['server_url']}/v1/memory/augmented-prompt",
            json={
                "query": user_msg,
                "messages": messages or [],
                "max_memories": _config["max_recall_memories"],
            },
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            context_block = data.get("context_block", "")
            if context_block:
                return {"context": context_block}
    except Exception as e:
        logger.debug("Memory recall failed (non-fatal): %s", e)

    return None


def _on_post_tool_call(tool_name=None, args=None, result=None,
                       task_id=None, **kwargs):
    """
    post_tool_call hook — track conversation for auto-capture.

    We accumulate messages and periodically flush to the ingest endpoint.
    """
    global _message_count
    _message_count += 1


def _on_session_finalize(task_id=None, messages=None, **kwargs):
    """
    on_session_finalize hook — auto-capture + diary write at session end.
    """
    if not messages:
        return

    server = _config["server_url"]
    max_chars = _config["capture_max_chars"]

    # 1. Auto-capture: ingest the last conversation turns
    if _config["auto_capture"] and len(messages) >= _config["capture_min_messages"]:
        try:
            # Take last few turns as user/assistant pairs
            pairs = []
            i = len(messages) - 1
            while i > 0 and len(pairs) < 5:
                if (messages[i].get("role") == "assistant" and
                        i > 0 and messages[i - 1].get("role") == "user"):
                    user_content = str(messages[i - 1].get("content", ""))[:max_chars]
                    asst_content = str(messages[i].get("content", ""))[:max_chars]
                    if user_content.strip() and asst_content.strip():
                        pairs.append({
                            "user": user_content,
                            "assistant": asst_content,
                        })
                    i -= 2
                else:
                    i -= 1

            if pairs:
                requests.post(
                    f"{server}/v1/memory/ingest/batch",
                    json={
                        "turns": list(reversed(pairs)),
                        "source": f"hermes_{task_id or 'unknown'}",
                    },
                    timeout=10,
                )
                logger.debug("Session capture: %d turn pairs ingested", len(pairs))
        except Exception as e:
            logger.debug("Session capture failed (non-fatal): %s", e)

    # 2. Diary: write session summary
    try:
        # Build summary from last 3 messages
        tail = messages[-6:]  # up to 3 pairs
        lines = []
        for msg in tail:
            role = msg.get("role", "")
            content = str(msg.get("content", "")).strip()[:500]
            if content and role in ("user", "assistant"):
                prefix = "U" if role == "user" else "A"
                lines.append(f"[{prefix}] {content}")

        if lines:
            summary = "\n".join(lines[-3:])  # last 3 messages
            requests.post(
                f"{server}/v1/diary/write",
                json={
                    "entry_text": summary,
                    "agent_id": "hermes",
                    "session_id": str(task_id or ""),
                    "entry_type": "summary",
                    "metadata": {"auto": True, "plugin": "astral-memory"},
                },
                timeout=5,
            )
            logger.debug("Session diary entry written")
    except Exception as e:
        logger.debug("Session diary write failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Registration — called by Hermes on plugin load
# ---------------------------------------------------------------------------

def register(ctx):
    """Wire schemas to handlers and register hooks."""

    # Load config
    _load_config_from_hermes()

    # Wire server URL into tools module
    tools._SERVER_URL = _config["server_url"]

    # Health check on startup
    if _config["health_check_on_start"]:
        _health_check()

    # --- Register tools ---
    ctx.register_tool(
        name="astral_recall",
        toolset="astral-memory",
        schema=schemas.ASTRAL_RECALL,
        handler=tools.recall,
    )
    ctx.register_tool(
        name="astral_store",
        toolset="astral-memory",
        schema=schemas.ASTRAL_STORE,
        handler=tools.store,
    )
    ctx.register_tool(
        name="astral_forget",
        toolset="astral-memory",
        schema=schemas.ASTRAL_FORGET,
        handler=tools.forget,
    )
    ctx.register_tool(
        name="astral_briefing",
        toolset="astral-memory",
        schema=schemas.ASTRAL_BRIEFING,
        handler=tools.briefing,
    )
    ctx.register_tool(
        name="astral_diary",
        toolset="astral-memory",
        schema=schemas.ASTRAL_DIARY,
        handler=tools.diary,
    )
    ctx.register_tool(
        name="astral_stats",
        toolset="astral-memory",
        schema=schemas.ASTRAL_STATS,
        handler=tools.stats,
    )
    ctx.register_tool(
        name="astral_sync",
        toolset="astral-memory",
        schema=schemas.ASTRAL_SYNC,
        handler=tools.sync,
    )

    # --- Register hooks ---
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("on_session_finalize", _on_session_finalize)

    logger.info(
        "Astral Core Memory plugin registered: 7 tools, 3 hooks "
        "(server: %s)", _config["server_url"],
    )
