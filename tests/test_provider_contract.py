# Generation Timestamp: 2026-08-25T12:30:00Z
# Purpose: Contract tests for the Hermes memory plugin; v1.2.0 retargets the
#          astral_store tests to /v1/memory/add and pins STORE-1 (explicit
#          stores must reach the server verbatim, never via the extractor).
#
# tests/test_provider_contract.py
# v1.4.0 — RT-13 P0 (plugin v2.11.1): fidelity hook regression pin —
#          the 2.10.0 capture guard (D) must gate INGEST, never the
#          observation hooks — plus the INFO instrumentation contract
#          (one line per hook invocation, one per POST result), and
#          the §7 status-line /health key tests (server >= 2.16.1 keys
#          with legacy fallback). FIX: _serve_search no longer
#          double-appends delegated posts to http.calls.
# v1.3.0 — RT-10 confidence rendering tests (plugin v2.11.0,
#          SPEC-METAMEMORY-001 v1.1 §7.1): warning on LOW/MEDIUM, silence
#          on HIGH, label-only empty reason, feature-detect golden
#          compare, malformed payload tolerance.
# v1.2.0 — STORE-1: astral_store -> /v1/memory/add (plugin v2.9.1).
#          FIX: provider fixture's _HttpClient stub now accepts the v2.5.0
#          Bearer-auth kwargs; the suite had been erroring since then.
# v1.1.0 — added namespace tests (SPEC-NAMESPACE-001).
#
# WHY THIS FILE EXISTS
#   astral-memory v2.1.0 shipped with `prefetch(self, query)` against a Hermes
#   ABC that had moved to `prefetch(self, query, *, session_id="")`. Every call
#   raised TypeError. MemoryManager caught it — at DEBUG level for prefetch,
#   WARNING for sync_turn — so the agent kept running and memory silently did
#   nothing. Users reported "memory stopped working after update"; the plugin
#   looked healthy, tools were registered, no traceback surfaced.
#
#   Signature drift in a duck-typed plugin boundary is invisible unless
#   something explicitly looks for it. This file looks for it.
#
# WHAT IT CHECKS
#   1. Every override is call-compatible with the ABC (parameter names + kinds).
#   2. Every abstractmethod is implemented.
#   3. Call-site simulation: the provider survives being invoked exactly the
#      way agent/memory_manager.py invokes it. This is the regression test —
#      (1) would have caught the v2.1.0 bug, but (3) catches drift in *how*
#      the manager calls us, which the ABC alone does not describe.
#   4. Return types the manager depends on (str, not None).
#   5. backup_paths() works without initialize() and without network.
#   6. Per-session prefetch isolation (no cross-session context leakage).
#   7. on_session_switch actually reassigns session state.
#
# RUNNING
#   Hermes must be importable. In CI:
#       pip install -e /path/to/hermes-agent
#   or add the checkout to PYTHONPATH. Set ASTRAL_CONTRACT_REQUIRE=1 to make a
#   missing Hermes a hard failure rather than a skip — do this in CI so the
#   guard cannot silently stop guarding.
#
#       ASTRAL_CONTRACT_REQUIRE=1 pytest tests/test_provider_contract.py -v

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import the Hermes ABC
# ---------------------------------------------------------------------------

_REQUIRE = os.environ.get("ASTRAL_CONTRACT_REQUIRE", "").strip() in ("1", "true", "yes")

try:
    from agent.memory_provider import MemoryProvider  # type: ignore
    _HERMES_AVAILABLE = True
    _IMPORT_ERROR = ""
except ImportError as exc:  # pragma: no cover
    MemoryProvider = None  # type: ignore
    _HERMES_AVAILABLE = False
    _IMPORT_ERROR = str(exc)

