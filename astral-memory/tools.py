# Generation Timestamp: 2026-04-10T03:45:00Z
"""Tool handlers — what runs when the LLM calls an Astral Core tool.

Each handler receives params dict + **kwargs, talks to the memory
server over HTTP, and returns a JSON string (Hermes convention).
"""

from __future__ import annotations

import json
import logging

import requests

logger = logging.getLogger("astral-memory")

# Populated by __init__.py register()
_SERVER_URL: str = "http://localhost:8090"
_TIMEOUT: int = 10


def _url(path: str) -> str:
    return f"{_SERVER_URL}{path}"


def _safe_request(method: str, path: str, **kwargs) -> dict:
    """HTTP request with error handling. Returns parsed JSON or error dict."""
    kwargs.setdefault("timeout", _TIMEOUT)
    try:
        resp = getattr(requests, method)(_url(path), **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        return {"error": f"Memory server not reachable at {_SERVER_URL}"}
    except requests.Timeout:
        return {"error": f"Memory server timed out ({_TIMEOUT}s)"}
    except requests.HTTPError as e:
        try:
            body = e.response.json()
        except Exception:
            body = {"detail": e.response.text[:200]}
        return {"error": f"HTTP {e.response.status_code}", **body}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def recall(params, **kwargs) -> str:
    query = params.get("query", "")
    limit = params.get("limit", 5)
    result = _safe_request("post", "/v1/memory/search", json={
        "query": query,
        "limit": limit,
    })
    return json.dumps(result, indent=2, default=str)


def store(params, **kwargs) -> str:
    text = params.get("text", "")
    category = params.get("category", "fact")
    if not text:
        return json.dumps({"error": "text is required"})
    result = _safe_request("post", "/v1/memory/ingest", json={
        "user_message": text,
        "assistant_response": f"Stored as {category}.",
        "source": "hermes_explicit",
    })
    return json.dumps(result, indent=2, default=str)


def forget(params, **kwargs) -> str:
    source = params.get("source", "")
    if not source:
        return json.dumps({"error": "source is required"})
    result = _safe_request("delete", f"/v1/memory/source/{source}")
    return json.dumps(result, indent=2, default=str)


def briefing(params, **kwargs) -> str:
    max_tokens = params.get("max_tokens", 200)
    result = _safe_request("get", "/v1/memory/briefing", params={
        "max_tokens": max_tokens,
    })
    return json.dumps(result, indent=2, default=str)


def diary(params, **kwargs) -> str:
    action = params.get("action", "read")

    if action == "write":
        text = params.get("text", "")
        if not text:
            return json.dumps({"error": "text is required for diary write"})
        result = _safe_request("post", "/v1/diary/write", json={
            "entry_text": text,
            "agent_id": "hermes",
            "entry_type": params.get("entry_type", "note"),
        })
    else:
        result = _safe_request("get", "/v1/diary/read", params={
            "limit": params.get("limit", 10),
            "agent_id": params.get("agent_id"),
        })

    return json.dumps(result, indent=2, default=str)


def stats(params, **kwargs) -> str:
    result = _safe_request("get", "/v1/memory/stats")
    return json.dumps(result, indent=2, default=str)


def sync(params, **kwargs) -> str:
    result = _safe_request("post", "/v1/sync/trigger", json={})
    return json.dumps(result, indent=2, default=str)
