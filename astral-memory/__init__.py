# Generation Timestamp: 2026-04-28T15:10:00Z
"""
Astral Core Memory — Hermes Agent Memory Provider Plugin
Version: 2.0.0

Offline-first persistent memory with surprise-gated learning.
Implements the MemoryProvider ABC for proper Hermes integration.

Install:
  Copy this directory to ~/.hermes/plugins/memory/astral-memory/
  Set memory.provider: astral-memory in ~/.hermes/config.yaml

Architecture:
  Thin HTTP bridge → Astral Core memory server (:8090).
  The server runs the full MASK/HOPE pipeline — surprise gating,
  Delta Rule matrix, 5-tier lifecycle, hybrid retrieval, Dreamer
  consolidation.  This plugin just talks to it.

Repository: https://github.com/Suo-commerce/memory-hermes
License: MIT
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import schemas

logger = logging.getLogger("astral-memory")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERSION = "2.0.0"
_DEFAULT_SERVER_URL = "http://localhost:8090"
_CONNECT_TIMEOUT = 3.0
_REQUEST_TIMEOUT = 10.0
_PREFETCH_TIMEOUT = 5.0

# Circuit breaker — stop hammering a dead server
_CB_THRESHOLD = 3
_CB_RESET_SECONDS = 60


# ---------------------------------------------------------------------------
# HTTP helpers — requests (bundled with Hermes)
# ---------------------------------------------------------------------------

class _HttpClient:
    """HTTP client with circuit breaker for the memory server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._failures = 0
        self._circuit_open_at: float = 0.0

    @property
    def _circuit_open(self) -> bool:
        if self._failures < _CB_THRESHOLD:
            return False
        if time.monotonic() - self._circuit_open_at > _CB_RESET_SECONDS:
            self._failures = 0
            return False
        return True

    def _record_failure(self):
        self._failures += 1
        if self._failures >= _CB_THRESHOLD:
            self._circuit_open_at = time.monotonic()
            logger.warning(
                "Circuit breaker open — suppressing requests to %s for %ds",
                self.base_url, _CB_RESET_SECONDS,
            )

    def _record_success(self):
        self._failures = 0

    def get(self, path: str, timeout: float = _REQUEST_TIMEOUT,
            params: dict | None = None) -> dict:
        if self._circuit_open:
            return {"error": "circuit_breaker_open"}
        try:
            r = requests.get(
                f"{self.base_url}{path}",
                params=params, timeout=timeout,
            )
            r.raise_for_status()
            self._record_success()
            return r.json()
        except requests.ConnectionError:
            self._record_failure()
            return {"error": f"Memory server not reachable at {self.base_url}"}
        except requests.Timeout:
            self._record_failure()
            return {"error": f"Request timed out ({timeout}s)"}
        except requests.HTTPError as e:
            self._record_success()  # server is alive, just returned error
            try:
                body = e.response.json()
            except Exception:
                body = {"detail": e.response.text[:200]}
            return {"error": f"HTTP {e.response.status_code}", **body}
        except Exception as e:
            self._record_failure()
            return {"error": str(e)}

    def post(self, path: str, payload: dict | None = None,
             timeout: float = _REQUEST_TIMEOUT) -> dict:
        if self._circuit_open:
            return {"error": "circuit_breaker_open"}
        try:
            r = requests.post(
                f"{self.base_url}{path}",
                json=payload or {}, timeout=timeout,
            )
            r.raise_for_status()
            self._record_success()
            return r.json()
        except requests.ConnectionError:
            self._record_failure()
            return {"error": f"Memory server not reachable at {self.base_url}"}
        except requests.Timeout:
            self._record_failure()
            return {"error": f"Request timed out ({timeout}s)"}
        except requests.HTTPError as e:
            self._record_success()
            try:
                body = e.response.json()
            except Exception:
                body = {"detail": e.response.text[:200]}
            return {"error": f"HTTP {e.response.status_code}", **body}
        except Exception as e:
            self._record_failure()
            return {"error": str(e)}

    def delete(self, path: str, timeout: float = _REQUEST_TIMEOUT) -> dict:
        if self._circuit_open:
            return {"error": "circuit_breaker_open"}
        try:
            r = requests.delete(
                f"{self.base_url}{path}", timeout=timeout,
            )
            r.raise_for_status()
            self._record_success()
            return r.json()
        except requests.ConnectionError:
            self._record_failure()
            return {"error": f"Memory server not reachable at {self.base_url}"}
        except requests.Timeout:
            self._record_failure()
            return {"error": f"Request timed out ({timeout}s)"}
        except requests.HTTPError as e:
            self._record_success()
            try:
                body = e.response.json()
            except Exception:
                body = {"detail": e.response.text[:200]}
            return {"error": f"HTTP {e.response.status_code}", **body}
        except Exception as e:
            self._record_failure()
            return {"error": str(e)}

    def health(self) -> dict | None:
        """Health check with short timeout.  Returns None on failure."""
        try:
            r = requests.get(
                f"{self.base_url}/health", timeout=_CONNECT_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    # Fallback for development/testing outside Hermes
    class MemoryProvider:  # type: ignore[no-redef]
        """Stub ABC when running outside Hermes."""
        @property
        def name(self) -> str: ...
        def is_available(self) -> bool: ...
        def initialize(self, session_id: str, **kwargs) -> None: ...
        def get_tool_schemas(self) -> list: ...
        def handle_tool_call(self, name: str, args: dict) -> str: ...
        def get_config_schema(self) -> list: ...
        def save_config(self, values: dict, hermes_home: str) -> None: ...
        def prefetch(self, query: str) -> str | None: ...
        def queue_prefetch(self, query: str) -> None: ...
        def sync_turn(self, user_content: str,
                       assistant_content: str) -> None: ...
        def on_session_end(self, messages: list) -> None: ...
        def on_memory_write(self, action: str, target: str,
                            content: str) -> None: ...
        def on_pre_compress(self, messages: list) -> None: ...
        def system_prompt_block(self) -> str | None: ...
        def shutdown(self) -> None: ...


class AstralCoreMemoryProvider(MemoryProvider):
    """
    Astral Core — offline-first memory provider for Hermes Agent.

    Talks to the Astral Core memory server over HTTP (localhost:8090).
    The server handles surprise gating, embeddings, retrieval, and
    everything else.  This provider is a thin bridge.
    """

    def __init__(self):
        self._http: _HttpClient | None = None
        self._session_id: str = ""
        self._hermes_home: str = ""
        self._server_url: str = _DEFAULT_SERVER_URL
        self._server_healthy: bool = False

        # Background thread management
        self._sync_thread: threading.Thread | None = None
        self._prefetch_thread: threading.Thread | None = None
        self._prefetch_cache: str | None = None
        self._prefetch_lock = threading.Lock()

        # Config (loaded in initialize)
        self._auto_capture: bool = True
        self._auto_recall: bool = True
        self._max_recall: int = 5
        self._capture_max_chars: int = 8000

    # ------------------------------------------------------------------
    # Required: identity & availability
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "astral-memory"

    def is_available(self) -> bool:
        """No network calls allowed here per Hermes contract.
        We check if the server URL is configured — actual connectivity
        is tested in initialize()."""
        return True  # always available; server check is in initialize

    # ------------------------------------------------------------------
    # Required: initialization
    # ------------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        """Called once at agent startup."""
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))

        # Load config from file
        self._load_config()

        # Create HTTP client
        self._http = _HttpClient(self._server_url)

        # Health check
        health = self._http.health()
        if health:
            self._server_healthy = True
            logger.info(
                "Astral Core Memory: %s v%s — %d memories, embedding=%s",
                health.get("status", "?"),
                health.get("version", "?"),
                health.get("total_memories", 0),
                health.get("embedding_backend", "?"),
            )
        else:
            logger.warning(
                "Astral Core memory server not reachable at %s. "
                "Start it with: python memory_api_server.py --port 8090",
                self._server_url,
            )

    def _load_config(self):
        """Load plugin config from astral-memory.json in HERMES_HOME."""
        config_path = Path(self._hermes_home) / "astral-memory.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
                self._server_url = cfg.get("server_url", self._server_url)
                self._auto_capture = cfg.get("auto_capture", self._auto_capture)
                self._auto_recall = cfg.get("auto_recall", self._auto_recall)
                self._max_recall = cfg.get("max_recall_memories", self._max_recall)
                self._capture_max_chars = cfg.get(
                    "capture_max_chars", self._capture_max_chars,
                )
            except Exception as e:
                logger.debug("Could not load config: %s", e)

        # Environment override
        env_url = os.environ.get("ASTRAL_SERVER_URL")
        if env_url:
            self._server_url = env_url

    # ------------------------------------------------------------------
    # Required: config schema (for `hermes memory setup`)
    # ------------------------------------------------------------------

    def get_config_schema(self) -> list:
        return [
            {
                "key": "server_url",
                "description": "Astral Core memory server URL",
                "default": _DEFAULT_SERVER_URL,
            },
            {
                "key": "auto_capture",
                "description": "Automatically capture conversation turns",
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "auto_recall",
                "description": "Automatically recall memories before each turn",
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "max_recall_memories",
                "description": "Maximum memories to inject per turn",
                "default": "5",
            },
        ]

    def save_config(self, values: dict, hermes_home: str) -> None:
        """Write non-secret config to astral-memory.json."""
        config_path = Path(hermes_home) / "astral-memory.json"

        # Coerce string booleans
        for key in ("auto_capture", "auto_recall"):
            if key in values and isinstance(values[key], str):
                values[key] = values[key].lower() in ("true", "1", "yes")

        # Coerce numeric
        if "max_recall_memories" in values:
            try:
                values["max_recall_memories"] = int(values["max_recall_memories"])
            except (ValueError, TypeError):
                values["max_recall_memories"] = 5

        config_path.write_text(json.dumps(values, indent=2))
        logger.info("Config saved to %s", config_path)

    # ------------------------------------------------------------------
    # Required: tools
    # ------------------------------------------------------------------

    def get_tool_schemas(self) -> list:
        """Return tool schemas for LLM tool injection."""
        return [
            schemas.ASTRAL_RECALL,
            schemas.ASTRAL_STORE,
            schemas.ASTRAL_FORGET,
            schemas.ASTRAL_BRIEFING,
            schemas.ASTRAL_DIARY,
            schemas.ASTRAL_STATS,
            schemas.ASTRAL_SYNC,
        ]

    def handle_tool_call(self, name: str, args: dict) -> str:
        """Dispatch tool calls to handlers."""
        if not self._http:
            return json.dumps({"error": "Provider not initialized"})

        dispatch = {
            "astral_recall":   self._tool_recall,
            "astral_store":    self._tool_store,
            "astral_forget":   self._tool_forget,
            "astral_briefing": self._tool_briefing,
            "astral_diary":    self._tool_diary,
            "astral_stats":    self._tool_stats,
            "astral_sync":     self._tool_sync,
        }

        handler = dispatch.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})

        try:
            return handler(args)
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e)
            return json.dumps({"error": str(e)})

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _tool_recall(self, args: dict) -> str:
        query = args.get("query", "")
        limit = args.get("limit", self._max_recall)
        result = self._http.post("/v1/memory/search", {
            "query": query,
            "limit": limit,
        })
        return json.dumps(result, indent=2, default=str)

    def _tool_store(self, args: dict) -> str:
        text = args.get("text", "")
        category = args.get("category", "fact")
        if not text:
            return json.dumps({"error": "text is required"})
        result = self._http.post("/v1/memory/ingest", {
            "user_message": text,
            "assistant_response": f"Stored as {category}.",
            "source": "hermes_explicit",
        })
        return json.dumps(result, indent=2, default=str)

    def _tool_forget(self, args: dict) -> str:
        source = args.get("source", "")
        if not source:
            return json.dumps({"error": "source is required"})
        result = self._http.delete(f"/v1/memory/source/{source}")
        return json.dumps(result, indent=2, default=str)

    def _tool_briefing(self, args: dict) -> str:
        max_tokens = args.get("max_tokens", 200)
        result = self._http.get("/v1/memory/briefing", params={
            "max_tokens": max_tokens,
        })
        return json.dumps(result, indent=2, default=str)

    def _tool_diary(self, args: dict) -> str:
        action = args.get("action", "read")
        if action == "write":
            text = args.get("text", "")
            if not text:
                return json.dumps({"error": "text is required for diary write"})
            result = self._http.post("/v1/diary/write", {
                "entry_text": text,
                "agent_id": "hermes",
                "entry_type": args.get("entry_type", "note"),
                "session_id": self._session_id,
            })
        else:
            result = self._http.get("/v1/diary/read", params={
                "limit": args.get("limit", 10),
                "agent_id": "hermes",
            })
        return json.dumps(result, indent=2, default=str)

    def _tool_stats(self, args: dict) -> str:
        result = self._http.get("/v1/memory/stats")
        return json.dumps(result, indent=2, default=str)

    def _tool_sync(self, args: dict) -> str:
        result = self._http.post("/v1/sync/trigger")
        return json.dumps(result, indent=2, default=str)

    # ------------------------------------------------------------------
    # Memory lifecycle hooks
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str | None:
        """Static block appended to system prompt."""
        if not self._server_healthy:
            return None
        return (
            "[Astral Core Memory active — surprise-gated, offline, "
            f"{self._max_recall} memories per turn. "
            "Use astral_recall before answering from past context.]"
        )

    def prefetch(self, query: str) -> str | None:
        """Called before each LLM call.  Return context to inject.

        If queue_prefetch pre-warmed a result, use the cached value.
        Otherwise do a synchronous fetch (blocks the turn briefly).
        """
        if not self._auto_recall or not self._http:
            return None

        # Check if queue_prefetch already has a result
        with self._prefetch_lock:
            cached = self._prefetch_cache
            self._prefetch_cache = None

        if cached:
            return cached

        # Synchronous fallback
        return self._do_prefetch(query)

    def queue_prefetch(self, query: str) -> None:
        """Background pre-warm for next turn.  Non-blocking."""
        if not self._auto_recall or not self._http:
            return

        def _bg():
            result = self._do_prefetch(query)
            with self._prefetch_lock:
                self._prefetch_cache = result

        # Wait for previous prefetch to finish
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=2.0)

        self._prefetch_thread = threading.Thread(target=_bg, daemon=True)
        self._prefetch_thread.start()

    def _do_prefetch(self, query: str) -> str | None:
        """Fetch augmented prompt from memory server."""
        if not query.strip():
            return None
        try:
            data = self._http.post("/v1/memory/augmented-prompt", {
                "query": query,
                "max_memories": self._max_recall,
            })
            if "error" in data:
                return None
            block = data.get("context_block", "")
            return block if block else None
        except Exception as e:
            logger.debug("Prefetch failed: %s", e)
            return None

    def sync_turn(self, user_content: str, assistant_content: str) -> None:
        """Persist conversation turn.  MUST be non-blocking per contract."""
        if not self._auto_capture or not self._http:
            return

        def _sync():
            try:
                self._http.post("/v1/memory/ingest", {
                    "user_message": user_content[:self._capture_max_chars],
                    "assistant_response": assistant_content[:self._capture_max_chars],
                    "source": f"hermes_{self._session_id}",
                })
            except Exception as e:
                logger.debug("sync_turn failed (non-fatal): %s", e)

        # Wait for previous sync thread
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(target=_sync, daemon=True)
        self._sync_thread.start()

    def on_session_end(self, messages: list) -> None:
        """Write a diary summary when the session ends."""
        if not self._http or not messages:
            return

        # Build summary from last few messages
        tail = messages[-6:]
        lines = []
        for msg in tail:
            role = msg.get("role", "")
            content = str(msg.get("content", "")).strip()[:500]
            if content and role in ("user", "assistant"):
                prefix = "U" if role == "user" else "A"
                lines.append(f"[{prefix}] {content}")

        if not lines:
            return

        try:
            self._http.post("/v1/diary/write", {
                "entry_text": "\n".join(lines[-4:]),
                "agent_id": "hermes",
                "session_id": self._session_id,
                "entry_type": "summary",
                "metadata": {"auto": True, "plugin": "astral-memory",
                              "version": _VERSION},
            })
            logger.debug("Session diary entry written")
        except Exception as e:
            logger.debug("Diary write failed: %s", e)

    def on_pre_compress(self, messages: list) -> None:
        """Save insights before Hermes discards old context."""
        if not self._http or not messages:
            return

        # Extract user/assistant pairs about to be compressed
        pairs = []
        for i in range(len(messages) - 1):
            if (messages[i].get("role") == "user" and
                    messages[i + 1].get("role") == "assistant"):
                user_text = str(messages[i].get("content", "")).strip()
                asst_text = str(messages[i + 1].get("content", "")).strip()
                if user_text and asst_text and len(user_text) > 20:
                    pairs.append({
                        "user": user_text[:self._capture_max_chars],
                        "assistant": asst_text[:self._capture_max_chars],
                    })

        if not pairs:
            return

        def _ingest():
            try:
                self._http.post("/v1/memory/ingest/batch", {
                    "turns": pairs[:10],
                    "source": f"hermes_compress_{self._session_id}",
                })
                logger.debug("Pre-compress capture: %d pairs", len(pairs[:10]))
            except Exception as e:
                logger.debug("Pre-compress capture failed: %s", e)

        t = threading.Thread(target=_ingest, daemon=True)
        t.start()

    def on_memory_write(self, action: str, target: str,
                        content: str) -> None:
        """Mirror built-in MEMORY.md / USER.md writes to Astral Core."""
        if not self._http or not content.strip():
            return

        if action not in ("add", "replace"):
            return

        def _mirror():
            try:
                self._http.post("/v1/memory/ingest", {
                    "user_message": content[:self._capture_max_chars],
                    "assistant_response": (
                        f"Mirrored from Hermes {target} ({action})."
                    ),
                    "source": f"hermes_builtin_{target}",
                })
            except Exception as e:
                logger.debug("Memory mirror failed: %s", e)

        t = threading.Thread(target=_mirror, daemon=True)
        t.start()

    def shutdown(self) -> None:
        """Clean up background threads."""
        for thread in (self._sync_thread, self._prefetch_thread):
            if thread and thread.is_alive():
                thread.join(timeout=3.0)
        logger.debug("Astral Core Memory provider shut down")


# ---------------------------------------------------------------------------
# Plugin entry point — called by Hermes memory plugin discovery
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the Astral Core memory provider with Hermes."""
    ctx.register_memory_provider(AstralCoreMemoryProvider())