if not _HERMES_AVAILABLE:
    if _REQUIRE:
        raise RuntimeError(
            "ASTRAL_CONTRACT_REQUIRE=1 but `agent.memory_provider` is not "
            f"importable ({_IMPORT_ERROR}). The contract guard cannot run. "
            "Install Hermes Agent (pip install -e path/to/hermes-agent) or "
            "add the checkout to PYTHONPATH."
        )
    pytest.skip(
        "Hermes Agent not importable — contract guard skipped. "
        "Set ASTRAL_CONTRACT_REQUIRE=1 to make this a failure.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Import our plugin package
#
# The on-disk directory is hyphenated (`astral-memory/`), which is not a legal
# Python identifier, so a plain `import` will not find it. Load by path and
# register under the underscore name the package expects for `from . import
# schemas` to resolve.
# ---------------------------------------------------------------------------

def _load_plugin():
    repo_root = Path(__file__).resolve().parents[1]
    for dirname in ("astral_memory", "astral-memory"):
        pkg_dir = repo_root / dirname
        init = pkg_dir / "__init__.py"
        if init.exists():
            break
    else:
        raise RuntimeError(
            f"Could not locate the plugin package under {repo_root}. "
            "Expected astral_memory/ or astral-memory/."
        )

    if "astral_memory" in sys.modules:
        return sys.modules["astral_memory"]

    spec = importlib.util.spec_from_file_location(
        "astral_memory", init, submodule_search_locations=[str(pkg_dir)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["astral_memory"] = module
    spec.loader.exec_module(module)
    return module


astral_memory = _load_plugin()
AstralCoreMemoryProvider = astral_memory.AstralCoreMemoryProvider


# ---------------------------------------------------------------------------
# Fakes — no network, ever
# ---------------------------------------------------------------------------

class _FakeHttp:
    """Stands in for _HttpClient. Records calls, returns canned responses."""

    def __init__(self, base_url: str = "http://fake:8090"):
        self.base_url = base_url
        self.calls: list[tuple] = []

    def get(self, path: str, params: dict | None = None, timeout: float = 0) -> dict:
        self.calls.append(("GET", path, params))
        if path == "/v1/memory/briefing":
            return {"briefing": "BRIEFING-CARD"}
        if path == "/v1/diary/read":
            return {"entries": []}
        if path == "/v1/memory/stats":
            return {"total_memories": 42}
        return {}

    def post(self, path: str, payload: dict | None = None, timeout: float = 0) -> dict:
        self.calls.append(("POST", path, payload))
        if path == "/v1/memory/augmented-prompt":
            q = (payload or {}).get("query", "")
            return {"context_block": f"CONTEXT<{q}>"}
        if path == "/v1/memory/search":
            return {"results": []}
        if path == "/v1/memory/add":
            return {"stored": True, "memory_id": "mem-fake-0001",
                    "total_memories": 43}
        return {"ok": True}

    def delete(self, path: str, timeout: float = 0) -> dict:
        self.calls.append(("DELETE", path, None))
        return {"deleted": 0}

    def health(self) -> dict:
        return {
            "status": "ok", "version": "test", "total_memories": 0,
            "embedding_backend": "fake", "deep_rerank_enabled": False,
            "hyde_enabled": False, "namespace_enabled": True,
        }

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _ in self.calls if method is None or m == method]

    def payload_for(self, path: str) -> dict | None:
        for _, p, body in self.calls:
            if p == path:
                return body
        return None


@pytest.fixture
def http() -> _FakeHttp:
    return _FakeHttp()


@pytest.fixture
def provider(monkeypatch, tmp_path, http):
    """An initialized provider wired to a fake transport."""
    # v1.2.0: _HttpClient has taken api_token= / token_loader= since plugin
    # v2.5.0 (Bearer auth). The old `lambda url:` stub raised TypeError inside
    # initialize(), which errored every fixture-backed test — the guard had
    # been silently disarmed. Accept and ignore any keyword arguments.
    monkeypatch.setattr(
        astral_memory, "_HttpClient", lambda url, *a, **kw: http,
    )
    p = AstralCoreMemoryProvider()
    p.initialize("session-alpha", hermes_home=str(tmp_path))
    yield p
    p.shutdown()


def _drain(p) -> None:
    """Block until background work submitted via _submit() has completed."""
    pool = p._pool
    if pool is not None:
        pool.shutdown(wait=True)
        p._pool = None


# ---------------------------------------------------------------------------
# 1. Signature compatibility with the ABC
# ---------------------------------------------------------------------------

# Methods the manager calls with keyword arguments the old plugin did not accept.
# Extend this list whenever Hermes adds a hook we implement.
_OVERRIDES = [
    "name", "is_available", "initialize",
    "get_tool_schemas", "handle_tool_call",
    "system_prompt_block", "prefetch", "queue_prefetch", "sync_turn",
    "on_turn_start", "on_session_end", "on_session_switch",
    "on_pre_compress", "on_delegation", "on_memory_write",
    "get_config_schema", "save_config", "backup_paths", "shutdown",
]


def _param_shape(func) -> list[tuple]:
    """(name, kind) pairs — ignores annotations and defaults."""
    return [(p.name, p.kind) for p in inspect.signature(func).parameters.values()]


@pytest.mark.parametrize("method", _OVERRIDES)
def test_override_matches_abc_signature(method: str):
    """Our override must accept exactly what the ABC declares.

    Annotations and default values are deliberately ignored — only parameter
    names and kinds determine whether a call succeeds.
    """
    base = getattr(MemoryProvider, method, None)
    assert base is not None, (
        f"MemoryProvider has no '{method}'. Either Hermes removed it (drop our "
        f"override) or the installed Hermes is too old for this plugin."
    )
    ours = getattr(AstralCoreMemoryProvider, method)

    if isinstance(base, property):
        assert isinstance(ours, property), f"'{method}' is a property on the ABC"
        return

    assert _param_shape(ours) == _param_shape(base), (
        f"{method}() signature drift.\n"
        f"  ABC:  {inspect.signature(base)}\n"
        f"  ours: {inspect.signature(ours)}\n"
        f"MemoryManager will call the ABC form; the mismatch raises TypeError, "
        f"which the manager swallows. Memory would silently stop working."
    )


def test_no_unimplemented_abstract_methods():
    """Instantiation fails loudly if an abstractmethod is missing."""
    assert not getattr(AstralCoreMemoryProvider, "__abstractmethods__", frozenset()), (
        "Unimplemented abstract methods: "
        f"{sorted(AstralCoreMemoryProvider.__abstractmethods__)}"
    )
    AstralCoreMemoryProvider()  # must not raise


def test_no_fallback_stub():
    """The ABC must be the real one, not a locally-defined stand-in.

    v2.1.0 defined a stub MemoryProvider in an `except ImportError` branch.
    It froze the old contract and is precisely why the drift went unnoticed.
    """
    assert AstralCoreMemoryProvider.__mro__[1] is MemoryProvider, (
        "AstralCoreMemoryProvider does not inherit from the real "
        "agent.memory_provider.MemoryProvider. A fallback stub is in play."
    )


# ---------------------------------------------------------------------------
# 2. Call-site simulation — how memory_manager.py actually invokes us
# ---------------------------------------------------------------------------

def test_prefetch_accepts_session_id_kwarg(provider):
    """memory_manager.py:507 — provider.prefetch(query, session_id=session_id)"""
    result = provider.prefetch("what did we decide", session_id="session-alpha")
    assert isinstance(result, str), "prefetch must return str, not None"
    assert "CONTEXT<" in result


def test_queue_prefetch_accepts_session_id_kwarg(provider):
    """memory_manager.py:535 — provider.queue_prefetch(query, session_id=...)"""
    provider.queue_prefetch("warm me", session_id="session-alpha")
    # Next prefetch must consume the warmed cache, not refetch.
    before = len(provider._http.calls)
    result = provider.prefetch("ignored", session_id="session-alpha")
    assert "CONTEXT<warm me>" in result
    assert len(provider._http.calls) == before, "cached prefetch should not hit the server"


def test_sync_turn_accepts_session_id_and_messages(provider, http):
    """memory_manager.py:596 — sync_turn(u, a, session_id=..., messages=[...])"""
    provider.sync_turn(
        "user says", "assistant says",
        session_id="session-alpha",
        messages=[{"role": "user", "content": "user says"}],
    )
    _drain(provider)
    assert "/v1/memory/ingest" in http.paths("POST")
    body = http.payload_for("/v1/memory/ingest")
    assert body["source"] == "hermes_session-alpha"


def test_sync_turn_accepts_session_id_without_messages(provider, http):
    """memory_manager.py:603 — the messages-less branch."""
    provider.sync_turn("u", "a", session_id="session-alpha")
    _drain(provider)
    assert "/v1/memory/ingest" in http.paths("POST")


def test_manager_detects_messages_support():
    """Mirrors MemoryManager._provider_sync_accepts_messages().

    If this returns False the manager silently drops the `messages` payload
    and we lose tool-call context from every captured turn.
    """
    sig = inspect.signature(AstralCoreMemoryProvider.sync_turn)
    params = list(sig.parameters.values())
    accepts = (
        any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
        or "messages" in sig.parameters
    )
    assert accepts, "sync_turn must accept `messages` or **kwargs"


def test_handle_tool_call_accepts_kwargs(provider):
    """memory_manager.py:750 — provider.handle_tool_call(tool_name, args, **kwargs)"""
    out = provider.handle_tool_call(
        "astral_recall", {"query": "x"}, session_id="session-alpha",
    )
    parsed = json.loads(out)
    assert "error" not in parsed


def test_handle_tool_call_first_param_is_tool_name():
    """The ABC names it `tool_name`. Positional callers don't care, but the
    manager may switch to keyword dispatch; keep the names aligned."""
    params = list(inspect.signature(AstralCoreMemoryProvider.handle_tool_call).parameters)
    assert params[1] == "tool_name"


def test_on_pre_compress_returns_str(provider):
    """The manager folds the return value into the compression prompt.
    Returning None is tolerated by truthiness checks but drops the feature."""
    messages = [
        {"role": "user", "content": "a question long enough to pass the filter"},
        {"role": "assistant", "content": "an answer"},
    ]
    result = provider.on_pre_compress(messages)
    _drain(provider)
    assert isinstance(result, str)
    assert result.strip(), "on_pre_compress should contribute to the summary prompt"


def test_system_prompt_block_returns_str(provider):
    assert isinstance(provider.system_prompt_block(), str)


def test_unhealthy_server_yields_empty_prompt_block(monkeypatch, tmp_path):
    class _Dead(_FakeHttp):
        def health(self):
            return None

    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url, *a, **kw: _Dead())
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))
    assert p.system_prompt_block() == ""
    p.shutdown()


# ---------------------------------------------------------------------------
# 3. Tools
# ---------------------------------------------------------------------------

def test_every_declared_tool_has_a_handler(provider):
    """A schema with no dispatch entry is a tool the LLM can call into a hole."""
    for schema in provider.get_tool_schemas():
        name = schema["name"]
        out = json.loads(provider.handle_tool_call(name, _minimal_args(name)))
        assert out.get("error") != f"Unknown tool: {name}", f"{name} has no handler"


def _minimal_args(name: str) -> dict[str, Any]:
    return {
        "astral_recall":    {"query": "q"},
        "astral_store":     {"text": "t"},
        "astral_forget":    {"source": "s"},
        "astral_diary":     {"action": "read"},
        "astral_namespace": {"action": "get"},
    }.get(name, {})


def test_tool_schemas_are_well_formed(provider):
    for schema in provider.get_tool_schemas():
        assert set(schema) >= {"name", "description", "parameters"}
        assert schema["parameters"]["type"] == "object"
        assert isinstance(schema["parameters"].get("properties", {}), dict)


def test_unknown_tool_returns_json_error(provider):
    out = json.loads(provider.handle_tool_call("astral_nonexistent", {}))
    assert "error" in out


def test_tool_call_before_initialize_returns_json_error():
    """Never raise into the agent loop, even uninitialized."""
    out = json.loads(AstralCoreMemoryProvider().handle_tool_call("astral_stats", {}))
    assert "error" in out


# ---------------------------------------------------------------------------
# 4. Session scoping
# ---------------------------------------------------------------------------

def test_prefetch_cache_is_per_session(provider):
    """A single shared cache slot leaked context between concurrent gateway
    sessions in <= 2.1.0."""
    provider.queue_prefetch("alpha secret", session_id="session-alpha")
    provider.queue_prefetch("beta secret", session_id="session-beta")

    assert "alpha secret" in provider.prefetch("x", session_id="session-alpha")
    assert "beta secret" in provider.prefetch("x", session_id="session-beta")


def test_session_switch_reassigns_active_session(provider, http):
    """Without on_session_switch, `_active_session_id` stayed frozen at whatever
    initialize() saw and every later write carried a stale source tag."""
    provider.on_session_switch("session-omega", reset=True)
    provider.sync_turn("u", "a")  # no explicit session_id — must use the new one
    _drain(provider)
    assert http.payload_for("/v1/memory/ingest")["source"] == "hermes_session-omega"


def test_session_switch_reset_clears_cache(provider):
    provider.queue_prefetch("stale", session_id="session-alpha")
    provider.on_session_switch("session-new", reset=True)
    assert provider._prefetch_cache == {}
    assert provider._turn_counts == {}


def test_session_switch_continuation_never_carries_context(provider):
    """/branch, /resume and compression keep the conversation but change the id.
    Turn counts may carry; recalled context must not."""
    provider.on_turn_start(7, "msg")
    provider.queue_prefetch("old context", session_id="session-alpha")
    provider.on_session_switch("session-branch", parent_session_id="session-alpha")

    assert "session-branch" not in provider._prefetch_cache
    assert "session-alpha" not in provider._prefetch_cache
    assert provider._turn_counts.get("session-branch") == 7


def test_delegation_is_captured(provider, http):
    """Subagents run with skip_memory=True — the parent is the only place
    delegation results can land. v2.1.0 discarded them entirely."""
    provider.on_delegation("do the thing", "did the thing", child_session_id="kid-1")
    _drain(provider)
    body = http.payload_for("/v1/memory/ingest")
    assert body is not None
    assert body["source"] == "hermes_delegation_kid-1"


def test_on_memory_write_accepts_metadata(provider, http):
    provider.on_memory_write(
        "add", "user", "prefers tabs",
        metadata={"write_origin": "tool", "session_id": "session-alpha"},
    )
    _drain(provider)
    assert http.payload_for("/v1/memory/ingest")["source"] == "hermes_builtin_user"


def test_on_memory_write_ignores_remove(provider, http):
    provider.on_memory_write("remove", "user", "x")
    _drain(provider)
    assert "/v1/memory/ingest" not in http.paths("POST")


# ---------------------------------------------------------------------------
# 5. backup_paths — no initialize(), no network
# ---------------------------------------------------------------------------

def test_backup_paths_without_initialize_or_network(monkeypatch):
    """Contract: 'MUST be callable without initialize() and without network.'
    `hermes backup` calls this on a bare instance."""
    class _Forbidden:
        ConnectionError = Exception
        Timeout = Exception
        HTTPError = Exception

        def __getattr__(self, _name):
            raise AssertionError("backup_paths() must not touch the network")

    monkeypatch.setattr(astral_memory, "requests", _Forbidden())

    paths = AstralCoreMemoryProvider().backup_paths()
    assert isinstance(paths, list)
    assert all(isinstance(p, str) and Path(p).is_absolute() for p in paths)


def test_backup_paths_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRAL_DATA_DIR", str(tmp_path / "corpus"))
    assert str(tmp_path / "corpus") in AstralCoreMemoryProvider().backup_paths()


def test_is_available_without_network(monkeypatch):
    """Contract: no network calls in is_available()."""
    class _Forbidden:
        ConnectionError = Exception
        Timeout = Exception
        HTTPError = Exception

        def __getattr__(self, _name):
            raise AssertionError("is_available() must not touch the network")

    monkeypatch.setattr(astral_memory, "requests", _Forbidden())
    assert AstralCoreMemoryProvider().is_available() is True


# ---------------------------------------------------------------------------
# 6. Config
# ---------------------------------------------------------------------------

def test_save_config_merges_rather_than_clobbers(tmp_path):
    p = AstralCoreMemoryProvider()
    p.save_config({"server_url": "http://a:1"}, str(tmp_path))
    p.save_config({"auto_recall": "false"}, str(tmp_path))

    cfg = json.loads((tmp_path / "astral-memory.json").read_text())
    assert cfg["server_url"] == "http://a:1", "second save clobbered the first"
    assert cfg["auto_recall"] is False, "string booleans must be coerced"


def test_config_schema_keys_are_read_by_load_config(monkeypatch, tmp_path, http):
    """Every advertised config key must actually reach an attribute.
    The README documented keys the code never read."""
    (tmp_path / "astral-memory.json").write_text(json.dumps({
        "server_url": "http://x:9",
        "auto_capture": False,
        "auto_recall": False,
        "max_recall_memories": 11,
        "briefing_on_start": True,
        "data_dir": "/tmp/astral-corpus",
    }))
    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url, *a, **kw: http)
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))

    assert p._server_url == "http://x:9"
    assert p._auto_capture is False
    assert p._auto_recall is False
    assert p._max_recall == 11
    assert p._briefing_on_start is True
    assert p._data_dir == "/tmp/astral-corpus"
    p.shutdown()


def test_auto_recall_disabled_short_circuits(monkeypatch, tmp_path, http):
    (tmp_path / "astral-memory.json").write_text(json.dumps({"auto_recall": False}))
    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url, *a, **kw: http)
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))
    assert p.prefetch("q", session_id="s") == ""
    assert http.calls == []
    p.shutdown()


# ---------------------------------------------------------------------------
# 7. Failure isolation — a dead server must never break a turn
# ---------------------------------------------------------------------------

def test_dead_server_never_raises(monkeypatch, tmp_path):
    class _Dead(_FakeHttp):
        def health(self):
            return None

        def get(self, path, params=None, timeout=0):
            return {"error": "Memory server not reachable"}

        def post(self, path, payload=None, timeout=0):
            return {"error": "Memory server not reachable"}

        def delete(self, path, timeout=0):
            return {"error": "Memory server not reachable"}

    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url, *a, **kw: _Dead())
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))

    assert p.prefetch("q", session_id="s") == ""
    p.queue_prefetch("q", session_id="s")
    p.sync_turn("u", "a", session_id="s")
    p.on_turn_start(1, "m")
    p.on_session_end([{"role": "user", "content": "hi"}])
    assert isinstance(p.on_pre_compress([]), str)
    assert "error" in json.loads(p.handle_tool_call("astral_stats", {}))
    p.shutdown()


def test_shutdown_is_idempotent(provider):
    provider.shutdown()
    provider.shutdown()


# ---------------------------------------------------------------------------
# 8. Namespace (SPEC-NAMESPACE-001 §7-§15)
# ---------------------------------------------------------------------------

def test_namespace_tool_is_registered(provider):
    """The LLM must see astral_namespace in the tool schemas."""
    names = [s["name"] for s in provider.get_tool_schemas()]
    assert "astral_namespace" in names


def test_namespace_tool_has_handler(provider):
    """astral_namespace must be in the dispatch table."""
    out = json.loads(provider.handle_tool_call(
        "astral_namespace", {"action": "get"},
    ))
    assert "error" not in out


def test_namespace_get_returns_cross_namespace_by_default(provider):
    out = json.loads(provider.handle_tool_call(
        "astral_namespace", {"action": "get"},
    ))
    assert out["mode"] == "cross-namespace"
    assert out["active_namespace"] is None
    assert out["server_supported"] is True


def test_namespace_set_and_get(provider):
    out = json.loads(provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    ))
    assert out["active_namespace"] == "tree-guardians"
    assert out["mode"] == "namespace"

    out = json.loads(provider.handle_tool_call(
        "astral_namespace", {"action": "get"},
    ))
    assert out["active_namespace"] == "tree-guardians"
    assert out["mode"] == "namespace"


def test_namespace_clear(provider):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    out = json.loads(provider.handle_tool_call(
        "astral_namespace", {"action": "clear"},
    ))
    assert out["active_namespace"] is None
    assert out["mode"] == "cross-namespace"


def test_namespace_set_rejects_invalid_slug(provider):
    for bad in ("UPPER", "has space", "-leading", "trailing-", "x" * 65, "under_score"):
        out = json.loads(provider.handle_tool_call(
            "astral_namespace", {"action": "set", "namespace": bad},
        ))
        assert "error" in out, f"'{bad}' should be rejected"


def test_namespace_set_accepts_valid_slugs(provider):
    for good in ("tree-guardians", "astral-core", "sprint-14", "a", "all", "abc123"):
        out = json.loads(provider.handle_tool_call(
            "astral_namespace", {"action": "set", "namespace": good},
        ))
        assert "error" not in out, f"'{good}' should be accepted"


def test_namespace_set_requires_namespace_arg(provider):
    out = json.loads(provider.handle_tool_call(
        "astral_namespace", {"action": "set"},
    ))
    assert "error" in out


def test_namespace_set_empty_string_rejected(provider):
    out = json.loads(provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": ""},
    ))
    assert "error" in out


def test_namespace_unknown_action_returns_error(provider):
    out = json.loads(provider.handle_tool_call(
        "astral_namespace", {"action": "frobnicate"},
    ))
    assert "error" in out


def test_sync_turn_includes_namespace_when_active(provider, http):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.sync_turn("user says something here", "assistant responds at length")
    _drain(provider)
    body = http.payload_for("/v1/memory/ingest")
    assert body["namespace"] == "tree-guardians"


def test_sync_turn_omits_namespace_when_inactive(provider, http):
    provider.sync_turn("user says something here", "assistant responds at length")
    _drain(provider)
    body = http.payload_for("/v1/memory/ingest")
    assert "namespace" not in body


def test_prefetch_includes_namespace_filter_when_active(provider, http):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "astral-core"},
    )
    provider.prefetch("query text", session_id="session-alpha")
    body = http.payload_for("/v1/memory/augmented-prompt")
    assert body["namespace_filter"] == "astral-core"


def test_prefetch_omits_namespace_filter_when_inactive(provider, http):
    provider.prefetch("query text", session_id="session-alpha")
    body = http.payload_for("/v1/memory/augmented-prompt")
    assert "namespace_filter" not in body


def test_recall_includes_namespace_filter_when_active(provider, http):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.handle_tool_call("astral_recall", {"query": "spruce"})
    body = http.payload_for("/v1/memory/search")
    assert body["namespace_filter"] == "tree-guardians"


def test_recall_omits_namespace_filter_when_inactive(provider, http):
    provider.handle_tool_call("astral_recall", {"query": "spruce"})
    body = http.payload_for("/v1/memory/search")
    assert "namespace_filter" not in body


def test_recall_per_call_override(provider, http):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.handle_tool_call(
        "astral_recall", {"query": "spruce", "namespace": "astral-core"},
    )
    body = http.payload_for("/v1/memory/search")
    assert body["namespace_filter"] == "astral-core"


def test_recall_all_overrides_active_namespace(provider, http):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.handle_tool_call(
        "astral_recall", {"query": "spruce", "namespace": "all"},
    )
    body = http.payload_for("/v1/memory/search")
    assert "namespace_filter" not in body


def test_store_includes_namespace_when_active(provider, http):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.handle_tool_call("astral_store", {"text": "remember this"})
    body = http.payload_for("/v1/memory/add")
    assert body["namespace"] == "tree-guardians"


def test_store_per_call_override(provider, http):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.handle_tool_call(
        "astral_store", {"text": "remember this", "namespace": "astral-core"},
    )
    body = http.payload_for("/v1/memory/add")
    assert body["namespace"] == "astral-core"


def test_store_omits_namespace_when_inactive(provider, http):
    provider.handle_tool_call("astral_store", {"text": "remember this"})
    body = http.payload_for("/v1/memory/add")
    assert "namespace" not in body


# ---------------------------------------------------------------------------
# STORE-1 regression pins (plugin v2.9.1)
#
#   Before v2.9.1 astral_store POSTed a synthetic dialogue to
#   /v1/memory/ingest. The server ran dyad extraction on it and persisted the
#   Dreamer's paraphrase instead of the caller's text (Finnish came back as
#   English summaries; segments_stored reported 0). These tests make that
#   route a hard failure.
# ---------------------------------------------------------------------------

_STORE_TEXT_FI = "Sovittu: komponenttiarkkitehtuuri säilyy, mikropalvelut eivät."


def test_store_uses_direct_add_not_ingest(provider, http):
    provider.handle_tool_call("astral_store", {"text": _STORE_TEXT_FI})
    _drain(provider)
    posts = http.paths("POST")
    assert "/v1/memory/add" in posts
    assert "/v1/memory/ingest" not in posts, (
        "astral_store must never route through the dyad extractor"
    )


def test_store_sends_text_verbatim_with_category_and_source(provider, http):
    provider.handle_tool_call(
        "astral_store", {"text": f"  {_STORE_TEXT_FI}  ", "category": "decision"},
    )
    body = http.payload_for("/v1/memory/add")
    assert body["text"] == _STORE_TEXT_FI          # trimmed, otherwise untouched
    assert body["category"] == "decision"
    assert body["source"] == "hermes_explicit_session-alpha"
    assert body["metadata"]["category"] == "decision"
    assert body["metadata"]["source"] == "hermes_explicit"
    # The old synthetic-dialogue shape must be gone entirely.
    assert "user_message" not in body
    assert "assistant_response" not in body


def test_store_default_category_is_fact(provider, http):
    provider.handle_tool_call("astral_store", {"text": "plain note"})
    body = http.payload_for("/v1/memory/add")
    assert body["category"] == "fact"
    assert body["metadata"]["category"] == "fact"


def test_store_response_is_normalised(provider, http):
    out = json.loads(provider.handle_tool_call(
        "astral_store", {"text": _STORE_TEXT_FI, "category": "decision"},
    ))
    assert out["success"] is True
    assert out["id"] == "mem-fake-0001"
    assert out["text"] == _STORE_TEXT_FI
    assert out["category"] == "decision"
    assert out["namespace"] is None          # nothing active in this fixture
    assert out["total_memories"] == 43
    assert "segments_stored" not in out      # old ingest counter is gone


def test_store_surfaces_server_error(provider, http, monkeypatch):
    def _post(path, payload=None, timeout=0):
        http.calls.append(("POST", path, payload))
        return {"error": "namespace rejected"}
    monkeypatch.setattr(http, "post", _post)
    out = json.loads(provider.handle_tool_call("astral_store", {"text": "x"}))
    assert out == {"error": "namespace rejected"}


def test_store_rejects_empty_text(provider, http):
    out = json.loads(provider.handle_tool_call("astral_store", {"text": "   "}))
    assert out == {"error": "text is required"}
    assert "/v1/memory/add" not in http.paths("POST")


def test_session_switch_resets_namespace(provider, http):
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.on_session_switch("session-new", reset=True)
    provider.sync_turn("user says something here", "assistant responds at length")
    _drain(provider)
    body = http.payload_for("/v1/memory/ingest")
    assert "namespace" not in body


def test_config_default_namespace_loaded(monkeypatch, tmp_path, http):
    """§8.1: default_namespace from config is applied on init."""
    (tmp_path / "astral-memory.json").write_text(json.dumps({
        "default_namespace": "astral-core",
    }))
    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url, *a, **kw: http)
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))
    assert p._config_namespace == "astral-core"
    # Active namespace should match config on session start.
    assert p._resolve_namespace() == "astral-core"
    p.shutdown()


def test_config_without_default_namespace(monkeypatch, tmp_path, http):
    """No default_namespace → cross-namespace mode."""
    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url, *a, **kw: http)
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))
    assert p._config_namespace == ""
    assert p._resolve_namespace() == ""
    p.shutdown()


def test_config_invalid_namespace_ignored(monkeypatch, tmp_path, http, caplog):
    """§8.2: invalid config namespace is logged and treated as empty."""
    (tmp_path / "astral-memory.json").write_text(json.dumps({
        "default_namespace": "INVALID UPPER",
    }))
    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url, *a, **kw: http)
    with caplog.at_level("WARNING", logger="astral-memory"):
        p = AstralCoreMemoryProvider()
        p.initialize("s", hermes_home=str(tmp_path))
    assert p._config_namespace == ""
    assert any("Invalid default_namespace" in r.getMessage() for r in caplog.records)
    p.shutdown()


def test_server_without_namespace_support_omits_params(monkeypatch, tmp_path):
    """§14.1: old server (no namespace_enabled in health) → params omitted."""

    class _NoNs(_FakeHttp):
        def health(self) -> dict:
            return {
                "status": "ok", "version": "old", "total_memories": 0,
                "embedding_backend": "fake", "deep_rerank_enabled": False,
                "hyde_enabled": False,
                # No namespace_enabled field.
            }

    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url, *a, **kw: _NoNs())
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))

    # Setting namespace should work (stored in state) but _resolve_namespace
    # returns "" because server doesn't support it.
    p.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    assert p._resolve_namespace() == ""

    # sync_turn should NOT include namespace in the payload.
    p.sync_turn("user says something here", "assistant responds at length")
    _drain(p)
    body = p._http.payload_for("/v1/memory/ingest")
    assert "namespace" not in body
    p.shutdown()


def test_delegation_inherits_namespace(provider, http):
    """§9.3: subagent results inherit the parent's namespace."""
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.on_delegation("do the thing", "did the thing", child_session_id="kid-1")
    _drain(provider)
    body = http.payload_for("/v1/memory/ingest")
    assert body["namespace"] == "tree-guardians"


def test_pre_compress_includes_namespace(provider, http):
    """§10.3: pre-compress capture inherits session namespace."""
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "astral-core"},
    )
    messages = [
        {"role": "user", "content": "a question long enough to pass the filter"},
        {"role": "assistant", "content": "an answer"},
    ]
    provider.on_pre_compress(messages)
    _drain(provider)
    body = http.payload_for("/v1/memory/ingest/batch")
    assert body["namespace"] == "astral-core"


def test_validate_namespace_function():
    """Direct unit tests for the validation function."""
    assert astral_memory._validate_namespace("tree-guardians") is None
    assert astral_memory._validate_namespace("a") is None
    assert astral_memory._validate_namespace("all") is None
    assert astral_memory._validate_namespace("abc123") is None

    assert astral_memory._validate_namespace("") is not None
    assert astral_memory._validate_namespace("UPPER") is not None
    assert astral_memory._validate_namespace("has space") is not None
    assert astral_memory._validate_namespace("-leading") is not None
    assert astral_memory._validate_namespace("trailing-") is not None
    assert astral_memory._validate_namespace("x" * 65) is not None
    assert astral_memory._validate_namespace("under_score") is not None


def test_save_config_validates_namespace(tmp_path):
    """save_config should reject invalid namespace and store empty."""
    p = AstralCoreMemoryProvider()
    p.save_config({"default_namespace": "INVALID"}, str(tmp_path))
    cfg = json.loads((tmp_path / "astral-memory.json").read_text())
    assert cfg["default_namespace"] == ""


def test_save_config_accepts_valid_namespace(tmp_path):
    p = AstralCoreMemoryProvider()
    p.save_config({"default_namespace": "tree-guardians"}, str(tmp_path))
    cfg = json.loads((tmp_path / "astral-memory.json").read_text())
    assert cfg["default_namespace"] == "tree-guardians"


def test_briefing_is_cross_namespace(provider, http):
    """§7.1: briefings are intentionally cross-namespace."""
    provider.handle_tool_call(
        "astral_namespace", {"action": "set", "namespace": "tree-guardians"},
    )
    provider.handle_tool_call("astral_briefing", {})
    # Briefing endpoint should NOT receive a namespace filter.
    for _, path, body in http.calls:
        if path == "/v1/memory/briefing":
            assert body is None or "namespace" not in (body or {})
            return
    # If we get here, the briefing call didn't happen — that's also fine.


# ---------------------------------------------------------------------------
# 9. The guard itself
# ---------------------------------------------------------------------------

def test_assert_contract_passes_on_current_provider(caplog):
    assert astral_memory._assert_contract(AstralCoreMemoryProvider()) is True


def test_assert_contract_catches_the_v2_1_0_regression(caplog):
    """Reconstruct the exact bug and confirm the guard would have caught it."""

    class _Regressed(AstralCoreMemoryProvider):
        def prefetch(self, query):  # type: ignore[override]  # the v2.1.0 signature
            return ""

    with caplog.at_level("ERROR", logger="astral-memory"):
        assert astral_memory._assert_contract(_Regressed()) is False

    messages = [r.getMessage() for r in caplog.records]
    assert any("prefetch" in m and "session_id" in m for m in messages), (
        f"guard did not name the offending method/parameter; got {messages}"
    )


# ---------------------------------------------------------------------------
# 10. RT-10 confidence rendering (plugin v2.11.0, SPEC-METAMEMORY-001 v1.1)
#
#   The server annotates search results with a `confidence` object
#   {score, label, reason} (absent on pre-RT-10 servers / kill switch).
#   The plugin renders LOW/MEDIUM as the mandated warning string and is
#   silent on HIGH; results without the key render exactly as v2.10.0.
# ---------------------------------------------------------------------------


def _serve_search(http, monkeypatch, results):
    """Route /v1/memory/search to canned results (fresh copy per call).

    v1.4.0 fix: this wrapper used to append EVERY post to http.calls and
    then delegate non-search paths to _FakeHttp.post — which appends the
    same call again. No earlier test counted calls under this monkeypatch
    so the duplicate entries were harmless; the RT-13 tests count fidelity
    POSTs and must read exactly one entry per real request. Append only on
    the path handled here; delegation stays single-append.
    """
    def _post(path, payload=None, timeout=0):
        if path == "/v1/memory/search":
            http.calls.append(("POST", path, payload))
            return {"results": json.loads(json.dumps(results))}
        return _FakeHttp.post(http, path, payload, timeout)
    monkeypatch.setattr(http, "post", _post)


def test_recall_renders_low_confidence_warning(provider, http, monkeypatch):
    _serve_search(http, monkeypatch, [
        {"id": "m-low", "text": "Client discussed possible budget reduction",
         "similarity": 0.71,
         "confidence": {"score": 0.31, "label": "low",
                        "reason": "single observation, 122 days old"}},
    ])
    out = provider.handle_tool_call("astral_recall", {"query": "budget"})
    assert "\u26a0 low confidence: single observation, 122 days old" in out


def test_recall_silent_on_high(provider, http, monkeypatch):
    _serve_search(http, monkeypatch, [
        {"id": "m-high", "text": "API rate limit is 1000 requests per hour",
         "similarity": 0.78,
         "confidence": {"score": 0.82, "label": "high",
                        "reason": "well-established"}},
    ])
    out = provider.handle_tool_call("astral_recall", {"query": "rate limit"})
    assert "\u26a0" not in out
    assert "confidence" not in out, (
        "HIGH must be silent — absence of a warning is the high-confidence "
        "signal (SPEC-METAMEMORY-001 v1.1 §7.1)"
    )


def test_recall_medium_renders(provider, http, monkeypatch):
    _serve_search(http, monkeypatch, [
        {"id": "m-med", "text": "Budget review scheduled for Q3",
         "similarity": 0.55,
         "confidence": {"score": 0.52, "label": "medium",
                        "reason": "mixed signals"}},
    ])
    out = provider.handle_tool_call("astral_recall", {"query": "budget"})
    parsed = json.loads(out)
    conf = parsed["results"][0]["confidence"]
    assert conf == "medium: mixed signals"
    assert "\u26a0" not in conf, "glyph is reserved for LOW (E9)"
    assert "confidence_legend" in parsed


def test_recall_medium_empty_reason_label_only(provider, http, monkeypatch):
    _serve_search(http, monkeypatch, [
        {"id": "m-med-quiet", "text": "Middling, quiet reason", "similarity": 0.5,
         "confidence": {"score": 0.45, "label": "medium", "reason": ""}},
    ])
    out = provider.handle_tool_call("astral_recall", {"query": "q"})
    parsed = json.loads(out)
    assert parsed["results"][0]["confidence"] == "medium"
    assert "confidence_legend" in parsed


def test_recall_empty_reason_label_only(provider, http, monkeypatch):
    _serve_search(http, monkeypatch, [
        {"id": "m-quiet", "text": "Quiet reason", "similarity": 0.44,
         "confidence": {"score": 0.3, "label": "low", "reason": ""}},
    ])
    out = provider.handle_tool_call("astral_recall", {"query": "q"})
    parsed = json.loads(out)
    assert parsed["results"][0]["confidence"] == "\u26a0 low confidence"
    assert "\u26a0 low confidence: " not in out  # no trailing colon-space


def test_recall_feature_detect_absent_key(provider, http, monkeypatch):
    """No confidence key → output identical to the v2.10.0 render."""
    raw = {"results": [
        {"id": "m-legacy-1", "text": "alpha memory", "similarity": 0.72,
         "source_role": "user"},
        {"id": "m-legacy-2", "text": "beta memory", "similarity": 0.41},
    ]}
    _serve_search(http, monkeypatch, raw["results"])
    out = provider.handle_tool_call("astral_recall", {"query": "q"})

    # The v2.10.0 render: provenance tag where source_role is known, the
    # legend once, and nothing else. Key order matters for a byte compare:
    # `provenance` lands after the result's own keys, `provenance_legend`
    # after `results` — both appended, exactly as v2.10.0 did. The golden
    # must mirror the render's dump kwargs exactly, including the v2.11.0
    # ensure_ascii=False encoding change — the legend carries an em-dash,
    # which the default escaping would render as \u2014.
    golden = json.loads(json.dumps(raw))
    golden["results"][0]["provenance"] = "[user]"
    golden["provenance_legend"] = astral_memory._PROVENANCE_LEGEND
    assert out == json.dumps(golden, indent=2, default=str, ensure_ascii=False)


def test_recall_malformed_confidence(provider, http, monkeypatch):
    _serve_search(http, monkeypatch, [
        {"id": "m-bad-1", "text": "empty object", "similarity": 0.5,
         "confidence": {}},
        {"id": "m-bad-2", "text": "weird label", "similarity": 0.5,
         "confidence": {"label": "weird", "reason": "???"}},
    ])
    out = provider.handle_tool_call("astral_recall", {"query": "q"})
    parsed = json.loads(out)
    assert "confidence" not in parsed["results"][0]
    assert "confidence" not in parsed["results"][1]
    assert "\u26a0" not in out


# ---------------------------------------------------------------------------
# 11. RT-13 response fidelity (plugin v2.11.1 — P0 regression pin)
#
#   v2.10.0 placed the capture guard (D) ABOVE the _fidelity_after_turn call
#   in sync_turn: a turn quoting the distilled block ("[astral-distillation
#   v…") returned early, skipping A1 AND stashing no _fidelity_pending —
#   which also killed the next turn's A2. No /v1/memories/fidelity POST
#   reached the server for 18 days (2026-09-03 18:51 → 2026-09-22).
#
#   These pin: the hooks observe every non-empty turn (marker turns and
#   auto-capture-off included — observation is not capture), guard D still
#   skips the INGEST for marker turns, and the P0 instrumentation emits
#   exactly one INFO line per hook invocation plus one per POST result.
#
#   fidelity.py classification logic is deliberately NOT re-tested here
#   (it has its own self-test); these are wiring tests only.
# ---------------------------------------------------------------------------

_RT13_MEMORY = "API rate limit is 1000 requests per hour"


def _rt13_serve_one(provider, http, monkeypatch):
    """Serve exactly one recallable memory via the astral_recall path."""
    _serve_search(http, monkeypatch, [
        {"id": "m-rt13-1", "text": _RT13_MEMORY, "similarity": 0.9},
    ])
    provider.handle_tool_call("astral_recall", {"query": "rate limit"})
    assert provider._served_texts.get("session-alpha", {}).get("m-rt13-1")


def _rt13_posts(http) -> list:
    return [b for m, p, b in http.calls if p == "/v1/memories/fidelity"]


def test_fidelity_marker_turn_still_observed(provider, http, monkeypatch):
    """The P0 regression (brief §P0 commit 1, item 3): a turn whose
    user_content contains `[astral-distillation v…` must still issue a
    fidelity POST — exactly one for that turn — while the INGEST is
    skipped (guard D, unchanged)."""
    _serve_search(http, monkeypatch, [
        {"id": "m-rt13-1", "text": "Client prefers async email over meetings",
         "similarity": 0.88},
    ])
    provider.handle_tool_call("astral_recall", {"query": "communication"})

    # Gateway-style user turn: the Signal message quotes the distilled
    # block back (the digest-consent flow makes this routine on bot-jarmo).
    marker_user_msg = (
        "why does my digest still say this?\n"
        "<!-- astral:distilled:begin [astral-distillation v1.2.0] — "
        "auto-generated, do not edit -->\n"
        "- Client prefers async email over meetings\n"
        "<!-- astral:distilled:end -->"
    )
    provider.sync_turn(
        marker_user_msg,
        "You prefer async email over meetings, so the digest keeps it "
        "as a standing claim.",
    )

    # Guard D intact: the marker turn must NOT re-enter the store...
    assert "/v1/memory/ingest" not in http.paths("POST")
    assert provider._capture_skipped_distilled == 1
    # ...but the A2 context must have been stashed anyway.
    pending = provider._fidelity_pending.get("session-alpha")
    assert pending is not None, (
        "guard (D) must not gate _fidelity_pending — its early return is "
        "what silenced RT-13 for 18 days"
    )
    assert pending["served"] == ["m-rt13-1"]

    provider.on_turn_start(2, "Thanks, what about the user file?")
    _drain(provider)

    fid = _rt13_posts(http)
    # Exactly one POST for the marker turn (A1) + one for the follow-up
    # (A2) — no more, no less.
    assert len(fid) == 2, [b for _, p, b in http.calls if p == "/v1/memories/fidelity"]
    layers = {s["layer"] for body in fid for s in body["signals"]}
    assert layers == {"A1", "A2"}
    for body in fid:
        assert body["session_id"] == "session-alpha"
        assert body["source"] == f"hermes-{astral_memory._VERSION}"
        assert body["cosine_available"] is False
        assert {s["memory_id"] for s in body["signals"]} == {"m-rt13-1"}
        assert all("text" not in s for s in body["signals"]), (
            "payload is ids-only (A5 §2.4: never memory text)"
        )


def test_fidelity_observed_with_auto_capture_off(provider, http, monkeypatch):
    """Observation is not capture: auto_capture=false silences the ingest,
    never the fidelity channel (it has its own `fidelity` config gate)."""
    provider._auto_capture = False
    _rt13_serve_one(provider, http, monkeypatch)

    provider.sync_turn("what's the API rate limit?",
                       "The API rate limit is 1000 requests per hour.")
    provider.on_turn_start(2, "Thanks, what about retries?")
    _drain(provider)

    assert "/v1/memory/ingest" not in http.paths("POST")
    assert _rt13_posts(http), (
        "fidelity must observe turns even when auto-capture is disabled"
    )


def test_fidelity_info_one_line_per_hook_invocation(
        provider, http, monkeypatch, caplog):
    """P0 instrumentation: exactly one INFO line per hook invocation
    (session, turn, signals) and one on each POST result. An 18-day
    silence must be visible without debug logging."""
    with caplog.at_level(logging.INFO, logger="astral-memory"):
        _rt13_serve_one(provider, http, monkeypatch)

        provider.on_turn_start(1, "what's the API rate limit?")
        provider.sync_turn("what's the API rate limit?",
                           "The API rate limit is 1000 requests per hour.")
        provider.on_turn_start(2, "Thanks, what about retries?")
        _drain(provider)

    lines = [r.getMessage() for r in caplog.records
             if r.levelno == logging.INFO and "rt13_fidelity" in r.getMessage()]
    after = [l for l in lines if "hook=after_turn" in l]
    nxt = [l for l in lines if "hook=next_turn" in l]
    posts = [l for l in lines if l.startswith("rt13_fidelity: post ")]

    # One line per invocation: 1x sync_turn, 2x on_turn_start.
    assert len(after) == 1, lines
    assert len(nxt) == 2, lines
    assert len(posts) == 2, lines  # A1 same-turn + A2 next-turn

    assert "session=session-alpha" in after[0]
    assert "turn=1" in after[0]
    assert "served=1" in after[0]
    assert "signals=1" in after[0]

    assert "session=session-alpha" in nxt[1]
    assert "verdict=POSITIVE" in nxt[1]
    assert "signals=1" in nxt[1]
    # The no-pending signature — the exact smoke the P0 left behind: the
    # hook fires but the sync_turn-side stash never happened.
    assert "verdict=no_pending" in nxt[0]

    assert all("result=ok" in l and "signals=1" in l for l in posts)


def test_fidelity_hook_line_when_nothing_served(provider, http, caplog):
    """The hook line fires even when nothing was served — 'hook alive,
    zero served' must be distinguishable from 'hook never invoked'."""
    with caplog.at_level(logging.INFO, logger="astral-memory"):
        provider.sync_turn("hello there", "hi!")
    _drain(provider)

    lines = [r.getMessage() for r in caplog.records
             if r.levelno == logging.INFO and "hook=after_turn" in r.getMessage()]
    assert len(lines) == 1
    assert "served=0" in lines[0] and "signals=0" in lines[0]
    assert "/v1/memories/fidelity" not in http.paths("POST")


def test_fidelity_post_error_logged_at_info(provider, http, monkeypatch, caplog):
    """A rejected POST gets its result line too — failures were invisible
    at the old DEBUG level."""
    def _post(path, payload=None, timeout=0):
        # NOTE: installed AFTER _rt13_serve_one so this wrapper wins over
        # _serve_search's routing for the fidelity path.
        if path == "/v1/memories/fidelity":
            http.calls.append(("POST", path, payload))
            return {"error": "fidelity disabled server-side"}
        return _FakeHttp.post(http, path, payload, timeout)

    with caplog.at_level(logging.INFO, logger="astral-memory"):
        _rt13_serve_one(provider, http, monkeypatch)
        monkeypatch.setattr(http, "post", _post)
        provider.on_turn_start(1, "what's the API rate limit?")
        provider.sync_turn("what's the API rate limit?",
                           "The API rate limit is 1000 requests per hour.")
        _drain(provider)

    posts = [r.getMessage() for r in caplog.records
             if r.levelno == logging.INFO
             and r.getMessage().startswith("rt13_fidelity: post ")]
    assert len(posts) == 1
    assert "result=error" in posts[0]
    assert "fidelity disabled server-side" in posts[0]


# ---------------------------------------------------------------------------
# 12. Status line /health keys (plugin v2.11.1, §7 of the 2026-09-21 brief)
#
#   Server >= 2.16.1 /health carries `embedding` ("onnx:1024"), `hyde`,
#   `deep_rerank`, `temporal_retrieval`, `metamemory`. The plugin printed
#   embedding=?, rerank=off, hyde=off against it. It must read the new
#   keys first and fall back to the legacy ones for older servers.
# ---------------------------------------------------------------------------


def test_status_line_reads_server_2161_health_keys(
        monkeypatch, tmp_path, http, caplog):
    class _NewHealth(_FakeHttp):
        def health(self):
            return {
                "status": "ok", "version": "2.16.1", "total_memories": 1234,
                "embedding": "onnx:1024", "hyde": True, "deep_rerank": True,
                "temporal_retrieval": True, "metamemory": True,
                "namespace_enabled": True,
            }

    monkeypatch.setattr(astral_memory, "_HttpClient",
                        lambda url, *a, **kw: _NewHealth())
    p = AstralCoreMemoryProvider()
    with caplog.at_level(logging.INFO, logger="astral-memory"):
        p.initialize("s", hermes_home=str(tmp_path))
    p.shutdown()

    lines = [r.getMessage() for r in caplog.records
             if "Astral Core Memory v" in r.getMessage()]
    assert len(lines) == 1
    # §7 acceptance: embedding=onnx:1024, rerank=on, hyde=on.
    assert "embedding=onnx:1024" in lines[0]
    assert "rerank=on" in lines[0]
    assert "hyde=on" in lines[0]
    assert "temporal=on" in lines[0]
    assert "metamemory=on" in lines[0]


def test_status_line_falls_back_to_legacy_health_keys(
        monkeypatch, tmp_path, http, caplog):
    """_FakeHttp.health() carries only the legacy keys — the render must
    be unchanged for pre-2.16.1 servers (embedding_backend, …_enabled)."""
    monkeypatch.setattr(astral_memory, "_HttpClient",
                        lambda url, *a, **kw: http)
    p = AstralCoreMemoryProvider()
    with caplog.at_level(logging.INFO, logger="astral-memory"):
        p.initialize("s", hermes_home=str(tmp_path))
    p.shutdown()

    lines = [r.getMessage() for r in caplog.records
             if "Astral Core Memory v" in r.getMessage()]
    assert len(lines) == 1
    assert "embedding=fake" in lines[0]
    assert "rerank=off" in lines[0]
    assert "hyde=off" in lines[0]
